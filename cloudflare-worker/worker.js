/**
 * worker.js
 * ---------
 * Cloudflare Worker acting as the Telegram bot's webhook receiver.
 * It never runs ffmpeg itself - it only:
 *   1. Talks to a user via inline keyboards to figure out which tool +
 *      options they want.
 *   2. Fires a `repository_dispatch` event at GitHub Actions to do the
 *      actual (heavy) processing.
 *   3. GitHub Actions sends the result back to the same chat directly via
 *      the Telegram Bot API (see bot/telegram_notify.py) - this Worker is
 *      not involved in delivering the final file.
 *
 * Required secrets (set via `wrangler secret put <NAME>`):
 *   TELEGRAM_BOT_TOKEN     - from @BotFather
 *   TELEGRAM_WEBHOOK_SECRET- random string you choose; must match the
 *                            secret_token used in setWebhook (see README)
 *   GITHUB_TOKEN           - a fine-grained PAT with "Contents: read" and
 *                            "Actions: write" on this repo (for dispatch)
 *   GITHUB_REPO            - "owner/repo"
 *   AGENT_CHAT_ID          - (optional) Telegram chat_id allowed to run
 *                            `/agent <task>`. If unset, /agent is disabled
 *                            for everyone (fails closed, never open).
 *
 * Required KV namespace binding (see wrangler.toml): BOT_STATE
 * Stores short-lived per-chat conversation state while collecting options.
 */

const TELEGRAM_API = (token, method) => `https://api.telegram.org/bot${token}/${method}`;

async function tg(env, method, payload) {
  const resp = await fetch(TELEGRAM_API(env.TELEGRAM_BOT_TOKEN, method), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) {
    console.error(`Telegram API ${method} failed: ${resp.status} ${await resp.clone().text()}`);
  }
  return resp;
}

async function sendMessage(env, chatId, text, replyMarkup) {
  const payload = { chat_id: chatId, text, parse_mode: "Markdown", disable_web_page_preview: true };
  if (replyMarkup) payload.reply_markup = replyMarkup;
  const resp = await tg(env, "sendMessage", payload);
  if (!resp.ok) {
    // Markdown entity errors would otherwise silently swallow the message —
    // retry once as plain text so the user always gets a reply.
    delete payload.parse_mode;
    return tg(env, "sendMessage", payload);
  }
  return resp;
}

function answerCallback(env, callbackQueryId, text) {
  return tg(env, "answerCallbackQuery", { callback_query_id: callbackQueryId, text: text || "" });
}

function inlineKeyboard(rows) {
  return { inline_keyboard: rows.map(row => row.map(btn => ({ text: btn.text, callback_data: btn.data }))) };
}

async function getState(env, chatId) {
  const raw = await env.BOT_STATE.get(`chat:${chatId}`);
  return raw ? JSON.parse(raw) : null;
}

async function setState(env, chatId, state) {
  await env.BOT_STATE.put(`chat:${chatId}`, JSON.stringify(state), { expirationTtl: 3600 });
}

async function clearState(env, chatId) {
  await env.BOT_STATE.delete(`chat:${chatId}`);
}

async function dispatchJob(env, payload) {
  const [owner, repo] = env.GITHUB_REPO.split("/");
  const url = `https://api.github.com/repos/${owner}/${repo}/dispatches`;

  // Up to 3 attempts with exponential backoff — GitHub occasionally returns
  // transient 5xx / network errors and a lost dispatch means a silent no-op
  // for the user.
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const resp = await fetch(url, {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
          "Accept": "application/vnd.github+json",
          "User-Agent": "video-pipeline-telegram-bot",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ event_type: "telegram-job", client_payload: payload }),
      });
      if (resp.status === 204) return true;
      const body = await resp.text();
      console.error(`GitHub dispatch attempt ${attempt} failed: ${resp.status} ${resp.statusText} ${body.slice(0, 300) || "<empty>"}`);
      // 4xx (bad token / repo) won't heal on retry — bail out immediately.
      if (resp.status >= 400 && resp.status < 500) return false;
    } catch (e) {
      console.error(`GitHub dispatch attempt ${attempt} error: ${e}`);
    }
    if (attempt < 3) await new Promise(r => setTimeout(r, attempt * 750));
  }
  return false;
}

