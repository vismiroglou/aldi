import os
import torch
import copy
import logging
from torch.nn.parallel import DistributedDataParallel as DDP

from detectron2.data.build import build_detection_train_loader, get_detection_dataset_dicts
from detectron2.engine import hooks, BestCheckpointer
from detectron2.evaluation import COCOEvaluator, DatasetEvaluators
from detectron2.modeling.meta_arch.build import build_model
from detectron2.solver import build_optimizer
from detectron2.utils.events import get_event_storage
from detectron2.utils import comm

from aug import WEAK_IMG_KEY, get_augs
from dropin import DefaultTrainer, AMPTrainer, SimpleTrainer
from dataloader import SaveWeakDatasetMapper, UnlabeledDatasetMapper, WeakStrongDataloader
from ema import EMA
from pseudolabeler import PseudoLabeler


DEBUG = False

def run_model_labeled_unlabeled(model, labeled_weak, labeled_strong, unlabeled_weak, unlabeled_strong, 
                                pseudo_labeler, trainer=None):
     """
     Main logic of running Mean Teacher style training for one iteration.
     - If no unlabeled data is supplied, run a training step as usual.
     - If unlabeled data is supplied, use teacher to create pseudo-labels
     """
     loss_dict = {}

     #### Weakly augmented source imagery
     #### (Used for normal training and/or domain alignment)
     do_weak = labeled_weak is not None
     _model = model.module if type(model) == DDP else model
     do_sada = _model.sada_heads is not None
     if do_weak or do_sada:
          # Added try/catch for debugging Probabilistic Teacher - can hopefully remove later
          try:
               loss_weak = model(labeled_weak, do_sada=do_sada)
          except FloatingPointError as e:
               print("Floating point error in weak forward pass. Skipping batch.")
               torch.save(labeled_weak, "labeled_weak_bad_batch.pt")
               return {"bad_loss": torch.tensor(0, device="cuda")}
          for k, v in loss_weak.items():
               if do_weak or (do_sada and "_da_" in k):
                    loss_dict[f"{k}_source_weak"] = v

     #### Weakly augmented target imagery
     #### (Only used for domain alignment)
     if do_sada:
          loss_sada_target = model(unlabeled_weak, labeled=False, do_sada=True)
          for k, v in loss_sada_target.items():
               if "_da_" in k:
                    loss_dict[f"{k}_target_weak"] = v

     #### Strongly augmented source imagery
     do_strong = labeled_strong is not None
     if do_strong:
          loss_strong = model(labeled_strong, do_sada=False)
          for k, v in loss_strong.items():
               loss_dict[f"{k}_source_strong"] = v

     if DEBUG and trainer is not None:
          trainer._last_labeled = copy.deepcopy(labeled_strong)
          trainer._last_unlabeled = copy.deepcopy(unlabeled_strong)

     #### Pseudo-labeled target imagery
     do_unlabeled = unlabeled_weak is not None
     if do_unlabeled:
          if DEBUG and trainer is not None:
               data_to_pseudolabel = unlabeled_strong if unlabeled_strong is not None else unlabeled_weak
               trainer._last_unlabeled_before_teacher = copy.deepcopy(data_to_pseudolabel)

          pseudolabeled_data = pseudo_labeler(unlabeled_weak, unlabeled_strong)
               
          if DEBUG and trainer is not None:
               trainer._last_unlabeled_after_teacher = copy.deepcopy(data_to_pseudolabel)
               with torch.no_grad():
                    model.eval()
                    if type(model) == DDP:
                         trainer._last_student_preds = model.module.inference(data_to_pseudolabel, do_postprocess=False)
                    else:
                         trainer._last_student_preds = model.inference(data_to_pseudolabel, do_postprocess=False)
                    model.train()

          losses_pseudolabeled = model(pseudolabeled_data, labeled=False, do_sada=False)
          for k, v in losses_pseudolabeled.items():
               loss_dict[k + "_pseudo"] = v
     
     # scale the loss to account for the gradient accumulation we've done
     num_grad_accum_steps = int(do_weak) + int(do_strong) +  int(do_unlabeled)
     for k, v in loss_dict.items():
          loss_dict[k] = v / num_grad_accum_steps

     return loss_dict

class DAAMPTrainer(AMPTrainer):
     def __init__(self, model, data_loader, optimizer, pseudo_labeler):
          super().__init__(model, data_loader, optimizer)
          self.pseudo_labeler = pseudo_labeler

     def run_model(self, data):
          return run_model_labeled_unlabeled(self.model, *data, self.pseudo_labeler, trainer=self)
     
class DASimpleTrainer(SimpleTrainer):
     def __init__(self, model, data_loader, optimizer, pseudo_labeler):
          super().__init__(model, data_loader, optimizer)
          self.pseudo_labeler = pseudo_labeler

     def run_model(self, data):
          return run_model_labeled_unlabeled(self.model, *data, self.pseudo_labeler, trainer=self)
     
