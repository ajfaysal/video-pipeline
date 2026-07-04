"""
transcriber.py
--------------
Word-level transcription using faster-whisper. Results are cached to disk
(keyed by a hash of the input file + model size) so retries don't re-run
the (slow) transcription step.

Output:
    {
      "language": "en",
      "segments": [
        {"start": 0.0, "end": 3.2, "text": "...",
         "words": [{"start":0.0,"end":0.3,"word":"So","probability":0.98}, ...]},
        ...
      ]
    }
Also writes a standard .srt alongside the JSON cache.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

from clipharvest.config import WHISPER_MODEL_SIZE


def _file_hash(path: str, block_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    h.update(str(os.path.getsize(path)).encode())
    with open(path, "rb") as f:
        # Hash first and last MB plus size - fast and collision-resistant enough for caching.
        chunk = f.read(block_size)
        h.update(chunk)
        try:
            f.seek(-block_size, os.SEEK_END)
            h.update(f.read(block_size))
        except OSError:
            pass
    return h.hexdigest()[:16]


def _format_srt_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _write_srt(segments: list[dict], srt_path: str) -> None:
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, start=1):
            f.write(f"{i}\n")
            f.write(f"{_format_srt_timestamp(seg['start'])} --> {_format_srt_timestamp(seg['end'])}\n")
            f.write(f"{seg['text'].strip()}\n\n")


def transcribe(video_path: str, cache_dir: str, model_size: str = WHISPER_MODEL_SIZE,
               device: str = "auto", compute_type: str = "auto") -> dict:
    """
    Transcribes `video_path` with word-level timestamps. Returns the parsed
    transcript dict and writes {hash}.json + {hash}.srt into cache_dir.
    Re-uses the cached result on subsequent calls with the same file+model.
    """
    os.makedirs(cache_dir, exist_ok=True)
    key = f"{_file_hash(video_path)}_{model_size}"
    json_path = os.path.join(cache_dir, f"{key}.json")
    srt_path = os.path.join(cache_dir, f"{key}.srt")

    if os.path.isfile(json_path):
        print(f"[transcriber] Using cached transcript: {json_path}", file=sys.stderr)
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)

    # Imported lazily - faster-whisper + torch are heavy and only needed here.
    from faster_whisper import WhisperModel

    if device == "auto":
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"
    if compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"

    print(f"[transcriber] Transcribing '{video_path}' with model={model_size} device={device} ...", file=sys.stderr)
    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    segments_iter, info = model.transcribe(video_path, word_timestamps=True, vad_filter=True)

    segments = []
    for seg in segments_iter:
        words = []
        if seg.words:
            for w in seg.words:
                words.append({
                    "start": float(w.start),
                    "end": float(w.end),
                    "word": w.word.strip(),
                    "probability": float(w.probability),
                })
        segments.append({
            "start": float(seg.start),
            "end": float(seg.end),
            "text": seg.text.strip(),
            "words": words,
        })

    transcript = {"language": info.language, "duration": info.duration, "segments": segments}

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(transcript, f, ensure_ascii=False, indent=2)
    _write_srt(segments, srt_path)

    print(f"[transcriber] Transcript cached at {json_path} ({len(segments)} segments)", file=sys.stderr)
    return transcript