// ---------------------------------------------------------------------------
// AI Coding Agent bridge — `/agent <task description>`
// ---------------------------------------------------------------------------
// Separate, additive feature. Does not touch dispatchJob() (video-tool jobs)
// or any existing command handler above/below it. Fires a distinct
// repository_dispatch event_type ("agent_command") that only
// `.github/workflows/agent-task.yml` listens for, so it cannot collide with
// `telegram-job` (the video-processing dispatch).
async function dispatchAgentTask(env, command, chatId) {
  const [owner, repo] = env.GITHUB_REPO.split("/");
  const url = `https://api.github.com/repos/${owner}/${repo}/dispatches`;

  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const resp = await fetch(url, {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
          "Accept": "application/vnd.github+json",
          "User-Agent": "video-pipeline-telegram-bot",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          event_type: "agent_command",
          client_payload: { command, chat_id: String(chatId) },
        }),
      });
      if (resp.status === 204) return { ok: true };
      const body = await resp.text();
      console.error(`Agent dispatch attempt ${attempt} failed: ${resp.status} ${resp.statusText} ${body.slice(0, 300) || "<empty>"}`);
      if (resp.status >= 400 && resp.status < 500) {
        return { ok: false, error: `GitHub rejected the dispatch (${resp.status}): ${body.slice(0, 200) || resp.statusText}` };
      }
    } catch (e) {
      console.error(`Agent dispatch attempt ${attempt} error: ${e}`);
      if (attempt === 3) return { ok: false, error: `Network error talking to GitHub: ${e.message || e}` };
    }
    if (attempt < 3) await new Promise(r => setTimeout(r, attempt * 750));
  }
  return { ok: false, error: "GitHub dispatch failed after 3 attempts (transient error)." };
}

// Handles `/agent <task description>`. Only ever called from handleMessage's
// dedicated branch below — never wired into the existing tool/menu flow, so
// none of the video-tool command handling is touched.
async function handleAgentCommand(env, chatId, text) {
  const task = text.replace(/^\/agent(@\w+)?\s*/i, "").trim();

  if (!env.AGENT_CHAT_ID) {
    // Fail closed: if no authorized chat is configured, the feature is off
    // for everyone rather than silently open to any chat.
    await sendMessage(env, chatId, "❌ The `/agent` command isn't configured yet (missing AGENT_CHAT_ID secret).");
    return;
  }

  if (String(chatId) !== String(env.AGENT_CHAT_ID)) {
    await sendMessage(env, chatId, "❌ You're not authorized to use `/agent`.");
    return;
  }

  if (!task) {
    await sendMessage(env, chatId,
      "Usage: `/agent <describe the code change you want>`\n\n" +
      "Example: `/agent add a --speed flag to aspectshift`");
    return;
  }

  const result = await dispatchAgentTask(env, task, chatId);

  if (result.ok) {
    await sendMessage(env, chatId, `🚀 Started: _${task}_\n\nI'll open a PR and message you here when it's ready for review.`);
  } else {
    await sendMessage(env, chatId, `❌ Couldn't start the agent task.\n\nReason: ${result.error}`);
  }
}

const TOOL_LABELS = {
  lofiloop: "🎧 LofiLoop",
  aspectshift: "🔳 AspectShift",
  clipharvest: "✂️ ClipHarvest",
  watermarkwipe: "🧽 WatermarkWipe",
  introoutro: "🎬 IntroOutro",
  abroll: "🧩 ABRoll",
  stitcher: "🧵 Stitcher",
  audioduck: "🎙️ AudioDuck",
  loudnorm: "📶 LoudNorm",
  autochapters: "📊 AutoChapters",
  photostudio: "📸 PhotoStudio",
};

