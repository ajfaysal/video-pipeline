# Video Content Pipeline

Three CLI video-processing tools, plus a Telegram bot front-end that runs them
with zero VPS required (Cloudflare Workers + GitHub Actions).

- **AspectShift** - 16:9 → 9:16 conversion, zero visible quality loss
- **ClipHarvest** - auto-extracts the best short-clip-worthy segments from a long video
- **WatermarkWipe** - removes watermarks/logos, plus optional color grading and background blur

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

**Known limitation:** inpaint-mode quality depends on background complexity. It works best on
simple/static backgrounds (sky, walls, solid colors) and may show slight softness on complex/detailed
regions (faces, busy textures) that sit under the watermark - this is an inherent limitation of
inpainting-based reconstruction, not a bug. Always run `--quality-check` first on footage with a
complex watermark region, and prefer `--mode crop` when the watermark sits in a sacrificeable
corner/edge strip.

---

## Running via GitHub Actions (no local setup needed)

Each tool has a `workflow_dispatch`-triggered workflow under `.github/workflows/`:
`aspectshift.yml`, `clipharvest.yml`, `watermarkwipe.yml`. Trigger them from the Actions tab with
a video URL or repo-relative path, and download the result from the run's Artifacts.

---

## Telegram Bot (no VPS required)

Architecture: **Cloudflare Worker** (receives Telegram messages, collects your tool/options choice
via inline buttons) → triggers **GitHub Actions** (`telegram-dispatch.yml`, does the actual
ffmpeg/whisper work) → Actions sends the finished video/thumbnail straight back to your chat.
Nothing needs to run 24/7 on a server you manage.

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
bot/                telegram_notify.py, run_job.py   (used by telegram-dispatch.yml)
cloudflare-worker/  worker.js, wrangler.toml
.github/workflows/  aspectshift.yml, clipharvest.yml, watermarkwipe.yml, telegram-dispatch.yml
requirements.txt    combined dependencies for all tools + the bot job runner
```
