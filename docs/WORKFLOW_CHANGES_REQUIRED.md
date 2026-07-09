# Manual workflow changes required for LofiLoop

GitHub Apps cannot push `.github/workflows/*` changes without the `workflows`
permission, so these two workflow edits must be applied by hand (copy-paste in
the GitHub web editor or a normal `git` push from your machine). Everything else
in the LofiLoop feature is already merged and working.

---

## 1. Add the new standalone workflow

Copy `docs/lofiloop.workflow.yml` from this repo to
`.github/workflows/lofiloop.yml` (verbatim). It lets you render + upload a lofi
video manually from the Actions tab without going through the bot.

```bash
cp docs/lofiloop.workflow.yml .github/workflows/lofiloop.yml
git add .github/workflows/lofiloop.yml && git commit -m "ci: add lofiloop workflow" && git push
```

---

## 2. Extend the existing Telegram dispatch workflow

Edit `.github/workflows/telegram-dispatch.yml`. In the
`Run requested tool and reply on Telegram` step, inside the `env:` block, add
these lines right after `TARGET_LRA:` (the exact indentation must match the
other `env:` keys — 10 spaces):

```yaml
          LOFI_AUDIO: ${{ github.event.client_payload.options.lofi_audio }}
          LOFI_HOURS: ${{ github.event.client_payload.options.lofi_hours }}
          LOFI_CRF: ${{ github.event.client_payload.options.lofi_crf }}
          LOFI_PRESET: ${{ github.event.client_payload.options.lofi_preset }}
          LOFI_NOISE: ${{ github.event.client_payload.options.lofi_noise }}
          TELEGRAM_API_ID: ${{ secrets.TELEGRAM_API_ID }}
          TELEGRAM_API_HASH: ${{ secrets.TELEGRAM_API_HASH }}
```

Without this step LofiLoop jobs will still render, but the runner won't receive
the audio link / target hours, so the bot flow needs these vars.

---

## 3. (Optional) Add GitHub Actions secrets to override the baked-in MTProto keys

The bot ships with working MTProto credentials baked into
`bot/mtproto_transfer.py`. To override them, add repo secrets
(`Settings → Secrets and variables → Actions`):

```
TELEGRAM_API_ID     = 34256648
TELEGRAM_API_HASH   = 0745651c919deb785fea32bf664cd262
```

These are the same defaults already compiled in, so this step is optional.
