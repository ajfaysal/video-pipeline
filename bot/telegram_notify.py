"""
telegram_notify.py
------------------
Thin wrapper around the Telegram Bot API for sending progress messages and
final result files back to a chat from inside a GitHub Actions job.
Uses only `requests` - no bot framework needed since the runner never
listens for updates, it only sends.
"""

from __future__ import annotations

import os
import sys

import requests

API_ROOT = "https://api.telegram.org/bot{token}/{method}"


def _token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set.")
    return token


def send_message(chat_id: str, text: str) -> None:
    url = API_ROOT.format(token=_token(), method="sendMessage")
    resp = requests.post(url, data={"chat_id": chat_id, "text": text[:4000]})
    if not resp.ok:
        print(f"[telegram] sendMessage failed: {resp.status_code} {resp.text[:300]}", file=sys.stderr)


def send_video(chat_id: str, video_path: str, caption: str = "") -> None:
    url = API_ROOT.format(token=_token(), method="sendVideo")
    with open(video_path, "rb") as f:
        resp = requests.post(
            url,
            data={"chat_id": chat_id, "caption": caption[:1024], "supports_streaming": True},
            files={"video": f},
            timeout=600,
        )
    if not resp.ok:
        print(f"[telegram] sendVideo failed for {video_path}: {resp.status_code} {resp.text[:300]}", file=sys.stderr)
        # Videos over Telegram's 50MB bot-upload limit land here - fall back to a text notice.
        send_message(chat_id, f"⚠️ Could not upload '{os.path.basename(video_path)}' "
                               f"(likely over Telegram's 50MB bot upload limit). "
                               f"It's still available in the GitHub Actions run artifacts.")


def send_photo(chat_id: str, photo_path: str, caption: str = "") -> None:
    url = API_ROOT.format(token=_token(), method="sendPhoto")
    with open(photo_path, "rb") as f:
        resp = requests.post(
            url, data={"chat_id": chat_id, "caption": caption[:1024]}, files={"photo": f}, timeout=120,
        )
    if not resp.ok:
        print(f"[telegram] sendPhoto failed for {photo_path}: {resp.status_code} {resp.text[:300]}", file=sys.stderr)


def send_document(chat_id: str, doc_path: str, caption: str = "") -> None:
    url = API_ROOT.format(token=_token(), method="sendDocument")
    with open(doc_path, "rb") as f:
        resp = requests.post(
            url, data={"chat_id": chat_id, "caption": caption[:1024]}, files={"document": f}, timeout=300,
        )
    if not resp.ok:
        print(f"[telegram] sendDocument failed for {doc_path}: {resp.status_code} {resp.text[:300]}", file=sys.stderr)