// Photo effect presets - must match photostudio/effects.py PRESETS keys.
const PHOTO_EFFECTS = [
  [{ text: "📷 DSLR Look", data: "fx:dslr" }, { text: "🎬 Cinematic", data: "fx:cinematic" }],
  [{ text: "🌅 HDR", data: "fx:hdr" }, { text: "👤 Portrait Bokeh", data: "fx:portrait" }],
  [{ text: "🌈 Vivid", data: "fx:vivid" }, { text: "🌫️ Matte", data: "fx:matte" }],
  [{ text: "⚪ B&W", data: "fx:bw" }, { text: "🏝️ Teal & Orange", data: "fx:teal_orange" }],
  [{ text: "🌇 Golden Hour", data: "fx:golden_hour" }, { text: "🎞️ Film Grain", data: "fx:film" }],
  [{ text: "⏭️ No effect (upscale only)", data: "fx:none" }],
];

const TOOL_GROUPS = [
  {
    title: "🔥 Viral Studio",
    items: [
      { tool: "lofiloop", description: "loop a 10s clip + long audio into a monetization-safe 2h/10h/24h video" },
    ],
  },
  {
    title: "🎬 Video Format",
    items: [
      { tool: "aspectshift", description: "convert 16:9 video into vertical 9:16" },
      { tool: "loudnorm", description: "normalize audio loudness to -14 LUFS" },
    ],
  },
  {
    title: "✂️ Editing",
    items: [
      { tool: "clipharvest", description: "extract the best short clips from a long video" },
      { tool: "abroll", description: "insert B-roll around natural cut points" },
      { tool: "introoutro", description: "add a branded intro and outro card" },
      { tool: "stitcher", description: "join multiple clips with transitions" },
      { tool: "audioduck", description: "duck background music under voiceover" },
    ],
  },
  {
    title: "🎨 Enhancement",
    items: [
      { tool: "watermarkwipe", description: "remove logos and watermarks" },
      { tool: "photostudio", description: "upscale photos up to 16K Ultra HD + pro color effects (DSLR, HDR, Portrait…)" },
    ],
  },
  {
    title: "📊 Metadata",
    items: [
      { tool: "autochapters", description: "generate YouTube chapters from the transcript" },
    ],
  },
];

function menuText() {
  return [
    "🎛️ *Creator Studio Bot* — the most powerful video toolkit on Telegram.",
    "",
    "Pick a tool below, then I’ll walk you through it:",
    "",
    ...TOOL_GROUPS.flatMap(group => [
      group.title,
      ...group.items.map(item => `  • ${TOOL_LABELS[item.tool]} — ${item.description}`),
      "",
    ]),
    "📤 Send links for big files. Rendered lofi videos up to *2GB* are delivered straight to your chat.",
    "Tap ⋯ for more options.",
  ].join("\n");
}

// Featured tool sits on top; everything else is grouped. A trailing "⋯ More"
// overflow row exposes help / about / large-file info in a modern 3-dot menu.
function menuKeyboard() {
  return inlineKeyboard([
    [{ text: "🎧 LofiLoop — Viral Loop Studio 🔥", data: "tool:lofiloop" }],
    [{ text: TOOL_LABELS.aspectshift, data: "tool:aspectshift" },
     { text: TOOL_LABELS.clipharvest, data: "tool:clipharvest" }],
    [{ text: TOOL_LABELS.watermarkwipe, data: "tool:watermarkwipe" },
     { text: TOOL_LABELS.introoutro, data: "tool:introoutro" }],
    [{ text: TOOL_LABELS.abroll, data: "tool:abroll" },
     { text: TOOL_LABELS.stitcher, data: "tool:stitcher" }],
    [{ text: TOOL_LABELS.audioduck, data: "tool:audioduck" },
     { text: TOOL_LABELS.loudnorm, data: "tool:loudnorm" }],
    [{ text: TOOL_LABELS.photostudio, data: "tool:photostudio" },
     { text: TOOL_LABELS.autochapters, data: "tool:autochapters" }],
    [{ text: "⋯ More", data: "overflow" }],
  ]);
}

