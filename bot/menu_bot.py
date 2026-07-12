"""
menu_bot.py
-----------
Solution B: the Telegram menu UI served straight from the Python backend.

This is a zero-Cloudflare, long-polling bot that renders the exact same
menu/conversation flow as cloudflare-worker/worker.js — including the featured
🎧 LofiLoop tool and the ⋯ 3-dot overflow menu — with NO worker deployment
needed. Run it from GitHub Actions (.github/workflows/bot-poller.yml) or any
machine with just TELEGRAM_BOT_TOKEN set.

How jobs get executed
=====================
1. If GH_DISPATCH_TOKEN (a PAT with Actions:write) is set, the bot fires the
   same `repository_dispatch` event the Cloudflare Worker uses, so
   telegram-dispatch.yml picks the job up in a fresh runner (best: polling
   never blocks).
2. Otherwise the bot runs bot/run_job.py inline in a worker thread — polling
   keeps responding to users while the render happens in the background.
   This means the ONLY required secret is TELEGRAM_BOT_TOKEN.

On startup the bot calls deleteWebhook so getUpdates works even if the
Cloudflare Worker webhook was previously registered (the poller supersedes
the worker while it runs).

Env vars:
    TELEGRAM_BOT_TOKEN   (required)  bot token from @BotFather
    GH_DISPATCH_TOKEN    (optional)  PAT to dispatch jobs to GitHub Actions
    GITHUB_REPOSITORY    (optional)  "owner/repo" for dispatch (auto-set in CI)
    POLLER_MAX_SECONDS   (optional)  exit cleanly after N seconds (CI chaining)
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time

import requests

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

API = "https://api.telegram.org/bot{token}/{method}"
STATE_TTL = 3600  # seconds a conversation step stays alive


# --------------------------------------------------------------------------- #
# Telegram API helpers (with retry + markdown fallback)
# --------------------------------------------------------------------------- #
def _token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required.")
    return token


def tg(method: str, payload: dict, retries: int = 3, timeout: int = 65) -> dict:
    url = API.format(token=_token(), method=method)
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(url, json=payload, timeout=timeout)
            data = resp.json() if resp.content else {}
            if resp.ok and data.get("ok"):
                return data
            # 409 = another getUpdates consumer; bubble up immediately.
            if resp.status_code == 409:
                raise RuntimeError(f"409 Conflict from Telegram: {data}")
            # Markdown parse errors -> retry once without parse_mode.
            desc = str(data.get("description", ""))
            if "can't parse entities" in desc.lower() and payload.get("parse_mode"):
                payload = dict(payload)
                payload.pop("parse_mode", None)
                continue
            if resp.status_code == 429:
                time.sleep(int(data.get("parameters", {}).get("retry_after", 2)) + 1)
                continue
            last_err = RuntimeError(f"{method} failed: {resp.status_code} {desc[:200]}")
        except requests.RequestException as e:
            last_err = e
        time.sleep(min(2 ** attempt, 10))
    print(f"[menu_bot] {method} gave up after {retries} attempts: {last_err}", file=sys.stderr)
    return {}


def send_message(chat_id, text: str, reply_markup: dict | None = None) -> None:
    payload = {"chat_id": chat_id, "text": text[:4000], "parse_mode": "Markdown",
               "disable_web_page_preview": True}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    tg("sendMessage", payload)


def answer_callback(cb_id: str, text: str = "") -> None:
    tg("answerCallbackQuery", {"callback_query_id": cb_id, "text": text}, retries=1, timeout=15)


def kb(rows) -> dict:
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for (t, d) in row] for row in rows]}


def with_back(rows) -> dict:
    return kb(list(rows) + [[("🏠 Back to Menu", "menu")]])


# --------------------------------------------------------------------------- #
# Menu definition (mirrors worker.js exactly)
# --------------------------------------------------------------------------- #
TOOL_LABELS = {
    "lofiloop": "🎧 LofiLoop",
    "aspectshift": "🔳 AspectShift",
    "clipharvest": "✂️ ClipHarvest",
    "watermarkwipe": "🧽 WatermarkWipe",
    "introoutro": "🎬 IntroOutro",
    "abroll": "🧩 ABRoll",
    "stitcher": "🧵 Stitcher",
    "audioduck": "🎙️ AudioDuck",
    "loudnorm": "📶 LoudNorm",
    "autochapters": "📊 AutoChapters",
}

TOOL_GROUPS = [
    ("🔥 Viral Studio", [("lofiloop", "loop a 10s clip + long audio into a monetization-safe 2h/10h/24h video")]),
    ("🎬 Video Format", [("aspectshift", "convert 16:9 video into vertical 9:16"),
                         ("loudnorm", "normalize audio loudness to -14 LUFS")]),
    ("✂️ Editing", [("clipharvest", "extract the best short clips from a long video"),
                    ("abroll", "insert B-roll around natural cut points"),
                    ("introoutro", "add a branded intro and outro card"),
                    ("stitcher", "join multiple clips with transitions"),
                    ("audioduck", "duck background music under voiceover")]),
    ("🎨 Enhancement", [("watermarkwipe", "remove logos and watermarks")]),
    ("📊 Metadata", [("autochapters", "generate YouTube chapters from the transcript")]),
]


def menu_text() -> str:
    lines = ["🎛️ *Creator Studio Bot* — the most powerful video toolkit on Telegram.",
             "", "Pick a tool below, then I’ll walk you through it:", ""]
    for title, items in TOOL_GROUPS:
        lines.append(title)
        for tool, desc in items:
            lines.append(f"  • {TOOL_LABELS[tool]} — {desc}")
        lines.append("")
    lines.append("📤 Send links for big files. Rendered lofi videos up to *2GB* are delivered straight to your chat.")
    lines.append("Tap ⋯ for more options.")
    return "\n".join(lines)


def menu_keyboard() -> dict:
    return kb([
        [("🎧 LofiLoop — Viral Loop Studio 🔥", "tool:lofiloop")],
        [(TOOL_LABELS["aspectshift"], "tool:aspectshift"), (TOOL_LABELS["clipharvest"], "tool:clipharvest")],
        [(TOOL_LABELS["watermarkwipe"], "tool:watermarkwipe"), (TOOL_LABELS["introoutro"], "tool:introoutro")],
        [(TOOL_LABELS["abroll"], "tool:abroll"), (TOOL_LABELS["stitcher"], "tool:stitcher")],
        [(TOOL_LABELS["audioduck"], "tool:audioduck"), (TOOL_LABELS["loudnorm"], "tool:loudnorm")],
        [(TOOL_LABELS["autochapters"], "tool:autochapters")],
        [("⋯ More", "overflow")],
    ])


def overflow_text() -> str:
    return ("⋯ *More options*\n\n"
            "ℹ️ About — what this studio can do\n"
            "❓ Help — how each tool works\n"
            "🚀 Large files — up to 2GB delivered in-chat via MTProto\n\n"
            "Pick one:")


def overflow_keyboard() -> dict:
    return kb([
        [("ℹ️ About", "info:about"), ("❓ Help", "info:help")],
        [("🚀 Large files (2GB)", "info:largefiles")],
        [("🏠 Back to Menu", "menu")],
    ])


def info_text(topic: str) -> str:
    if topic == "about":
        return ("ℹ️ *About Creator Studio Bot*\n\n"
                "A studio-grade FFmpeg pipeline that runs on GitHub Actions and delivers "
                "results straight to Telegram. The flagship 🎧 *LofiLoop* renders seamless, "
                "monetization-safe long-form lofi videos (2h / 10h / 24h) with a unique "
                "per-frame digital fingerprint so YouTube never flags them as reused content.")
    if topic == "help":
        return ("❓ *Help*\n\n"
                "1. Tap a tool.\n"
                "2. Send the video/audio *file* or a *direct link*.\n"
                "3. Answer the quick questions (mode, duration, …).\n"
                "4. I dispatch the render and message you when it’s done.\n\n"
                "For 🎧 LofiLoop: send the short 10s loop clip, then paste a public Google "
                "Drive link for the long audio, then pick the target hours.")
    return ("🚀 *Large files*\n\n"
            "Rendered lofi videos are delivered *directly in this chat* up to *2GB* using "
            "MTProto (no more 20MB limit). Anything larger falls back to a free direct "
            "download link (GoFile / transfer.sh) — no signup, no API keys.")


SOURCE_PROMPTS = {
    "lofiloop": "🎧 *LofiLoop* — Step 1 of 3\n\nSend your short *seamlessly-looping* clip (a ~10s .mp4 works best) as a file or a direct link.",
    "aspectshift": "Send the video you want converted to vertical 9:16.",
    "clipharvest": "Send your long video link or file to analyze for the best clips.",
    "watermarkwipe": "Send the video you want to clean up.",
    "introoutro": "Send the video you want to wrap with an intro and outro.",
    "abroll": "Send the main video first. I’ll ask for B-roll clips next.",
    "stitcher": "Send the first clip you want stitched. I’ll ask for the remaining clips next.",
    "audioduck": "Send the main video first. I’ll ask for the voiceover track next.",
    "loudnorm": "Send the video you want normalized to broadcast loudness.",
    "autochapters": "Send your long video link or file. I’ll generate chapter timestamps from the transcript.",
}

HOURS_KB = [[("1h", "hours:1"), ("2h", "hours:2"), ("3h", "hours:3")],
            [("6h", "hours:6"), ("10h", "hours:10"), ("24h", "hours:24")]]


# --------------------------------------------------------------------------- #
# Conversation state (in-memory, per-chat, TTL'd)
# --------------------------------------------------------------------------- #
_states: dict[str, dict] = {}
_states_lock = threading.Lock()


def get_state(chat_id) -> dict | None:
    with _states_lock:
        entry = _states.get(str(chat_id))
        if not entry:
            return None
        if time.time() - entry["ts"] > STATE_TTL:
            del _states[str(chat_id)]
            return None
        return entry["state"]


def set_state(chat_id, state: dict) -> None:
    with _states_lock:
        _states[str(chat_id)] = {"state": state, "ts": time.time()}


def clear_state(chat_id) -> None:
    with _states_lock:
        _states.pop(str(chat_id), None)


# --------------------------------------------------------------------------- #
# Job execution: GitHub dispatch (preferred) or inline thread (zero-config)
# --------------------------------------------------------------------------- #
_active_jobs = 0
_jobs_lock = threading.Lock()
MAX_INLINE_JOBS = 2


def _payload_from_state(chat_id, state: dict) -> dict:
    return {
        "chat_id": str(chat_id),
        "tool": state["tool"],
        "source_type": state.get("source_type", "url"),
        "source_value": state.get("source_value", ""),
        "options": {
            "mode": state.get("mode", ""),
            "num_clips": state.get("num_clips", ""),
            "min_duration": state.get("min_duration", ""),
            "max_duration": state.get("max_duration", ""),
            "captions": state.get("captions", ""),
            "region": "",
            "color_grade": state.get("color_grade", ""),
            "background_blur": state.get("background_blur", "false"),
            "intro_text": state.get("intro_text", "Your Channel Name"),
            "outro_text": state.get("outro_text", "Subscribe for more"),
            "intro_duration": state.get("intro_duration", "3.5"),
            "outro_duration": state.get("outro_duration", "3.5"),
            "broll_sources_json": json.dumps(state.get("extra_sources", [])),
            "stitch_clips_json": json.dumps([state.get("source_value", "")] + state.get("extra_sources", [])),
            "transition": state.get("transition", "crossfade"),
            "transition_duration": state.get("transition_duration", "0.8"),
            "voiceover_source": state.get("voiceover_source", (state.get("extra_sources") or [""])[0]),
            "target_lufs": state.get("target_lufs", "-14"),
            "target_tp": state.get("target_tp", "-1.5"),
            "target_lra": state.get("target_lra", "11"),
            "lofi_audio": state.get("lofi_audio", ""),
            "lofi_hours": state.get("lofi_hours", "2"),
            "lofi_crf": state.get("lofi_crf", "18"),
            "lofi_preset": state.get("lofi_preset", "veryfast"),
            "lofi_noise": state.get("lofi_noise", "1"),
        },
    }


def _dispatch_github(payload: dict) -> bool:
    pat = os.environ.get("GH_DISPATCH_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not pat or not repo:
        return False
    url = f"https://api.github.com/repos/{repo}/dispatches"
    for attempt in range(1, 4):
        try:
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {pat}",
                         "Accept": "application/vnd.github+json",
                         "User-Agent": "video-pipeline-menu-bot"},
                json={"event_type": "telegram-job", "client_payload": payload},
                timeout=30,
            )
            if resp.status_code == 204:
                return True
            print(f"[menu_bot] dispatch attempt {attempt}: {resp.status_code} {resp.text[:200]}", file=sys.stderr)
        except requests.RequestException as e:
            print(f"[menu_bot] dispatch attempt {attempt} error: {e}", file=sys.stderr)
        time.sleep(2 ** attempt)
    return False


def _run_inline(payload: dict) -> None:
    """Run bot/run_job.py in a subprocess (worker thread) so polling continues."""
    global _active_jobs
    env = dict(os.environ)
    env.update({
        "CHAT_ID": payload["chat_id"],
        "TOOL": payload["tool"],
        "SOURCE_TYPE": payload["source_type"],
        "SOURCE_VALUE": payload["source_value"],
    })
    for k, v in payload["options"].items():
        env[k.upper()] = str(v)
    try:
        subprocess.run([sys.executable, os.path.join(_REPO_ROOT, "bot", "run_job.py")],
                       env=env, cwd=_REPO_ROOT)
    finally:
        with _jobs_lock:
            _active_jobs -= 1


def dispatch_and_finish(chat_id, state: dict) -> None:
    global _active_jobs
    payload = _payload_from_state(chat_id, state)
    clear_state(chat_id)

    if _dispatch_github(payload):
        send_message(chat_id, "🚀 Job dispatched to a dedicated runner! I'll message you here with progress and the finished file(s).", menu_keyboard())
        return

    with _jobs_lock:
        if _active_jobs >= MAX_INLINE_JOBS:
            send_message(chat_id, "⏳ The studio is at full capacity right now — please try again in a few minutes.", menu_keyboard())
            return
        _active_jobs += 1

    threading.Thread(target=_run_inline, args=(payload,), daemon=False).start()
    send_message(chat_id, "🚀 Job started! I'll message you here with progress and the finished file(s).", menu_keyboard())


# --------------------------------------------------------------------------- #
# Update handling (same flow as worker.js)
# --------------------------------------------------------------------------- #
def _looks_like_url(text: str) -> bool:
    t = (text or "").strip()
    return t.startswith(("http://", "https://")) and " " not in t


def _source_from_message(message: dict, allow_audio: bool = False) -> str | None:
    text = (message.get("text") or "").strip()
    if _looks_like_url(text):
        return text
    file = message.get("video") or message.get("document") or (message.get("audio") if allow_audio else None)
    if not file:
        return None
    if file.get("file_size", 0) > 20 * 1024 * 1024:
        raise ValueError("That file is over Telegram's 20MB bot-download limit. Please send a link instead.")
    data = tg("getFile", {"file_id": file["file_id"]}, retries=2, timeout=30)
    if not data.get("ok"):
        raise ValueError("Couldn't read that file from Telegram.")
    return f"https://api.telegram.org/file/bot{_token()}/{data['result']['file_path']}"


def show_main_menu(chat_id) -> None:
    clear_state(chat_id)
    send_message(chat_id, menu_text(), menu_keyboard())


def prompt_for_source(chat_id, tool: str) -> None:
    set_state(chat_id, {"step": "awaiting_source", "tool": tool})
    send_message(chat_id, SOURCE_PROMPTS.get(tool, "Send the video or link for this tool."), with_back([]))


def continue_after_source(chat_id, state: dict) -> None:
    tool = state["tool"]
    if tool == "lofiloop":
        state["step"] = "collect_lofi_audio"
        set_state(chat_id, state)
        send_message(chat_id,
                     "🎧 *LofiLoop* — Step 2 of 3\n\nNow paste a *public Google Drive link* to your long audio file "
                     "(set to “Anyone with the link”). A direct audio URL also works. No API keys needed — I fetch it automatically.",
                     with_back([]))
    elif tool == "aspectshift":
        state["step"] = "choose_mode_aspectshift"
        set_state(chat_id, state)
        send_message(chat_id, "Which conversion mode?", with_back([
            [("🌫️ Blur-pad (recommended, zero crop loss)", "mode:blur")],
            [("✂️ Smart crop (no blur pillarbox)", "mode:crop")]]))
    elif tool == "clipharvest":
        state["step"] = "choose_clip_count"
        set_state(chat_id, state)
        send_message(chat_id, "How many clips should I extract?", with_back([
            [("3 clips", "clips:3"), ("5 clips", "clips:5"), ("8 clips", "clips:8")]]))
    elif tool == "watermarkwipe":
        state["step"] = "choose_mode_watermark"
        set_state(chat_id, state)
        send_message(chat_id, "Which removal mode?", with_back([
            [("🖌️ Inpaint (center/moving logos)", "mode:inpaint")],
            [("✂️ Crop (corner/edge logos)", "mode:crop")]]))
    elif tool == "abroll":
        state.update(step="collect_brolls", extra_sources=[])
        set_state(chat_id, state)
        send_message(chat_id, "Send one or more B-roll clips or URLs, then send /done when finished.", with_back([]))
    elif tool == "stitcher":
        state.update(step="collect_stitch_clips", extra_sources=[])
        set_state(chat_id, state)
        send_message(chat_id, "Send the remaining clips you want to stitch together, then send /done. The first clip is the one you already sent.", with_back([]))
    elif tool == "audioduck":
        state.update(step="collect_voiceover", extra_sources=[])
        set_state(chat_id, state)
        send_message(chat_id, "Now send the voiceover audio file or URL. I’ll dispatch as soon as I receive it.", with_back([]))
    else:
        dispatch_and_finish(chat_id, state)


def handle_message(message: dict) -> None:
    chat_id = message["chat"]["id"]
    text = (message.get("text") or "").strip()
    state = get_state(chat_id)

    if text in ("/start", "/help", "/menu"):
        show_main_menu(chat_id)
        return

    if state and state.get("step") == "collect_lofi_audio":
        try:
            audio_url = text if _looks_like_url(text) else _source_from_message(message, allow_audio=True)
            if not audio_url:
                send_message(chat_id, "Paste a *public Google Drive link* or a direct audio URL (or send the audio file).", with_back([]))
                return
            state["lofi_audio"] = audio_url
            state["step"] = "choose_lofi_hours"
            set_state(chat_id, state)
            send_message(chat_id,
                         "🎧 *LofiLoop* — Step 3 of 3\n\n⏱️ *Enter target video duration in hours* (e.g. 2, 10, 24).\n\n"
                         "Tap a preset below or just type a number:", with_back(HOURS_KB))
        except ValueError as e:
            send_message(chat_id, f"❌ {e}", with_back([]))
        return

    if state and state.get("step") == "choose_lofi_hours":
        try:
            hours = float(text)
        except ValueError:
            hours = -1
        if 0 < hours <= 48:
            state["lofi_hours"] = str(hours)
            dispatch_and_finish(chat_id, state)
        else:
            send_message(chat_id, "Please send a number of hours between 0 and 48 (e.g. 2, 10, 24), or tap a preset.", with_back(HOURS_KB))
        return

    if state and state.get("step") in ("collect_brolls", "collect_stitch_clips", "collect_voiceover"):
        step = state["step"]
        if text == "/done":
            if step in ("collect_brolls", "collect_stitch_clips") and not state.get("extra_sources"):
                send_message(chat_id, "Send at least one clip before /done.", with_back([]))
            elif step == "collect_voiceover":
                send_message(chat_id, "Send the voiceover audio file or URL first.", with_back([]))
            else:
                dispatch_and_finish(chat_id, state)
            return
        try:
            src = _source_from_message(message, allow_audio=(step == "collect_voiceover"))
            if not src:
                send_message(chat_id, "Send a video/audio file or a direct URL, or /done when you're finished.", with_back([]))
                return
            state.setdefault("extra_sources", []).append(src)
            if step == "collect_voiceover":
                state["voiceover_source"] = src
                dispatch_and_finish(chat_id, state)
                return
            set_state(chat_id, state)
            send_message(chat_id, f"Added {len(state['extra_sources'])} item(s). Send more or /done.", with_back([]))
        except ValueError as e:
            send_message(chat_id, f"❌ {e}", with_back([]))
        return

    if state and state.get("step") == "awaiting_source":
        try:
            src = _source_from_message(message, allow_audio=(state["tool"] == "audioduck"))
            if not src:
                send_message(chat_id, SOURCE_PROMPTS.get(state["tool"], "Send the video or link."), with_back([]))
                return
            state["source_type"] = "url"
            state["source_value"] = src
            continue_after_source(chat_id, state)
        except ValueError as e:
            send_message(chat_id, f"❌ {e}", with_back([]))
        return

    if message.get("video") or message.get("document") or message.get("audio") or _looks_like_url(text):
        send_message(chat_id, "Choose a tool from the menu first.", menu_keyboard())
        return

    show_main_menu(chat_id)


def handle_callback(cb: dict) -> None:
    chat_id = cb["message"]["chat"]["id"]
    data = cb.get("data") or ""
    state = get_state(chat_id)
    kind, _, value = data.partition(":")

    answer_callback(cb["id"])

    if kind == "menu":
        show_main_menu(chat_id)
        return
    if kind == "overflow":
        send_message(chat_id, overflow_text(), overflow_keyboard())
        return
    if kind == "info":
        send_message(chat_id, info_text(value), overflow_keyboard())
        return
    if kind == "tool":
        prompt_for_source(chat_id, value)
        return
    if not state:
        answer_callback(cb["id"], "This step expired, please open the menu again.")
        return
    if kind == "hours" and state.get("step") == "choose_lofi_hours":
        state["lofi_hours"] = value
        dispatch_and_finish(chat_id, state)
        return
    if kind == "mode" and state.get("step") == "choose_mode_aspectshift":
        state["mode"] = value
        dispatch_and_finish(chat_id, state)
        return
    if kind == "clips":
        state.update(num_clips=value, min_duration="20", max_duration="90", captions="true")
        dispatch_and_finish(chat_id, state)
        return
    if kind == "mode" and state.get("step") == "choose_mode_watermark":
        state["mode"] = value
        state["step"] = "choose_color_grade"
        set_state(chat_id, state)
        send_message(chat_id, "Add color grading?", with_back([
            [("None", "grade:none")],
            [("🎬 Cinematic", "grade:cinematic"), ("🌈 Vibrant", "grade:vibrant")],
            [("🔥 Warm", "grade:warm"), ("❄️ Cool", "grade:cool")]]))
        return
    if kind == "grade":
        state["color_grade"] = "" if value == "none" else value
        state["step"] = "choose_bg_blur"
        set_state(chat_id, state)
        send_message(chat_id, "Add portrait-mode background blur?", with_back([
            [("Yes", "bgblur:true"), ("No", "bgblur:false")]]))
        return
    if kind == "bgblur":
        state["background_blur"] = value
        dispatch_and_finish(chat_id, state)
        return

    answer_callback(cb["id"], "That step is no longer active. Please reopen the menu.")


# --------------------------------------------------------------------------- #
# Long-polling loop
# --------------------------------------------------------------------------- #
_shutdown = threading.Event()


def _sig(_signum, _frame):
    _shutdown.set()


def main() -> int:
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    max_seconds = float(os.environ.get("POLLER_MAX_SECONDS", "0") or 0)
    deadline = time.time() + max_seconds if max_seconds > 0 else None

    # Take over from any webhook (e.g. the Cloudflare Worker) so getUpdates works.
    tg("deleteWebhook", {"drop_pending_updates": False}, retries=2, timeout=30)
    me = tg("getMe", {}, retries=3, timeout=30)
    print(f"[menu_bot] Polling as @{me.get('result', {}).get('username', '?')} "
          f"(dispatch={'github' if os.environ.get('GH_DISPATCH_TOKEN') else 'inline'})")

    offset = 0
    consecutive_errors = 0
    while not _shutdown.is_set():
        if deadline and time.time() > deadline:
            print("[menu_bot] POLLER_MAX_SECONDS reached — exiting cleanly.")
            break
        try:
            resp = requests.post(
                API.format(token=_token(), method="getUpdates"),
                json={"offset": offset, "timeout": 50,
                      "allowed_updates": ["message", "callback_query"]},
                timeout=65,
            )
            data = resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"getUpdates: {data}")
            consecutive_errors = 0
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                try:
                    if "message" in update:
                        handle_message(update["message"])
                    elif "callback_query" in update:
                        handle_callback(update["callback_query"])
                except Exception as e:  # never let one bad update kill the loop
                    print(f"[menu_bot] update error: {e}", file=sys.stderr)
        except Exception as e:
            consecutive_errors += 1
            wait = min(2 ** consecutive_errors, 60)
            print(f"[menu_bot] poll error ({consecutive_errors}): {e}; retrying in {wait}s", file=sys.stderr)
            _shutdown.wait(wait)

    # Wait for inline jobs to finish before the runner dies.
    while True:
        with _jobs_lock:
            if _active_jobs == 0:
                break
        print(f"[menu_bot] waiting for {_active_jobs} inline job(s) to finish...")
        time.sleep(15)
    return 0


if __name__ == "__main__":
    sys.exit(main())
