# StockFlow AI — Commercial Asset Production Engine for Adobe Stock

Turn one Telegram prompt into a complete, Adobe-Stock-ready asset bundle:

| Stage | What you get |
|---|---|
| 🎨 **Multi-engine generation** | Hugging Face **FLUX.1-schnell** (free default) — or your own OpenAI / Stability AI / Midjourney-compatible endpoint, switchable in `/settings` |
| 🛡️ **Uniqueness Shield** | A unique mathematical seed per asset (UUID4 → SHA-256 KDF → 32-bit seed), seed-mixed entropy tokens in the prompt, collision-proof registry, auditable shield code (`SF-XXXX-XXXX`) |
| 📊 **Market Intelligence** | `/niche` — VidIQ-style scoring (`0.45·demand + 0.35·(100−competition) + 0.20·momentum`), seasonal weighting + live trending-search RSS blending, ready-to-tap prompts |
| 🏷️ **SEO & sales pack** | 70-character commercial title + 50 ranked, deduped, comma-separated SEO tags — auto-generated for every run |
| 🔍 **16K Ultra-HD** | Staged ≤2× Lanczos upscaling with unsharp-mask acuity restoration, up to 15360 px long edge |
| 📦 **Delivery** | Watermark-free PNG or SVG, in-chat + full ZIP bundle (asset + `metadata.json`) |
| 🖥️ **Mini-App** | Obsidian/cobalt glassmorphism Telegram Web App with one-click HD download, niche browser, engine manager |

---

## 1. Files

```
bot.py                     Telegram bot (long polling, auto-starts Mini-App thread)
config.json                Engine registry + defaults (edit directly or via /settings)
requirements.txt           All dependencies
stockflow/
  config.py                Config persistence (atomic writes, env-var key references)
  engines.py               HF / OpenAI / OpenAI-compatible / Stability API clients
  seeds.py                 Uniqueness Shield (seed KDF, entropy tokens, registry)
  niches.py                Market-intelligence engine (local matrix + live trends)
  seo.py                   70-char title + 50-tag optimizer
  upscaler.py              16K staged-Lanczos upscaler
  svg.py                   PNG→SVG (vtracer tracing if installed, else embedded SVG)
  pipeline.py              Orchestration: generate → SEO → save bundle
  webapp.py                Mini-App backend (stdlib HTTP, JSON API)
  miniapp/index.html       Glassmorphism Web App UI
```

## 2. Local setup (2 minutes)

```bash
pip install -r requirements.txt          # core needs: python-telegram-bot, requests, Pillow

# Telegram bot token — @BotFather → /newbot
export TELEGRAM_BOT_TOKEN="123456:ABC..."

# Default engine: free Hugging Face token
# → huggingface.co/settings/tokens → "New token" (Read is enough)
export HF_TOKEN="hf_..."

python bot.py
```

Then in Telegram: `/start` → `/generate cozy home office with plants, morning light`.

### Custom APIs

- In Telegram: `/settings` → ➕ Add custom API (5-step wizard: id → format → URL → model → key).
- Or edit `config.json` → `engines.<id>` — set `api_format` to `openai`,
  `openai-compatible` (Midjourney proxies like GoAPI/TTAPI style gateways), or
  `stability`, fill `base_url` / `model` / `api_key` (or `api_key_env`).

## 3. ⚡ 1-minute $0 deployment guide (Oracle Cloud Free Tier)

Cheapest reliable $0 home for a long-polling bot (no credit card surprises,
always-free VM with 1GB RAM — plenty for everything except 16K tier, use ≤8K there):

```bash
# 0:00 — Create VM: cloud.oracle.com/free → "VM.Standard.E2.1.Micro" (Ubuntu 22.04, Always Free)
# 0:20 — SSH in, then:
sudo apt update && sudo apt install -y python3-pip git
git clone https://github.com/<you>/<repo>.git && cd <repo>
pip3 install -r requirements.txt

# 0:40 — Set secrets (from @BotFather + huggingface.co/settings/tokens)
export TELEGRAM_BOT_TOKEN="123:abc" HF_TOKEN="hf_..."

# 0:50 — Run forever with auto-restart
cat > run.sh <<'EOF'
#!/bin/bash
cd "$(dirname "$0")"
while true; do python3 bot.py; sleep 3; done
EOF
chmod +x run.sh && nohup ./run.sh > stockflow.log 2>&1 &

# 1:00 — Done. /start your bot. 🚀
```

**Alternatives (also $0):**
- **PythonAnywhere free tier** — clone repo → Bash console → same exports →
  "Always-on task": `python3 bot.py`.
- **Fly.io free allowance** — `fly launch` with a one-line `Dockerfile`
  (`FROM python:3.12-slim` + `pip install -r requirements.txt` + `CMD ["python","bot.py"]`),
  secrets via `fly secrets set TELEGRAM_BOT_TOKEN=... HF_TOKEN=...`.
- **Any free Codespace/Gitpod** for testing — not for 24/7.

> The bot uses **long polling**, so no public HTTPS/webhook is required.
> The Mini-App UI *does* need HTTPS — see below.

## 4. Mini-App (Web App button)

1. Run bot.py anywhere; the backend auto-serves on `:8080`.
2. Expose it over HTTPS: `cloudflared tunnel --url http://localhost:8080` (free)
   or deploy webapp.py to any free HTTPS host (Render/Railway/Fly).
3. Put the HTTPS URL in `config.json` → `telegram.miniapp_url`
   and set it in @BotFather → Bot Settings → Menu Button / Web App URL.
4. `/start` now shows **🖥 Open Studio Mini-App**.

## 5. Command reference

```
/start                                  Welcome + keyboards
/generate <prompt> [--res 1k|2k|4k|8k|16k] [--format png|svg]
                     [--aspect square|landscape|portrait] [--upscale]
                     [--engine <id>] [--niche <text>]
/niche                                  Live market intelligence (top 5 opportunities)
/settings                               Engines, custom APIs, keys, defaults (inline UI)
/engines                                List engines + key status
/defaults                               Show production defaults
/help                                   Reference
```

## 6. Notes on Adobe Stock compliance

- **No watermarks**: neither the engines nor StockFlow adds any.
- **Uniqueness**: every run uses a fresh 32-bit seed + seed-derived entropy
  tokens, so identical prompts still produce decorrelated latents → no
  "similar content" rejections between your own uploads.
- Titles are capped at 70 chars; tags at 50 (Adobe's hard limit); both are
  stripped of characters Adobe flags.
- Adobe Stock requires labeling AI-generated content on upload and does not
  accept prompts referencing real brands/people — the commercial style suffix
  actively excludes text/logos/trademarks from renders.
- 16K tier needs ~2GB+ free RAM; on 1GB free VMs use ≤8K.
