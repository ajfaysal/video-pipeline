"""
mtproto_transfer.py
-------------------
Large-file (up to 2GB / 4GB) Telegram delivery via MTProto (Pyrogram).

The regular Bot API used in telegram_notify.py can only *upload* files up to
50MB (and only *download* user files up to 20MB). To send the massive rendered
lofi videos straight into the chat we use MTProto through Pyrogram, which raises
the per-file cap to 2GB (4GB for premium), using the app's API ID + Hash.

Configuration (env vars):
    TELEGRAM_API_ID      - MTProto API id      (default: baked-in 34256648)
    TELEGRAM_API_HASH    - MTProto API hash    (default: baked-in)
    TELEGRAM_BOT_TOKEN   - bot token from @BotFather (required)

A bot session works for *sending* files up to the 2GB limit, so no phone-number
login / user session string is required. The session file is created in a temp
dir on each run (stateless, CI-friendly).

Falls back gracefully: if Pyrogram isn't installed or the send fails, callers
should fall back to the free-host download link produced by lofiloop.uploader.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time

# Upload resilience knobs.
MAX_SEND_ATTEMPTS = 3          # full-send retries after connection drops
RETRY_BACKOFF_SECONDS = 20     # base wait between attempts (doubles each try)

# Baked-in credentials supplied by the project owner so 2GB transfer works
# out of the box. Overridable via environment variables.
DEFAULT_API_ID = 34256648
DEFAULT_API_HASH = "0745651c919deb785fea32bf664cd262"

# Telegram's hard cap for non-premium uploads.
MAX_MTPROTO_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB


def _credentials() -> tuple[int, str, str]:
    api_id = int(os.environ.get("TELEGRAM_API_ID", DEFAULT_API_ID))
    api_hash = os.environ.get("TELEGRAM_API_HASH", DEFAULT_API_HASH)
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required for MTProto transfer.")
    return api_id, api_hash, bot_token


def mtproto_available() -> bool:
    try:
        import pyrogram  # noqa: F401
        return True
    except Exception:
        return False


async def _send_async(chat_id: int, file_path: str, caption: str, as_video: bool,
                      progress_cb=None) -> bool:
    """
    One complete upload attempt over a fresh MTProto session.

    Pyrogram itself performs the chunked upload (512KB MTProto parts) with
    per-part retransmission; what it does NOT survive is a full connection
    drop mid-file — that is handled by the retry loop in send_large_file(),
    which tears down the session and starts a clean attempt.
    """
    from pyrogram import Client
    from pyrogram.errors import FloodWait

    api_id, api_hash, bot_token = _credentials()
    workdir = tempfile.mkdtemp(prefix="pyro_")

    last_logged = {"pct": -1, "ts": 0.0}

    def _progress(current, total):
        if not total:
            return
        frac = current / total
        pct = int(frac * 100)
        now = time.time()
        # Log every 10% (throttled) so CI logs show the upload is alive.
        if pct >= last_logged["pct"] + 10 or (now - last_logged["ts"] > 60):
            last_logged["pct"] = pct - (pct % 10)
            last_logged["ts"] = now
            print(f"[mtproto] upload progress: {pct}% "
                  f"({current/1024/1024:.0f}/{total/1024/1024:.0f} MB)", flush=True)
        if progress_cb:
            try:
                progress_cb(frac)
            except Exception:
                pass

    app = Client(
        name="lofiloop_bot",
        api_id=api_id,
        api_hash=api_hash,
        bot_token=bot_token,
        workdir=workdir,
        in_memory=True,
        max_concurrent_transmissions=4,  # parallel chunk pipelines = faster upload
    )

    async with app:
        try:
            if as_video:
                await app.send_video(
                    chat_id=int(chat_id),
                    video=file_path,
                    caption=caption[:1024],
                    supports_streaming=True,
                    progress=_progress,
                )
            else:
                await app.send_document(
                    chat_id=int(chat_id),
                    document=file_path,
                    caption=caption[:1024],
                    progress=_progress,
                )
        except FloodWait as e:
            # Telegram asked us to slow down — obey exactly, then retry once
            # inside the same session (state is still valid for FloodWait).
            wait = int(getattr(e, "value", 30)) + 1
            print(f"[mtproto] FloodWait: sleeping {wait}s as instructed by Telegram...")
            await asyncio.sleep(wait)
            if as_video:
                await app.send_video(chat_id=int(chat_id), video=file_path,
                                     caption=caption[:1024], supports_streaming=True,
                                     progress=_progress)
            else:
                await app.send_document(chat_id=int(chat_id), document=file_path,
                                        caption=caption[:1024], progress=_progress)
    return True


def send_large_file(chat_id: str | int, file_path: str, caption: str = "",
                    as_video: bool = True, progress_cb=None) -> bool:
    """
    Send a file up to 2GB straight into the chat via MTProto.

    Returns True on success, False on any failure (caller should then fall back
    to a hosted download link).
    """
    if not os.path.isfile(file_path):
        print(f"[mtproto] File not found: {file_path}")
        return False

    size = os.path.getsize(file_path)
    if size > MAX_MTPROTO_BYTES:
        print(f"[mtproto] File is {size/1024/1024/1024:.2f}GB > 2GB cap; must use hosted link.")
        return False

    if not mtproto_available():
        print("[mtproto] Pyrogram not installed; cannot send large file directly.")
        return False

    print(f"[mtproto] Sending {os.path.basename(file_path)} "
          f"({size/1024/1024:.1f} MB) via MTProto...")

    # Connection-drop recovery: each attempt runs on a brand-new session, so a
    # broken TCP link / DC migration mid-upload can't poison later attempts.
    for attempt in range(1, MAX_SEND_ATTEMPTS + 1):
        try:
            return asyncio.run(
                _send_async(int(chat_id), file_path, caption, as_video, progress_cb)
            )
        except Exception as e:
            print(f"[mtproto] Send attempt {attempt}/{MAX_SEND_ATTEMPTS} failed: {e}")
            if attempt < MAX_SEND_ATTEMPTS:
                wait = RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
                print(f"[mtproto] Reconnecting with a fresh session in {wait}s...")
                time.sleep(wait)

    print(f"[mtproto] All {MAX_SEND_ATTEMPTS} MTProto attempts failed; falling back to hosted link.")
    return False


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3:
        cid, fp = sys.argv[1], sys.argv[2]
        ok = send_large_file(cid, fp, caption="Test upload")
        print("sent:", ok)
    else:
        print("usage: python -m bot.mtproto_transfer <chat_id> <file>")
