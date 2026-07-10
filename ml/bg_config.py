"""Single source of ML-side constants.

Values marked MIRROR are copies of firmware/main/app_config.h — if you
change one side, change the other in the same commit.
"""

# MIRROR of BG_MODEL_INPUT_{W,H,C}
INPUT_W = 96
INPUT_H = 96
INPUT_C = 3

# Manuscript strata. Class 0 is always the negative/background class; every
# non-zero label is a target pest. Folder slugs can be overridden with
# split_dataset.py --classes for an existing/custom dataset.
CLASS_NAMES = [
    "negative",
    "thrips_hawaiiensis",
    "erionota_thrax",
    "pentalonia_nigronervosa",
]

# MIRROR of BG_CONF_THRESHOLD_PCT (60 %)
CONF_THRESHOLD = 0.60

# Objective-I acceptance criterion: macro (unweighted mean per-class) F1 on
# the frozen held-out test set.
MEAN_F1_TARGET = 0.85

# Split fractions. The test fraction is FROZEN by split_dataset.py: once a
# file lands in the test manifest it never leaves it.
VAL_FRACTION = 0.15
TEST_FRACTION = 0.15

# Reproducibility
SEED = 22

# MobileNetV2 width multiplier. 0.35 keeps the INT8 flatbuffer well under
# 1 MB so it fits ESP32 flash next to the app, and the tensor arena inside
# BG_TFLM_ARENA_KB (300 KB). ImageNet weights exist for alpha=0.35 @ 96x96.
MNV2_ALPHA = 0.35

# Default artifact locations (relative to ml/)
SPLITS_DIR = "splits"
EXPORTS_DIR = "exports"
RUNS_DIR = "runs"
