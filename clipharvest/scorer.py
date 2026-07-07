"""
scorer.py
---------
Turns a word-level transcript into a ranked list of clip-worthy candidate
segments.

Pipeline:
  1. generate_candidates(): slide a window over sentence-boundary-aligned
     spans within [min_duration, max_duration] seconds.
  2. score_candidate(): combine audio energy, pace variation, boundary
     cleanliness, hook keywords, visual motion, and face presence into a
     single 0-100 score.
  3. deduplicate_candidates(): greedy NMS - drop lower-scoring candidates
     that overlap heavily with a higher-scoring one.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field

import cv2
import numpy as np

from aspectshift.downloader import load_face_cascade
from clipharvest.config import (
    HOOK_KEYWORD_GROUPS,
    MAX_BOUNDARY_SILENCE,
    OVERLAP_IOU_THRESHOLD,
    SCORE_WEIGHTS,
)

_SENTENCE_END_RE = re.compile(r'[.!?]["\')\]]?\s*$')


@dataclass
class Candidate:
    start: float
    end: float
    text: str
    word_count: int
    score: float = 0.0
    breakdown: dict = field(default_factory=dict)
    reason: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start


def _flatten_words(transcript: dict) -> list[dict]:
    words = []
    for seg in transcript["segments"]:
        words.extend(seg["words"] if seg["words"] else
                      [{"start": seg["start"], "end": seg["end"], "word": seg["text"], "probability": 1.0}])
    return words


def _sentence_boundaries(words: list[dict]) -> list[int]:
    """Indices (into `words`) immediately after which a sentence ends."""
    boundaries = []
    for i, w in enumerate(words):
        if _SENTENCE_END_RE.search(w["word"]):
            boundaries.append(i)
    if not boundaries or boundaries[-1] != len(words) - 1:
        boundaries.append(len(words) - 1)
    return boundaries


def generate_candidates(transcript: dict, min_duration: float, max_duration: float) -> list[Candidate]:
    """
    Generate candidate segments that start and end on sentence boundaries
    and fall within [min_duration, max_duration].
    """
    words = _flatten_words(transcript)
    if not words:
        return []

    boundaries = _sentence_boundaries(words)
    candidates = []

    for start_pos, start_idx in enumerate(boundaries):
        start_word_idx = start_idx + 1 if start_pos > 0 else 0
        if start_word_idx >= len(words):
            continue
        seg_start = words[start_word_idx]["start"]

        for end_idx in boundaries[start_pos:]:
            if end_idx < start_word_idx:
                continue
            seg_end = words[end_idx]["end"]
            duration = seg_end - seg_start
            if duration < min_duration:
                continue
            if duration > max_duration:
                break

            segment_words = words[start_word_idx:end_idx + 1]
            text = " ".join(w["word"] for w in segment_words).strip()
            candidates.append(Candidate(
                start=seg_start, end=seg_end, text=text, word_count=len(segment_words),
            ))

    return candidates


def _hook_score(text: str) -> float:
    text_lower = text.lower()
    total = 0.0
    for group in HOOK_KEYWORD_GROUPS.values():
        for pattern in group["patterns"]:
            matches = len(re.findall(pattern, text_lower))
            total += matches * group["weight"]
    # Normalize: cap around ~5 strong hook hits = full score.
    return float(np.clip(total / 5.0, 0, 1.0)) * 100.0


def _pace_score(candidate: Candidate, transcript: dict) -> float:
    words = _flatten_words(transcript)
    seg_words = [w for w in words if candidate.start <= w["start"] <= candidate.end]
    if len(seg_words) < 4:
        return 30.0
    # Words-per-second in rolling 3-second windows; high variance = dynamic delivery.
    rates = []
    window = 3.0
    t = candidate.start
    while t < candidate.end:
        count = sum(1 for w in seg_words if t <= w["start"] < t + window)
        rates.append(count / window)
        t += window
    if len(rates) < 2:
        return 40.0
    variation = float(np.std(rates))
    return float(np.clip(variation * 25.0, 0, 100.0))


def _boundary_score(candidate: Candidate, transcript: dict) -> float:
    words = _flatten_words(transcript)
    before = [w for w in words if w["end"] <= candidate.start]
    after = [w for w in words if w["start"] >= candidate.end]

    gap_before = candidate.start - before[-1]["end"] if before else 0.0
    gap_after = after[0]["start"] - candidate.end if after else 0.0

    penalty = 0.0
    if 0 < gap_before < 0.15:  # cut mid-breath, too tight
        penalty += 20
    if gap_before > MAX_BOUNDARY_SILENCE * 3:  # started with a long dead-air lead-in
        penalty += 15
    if 0 < gap_after < 0.15:
        penalty += 10

    return float(np.clip(100 - penalty, 0, 100))


def _audio_energy_score(candidate: Candidate, audio_path: str) -> float:
    try:
        import librosa
        y, sr = librosa.load(audio_path, sr=None, offset=candidate.start,
                              duration=max(candidate.duration, 0.1))
        if y.size == 0:
            return 50.0
        rms = librosa.feature.rms(y=y)[0]
        mean_rms = float(np.mean(rms))
        peak_rms = float(np.max(rms))
        # Normalize against a typical speech RMS range; combine mean level with peak "excitement".
        score = np.clip((mean_rms * 8.0) * 60 + (peak_rms * 8.0) * 40, 0, 100)
        return float(score)
    except Exception as e:
        print(f"[scorer] audio energy scoring failed for [{candidate.start:.1f},{candidate.end:.1f}]: {e}", file=sys.stderr)
        return 50.0


def _visual_score(candidate: Candidate, video_path: str, sample_count: int = 8) -> tuple[float, float]:
    """Returns (motion_score, face_score) both 0-100."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return 50.0, 50.0

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    start_frame = int(candidate.start * fps)
    end_frame = int(candidate.end * fps)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    end_frame = min(end_frame, total_frames - 1)
    if end_frame <= start_frame:
        cap.release()
        return 50.0, 50.0

    indices = np.linspace(start_frame, end_frame, num=min(sample_count, max(end_frame - start_frame, 2)), dtype=int)
    face_cascade = load_face_cascade()  # None if this OpenCV build lacks cascade support

    prev_gray = None
    diffs = []
    face_hits = 0
    sampled = 0

    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
        ok, frame = cap.read()
        if not ok:
            continue
        sampled += 1
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if prev_gray is not None:
            diff = cv2.absdiff(gray, prev_gray)
            diffs.append(float(np.mean(diff)))
        prev_gray = gray

        if face_cascade is not None:
            faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
            if len(faces) > 0:
                face_hits += 1

    cap.release()

    motion_score = float(np.clip(np.mean(diffs) * 6.0, 0, 100)) if diffs else 50.0
    face_score = float((face_hits / sampled) * 100.0) if sampled else 0.0
    return motion_score, face_score