// The 3-dot overflow menu.
function overflowText() {
  return [
    "⋯ *More options*",
    "",
    "ℹ️ About — what this studio can do",
    "❓ Help — how each tool works",
    "🚀 Large files — up to 2GB delivered in-chat via MTProto",
    "",
    "Pick one:",
  ].join("\n");
}

function overflowKeyboard() {
  return inlineKeyboard([
    [{ text: "ℹ️ About", data: "info:about" }, { text: "❓ Help", data: "info:help" }],
    [{ text: "🚀 Large files (2GB)", data: "info:largefiles" }],
    [{ text: "🏠 Back to Menu", data: "menu" }],
  ]);
}

function infoText(topic) {
  if (topic === "about") {
    return [
      "ℹ️ *About Creator Studio Bot*",
      "",
      "A studio-grade FFmpeg pipeline that runs on GitHub Actions and delivers",
      "results straight to Telegram. The flagship 🎧 *LofiLoop* renders",
      "seamless, monetization-safe long-form lofi videos (2h / 10h / 24h) with a",
      "unique per-frame digital fingerprint so YouTube never flags them as",
      "reused content.",
    ].join("\n");
  }
  if (topic === "help") {
    return [
      "❓ *Help*",
      "",
      "1. Tap a tool.",
      "2. Send the video/audio *file* or a *direct link*.",
      "3. Answer the quick questions (mode, duration, …).",
      "4. I dispatch the render and message you when it’s done.",
      "",
      "For 🎧 LofiLoop: send the short 10s loop clip, then paste a public Google",
      "Drive link for the long audio, then pick the target hours.",
    ].join("\n");
  }
  return [
    "🚀 *Large files*",
    "",
    "Rendered lofi videos are delivered *directly in this chat* up to *2GB* using",
    "MTProto (no more 20MB limit). Anything larger falls back to a free direct",
    "download link (GoFile / transfer.sh) — no signup, no API keys.",
  ].join("\n");
}

function withBackButton(rows) {
  return inlineKeyboard([...rows, [{ text: "🏠 Back to Menu", data: "menu" }]]);
}

function sourceInstructions(tool) {
  switch (tool) {
    case "lofiloop":
      return "🎧 *LofiLoop* — Step 1 of 3\n\nSend your short *seamlessly-looping* clip (a ~10s .mp4 works best) as a file or a direct link.";
    case "photostudio":
      return "📸 *PhotoStudio* — Step 1 of 3\n\nSend me the photo you want to enhance.\n\n💡 For best quality send it *as a file/document* (not a compressed photo), or paste a direct image / Google Drive link.";
    case "aspectshift":
      return "Send the video you want converted to vertical 9:16.";
    case "clipharvest":
      return "Send your long video link or file to analyze for the best clips.";
    case "watermarkwipe":
      return "Send the video you want to clean up.";
    case "introoutro":
      return "Send the video you want to wrap with an intro and outro.";
    case "abroll":
      return "Send the main video first. I’ll ask for B-roll clips next.";
    case "stitcher":
      return "Send the first clip you want stitched. I’ll ask for the remaining clips next.";
    case "audioduck":
      return "Send the main video first. I’ll ask for the voiceover track next.";
    case "loudnorm":
      return "Send the video you want normalized to broadcast loudness.";
    case "autochapters":
      return "Send your long video link or file. I’ll generate chapter timestamps from the transcript.";
    default:
      return "Send the video or link for this tool.";
  }
}

function looksLikeUrl(text) {
  return /^https?:\/\/\S+$/i.test(text.trim());
}

async function showMainMenu(env, chatId) {
  await clearState(env, chatId);
  await sendMessage(env, chatId, menuText(), menuKeyboard());
}

async function promptForSource(env, chatId, tool) {
  await setState(env, chatId, { step: "awaiting_source", tool });
  await sendMessage(env, chatId, sourceInstructions(tool), withBackButton([]));
}

