#!/usr/bin/env python3
"""
bot.py - StockFlow AI Telegram bot.

Professional-grade commercial asset production engine for Adobe Stock.

Commands
    /start            Welcome + quick-action keyboard (+ Mini-App button)
    /generate <prompt>  Full production run: unique-seed image + 70-char
                        commercial title + 50 ranked SEO tags + HD delivery
    /niche            VidIQ-style market intelligence (top opportunities now)
    /settings         Engine switching, custom API endpoints, defaults, keys
    /engines          List configured engines
    /defaults         Show current production defaults
    /help             Command reference

Runs in long-polling mode (zero-config, works on any $0 host). If
``telegram.miniapp_url`` is set in config.json, the Mini-App backend
(stockflow.webapp) is auto-started in a background thread and the /start
keyboard gains a Web App button.

Environment
    TELEGRAM_BOT_TOKEN  (required)  Bot token from @BotFather
    HF_TOKEN            (default engine)  Free token: huggingface.co/settings/tokens
    STOCKFLOW_PORT      Mini-App port (default 8080)
"""

from __future__ import annotations

import html
import logging
import os
import threading
from typing import Any, Dict, List, Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
    WebAppInfo,
)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from stockflow.config import BUILTIN_ENGINES, Config, load_config
from stockflow.engines import EngineError, RESOLUTION_TIERS
from stockflow.niches import format_niches_telegram, suggest_niches
from stockflow.pipeline import bundle_zip, run_production

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("stockflow.bot")

CONFIG: Config = load_config()

# Per-chat pending settings wizard state.
_pending: Dict[int, Dict[str, Any]] = {}

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [["⚡ Generate", "📊 Niche Intel"], ["⚙️ Settings", "❓ Help"]],
    resize_keyboard=True,
)

RES_TIERS = list(RESOLUTION_TIERS.keys())
FORMATS = ("png", "svg")
ASPECTS = ("square", "landscape", "portrait")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _esc(text: Any) -> str:
    return html.escape(str(text))


def _defaults_line() -> str:
    d = CONFIG.defaults
    return (
        f"engine <code>{_esc(d.get('engine'))}</code> · "
        f"{_esc(d.get('resolution', '2k')).upper()} · "
        f"{_esc(d.get('aspect'))} · {_esc(str(d.get('format')).upper())} · "
        f"upscale {'on' if d.get('upscale') else 'off'}"
    )


def _engines_keyboard() -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    current = CONFIG.get_default("engine")
    for eid, eng in CONFIG.engines.items():
        has_key = bool(CONFIG.api_key_for(eng))
        mark = "✅ " if eid == current else ""
        key_icon = "🔑" if has_key else "⚠️"
        rows.append([InlineKeyboardButton(
            f"{mark}{eng.get('label', eid)} {key_icon}",
            callback_data=f"engine:use:{eid}",
        )])
    rows.append([
        InlineKeyboardButton("➕ Add custom API", callback_data="engine:add"),
        InlineKeyboardButton("🔑 Set API key", callback_data="engine:key"),
    ])
    rows.append([
        InlineKeyboardButton("🎛 Defaults", callback_data="settings:defaults"),
        InlineKeyboardButton("🗑 Remove engine", callback_data="engine:remove"),
    ])
    return InlineKeyboardMarkup(rows)


def _send_welcome() -> str:
    return (
        "📈 <b>StockFlow AI</b> — commercial asset production engine\n\n"
        "Turn one prompt into an Adobe-Stock-ready bundle:\n"
        "• 🎨 FLUX.1-schnell generation (free) or your own OpenAI / Stability / Midjourney API\n"
        "• 🛡️ <b>Uniqueness Shield</b> — a unique mathematical seed per asset\n"
        "• 🏷️ 70-char commercial title + 50 ranked SEO tags\n"
        "• 🔍 Up to 16K Ultra-HD staged upscaling · PNG/SVG · zero watermarks\n\n"
        "<b>Quick start</b>\n"
        "<code>/generate cozy home office with plants, morning light, lifestyle photo</code>\n\n"
        "Commands: /generate /niche /settings /engines /defaults /help\n\n"
        f"Current setup: {_defaults_line()}"
    )


