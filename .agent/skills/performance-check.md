# Skill: Performance Check

> **Trigger**: "slow", "performance", "optimize", "speed up", "benchmark"

## Procedure

### 1. Identify the slow stage
Video pipelines have a small number of genuinely expensive stages — check
these first before micro-optimizing anything else:

| Stage | Where | Typical cost driver |
|-------|-------|---------------------|
| Download | `aspectshift/downloader.py` (`resolve_input`), used by most tools | yt-dlp format selection, network |
| Transcription | `clipharvest/transcriber.py`, `autochapters/` | faster-whisper model size, CPU vs no GPU on Actions runners |
| ffmpeg encode | every tool's final render step | `-preset` choice, resolution, `-crf`, filter complexity (blur/inpaint/xfade are expensive) |
| Face/motion detection | `watermarkwipe/watermark_detector.py`, `aspectshift` crop mode | opencv-python-headless per-frame cost, frame sampling rate |
| Audio scoring | `clipharvest/scorer.py` | librosa full-track analysis |
| Upload/delivery | `bot/mtproto_transfer.py`, `lofiloop/uploader.py` | file size, MTProto chunking |

### 2. Measure before changing anything
```bash
# ffmpeg: prepend -benchmark, or wrap the whole cmd with time
time python <tool>/main.py --input sample.mp4 --output-dir /tmp/perf_test

# For a specific ffmpeg command, ffmpeg itself reports speed=Nx in stderr —
# capture and compare before/after:
ffmpeg -i sample.mp4 ... 2>&1 | grep -o 'speed=[0-9.]*x'
```
Always benchmark on the same input file before and after a change — relative
improvement matters more than absolute numbers on a shared CI runner.

### 3. Concrete optimization levers (in order of typical impact)

1. **`-preset`**: `slow` -> `medium`/`fast`/`veryfast` trades quality for
   speed. This repo already uses `veryfast` for LofiLoop (long-duration
   renders where speed matters most) and `slow` elsewhere (short clips where
   quality matters most) — respect that existing tradeoff decision unless the
   task says otherwise.
2. **Frame sampling for detection**: watermark/motion detection scanning
   every frame is usually unnecessary — sampling every Nth frame and
   interpolating the result is the highest-leverage single change for
   `watermarkwipe` and `aspectshift --mode crop`.
3. **Avoid re-transcoding when only trimming/concatenating**: `stitcher`'s
   cut transition and any pure trim operation should prefer `-c copy` when
   codecs match, falling back to re-encode only when a transition/filter
   requires it.
4. **Whisper model size**: faster-whisper's `base`/`small`/`medium` models
   trade accuracy for speed roughly linearly — check what size is currently
   configured before assuming a code-level fix is needed.
5. **Parallelism**: GitHub Actions runners are typically 2-4 vCPU — ffmpeg's
   `-threads 0` (auto) is usually already optimal; don't hardcode a thread
   count without benchmarking on the actual runner class.

### 4. Anti-patterns
- Do not lower `-crf` (raise = lower quality) or swap encoding presets on
  quality-sensitive tools (aspectshift, watermarkwipe) without being asked —
  that's a quality regression disguised as a perf fix.
- Do not add caching/threading complexity for a one-off script that runs
  once per job — GitHub Actions runners are ephemeral, in-process caches
  don't persist across jobs (use `actions/cache` at the workflow level
  instead, as `telegram-dispatch.yml` already does for pip + whisper models).

### 5. Verify and report with numbers
```
Benchmark (sample.mp4, 45s 1080p, GitHub Actions ubuntu-latest runner):
  Before: 38.2s wall time, ffmpeg speed=1.2x
  After:  22.1s wall time, ffmpeg speed=2.1x  (-preset fast, watermark
          detection sampling every 5th frame instead of every frame)
  Quality: visually compared before/after PNG frame extracts at t=10s,
           t=25s, t=40s — no visible difference at the sampling rate used.
```

### 6. Commit
`perf(<tool>): <what changed> — Nx faster on <benchmark description>`

## Example

**Input**: "WatermarkWipe feels slow on long videos, can we speed it up?"

**Output**:
```
Diagnosis: watermark_detector.py scans every frame with cv2 template
matching. For a 10-minute 30fps video that's 18,000 full-resolution scans.

Fix: sample every 5th frame for detection (region rarely moves fast enough
to need per-frame precision at 30fps), interpolate the bounding box for
skipped frames.

Benchmark (3-min 1080p sample):
  Before: 61s detection stage
  After:  14s detection stage (4.3x)
  Visual diff: no change in final crop/inpaint region across 10 spot-checked frames

Commit: perf(watermarkwipe): sample every 5th frame in detector, 4x faster
```