async function sourceFromMessage(env, message, allowAudio = false, allowPhoto = false) {
  const text = (message.text || "").trim();
  if (looksLikeUrl(text)) {
    return text;
  }

  // Telegram sends photos as an array of progressively larger sizes; take the largest.
  const photo = allowPhoto && Array.isArray(message.photo) && message.photo.length
    ? message.photo[message.photo.length - 1]
    : null;
  const file = message.video || message.document || photo || (allowAudio ? message.audio : null);
  if (!file) return null;

  if (file.file_size && file.file_size > 20 * 1024 * 1024) {
    throw new Error("That file is over Telegram's 20MB bot-download limit. Please send a direct link (Google Drive works) instead.");
  }

  const fileResp = await tg(env, "getFile", { file_id: file.file_id });
  const fileData = await fileResp.json();
  if (!fileData.ok) {
    throw new Error("Couldn't read that file from Telegram.");
  }

  return `https://api.telegram.org/file/bot${env.TELEGRAM_BOT_TOKEN}/${fileData.result.file_path}`;
}

async function continueAfterSource(env, chatId, state) {
  if (state.tool === "lofiloop") {
    state.step = "collect_lofi_audio";
    await setState(env, chatId, state);
    await sendMessage(env, chatId,
      "🎧 *LofiLoop* — Step 2 of 3\n\nNow paste a *public Google Drive link* to your long audio file " +
      "(set to “Anyone with the link”). A direct audio URL also works. No API keys needed — I fetch it automatically.",
      withBackButton([]));
    return;
  }

  if (state.tool === "photostudio") {
    state.step = "choose_resolution";
    await setState(env, chatId, state);
    await sendMessage(env, chatId,
      "📸 *Step 2 of 3* — Choose your upscale resolution:\n\n" +
      "💡 16K = 15360px long edge, true Ultra HD print quality. Higher resolutions take longer.",
      withBackButton([
        [{ text: "🖥️ 2K (2048px)", data: "res:2k" }, { text: "📺 4K (4096px)", data: "res:4k" }],
        [{ text: "🎥 8K (8192px)", data: "res:8k" }, { text: "🚀 16K Ultra HD", data: "res:16k" }],
        [{ text: "⏭️ Keep original size (effect only)", data: "res:none" }],
      ]));
    return;
  }

  if (state.tool === "aspectshift") {
    state.step = "choose_mode_aspectshift";
    await setState(env, chatId, state);
    await sendMessage(env, chatId, "Which conversion mode?", withBackButton([
      [{ text: "🌫️ Blur-pad (recommended, zero crop loss)", data: "mode:blur" }],
      [{ text: "✂️ Smart crop (no blur pillarbox)", data: "mode:crop" }],
    ]));
    return;
  }

  if (state.tool === "clipharvest") {
    state.step = "choose_clip_count";
    await setState(env, chatId, state);
    await sendMessage(env, chatId, "How many clips should I extract?", withBackButton([
      [{ text: "3 clips", data: "clips:3" }, { text: "5 clips", data: "clips:5" }, { text: "8 clips", data: "clips:8" }],
    ]));
    return;
  }

  if (state.tool === "watermarkwipe") {
    state.step = "choose_mode_watermark";
    await setState(env, chatId, state);
    await sendMessage(env, chatId, "Which removal mode?", withBackButton([
      [{ text: "🖌️ Inpaint (center/moving logos)", data: "mode:inpaint" }],
      [{ text: "✂️ Crop (corner/edge logos)", data: "mode:crop" }],
    ]));
    return;
  }

  if (state.tool === "abroll") {
    state.step = "collect_brolls";
    state.extra_sources = [];
    await setState(env, chatId, state);
    await sendMessage(env, chatId, "Send one or more B-roll clips or URLs, then send /done when finished.", withBackButton([]));
    return;
  }

  if (state.tool === "stitcher") {
    state.step = "collect_stitch_clips";
    state.extra_sources = [];
    await setState(env, chatId, state);
    await sendMessage(env, chatId, "Send the remaining clips you want to stitch together, then send /done. The first clip is the one you already sent.", withBackButton([]));
    return;
  }

  if (state.tool === "audioduck") {
    state.step = "collect_voiceover";
    state.extra_sources = [];
    await setState(env, chatId, state);
    await sendMessage(env, chatId, "Now send the voiceover audio file or URL. I’ll dispatch as soon as I receive it.", withBackButton([]));
    return;
  }

  await dispatchAndFinish(env, chatId, state);
}

