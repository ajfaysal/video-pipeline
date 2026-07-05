# Video Content Pipeline

Three CLI video-processing tools, plus a Telegram bot front-end that runs them
with zero VPS required (Cloudflare Workers + GitHub Actions).

- **AspectShift** - 16:9 → 9:16 conversion, zero visible quality loss
- **ClipHarvest** - auto-extracts the best short-clip-worthy segments from a long video
- **WatermarkWipe** - removes watermarks/logos, plus optional color grading and background blur
- **ABRoll** - auto-detects cut points and inserts B-roll with ffmpeg crossfades
- **IntroOutro** - adds a branded intro and outro card with ffmpeg drawtext fades
- **Stitcher** - joins multiple clips with crossfade, wipe, or cut transitions
- **AudioDuck** - ducks background music under a voiceover track

## Requirements

- Python 3.11+
- `ffmpeg` and `ffprobe` on PATH
- `yt-dlp` on PATH (for `--url` inputs)

```bash
pip install -r requirements.txt
```

---

## 1. AspectShift

```bash
python aspectshift/main.py --input video.mp4 --mode blur --output-dir ./output
python aspectshift/main.py --url "https://youtube.com/..." --mode blur --output-dir ./output
python aspectshift/main.py --input video.mp4 --mode crop --output-dir ./output
```

- `--mode blur` (default): blur-background-fill - sharp centered video over a blurred, full-frame
  copy of itself. Nothing is cropped away from the sharp subject.
- `--mode crop`: smart content-aware crop using face/motion detection to pick the crop window.
- Encodes `libx264 -crf 16 -preset slow`, audio copied (falls back to AAC 320k).
- Auto-generates a best-frame thumbnail (`.jpg` + `.png`), upscaled to ≥1920×1080 via Lanczos, then
  contrast/saturation-enhanced and sharpened.

## 2. ClipHarvest

```bash
python clipharvest/main.py --input long_video.mp4 --num-clips 5 --min-duration 20 --max-duration 90 --captions --output-dir ./clips
python clipharvest/main.py --url "https://youtube.com/..." --num-clips 8 --output-dir ./clips
```

- Transcribes with `faster-whisper` (word-level timestamps), cached to disk so retries don't
  re-transcribe.
- Generates candidates on sentence boundaries only, scores 0-100 on: hook/emotion keywords, audio
  energy, speech-pace variation, boundary cleanliness, visual motion, and face presence.
- De-duplicates overlapping candidates, keeping the highest scorer per cluster.
- `--captions` burns in word-by-word karaoke-style captions.
- Every clip is run through AspectShift's converter for a final 9:16 export + thumbnail.
- Writes `report.json` and `report.md` with score, duration, hook reason, and a suggested title per clip.

## 3. WatermarkWipe

```bash
python watermarkwipe/main.py --input video.mp4 --mode inpaint --auto-detect --quality-check --output-dir ./output
python watermarkwipe/main.py --input video.mp4 --mode crop --region 1600,50,300,100 --output-dir ./output
python watermarkwipe/main.py --input video.mp4 --mode inpaint --auto-detect --color-grade cinematic --background-blur --output-dir ./output
```

- `--mode crop`: for corner/edge watermarks - detects the static overlay region, crops the offending
  strip out, rescales back to the original resolution.
- `--mode inpaint`: for center/moving/semi-transparent watermarks - OpenCV inpainting
  (`INPAINT_TELEA`/`INPAINT_NS`) frame-by-frame, with temporal smoothing to reduce flicker.
- `--quality-check` exports a single before/after side-by-side JPEG so you can verify results
  before committing to processing the full video.
- `--color-grade {cinematic,vibrant,warm,cool}` and `--background-blur` are optional professional
  post-processing steps applied after watermark removal.
- `--color-grade` also supports `teal_orange`, `vintage_film`, `black_white`, and `high_contrast_news`.

**Known limitation:** inpaint-mode quality depends on background complexity. It works best on
simple/static backgrounds (sky, walls, solid colors) and may show slight softness on complex/detailed
regions (faces, busy textures) that sit under the watermark - this is an inherent limitation of
inpainting-based reconstruction, not a bug. Always run `--quality-check` first on footage with a
complex watermark region, and prefer `--mode crop` when the watermark sits in a sacrificeable
corner/edge strip.

## 4. ABRoll

```bash
python abroll/main.py --main video.mp4 --broll broll1.mp4 broll2.mp4 --output-dir ./output
```

- Detects silence gaps with `ffmpeg silencedetect` and scene changes with `ffmpeg` scene analysis.
- Interleaves short B-roll inserts around the selected cut points and joins them with crossfades.
- Encodes `libx264 -crf 16 -preset slow`.

**Known limitation:** cut detection is heuristic. Very dense dialogue, fast-cut montages, or noisy audio can produce too few or too many candidate points, so you may need to tweak `--scene-threshold`, `--silence-db`, `--silence-min-duration`, or `--max-inserts` for a specific source.

## 5. IntroOutro

```bash
python introoutro/main.py --input video.mp4 --intro-text "Channel Name" --outro-text "Subscribe for more" --output-dir ./output
```

- Builds branded intro/outro cards with `ffmpeg drawtext`, a dark background, and fade-in/out.
- Normalizes the main video to the same resolution/fps so the intro, body, and outro can be concatenated cleanly.
- Encodes `libx264 -crf 16 -preset slow`.

