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
    console.error(`Telegram API ${method} failed: ${resp.status} ${await resp.text()}`);
  }
  return resp;
}

function sendMessage(env, chatId, text, replyMarkup) {
  const payload = { chat_id: chatId, text };
  if (replyMarkup) payload.reply_markup = replyMarkup;
  return tg(env, "sendMessage", payload);
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
  console.error("GITHUB_TOKEN length:", env.GITHUB_TOKEN.length, "last4:", env.GITHUB_TOKEN.slice(-4));
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
  if (!resp.ok) {
    const body = await resp.text();
    console.error(`GitHub dispatch failed with status ${resp.status} ${resp.statusText}`);
    console.error(`GitHub dispatch response body: ${body || "<empty>"}`);
  }
  return resp.ok;
}

const TOOL_LABELS = {
  aspectshift: "🔳 AspectShift",
  clipharvest: "✂️ ClipHarvest",
  watermarkwipe: "🧽 WatermarkWipe",
  introoutro: "🎬 IntroOutro",
  abroll: "🧩 ABRoll",
  stitcher: "🧵 Stitcher",
  audioduck: "🎙️ AudioDuck",
  loudnorm: "📶 LoudNorm",
  autochapters: "📊 AutoChapters",
};

const TOOL_GROUPS = [
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
    "Pick a tool first, then I’ll ask you for the video or link:",
    "",
    ...TOOL_GROUPS.flatMap(group => [
      group.title,
      ...group.items.map(item => `• ${TOOL_LABELS[item.tool]} - ${item.description}`),
      "",
    ]),
    "Direct uploads over Telegram are limited to 20MB. For larger files, send a link instead.",
  ].join("\n");
}

function menuKeyboard() {
  return inlineKeyboard([
    [{ text: TOOL_LABELS.aspectshift, data: "tool:aspectshift" }],
    [{ text: TOOL_LABELS.clipharvest, data: "tool:clipharvest" }],
    [{ text: TOOL_LABELS.watermarkwipe, data: "tool:watermarkwipe" }],
    [{ text: TOOL_LABELS.introoutro, data: "tool:introoutro" }],
    [{ text: TOOL_LABELS.abroll, data: "tool:abroll" }],
    [{ text: TOOL_LABELS.stitcher, data: "tool:stitcher" }],
    [{ text: TOOL_LABELS.audioduck, data: "tool:audioduck" }],
    [{ text: TOOL_LABELS.loudnorm, data: "tool:loudnorm" }],
    [{ text: TOOL_LABELS.autochapters, data: "tool:autochapters" }],
  ]);
}

function withBackButton(rows) {
  return inlineKeyboard([...rows, [{ text: "🏠 Back to Menu", data: "menu" }]]);
}

function sourceInstructions(tool) {
  switch (tool) {
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

async function sourceFromMessage(env, message, allowAudio = false) {
  const text = (message.text || "").trim();
  if (looksLikeUrl(text)) {
    return text;
  }

  const file = message.video || message.document || (allowAudio ? message.audio : null);
  if (!file) return null;

  if (file.file_size && file.file_size > 20 * 1024 * 1024) {
    throw new Error("That file is over Telegram's 20MB bot-download limit.");
  }

  const fileResp = await tg(env, "getFile", { file_id: file.file_id });
  const fileData = await fileResp.json();
  if (!fileData.ok) {
    throw new Error("Couldn't read that file from Telegram.");
  }

  return `https://api.telegram.org/file/bot${env.TELEGRAM_BOT_TOKEN}/${fileData.result.file_path}`;
}

async function continueAfterSource(env, chatId, state) {
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
    const sourceValue = await sourceFromMessage(env, message, allowAudio);
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

  if (text === "/start" || text === "/help" || text === "/menu") {
    await showMainMenu(env, chatId);
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

  if (kind === "tool") {
    await setState(env, chatId, { tool: value });
    await promptForSource(env, chatId, value);
    return;
  }

  if (!state) {
    await answerCallback(env, callbackQuery.id, "This step expired, please open the menu again.");
    return;
  }

  if (kind === "mode" && state.step === "choose_mode_aspectshift") {
    state.mode = value;
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