async function handleSourceMessage(env, chatId, state, message) {
  try {
    const allowAudio = state.tool === "audioduck" && state.step === "collect_voiceover";
    const allowPhoto = state.tool === "photostudio";
    const sourceValue = await sourceFromMessage(env, message, allowAudio, allowPhoto);
    if (!sourceValue) {
      await sendMessage(env, chatId, sourceInstructions(state.tool), withBackButton([]));
      return;
    }

    state.source_type = "url";
    state.source_value = sourceValue;
    await continueAfterSource(env, chatId, state);
  } catch (e) {
    await sendMessage(env, chatId, `❌ ${e.message}`, withBackButton([]));
  }
}

async function handleMessage(env, message) {
  const chatId = message.chat.id;
  const text = (message.text || "").trim();
  const state = await getState(env, chatId);

  // `/agent <task>` — AI coding agent bridge. Checked first and returns
  // immediately so it can never fall through into (or be shadowed by) any
  // existing conversation-state or menu handling below.
  if (/^\/agent(@\w+)?(\s|$)/i.test(text)) {
    await handleAgentCommand(env, chatId, text);
    return;
  }

  if (text === "/start" || text === "/help" || text === "/menu") {
    await showMainMenu(env, chatId);
    return;
  }

  // LofiLoop step 2: collect the long audio (Google Drive / direct URL / file).
  if (state && state.step === "collect_lofi_audio") {
    try {
      let audioUrl = null;
      if (looksLikeUrl(text)) {
        audioUrl = text;
      } else {
        audioUrl = await sourceFromMessage(env, message, true);
      }
      if (!audioUrl) {
        await sendMessage(env, chatId,
          "Paste a *public Google Drive link* or a direct audio URL (or send the audio file).",
          withBackButton([]));
        return;
      }
      state.lofi_audio = audioUrl;
      state.step = "choose_lofi_hours";
      await setState(env, chatId, state);
      await sendMessage(env, chatId,
        "🎧 *LofiLoop* — Step 3 of 3\n\n⏱️ *Enter target video duration in hours* (e.g. 2, 10, 24).\n\n" +
        "Tap a preset below or just type a number:",
        withBackButton([
          [{ text: "1h", data: "hours:1" }, { text: "2h", data: "hours:2" }, { text: "3h", data: "hours:3" }],
          [{ text: "6h", data: "hours:6" }, { text: "10h", data: "hours:10" }, { text: "24h", data: "hours:24" }],
        ]));
      return;
    } catch (e) {
      await sendMessage(env, chatId, `❌ ${e.message}`, withBackButton([]));
      return;
    }
  }

  // LofiLoop step 3: user typed a custom number of hours.
  if (state && state.step === "choose_lofi_hours") {
    const hours = parseFloat(text);
    if (!isNaN(hours) && hours > 0 && hours <= 48) {
      state.lofi_hours = String(hours);
      await dispatchAndFinish(env, chatId, state);
      return;
    }
    await sendMessage(env, chatId,
      "Please send a number of hours between 0 and 48 (e.g. 2, 10, 24), or tap a preset.",
      withBackButton([
        [{ text: "1h", data: "hours:1" }, { text: "2h", data: "hours:2" }, { text: "3h", data: "hours:3" }],
        [{ text: "6h", data: "hours:6" }, { text: "10h", data: "hours:10" }, { text: "24h", data: "hours:24" }],
      ]));
    return;
  }

  if (state && ["collect_brolls", "collect_stitch_clips", "collect_voiceover"].includes(state.step)) {
    if (text === "/done") {
      if (state.step === "collect_brolls" && (!state.extra_sources || state.extra_sources.length < 1)) {
        await sendMessage(env, chatId, "Send at least one B-roll clip before /done.", withBackButton([]));
        return;
      }
      if (state.step === "collect_stitch_clips" && (!state.extra_sources || state.extra_sources.length < 1)) {
        await sendMessage(env, chatId, "Send at least one additional clip before /done so there are at least two clips total.", withBackButton([]));
        return;
      }
      if (state.step === "collect_voiceover") {
        await sendMessage(env, chatId, "Send the voiceover audio file or URL first.", withBackButton([]));
        return;
      }
      await dispatchAndFinish(env, chatId, state);
      return;
    }

    try {
      const directUrl = await sourceFromMessage(env, message, state.step === "collect_voiceover");
      if (!directUrl) {
        await sendMessage(env, chatId, "Send a video/audio file or a direct URL, or /done when you're finished.", withBackButton([]));
        return;
      }

      state.extra_sources = state.extra_sources || [];
      state.extra_sources.push(directUrl);

      if (state.step === "collect_voiceover") {
        state.voiceover_source = directUrl;
        await dispatchAndFinish(env, chatId, state);
        return;
      }

      await setState(env, chatId, state);
      await sendMessage(env, chatId, `Added ${state.extra_sources.length} item(s). Send more or /done.`, withBackButton([]));
      return;
    } catch (e) {
      await sendMessage(env, chatId, `❌ ${e.message}`, withBackButton([]));
      return;
    }
  }

  if (state && state.step === "awaiting_source") {
    await handleSourceMessage(env, chatId, state, message);
    return;
  }

  if (message.video || message.document || message.audio || looksLikeUrl(text)) {
    await sendMessage(env, chatId, "Choose a tool from the menu first.", menuKeyboard());
    return;
  }

  await showMainMenu(env, chatId);
}