def score_candidate(candidate: Candidate, transcript: dict, video_path: str, audio_path: str) -> Candidate:
    hook = _hook_score(candidate.text)
    energy = _audio_energy_score(candidate, audio_path)
    pace = _pace_score(candidate, transcript)
    boundary = _boundary_score(candidate, transcript)
    motion, face = _visual_score(candidate, video_path)

    breakdown = {
        "hook_keywords": hook,
        "audio_energy": energy,
        "pace_variation": pace,
        "boundary_cleanliness": boundary,
        "visual_motion": motion,
        "face_presence": face,
    }
    final = sum(breakdown[k] * SCORE_WEIGHTS[k] for k in SCORE_WEIGHTS)

    candidate.score = round(float(final), 2)
    candidate.breakdown = {k: round(v, 1) for k, v in breakdown.items()}
    candidate.reason = _explain(candidate, breakdown)
    return candidate


def _explain(candidate: Candidate, breakdown: dict) -> str:
    top_signal = max(breakdown, key=breakdown.get)
    labels = {
        "hook_keywords": "a strong verbal hook",
        "audio_energy": "high vocal energy",
        "pace_variation": "dynamic speech pacing",
        "boundary_cleanliness": "a clean, well-bounded cut",
        "visual_motion": "strong visual activity",
        "face_presence": "consistent face presence",
    }
    snippet = candidate.text if len(candidate.text) <= 90 else candidate.text[:87] + "..."
    return f"Selected for {labels.get(top_signal, top_signal)}. \"{snippet}\""


def _iou(a: Candidate, b: Candidate) -> float:
    inter = max(0.0, min(a.end, b.end) - max(a.start, b.start))
    union = (a.end - a.start) + (b.end - b.start) - inter
    return inter / union if union > 0 else 0.0


def deduplicate_candidates(candidates: list[Candidate], iou_threshold: float = OVERLAP_IOU_THRESHOLD) -> list[Candidate]:
    """Greedy NMS: keep the highest-scoring candidate per overlapping cluster."""
    ranked = sorted(candidates, key=lambda c: c.score, reverse=True)
    kept: list[Candidate] = []
    for cand in ranked:
        if all(_iou(cand, k) < iou_threshold for k in kept):
            kept.append(cand)
    return kept


def rank_top_clips(transcript: dict, video_path: str, audio_path: str,
                    min_duration: float, max_duration: float, num_clips: int) -> list[Candidate]:
    print("[scorer] Generating candidate segments on sentence boundaries...", file=sys.stderr)
    candidates = generate_candidates(transcript, min_duration, max_duration)
    print(f"[scorer] {len(candidates)} raw candidates generated. Scoring...", file=sys.stderr)

    scored = [score_candidate(c, transcript, video_path, audio_path) for c in candidates]
    deduped = deduplicate_candidates(scored)
    deduped.sort(key=lambda c: c.score, reverse=True)

    top = deduped[:num_clips]
    top.sort(key=lambda c: c.start)  # present in chronological order
    print(f"[scorer] Selected top {len(top)} clips after de-duplication.", file=sys.stderr)
    return top
