import os
import torch

from detectron2.engine import DefaultTrainer
from detectron2.evaluation import COCOEvaluator, DatasetEvaluators
from detectron2.modeling.meta_arch.build import build_model
from detectron2.data.build import build_detection_train_loader, get_detection_dataset_dicts
from detectron2.data.dataset_mapper import DatasetMapper

from aug import build_strong_augmentation
from dataloader import UnlabeledDatasetMapper, PrefetchableConcatDataloaders
from ema import EmaRCNN
from pseudolabels import process_pseudo_label, add_label


class DATrainer(DefaultTrainer):
    """
    Main idea:
        We are just "training" the student.
            -> "Step" in the training loop refers to a student step.
            Problem here is we may want different losses for labeled/unlabeld data
        We may be updating a teacher model.
        We may also be "training" other networks.
        But the Trainer is training the student.

    Assumption:
        Student is already burned in by another trainer (?).
    """
    
    @classmethod
    def build_evaluator(cls, cfg, dataset_name, output_folder=None):
        """Just do COCO Evaluation."""
        if output_folder is None:
            output_folder = os.path.join(cfg.OUTPUT_DIR, "inference")
        return DatasetEvaluators([COCOEvaluator(dataset_name, output_dir=output_folder)])
    
    def __init__(self, cfg):
        super().__init__(cfg)

        # EMA of student
        if cfg.DOMAIN_ADAPT.EMA.ENABLED:
            self.ema = EmaRCNN(build_model(cfg), cfg.DOMAIN_ADAPT.EMA.ALPHA)

        self.strong_aug = build_strong_augmentation()

    @classmethod
    def build_train_loader(cls, cfg):
        total_batch_size = cfg.SOLVER.IMS_PER_BATCH
        labeled_bs, unlabeled_bs = ( int(r * total_batch_size / sum(cfg.DATASETS.LABELED_UNLABELED_RATIO)) for r in cfg.DATASETS.LABELED_UNLABELED_RATIO )
        loaders = []

        labeled_loader = build_detection_train_loader(get_detection_dataset_dicts(
                cfg.DATASETS.TRAIN,
                filter_empty=cfg.DATALOADER.FILTER_EMPTY_ANNOTATIONS), 
            mapper=DatasetMapper(cfg, is_train=True), # default mapper
            num_workers=cfg.DATALOADER.NUM_WORKERS, # should we do this? two dataloaders...
            total_batch_size=labeled_bs)
        loaders.append(labeled_loader)

        # if we are utilizing unlabeled data, add it to the dataloader
        if unlabeled_bs > 0 and len(cfg.DATASETS.UNLABELED):
            unlabeled_loader = build_detection_train_loader(get_detection_dataset_dicts(
                    cfg.DATASETS.UNLABELED,
                    filter_empty=cfg.DATALOADER.FILTER_EMPTY_ANNOTATIONS), 
                mapper=UnlabeledDatasetMapper(cfg, is_train=True),
                num_workers=cfg.DATALOADER.NUM_WORKERS, # should we do this? two dataloaders...
                total_batch_size=unlabeled_bs)
            loaders.append(unlabeled_loader)

        return PrefetchableConcatDataloaders(loaders)

    def run_step(self):
        """Remember that self._trainer is the student trainer."""
        
        # Prefetch dataloader batch so we can add pseudo labels from teacher as needed
        data = self._trainer.data_loader.prefetch_batch()
        if len(data) == 1:
            labeled, unlabeled = data, None
        elif len(data) == 2:
            labeled, unlabeled = data
        else:
            raise ValueError("Unsupported number of dataloaders")
        
        # EMA update
        if self.cfg.DOMAIN_ADAPT.EMA.ENABLED:
            self.ema.update_weights(self.model, self.iter)

        # Teacher-student self-training
        if self.cfg.DOMAIN_ADAPT.TEACHER.ENABLED:
            with torch.no_grad():
                # run teacher on weakly augmented data
                self.ema.eval()
                _, teacher_preds, _ = self.ema(unlabeled)
                
                # postprocess pseudo labels
                teacher_preds, _ = process_pseudo_label(teacher_preds, self.cfg.DOMAIN_ADAPT.TEACHER.THRESHOLD, 
                                                             "roih", self.cfg.DOMAIN_ADAPT.TEACHER.PSEUDO_LABEL_METHOD)
                # add pseudo labels as "ground truth"
                unlabeled = add_label(unlabeled, teacher_preds)

        # apply stronger augmentation
        if self.cfg.DOMAIN_ADAPT.LABELED_STRONG_AUG:
            for img in labeled:
                img["image"] = self.strong_aug(img["image"])
        if self.cfg.DOMAIN_ADAPT.UNLABELED_STRONG_AUG and unlabeled is not None:
            for img in unlabeled:
                img["image"] = self.strong_aug(img["image"])

        # now call student.run_step as normal
        # problem is this doesn't allow custom loss functions (or filtering some losses out)
        # docs say "if you want to do something with the losses, you can wrap the model"
        self._trainer.iter = self.iter
        self._trainer.run_step()