async function handleCallback(env, callbackQuery) {
  const chatId = callbackQuery.message.chat.id;
  const data = callbackQuery.data || "";
  const state = await getState(env, chatId);
  const [kind, value] = data.split(":");

  await answerCallback(env, callbackQuery.id);

  if (kind === "menu") {
    await showMainMenu(env, chatId);
    return;
  }

  if (kind === "overflow") {
    await sendMessage(env, chatId, overflowText(), overflowKeyboard());
    return;
  }

  if (kind === "info") {
    await sendMessage(env, chatId, infoText(value), overflowKeyboard());
    return;
  }

  if (kind === "tool") {
    await setState(env, chatId, { tool: value });
    await promptForSource(env, chatId, value);
    return;
  }

  if (!state) {
    await answerCallback(env, callbackQuery.id, "This step expired, please open the menu again.");
    return;
  }

  if (kind === "hours" && state.step === "choose_lofi_hours") {
    state.lofi_hours = value;
    await dispatchAndFinish(env, chatId, state);
    return;
  }

  if (kind === "mode" && state.step === "choose_mode_aspectshift") {
    state.mode = value;
    await dispatchAndFinish(env, chatId, state);
    return;
  }

  if (kind === "res" && state.step === "choose_resolution") {
    state.resolution = value === "none" ? "" : value;
    state.step = "choose_effect";
    await setState(env, chatId, state);
    await sendMessage(env, chatId,
      "🎨 *Step 3 of 3* — Add a professional color effect?\n\n" +
      "📷 *DSLR Look* is our signature: full-frame camera depth, rich tones and a subtle optical vignette.",
      withBackButton(PHOTO_EFFECTS));
    return;
  }

  if (kind === "fx" && state.step === "choose_effect") {
    state.effect = value === "none" ? "" : value;
    if (!state.resolution && !state.effect) {
      await sendMessage(env, chatId,
        "⚠️ You skipped both the upscale *and* the effect — there’d be nothing to do! Pick at least one:",
        withBackButton(PHOTO_EFFECTS));
      return;
    }
    await dispatchAndFinish(env, chatId, state);
    return;
  }

  if (kind === "clips") {
    state.num_clips = value;
    state.min_duration = "20";
    state.max_duration = "90";
    state.captions = "true";
    await dispatchAndFinish(env, chatId, state);
    return;
  }

  if (kind === "mode" && state.step === "choose_mode_watermark") {
    state.mode = value;
    state.step = "choose_color_grade";
    await setState(env, chatId, state);
    await sendMessage(env, chatId, "Add color grading?", withBackButton([
      [{ text: "None", data: "grade:none" }],
      [{ text: "🎬 Cinematic", data: "grade:cinematic" }, { text: "🌈 Vibrant", data: "grade:vibrant" }],
      [{ text: "🔥 Warm", data: "grade:warm" }, { text: "❄️ Cool", data: "grade:cool" }],
    ]));
    return;
  }

  if (kind === "grade") {
    state.color_grade = value === "none" ? "" : value;
    state.step = "choose_bg_blur";
    await setState(env, chatId, state);
    await sendMessage(env, chatId, "Add portrait-mode background blur?", withBackButton([
      [{ text: "Yes", data: "bgblur:true" }, { text: "No", data: "bgblur:false" }],
    ]));
    return;
  }

  if (kind === "bgblur") {
    state.background_blur = value;
    await dispatchAndFinish(env, chatId, state);
    return;
  }

  await answerCallback(env, callbackQuery.id, "That step is no longer active. Please reopen the menu.");
}

