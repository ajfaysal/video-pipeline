"""
config.py
---------
Central configuration for ClipHarvest: default durations, clip counts, and
the scoring weights used to rank candidate segments. Tweak these to bias
the picker toward different kinds of clips.
"""

DEFAULT_MIN_DURATION = 20
DEFAULT_MAX_DURATION = 90
DEFAULT_NUM_CLIPS = 5

# Whisper model size used for transcription. "small" is a good speed/accuracy
# tradeoff for clip scoring; use "medium" or "large-v3" for higher accuracy.
WHISPER_MODEL_SIZE = "small"

# Weights must sum to 1.0. These control how much each signal contributes
# to a candidate segment's final 0-100 score.
SCORE_WEIGHTS = {
    "hook_keywords": 0.28,      # question/number/story-hook phrasing in the transcript
    "audio_energy": 0.16,       # RMS energy / excitement of the speech
    "pace_variation": 0.12,     # speech pace spikes (delivery dynamics)
    "boundary_cleanliness": 0.14,  # penalizes awkward start/end pauses or mid-sentence cuts
    "visual_motion": 0.12,      # frame-to-frame visual activity
    "face_presence": 0.18,      # consistent face presence across the segment
}

# Hook / emotion keyword groups used for transcript scoring. Each group has
# its own weight multiplier; matches are case-insensitive and can overlap.
HOOK_KEYWORD_GROUPS = {
    "question": {
        "weight": 1.0,
        "patterns": [r"\bwhy\b", r"\bhow\b", r"\bwhat if\b", r"\bwhat's\b", r"\bwhat is\b", r"\?\s*$"],
    },
    "number_stat": {
        "weight": 0.9,
        "patterns": [r"\b\d+%", r"\b\d+ (percent|times|years|dollars|million|billion|minutes)\b", r"\bnumber one\b"],
    },
    "surprise": {
        "weight": 1.1,
        "patterns": [
            r"\bnever\b", r"\bshocking\b", r"\bsurpris\w*\b", r"\binsane\b", r"\bcrazy\b",
            r"\bno one (tells|talks about)\b", r"\bsecret\b", r"\bmistake\b", r"\btruth\b",
        ],
    },
    "story_hook": {
        "weight": 1.0,
        "patterns": [
            r"\bimagine\b", r"\blisten\b", r"\bhere's the thing\b", r"\bthe problem is\b",
            r"\bi learned\b", r"\bi realized\b", r"\bturns out\b", r"\bso i\b",
        ],
    },
    "payoff": {
        "weight": 0.8,
        "patterns": [r"\bthat's why\b", r"\bwhich means\b", r"\bthe result\b", r"\bin the end\b", r"\bfinally\b"],
    },
}

# Minimum gap (seconds) allowed at a cut boundary before it is penalized as "awkward".
MAX_BOUNDARY_SILENCE = 0.6

# IoU threshold above which two candidate segments are considered overlapping
# for de-duplication (keep only the higher-scoring one per cluster).
OVERLAP_IOU_THRESHOLD = 0.35
