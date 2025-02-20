from detectron2.data.datasets import register_coco_instances

# Cityscapes 
register_coco_instances("cityscapes_train", {},         "datasets/cityscapes/annotations/cityscapes_train_instances.json",                  "datasets/cityscapes/leftImg8bit/train/")
register_coco_instances("cityscapes_val",   {},         "datasets/cityscapes/annotations/cityscapes_val_instances.json",                    "datasets/cityscapes/leftImg8bit/val/")

# Foggy Cityscapes
register_coco_instances("cityscapes_foggy_train", {},   "datasets/cityscapes/annotations/cityscapes_train_instances_foggyALL.json",   "datasets/cityscapes/leftImg8bit_foggy/train/")
register_coco_instances("cityscapes_foggy_val", {},     "datasets/cityscapes/annotations/cityscapes_val_instances_foggyALL.json",     "datasets/cityscapes/leftImg8bit_foggy/val/")
# for evaluating COCO-pretrained models: category IDs are remapped to match
register_coco_instances("cityscapes_foggy_val_coco_ids", {},     "datasets/cityscapes/annotations/cityscapes_val_instances_foggyALL_coco.json",     "datasets/cityscapes/leftImg8bit_foggy/val/")

# Sim10k
register_coco_instances("sim10k_cars_train", {},             "datasets/sim10k/coco_car_annotations.json",                  "datasets/sim10k/images/")
register_coco_instances("cityscapes_cars_train", {},         "datasets/cityscapes/annotations/cityscapes_train_instances_cars.json",                  "datasets/cityscapes/leftImg8bit/train/")
register_coco_instances("cityscapes_cars_val",   {},         "datasets/cityscapes/annotations/cityscapes_val_instances_cars.json",                    "datasets/cityscapes/leftImg8bit/val/")

# CFC
register_coco_instances("cfc_train", {},         "datasets/cfc_daod/coco_labels/cfc_train.json",                  "datasets/cfc_daod/images/cfc_train/")
register_coco_instances("cfc_val",   {},         "datasets/cfc_daod/coco_labels/cfc_val.json",                    "datasets/cfc_daod/images/cfc_val/")
register_coco_instances("cfc_channel_train", {},         "datasets/cfc_daod/coco_labels/cfc_channel_train.json",                  "datasets/cfc_daod/images/cfc_channel_train/")
register_coco_instances("cfc_channel_test",   {},         "datasets/cfc_daod/coco_labels/cfc_channel_test.json",                    "datasets/cfc_daod/images/cfc_channel_test/")

# RUOD
register_coco_instances("ruod_train", {}, "/home/vismiroglou/datasets/RUOD/RUOD_OD/labels/instances_train.json", "/home/vismiroglou/datasets/RUOD/RUOD_OD/images/train")
register_coco_instances("ruod_val", {}, "/home/vismiroglou/datasets/RUOD/RUOD_OD/labels/instances_test.json", "/home/vismiroglou/datasets/RUOD/RUOD_OD/images/test")

#Brackish
register_coco_instances("brackish_train", {}, "/home/vismiroglou/datasets/brackish/annotations/annotations_COCO/train_groundtruth.json", "/home/vismiroglou/datasets/brackish/yolo_r2train/images/train")
register_coco_instances("brackish_val", {}, "/home/vismiroglou/datasets/brackish/annotations/annotations_COCO/valid_groundtruth.json", "/home/vismiroglou/datasets/brackish/yolo_r2train/images/val")
register_coco_instances("brackish_test", {}, "/home/vismiroglou/datasets/brackish/annotations/annotations_COCO/test_groundtruth.json", "/home/vismiroglou/datasets/brackish/yolo_r2train/images/test")