async function dispatchAndFinish(env, chatId, state) {
  const ok = await dispatchJob(env, {
    chat_id: String(chatId),
    tool: state.tool,
    source_type: state.source_type,
    source_value: state.source_value,
    options: {
      mode: state.mode || "",
      num_clips: state.num_clips || "",
      min_duration: state.min_duration || "",
      max_duration: state.max_duration || "",
      captions: state.captions || "",
      region: "",
      color_grade: state.color_grade || "",
      background_blur: state.background_blur || "false",
      intro_text: state.intro_text || "Your Channel Name",
      outro_text: state.outro_text || "Subscribe for more",
      intro_duration: state.intro_duration || "3.5",
      outro_duration: state.outro_duration || "3.5",
      broll_sources_json: JSON.stringify(state.extra_sources || []),
      stitch_clips_json: JSON.stringify([state.source_value, ...(state.extra_sources || [])]),
      transition: state.transition || "crossfade",
      transition_duration: state.transition_duration || "0.8",
      voiceover_source: state.voiceover_source || ((state.extra_sources || [])[0] || ""),
      target_lufs: state.target_lufs || "-14",
      target_tp: state.target_tp || "-1.5",
      target_lra: state.target_lra || "11",
      lofi_audio: state.lofi_audio || "",
      lofi_hours: state.lofi_hours || "2",
      lofi_crf: state.lofi_crf || "18",
      lofi_preset: state.lofi_preset || "veryfast",
      lofi_noise: state.lofi_noise || "1",
      resolution: state.resolution || "",
      effect: state.effect || "",
    },
  });

  await clearState(env, chatId);

  if (ok) {
    await sendMessage(env, chatId, "🚀 Job dispatched! I'll message you here with progress and the finished file(s).", menuKeyboard());
  } else {
    await sendMessage(env, chatId, "❌ Couldn't start the job (GitHub dispatch failed). Please try again in a moment.", menuKeyboard());
  }
}

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("OK - Telegram webhook is listening.", { status: 200 });
    }

    const secretHeader = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
    if (secretHeader !== env.TELEGRAM_WEBHOOK_SECRET) {
      return new Response("Forbidden", { status: 403 });
    }

    let update;
    try {
      update = await request.json();
    } catch (e) {
      return new Response("Bad request", { status: 400 });
    }

    try {
      if (update.message) {
        await handleMessage(env, update.message);
      } else if (update.callback_query) {
        await handleCallback(env, update.callback_query);
      }
    } catch (e) {
      console.error("Webhook handling error:", e);
    }

    return new Response("OK", { status: 200 });
  },
};