# ---------------------------------------------------------------------------
# /start, /help
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = MAIN_KEYBOARD
    miniapp_url = CONFIG.telegram().get("miniapp_url")
    inline = None
    if miniapp_url:
        inline = InlineKeyboardMarkup([[
            InlineKeyboardButton("🖥 Open Studio Mini-App", web_app=WebAppInfo(miniapp_url))
        ]])
    await update.message.reply_text(
        _send_welcome(), parse_mode=ParseMode.HTML,
        reply_markup=inline or keyboard,
    )
    if inline:
        await update.message.reply_text("Or use the quick keyboard 👇", reply_markup=keyboard)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "<b>StockFlow AI — command reference</b>\n\n"
        "<code>/generate &lt;prompt&gt;</code> — produce a commercial asset\n"
        "   flags: <code>--res 1k|2k|4k|8k|16k</code> <code>--format png|svg</code>\n"
        "          <code>--aspect square|landscape|portrait</code> <code>--upscale</code>\n"
        "          <code>--engine &lt;id&gt;</code> <code>--niche &lt;text&gt;</code>\n\n"
        "<code>/niche</code> — live market intelligence, top opportunities\n"
        "<code>/settings</code> — engines, custom APIs, keys, defaults\n"
        "<code>/engines</code> — list engines · <code>/defaults</code> — show defaults\n\n"
        "<b>$0 setup</b>: default engine is Hugging Face free tier.\n"
        "Get a token at huggingface.co/settings/tokens, then either export\n"
        "<code>HF_TOKEN</code> or paste it via /settings → 🔑 Set API key.",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# /generate
# ---------------------------------------------------------------------------

def _parse_generate_args(args: List[str]) -> Dict[str, Any]:
    opts: Dict[str, Any] = {"prompt_parts": []}
    it = iter(range(len(args)))
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--res" and i + 1 < len(args):
            opts["resolution"] = args[i + 1].lower(); i += 2
        elif arg == "--format" and i + 1 < len(args):
            opts["out_format"] = args[i + 1].lower(); i += 2
        elif arg == "--aspect" and i + 1 < len(args):
            opts["aspect"] = args[i + 1].lower(); i += 2
        elif arg == "--engine" and i + 1 < len(args):
            opts["engine_id"] = args[i + 1].lower(); i += 2
        elif arg == "--niche" and i + 1 < len(args):
            opts["niche"] = args[i + 1]; i += 2
        elif arg == "--upscale":
            opts["upscale"] = True; i += 1
        else:
            opts["prompt_parts"].append(arg); i += 1
    return opts


async def cmd_generate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    opts = _parse_generate_args(context.args or [])
    prompt = " ".join(opts.pop("prompt_parts")).strip()
    if not prompt:
        await update.message.reply_text(
            "Usage: <code>/generate &lt;prompt&gt;</code>\n"
            "e.g. <code>/generate minimal eco cosmetic packaging mockup --res 4k --upscale</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    chat_id = update.effective_chat.id
    status = await update.message.reply_text(
        "⚙️ <b>Production run started…</b>\n"
        "1/3 Contacting engine (cold start can take ~60s on the free tier)…",
        parse_mode=ParseMode.HTML,
    )
    await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_PHOTO)

    loop = __import__("asyncio").get_running_loop()
    try:
        bundle = await loop.run_in_executor(
            None, lambda: run_production(CONFIG, prompt, save=True, **opts)
        )
    except EngineError as exc:
        await status.edit_text(f"❌ Engine error:\n<code>{_esc(exc)}</code>",
                               parse_mode=ParseMode.HTML)
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("generation failed")
        await status.edit_text(f"❌ Generation failed: <code>{_esc(exc)}</code>",
                               parse_mode=ParseMode.HTML)
        return

    g, s = bundle.generation, bundle.seo
    await status.edit_text(
        "⚙️ 2/3 Image rendered — applying SEO package & Uniqueness Shield…",
        parse_mode=ParseMode.HTML,
    )

    caption = (
        f"✅ <b>Asset ready</b> — {_esc(g.width)}×{_esc(g.height)} · {_esc(g.engine)}\n\n"
        f"🏷 <b>Title ({s.title_length}/70):</b>\n<code>{_esc(s.title)}</code>\n\n"
        f"🔖 <b>{s.tag_count} SEO tags:</b>\n<code>{_esc(s.tags_csv[:900])}</code>"
        + ("…" if len(s.tags_csv) > 900 else "")
        + f"\n\n🛡️ Shield <code>{g.shield_code}</code> · seed <code>{g.seed}</code>"
    )

    # Telegram photo limit is 10MB; PNG of a 16K frame is bigger -> document.
    asset_file = next((f for f in bundle.files if not f.endswith(".json")), None)
    with open(asset_file, "rb") as fh:
        payload = fh.read()
    await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_DOCUMENT)
    filename = os.path.basename(asset_file)
    if len(payload) <= 10_000_000 and filename.endswith(".png"):
        await update.message.reply_photo(
            photo=payload, caption=caption, parse_mode=ParseMode.HTML,
        )
    else:
        import io as _io
        await update.message.reply_document(
            document=_io.BytesIO(payload), filename=filename,
            caption=caption[:1024], parse_mode=ParseMode.HTML,
        )

    # Full metadata ZIP (asset + metadata.json) for one-click archival.
    zip_bytes = bundle_zip(bundle)
    import io as _io
    await update.message.reply_document(
        document=_io.BytesIO(zip_bytes),
        filename=f"stockflow_{bundle.job_id}.zip",
        caption="📦 Full bundle: asset + metadata.json (title/tags/seed/shield)",
    )
    await status.delete()