class DATrainer(DefaultTrainer):
     def _create_trainer(self, cfg, model, data_loader, optimizer):
          # build EMA model if applicable
          ema = EMA(build_model(cfg), cfg.EMA.ALPHA) if cfg.EMA.ENABLED else None
          pseudo_labeler = PseudoLabeler(cfg, ema or model) # if no EMA, student model creates its own pseudo-labels
          trainer = (DAAMPTrainer if cfg.SOLVER.AMP.ENABLED else DASimpleTrainer)(model, data_loader, optimizer, pseudo_labeler)
          trainer.ema = ema
          return trainer
     
     def _create_checkpointer(self, model, cfg):
          checkpointer = super(DATrainer, self)._create_checkpointer(model, cfg)
          if cfg.EMA.ENABLED:
               checkpointer.add_checkpointable("ema", self._trainer.ema.model)
          return checkpointer

     @classmethod
     def build_evaluator(cls, cfg, dataset_name, output_folder=None):
        """Just do COCO Evaluation."""
        if output_folder is None:
            output_folder = os.path.join(cfg.OUTPUT_DIR, "inference")
        return DatasetEvaluators([COCOEvaluator(dataset_name, output_dir=output_folder)])

     def build_hooks(self):
          ret = super(DATrainer, self).build_hooks()

          # add hooks to evaluate/save teacher model if applicable
          if self.cfg.EMA.ENABLED:
               def test_and_save_results_ema():
                    self._last_eval_results = self.test(self.cfg, self._trainer.ema.model)
                    return self._last_eval_results
               eval_hook = hooks.EvalHook(self.cfg.TEST.EVAL_PERIOD, test_and_save_results_ema)
               if comm.is_main_process():
                    ret.insert(-1, eval_hook) # before PeriodicWriter if in main process
               else:
                    ret.append(eval_hook)

          # add a hook to save the best (teacher, if EMA enabled) checkpoint to model_best.pth
          if comm.is_main_process():
               for test_set in self.cfg.DATASETS.TEST:
                    ret.insert(-1, BestCheckpointer(self.cfg.TEST.EVAL_PERIOD, self.checkpointer,
                                                    f"{test_set}/bbox/AP50", "max", file_prefix=f"{test_set}_model_best"))

          return ret
     
     @classmethod
     def build_optimizer(cls, cfg, model):
          """
          Change the learning rate to account for the fact that we are doing gradient accumulation in order
          to run multiple batches of labeled and unlabeled data each training step.
          """
          logger = logging.getLogger("detectron2")
          num_grad_accum = len(cfg.DATASETS.BATCH_CONTENTS)
          effective_batch_size = cfg.SOLVER.IMS_PER_BATCH * num_grad_accum
          lr_scale = effective_batch_size / cfg.SOLVER.IMS_PER_BATCH
          logger.info(f"Effective batch size is {effective_batch_size} due to {num_grad_accum} gradient accumulation steps.")
          logger.info(f"Scaling LR from {cfg.SOLVER.BASE_LR} to {lr_scale * cfg.SOLVER.BASE_LR}.")

          cfg.defrost()
          cfg.SOLVER.BASE_LR = lr_scale * cfg.SOLVER.BASE_LR
          cfg.freeze()
          return super(DATrainer, cls).build_optimizer(cfg, model)

     @classmethod
     def build_train_loader(cls, cfg):
          batch_contents = cfg.DATASETS.BATCH_CONTENTS
          batch_sizes = [ int(r * cfg.SOLVER.IMS_PER_BATCH) for r in cfg.DATASETS.BATCH_RATIOS ]
          assert len(batch_contents) == len(batch_sizes), "len(cfg.DATASETS.BATCH_CONTENTS) must equal len(cfg.DATASETS.BATCH_RATIOS)."
          labeled_bs = [batch_sizes[i] for i in range(len(batch_contents)) if batch_contents[i].startswith("labeled")]
          labeled_bs = max(labeled_bs) if len(labeled_bs) else 0
          unlabeled_bs = [batch_sizes[i] for i in range(len(batch_contents)) if batch_contents[i].startswith("unlabeled")]
          unlabeled_bs = max(unlabeled_bs) if len(unlabeled_bs) else 0

          # create labeled dataloader
          labeled_loader = None
          if labeled_bs > 0 and len(cfg.DATASETS.TRAIN):
               labeled_loader = build_detection_train_loader(get_detection_dataset_dicts(cfg.DATASETS.TRAIN, filter_empty=cfg.DATALOADER.FILTER_EMPTY_ANNOTATIONS), 
                    mapper=SaveWeakDatasetMapper(cfg, is_train=True, augmentations=get_augs(cfg, labeled=True, include_strong_augs="labeled_strong" in batch_contents)),
                    num_workers=cfg.DATALOADER.NUM_WORKERS, 
                    total_batch_size=labeled_bs)

          # create unlabeled dataloader
          unlabeled_loader = None
          if unlabeled_bs > 0 and len(cfg.DATASETS.UNLABELED):
               unlabeled_loader = build_detection_train_loader(get_detection_dataset_dicts(cfg.DATASETS.UNLABELED, filter_empty=cfg.DATALOADER.FILTER_EMPTY_ANNOTATIONS), 
                    mapper=UnlabeledDatasetMapper(cfg, is_train=True, augmentations=get_augs(cfg, labeled=False, include_strong_augs="unlabeled_strong" in batch_contents)),
                    num_workers=cfg.DATALOADER.NUM_WORKERS,
                    total_batch_size=unlabeled_bs)

          return WeakStrongDataloader(labeled_loader, unlabeled_loader, batch_contents)
     
     def before_step(self):
          super(DATrainer, self).before_step()
          if self.cfg.EMA.ENABLED:
               self._trainer.ema.update_weights(self._trainer.model, self.iter)