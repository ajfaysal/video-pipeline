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
  // rows: array of arrays of {text, data}
  return { inline_keyboard: rows.map(row => row.map(btn => ({ text: btn.text, callback_data: btn.data }))) };
}

async function getState(env, chatId) {
  const raw = await env.BOT_STATE.get(`chat:${chatId}`);
  return raw ? JSON.parse(raw) : null;
}

async function setState(env, chatId, state) {
  // Auto-expire stale flows after 1 hour so KV doesn't accumulate abandoned sessions.
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
  aspectshift: "🔳 AspectShift (16:9 → 9:16)",
  clipharvest: "✂️ ClipHarvest (extract best clips)",
  watermarkwipe: "🧽 WatermarkWipe (remove watermark)",
};

function looksLikeUrl(text) {
  return /^https?:\/\/\S+$/i.test(text.trim());
}

async function handleIncomingSource(env, chatId, sourceType, sourceValue) {
  await setState(env, chatId, { step: "choose_tool", source_type: sourceType, source_value: sourceValue });
  await sendMessage(env, chatId, "Got it! Which tool do you want to run?", inlineKeyboard([
    [{ text: TOOL_LABELS.aspectshift, data: "tool:aspectshift" }],
    [{ text: TOOL_LABELS.clipharvest, data: "tool:clipharvest" }],
    [{ text: TOOL_LABELS.watermarkwipe, data: "tool:watermarkwipe" }],
  ]));
}

async function handleMessage(env, message) {
  const chatId = message.chat.id;
  const text = (message.text || "").trim();

  if (text === "/start" || text === "/help") {
    await sendMessage(env, chatId,
      "👋 Send me a video link (YouTube etc.) to process, then pick a tool:\n\n" +
      "🔳 AspectShift - convert 16:9 to 9:16\n" +
      "✂️ ClipHarvest - auto-extract the best short clips\n" +
      "🧽 WatermarkWipe - remove a watermark/logo\n\n" +
      "Note: for a directly-uploaded video file, Telegram only lets bots fetch files up to 20MB - for anything bigger, send a link instead."
    );
    return;
  }

  if (message.video || message.document) {
    const file = message.video || message.document;
    if (file.file_size && file.file_size > 20 * 1024 * 1024) {
      await sendMessage(env, chatId, "⚠️ That file is over Telegram's 20MB bot-download limit. Please send a link instead.");
      return;
    }
    // Resolve file_id -> a direct download URL now, while we still have the token.
    const fileResp = await tg(env, "getFile", { file_id: file.file_id });
    const fileData = await fileResp.json();
    if (!fileData.ok) {
      await sendMessage(env, chatId, "❌ Couldn't read that file from Telegram. Please try sending a link instead.");
      return;
    }
    const directUrl = `https://api.telegram.org/file/bot${env.TELEGRAM_BOT_TOKEN}/${fileData.result.file_path}`;
    await handleIncomingSource(env, chatId, "url", directUrl);
    return;
  }

  if (looksLikeUrl(text)) {
    await handleIncomingSource(env, chatId, "url", text);
    return;
  }

  await sendMessage(env, chatId, "Send me a video link or a video file to get started (or /help).");
}

async function handleCallback(env, callbackQuery) {
  const chatId = callbackQuery.message.chat.id;
  const data = callbackQuery.data || "";
  const state = await getState(env, chatId);

  if (!state) {
    await answerCallback(env, callbackQuery.id, "This request expired, please send the video again.");
    return;
  }

  const [kind, value] = data.split(":");
  await answerCallback(env, callbackQuery.id);

  if (kind === "tool") {
    state.tool = value;
    if (value === "aspectshift") {
      state.step = "choose_mode_aspectshift";
      await setState(env, chatId, state);
      await sendMessage(env, chatId, "Which conversion mode?", inlineKeyboard([
        [{ text: "🌫️ Blur-pad (recommended, zero crop loss)", data: "mode:blur" }],
        [{ text: "✂️ Smart crop (no blur pillarbox)", data: "mode:crop" }],
      ]));
    } else if (value === "clipharvest") {
      state.step = "choose_clip_count";
      await setState(env, chatId, state);
      await sendMessage(env, chatId, "How many clips should I extract?", inlineKeyboard([
        [{ text: "3 clips", data: "clips:3" }, { text: "5 clips", data: "clips:5" }, { text: "8 clips", data: "clips:8" }],
      ]));
    } else if (value === "watermarkwipe") {
      state.step = "choose_mode_watermark";
      await setState(env, chatId, state);
      await sendMessage(env, chatId, "Which removal mode?", inlineKeyboard([
        [{ text: "🖌️ Inpaint (center/moving logos)", data: "mode:inpaint" }],
        [{ text: "✂️ Crop (corner/edge logos)", data: "mode:crop" }],
      ]));
    }
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
    await sendMessage(env, chatId, "Add color grading?", inlineKeyboard([
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
    await sendMessage(env, chatId, "Add portrait-mode background blur?", inlineKeyboard([
      [{ text: "Yes", data: "bgblur:true" }, { text: "No", data: "bgblur:false" }],
    ]));
    return;
  }

  if (kind === "bgblur") {
    state.background_blur = value;
    await dispatchAndFinish(env, chatId, state);
    return;
  }
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
    },
  });

  await clearState(env, chatId);

  if (ok) {
    await sendMessage(env, chatId, "🚀 Job dispatched! I'll message you here with progress and the finished file(s).");
  } else {
    await sendMessage(env, chatId, "❌ Couldn't start the job (GitHub dispatch failed). Please try again in a moment.");
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

    // Always 200 quickly - Telegram retries aggressively on non-2xx responses.
    return new Response("OK", { status: 200 });
  },
};