# ---------------------------------------------------------------------------
# /niche
# ---------------------------------------------------------------------------

async def cmd_niche(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("📡 Scanning market signals…")
    loop = __import__("asyncio").get_running_loop()
    try:
        text = await loop.run_in_executor(None, format_niches_telegram, 5)
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as exc:  # noqa: BLE001
        log.exception("niche scan failed")
        await msg.edit_text(f"❌ Niche scan failed: {_esc(exc)}",
                            parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /engines, /defaults, /settings
# ---------------------------------------------------------------------------

async def cmd_engines(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lines = ["<b>Configured engines</b>\n"]
    current = CONFIG.get_default("engine")
    for eid, eng in CONFIG.engines.items():
        mark = "✅" if eid == current else "•"
        builtin = "built-in" if eid in BUILTIN_ENGINES else "custom"
        lines.append(
            f"{mark} <code>{_esc(eid)}</code> — {_esc(eng.get('label', eid))}\n"
            f"    format <code>{_esc(eng.get('api_format'))}</code> · model "
            f"<code>{_esc(eng.get('model') or '-')}</code> · key {_esc(CONFIG.mask_key(eng))} · {builtin}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_defaults(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    d = CONFIG.defaults
    await update.message.reply_text(
        "<b>Production defaults</b>\n\n"
        f"Engine: <code>{_esc(d.get('engine'))}</code>\n"
        f"Resolution: <code>{_esc(d.get('resolution'))}</code>\n"
        f"Aspect: <code>{_esc(d.get('aspect'))}</code>\n"
        f"Format: <code>{_esc(d.get('format'))}</code>\n"
        f"Upscale pass: <code>{'on' if d.get('upscale') else 'off'}</code>\n"
        f"Title length: <code>{_esc(d.get('title_length'))}</code>\n"
        f"Tag count: <code>{_esc(d.get('tag_count'))}</code>\n\n"
        "Change them in /settings → 🎛 Defaults.",
        parse_mode=ParseMode.HTML,
    )


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "⚙️ <b>Settings</b>\n\nSwitch engines, add your own API endpoints "
        "(OpenAI / Stability / Midjourney-compatible), manage keys and defaults.",
        parse_mode=ParseMode.HTML,
        reply_markup=_engines_keyboard(),
    )


# ---------------------------------------------------------------------------
# Settings wizard (inline buttons + pending text input)
# ---------------------------------------------------------------------------

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    chat_id = query.message.chat_id

    if data.startswith("engine:use:"):
        eid = data.split(":", 2)[2]
        if eid not in CONFIG.engines:
            await query.edit_message_text("❌ Unknown engine.")
            return
        CONFIG.set_default("engine", eid)
        eng = CONFIG.get_engine(eid)
        warn = "" if CONFIG.api_key_for(eng) else (
            "\n\n⚠️ No API key set for this engine yet — tap 🔑 Set API key."
        )
        await query.edit_message_text(
            f"✅ Default engine switched to <b>{_esc(eng.get('label', eid))}</b>.{warn}",
            parse_mode=ParseMode.HTML,
            reply_markup=_engines_keyboard(),
        )

    elif data == "engine:add":
        _pending[chat_id] = {"step": "add:id", "data": {}}
        await query.edit_message_text(
            "➕ <b>Add custom engine</b> (1/5)\n\nSend a short id, e.g. <code>my-midjourney</code>:",
            parse_mode=ParseMode.HTML,
        )

    elif data == "engine:key":
        buttons = [
            [InlineKeyboardButton(eng.get("label", eid), callback_data=f"engine:keyfor:{eid}")]
            for eid, eng in CONFIG.engines.items()
        ]
        await query.edit_message_text(
            "🔑 Which engine's key do you want to set?",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif data.startswith("engine:keyfor:"):
        eid = data.split(":", 2)[2]
        _pending[chat_id] = {"step": "key:value", "data": {"engine": eid}}
        await query.edit_message_text(
            f"🔑 Paste the API key for <code>{_esc(eid)}</code>.\n"
            "It will be stored in config.json (or prefer env vars on shared hosts).",
            parse_mode=ParseMode.HTML,
        )

    elif data == "engine:remove":
        buttons = [
            [InlineKeyboardButton(eid, callback_data=f"engine:del:{eid}")]
            for eid in CONFIG.engines
            if eid not in BUILTIN_ENGINES
        ]
        if not buttons:
            await query.edit_message_text("No custom engines to remove.")
            return
        await query.edit_message_text("🗑 Remove which engine?",
                                      reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("engine:del:"):
        eid = data.split(":", 2)[2]
        try:
            CONFIG.remove_engine(eid)
            await query.edit_message_text(f"🗑 Removed <code>{_esc(eid)}</code>.",
                                          parse_mode=ParseMode.HTML)
        except ValueError as exc:
            await query.edit_message_text(f"❌ {_esc(exc)}", parse_mode=ParseMode.HTML)

    elif data == "settings:defaults":
        d = CONFIG.defaults
        rows = [
            [InlineKeyboardButton(f"Resolution: {d.get('resolution')}", callback_data="def:resolution")],
            [InlineKeyboardButton(f"Aspect: {d.get('aspect')}", callback_data="def:aspect")],
            [InlineKeyboardButton(f"Format: {d.get('format')}", callback_data="def:format")],
            [InlineKeyboardButton(
                f"Upscale: {'on ✅' if d.get('upscale') else 'off'}",
                callback_data="def:toggle:upscale",
            )],
        ]
        await query.edit_message_text("🎛 Tap a default to cycle it:",
                                      reply_markup=InlineKeyboardMarkup(rows))

    elif data == "def:resolution":
        cur = CONFIG.get_default("resolution", "2k")
        nxt = RES_TIERS[(RES_TIERS.index(cur) + 1) % len(RES_TIERS)] if cur in RES_TIERS else "2k"
        CONFIG.set_default("resolution", nxt)
        query.data = "settings:defaults"
        await on_callback(update, context)

    elif data == "def:aspect":
        cur = CONFIG.get_default("aspect", "square")
        nxt = ASPECTS[(ASPECTS.index(cur) + 1) % len(ASPECTS)] if cur in ASPECTS else "square"
        CONFIG.set_default("aspect", nxt)
        query.data = "settings:defaults"
        await on_callback(update, context)

    elif data == "def:format":
        cur = CONFIG.get_default("format", "png")
        nxt = FORMATS[(FORMATS.index(cur) + 1) % len(FORMATS)] if cur in FORMATS else "png"
        CONFIG.set_default("format", nxt)
        query.data = "settings:defaults"
        await on_callback(update, context)

    elif data == "def:toggle:upscale":
        CONFIG.set_default("upscale", not CONFIG.get_default("upscale", False))
        query.data = "settings:defaults"
        await on_callback(update, context)


_ADD_WIZARD_STEPS = [
    ("add:format", "API format — one of <code>huggingface</code>, <code>openai</code>, "
     "<code>openai-compatible</code> (Midjourney proxies), <code>stability</code>:"),
    ("add:url", "Endpoint URL (e.g. <code>https://api.openai.com/v1/images/generations</code>):"),
    ("add:model", "Model name (e.g. <code>gpt-image-1</code>, <code>sd3.5-large</code>):"),
    ("add:key", "API key (or send <code>-</code> to use an env var later):"),
]


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()

    # Quick keyboard shortcuts
    if text in ("⚡ Generate",):
        await update.message.reply_text(
            "Send <code>/generate &lt;your prompt&gt;</code> 🎨", parse_mode=ParseMode.HTML)
        return
    if text in ("📊 Niche Intel",):
        await cmd_niche(update, context)
        return
    if text in ("⚙️ Settings",):
        await cmd_settings(update, context)
        return
    if text in ("❓ Help",):
        await cmd_help(update, context)
        return

    state = _pending.get(chat_id)
    if not state:
        return

    step = state["step"]

    if step == "key:value":
        eid = state["data"]["engine"]
        if eid in CONFIG.engines and text and text != "-":
            CONFIG.engines[eid]["api_key"] = text
            CONFIG.save()
            await update.message.reply_text(
                f"🔑 Key saved for <code>{_esc(eid)}</code> "
                f"({_esc(CONFIG.mask_key(CONFIG.engines[eid]))}).",
                parse_mode=ParseMode.HTML)
            # Best-effort: delete the message containing the secret.
            try:
                await update.message.delete()
            except Exception:
                pass
        _pending.pop(chat_id, None)
        return

    if step == "add:id":
        state["data"]["id"] = text.lower().replace(" ", "-")
        state["step"], prompt = _ADD_WIZARD_STEPS[0]
        await update.message.reply_text(f"➕ (2/5) {prompt}", parse_mode=ParseMode.HTML)
        return

    for idx, (step_name, _) in enumerate(_ADD_WIZARD_STEPS):
        if step == step_name:
            field = step_name.split(":", 1)[1]
            state["data"][field] = "" if text == "-" else text
            if idx + 1 < len(_ADD_WIZARD_STEPS):
                state["step"], prompt = _ADD_WIZARD_STEPS[idx + 1]
                await update.message.reply_text(
                    f"➕ ({idx + 3}/5) {prompt}", parse_mode=ParseMode.HTML)
            else:
                d = state["data"]
                try:
                    CONFIG.add_engine(
                        engine_id=d["id"], api_format=d.get("format") or "openai-compatible",
                        base_url=d.get("url", ""), model=d.get("model", ""),
                        api_key=d.get("key", ""),
                    )
                    CONFIG.set_default("engine", d["id"])
                    await update.message.reply_text(
                        f"✅ Engine <code>{_esc(d['id'])}</code> added and set as default.\n"
                        f"Try it: <code>/generate test render --engine {_esc(d['id'])}</code>",
                        parse_mode=ParseMode.HTML)
                except ValueError as exc:
                    await update.message.reply_text(f"❌ {_esc(exc)}",
                                                    parse_mode=ParseMode.HTML)
                _pending.pop(chat_id, None)
            return


# ---------------------------------------------------------------------------
# Mini-App thread + entrypoint
# ---------------------------------------------------------------------------

def _maybe_start_miniapp() -> None:
    if os.environ.get("STOCKFLOW_MINIAPP", "1") not in ("1", "true", "yes"):
        return
    port = int(os.environ.get("STOCKFLOW_PORT", os.environ.get("PORT", 8080)))

    def _serve() -> None:
        try:
            from stockflow.webapp import run_server
            run_server(port=port)
        except OSError as exc:
            log.warning("Mini-App server not started: %s", exc)

    threading.Thread(target=_serve, daemon=True, name="miniapp").start()
    log.info("Mini-App backend thread started on port %d", port)


def main() -> None:
    token = CONFIG.bot_token()
    if not token:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN is not set. Create a bot with @BotFather, then:\n"
            "  export TELEGRAM_BOT_TOKEN=123456:ABC...\n"
            "  python bot.py"
        )
    _maybe_start_miniapp()
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("generate", cmd_generate))
    app.add_handler(CommandHandler("niche", cmd_niche))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("engines", cmd_engines))
    app.add_handler(CommandHandler("defaults", cmd_defaults))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    log.info("StockFlow AI bot starting (long polling)…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
