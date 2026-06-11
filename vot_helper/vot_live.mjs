#!/usr/bin/env node
/**
 * vot_live.mjs — прямой клиент Яндекс VOT API (новый протокол, как VOT 1.10+).
 *
 * Зачем: vot-cli-live работает по старому протоколу. С мая 2025 Яндекс требует
 * OAuth-авторизацию для «Живых голосов» (status=7 SESSION_REQUIRED), и старый
 * клиент получает отказ, который выглядит как "Translation not available".
 * Этот скрипт использует @vot.js/node и передаёт Authorization: OAuth <token>,
 * как делает сам VOT после логина в аккаунт Яндекса.
 *
 * Установка зависимостей (один раз):  cd vot_helper && npm install
 * Токен: переменная окружения VOT_API_TOKEN / YANDEX_OAUTH_TOKEN или --token.
 * Duration: --duration <seconds> важен для попадания в cache-key Яндекса.
 *
 * Использование:
 *   node vot_live.mjs --url <video_url> --output <dir> [--voice-style live|tts]
 *                     [--timeout 1800] [--duration seconds] [--token <oauth_token>]
 *
 * stdout (последняя строка) — абсолютный путь к скачанному MP3.
 * Маркеры ошибок на stderr:
 *   LIVEDUB_AUTH_REQUIRED  — Яндекс требует OAuth-токен (нет/протух VOT_API_TOKEN)
 *   LIVEDUB_NOT_AVAILABLE  — перевод недоступен/не получился
 */
import fs from "node:fs";
import path from "node:path";
import { parseArgs } from "node:util";

import VOTClient from "@vot.js/node";
import { getVideoData } from "@vot.js/node/utils/videoData";

const { values: args } = parseArgs({
  options: {
    url: { type: "string" },
    output: { type: "string" },
    "voice-style": { type: "string", default: "live" },
    timeout: { type: "string", default: "1800" },
    token: { type: "string" },
    duration: { type: "string" },
  },
});

if (!args.url || !args.output) {
  console.error("usage: vot_live.mjs --url <url> --output <dir> [--voice-style live|tts] [--timeout sec] [--duration sec] [--token t]");
  process.exit(2);
}

const token = args.token || process.env.VOT_API_TOKEN || process.env.YANDEX_OAUTH_TOKEN || undefined;
const useLively = (args["voice-style"] || "live").toLowerCase() !== "tts";
const timeoutSec = Math.max(60, parseInt(args.timeout, 10) || 1800);
const knownDuration = args.duration ? Math.max(0, Number(args.duration) || 0) : 0;

const client = new VOTClient({ apiToken: token });

function log(msg) {
  console.error(`[vot_live] ${msg}`);
}

try {
  const videoData = await getVideoData(args.url);
  if (knownDuration > 0) {
    // Для YouTube @vot.js обычно возвращает duration=undefined и иначе
    // подставляет defaultDuration (343s). Длительность входит в cache-key
    // Яндекса, поэтому прокидываем точную длительность из yt-dlp Python-пайплайна.
    videoData.duration = knownDuration;
    log(`duration=${knownDuration}s передан в VOT cache-key`);
  }
  const deadline = Date.now() + timeoutSec * 1000;
  let result = null;

  while (Date.now() < deadline) {
    let res;
    try {
      res = await client.translateVideo({
        videoData,
        extraOpts: { useLivelyVoice: useLively },
      });
    } catch (e) {
      const data = e?.data ?? {};
      if (data.status === 7 || /auth required/i.test(e?.message ?? "")) {
        console.error("LIVEDUB_AUTH_REQUIRED");
        log(`Яндекс требует авторизацию для живых голосов. ${token ? "Токен отклонён (протух?)." : "VOT_API_TOKEN не задан."}`);
        process.exit(3);
      }
      if (data.status === 0 || /couldn't translate/i.test(e?.message ?? "")) {
        console.error("LIVEDUB_NOT_AVAILABLE");
        log(`Яндекс не смог перевести: ${e.message} ${JSON.stringify(data)}`);
        process.exit(4);
      }
      throw e;
    }

    if (res.translated && res.url) {
      result = res;
      break;
    }
    // status 2/3 = WAITING / LONG_WAITING, 6 = AUDIO_REQUESTED (vot.js сам дожимает)
    const wait = Math.min(Math.max(res.remainingTime ?? 30, 10), 60);
    log(`status=${res.status} remaining=${res.remainingTime}s — жду ${wait}с (перевод готовится у Яндекса)`);
    await new Promise((r) => setTimeout(r, wait * 1000));
  }

  if (!result) {
    console.error("LIVEDUB_NOT_AVAILABLE");
    log(`Перевод не успел подготовиться за ${timeoutSec}с`);
    process.exit(4);
  }

  log(`Аудио готово: ${result.url.slice(0, 80)}...`);
  const resp = await fetch(result.url);
  if (!resp.ok) {
    console.error("LIVEDUB_NOT_AVAILABLE");
    log(`Скачивание аудио не удалось: HTTP ${resp.status}`);
    process.exit(4);
  }
  const buf = Buffer.from(await resp.arrayBuffer());
  fs.mkdirSync(args.output, { recursive: true });
  const safeId = (videoData.videoId ?? "translation").toString().replace(/[^\w-]/g, "_");
  const outPath = path.resolve(args.output, `${safeId}.${useLively ? "live" : "tts"}.mp3`);
  fs.writeFileSync(outPath, buf);
  log(`Сохранено ${buf.length} байт (${useLively ? "живые голоса" : "обычные голоса"})`);
  console.log(outPath);
  process.exit(0);
} catch (e) {
  console.error("LIVEDUB_NOT_AVAILABLE");
  log(`Ошибка: ${e?.message ?? e}`);
  process.exit(4);
}