**Known limitation:** the intro and outro are rendered as standalone cards, so any text longer than the frame allows will wrap or become cramped. Keep the copy short for best results.

## 6. Stitcher

```bash
python stitcher/main.py --clips clip1.mp4 clip2.mp4 clip3.mp4 --transition crossfade --output-dir ./output
python stitcher/main.py --clips clip1.mp4 clip2.mp4 --transition wipe-left --output-dir ./output
```

- Joins clips with ffmpeg `xfade` transitions: `crossfade`, `wipe-left`, `wipe-right`, or `cut`.
- Normalizes the clips to the first clip's resolution and frame rate so transition chains stay stable.
- Encodes `libx264 -crf 16 -preset slow`.

**Known limitation:** transition chains need clips with enough duration to overlap cleanly. Extremely short clips can force the effective transition shorter than requested, and `cut` is the only option that does not use an overlap.

## 7. AudioDuck

```bash
python audioduck/main.py --video video.mp4 --voiceover narration.mp3 --output-dir ./output
```

- Uses ffmpeg `sidechaincompress` to duck the music whenever the voiceover is active, then mixes the narration back in.
- Preserves the original video stream and re-encodes the output with `libx264 -crf 16 -preset slow`.
- Accepts either local files or URLs for the video and voiceover inputs.

**Known limitation:** this is automatic level control, not full dialogue replacement. If the original background music is extremely loud or the narration has poor recording quality, you may still need to normalize the source audio before ducking.

---

## Running via GitHub Actions (no local setup needed)

Each tool has a `workflow_dispatch`-triggered workflow under `.github/workflows/`:
`aspectshift.yml`, `clipharvest.yml`, `watermarkwipe.yml`, `abroll.yml`, `introoutro.yml`, `stitcher.yml`, `audioduck.yml`. Trigger them from the Actions tab with the requested repo-relative path(s) or URL(s), and download the result from the run's Artifacts.

---

## Telegram Bot (no VPS required)

Architecture: **Cloudflare Worker** (receives Telegram messages, collects your tool/options choice
via inline buttons) → triggers **GitHub Actions** (`telegram-dispatch.yml`, does the actual
ffmpeg/whisper work) → Actions sends the finished video/thumbnail straight back to your chat.
Nothing needs to run 24/7 on a server you manage.

The Worker now exposes the new ABRoll, IntroOutro, Stitcher, and AudioDuck tools as bot options;
ABRoll/Stitcher will ask for additional clips, and AudioDuck will ask for the narration track before dispatching.

### Setup

1. **Create the bot**: message [@BotFather](https://t.me/BotFather), `/newbot`, save the token.
2. **GitHub token**: create a fine-grained Personal Access Token with `Contents: read` and
   `Actions: write` on this repo. Add it as a repo secret named `TELEGRAM_BOT_TOKEN` is for the
   bot itself - separately add the GitHub PAT to the **Cloudflare Worker** (see below), and add
   the bot token as a GitHub Actions secret too (`Settings → Secrets and variables → Actions`),
   named `TELEGRAM_BOT_TOKEN`.
3. **Deploy the Worker**:
   ```bash
   cd cloudflare-worker
   npx wrangler kv namespace create BOT_STATE   # copy the id into wrangler.toml
   npx wrangler secret put TELEGRAM_BOT_TOKEN
   npx wrangler secret put TELEGRAM_WEBHOOK_SECRET   # any random string you choose
   npx wrangler secret put GITHUB_TOKEN
   npx wrangler secret put GITHUB_REPO               # e.g. yourname/video-pipeline
   npx wrangler deploy
   ```
4. **Point Telegram at the Worker** (replace placeholders):
   ```bash
   curl -X POST "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook" \
     -d "url=https://<your-worker>.workers.dev" \
     -d "secret_token=<same TELEGRAM_WEBHOOK_SECRET you set above>"
   ```
5. Message your bot a video link, pick a tool from the buttons, and it'll DM you the result when done.

**Limits to know:**
- Directly-uploaded video files: Telegram bots can only fetch files ≤20MB. For anything bigger,
  send a link (YouTube etc.) instead.
- Result delivery back into the chat is capped at 50MB per file (standard Bot API upload limit); if
  a result exceeds that, the bot sends a text notice and the file remains available in the GitHub
  Actions run's artifacts.
- A GitHub Actions job has a 6-hour ceiling - plenty for this pipeline, but very long source videos
  in ClipHarvest (transcription + multi-clip export) will take longer than a quick clip.

---

## Repo layout

```
aspectshift/       downloader.py, converter.py, thumbnail.py, enhance.py, main.py
clipharvest/        transcriber.py, scorer.py, clipper.py, captioner.py, config.py, main.py
watermarkwipe/      watermark_detector.py, watermark_remover.py, main.py
abroll/             main.py
introoutro/         main.py
stitcher/           main.py
audioduck/          main.py
bot/                telegram_notify.py, run_job.py   (used by telegram-dispatch.yml)
cloudflare-worker/  worker.js, wrangler.toml
.github/workflows/  aspectshift.yml, clipharvest.yml, watermarkwipe.yml, abroll.yml, introoutro.yml, stitcher.yml, audioduck.yml, telegram-dispatch.yml
requirements.txt    combined dependencies for all tools + the bot job runner
```
