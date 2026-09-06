#!/usr/bin/env node
/**
 * vot_live.mjs — прямой клиент Яндекс VOT API для Live Voices.
 *
 * Важный контракт: voice-style=live никогда не принимает обычный cached TTS
 * за успешный результат. Для новых YouTube-видео @vot.js/node 2.4.12 может
 * получить status=6 (AUDIO_REQUESTED). Его встроенный recursive retry теряет
 * extraOpts.useLivelyVoice, поэтому audio bootstrap выполняем здесь вручную и
 * каждый следующий translateVideo снова явно запрашивает Live Voices.
 */
import fs from "node:fs";
import path from "node:path";
import { parseArgs } from "node:util";

import VOTClient from "@vot.js/node";
import { getVideoData } from "@vot.js/node/utils/videoData";

const AUDIO_REQUESTED = 6;
const CACHE_FINISHED = 0;
const AUDIO_DOWNLOAD_TYPE = "web_api_get_all_generating_urls_data_from_iframe";

const { values: args } = parseArgs({
  options: {
    url: { type: "string" },
    output: { type: "string" },
    "voice-style": { type: "string", default: "live" },
    timeout: { type: "string", default: "1800" },
    token: { type: "string" },
    duration: { type: "string" },
    lang: { type: "string" },
  },
});

if (!args.url || !args.output) {
  console.error("usage: vot_live.mjs --url <video_url> --output <dir> [--voice-style live|tts] [--timeout sec] [--duration sec] [--token t]");
  process.exit(2);
}

const token = args.token || process.env.VOT_API_TOKEN || process.env.YANDEX_OAUTH_TOKEN || undefined;
const useLively = (args["voice-style"] || "live").toLowerCase() !== "tts";
const timeoutSec = Math.max(60, parseInt(args.timeout, 10) || 1800);
const knownDuration = args.duration ? Math.max(0, Number(args.duration) || 0) : 0;
const sourceLang = args.lang ? args.lang.trim().toLowerCase() : "";

const client = new VOTClient({ apiToken: token });

function log(msg) {
  console.error(`[vot_live] ${msg}`);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function liveCacheState(videoData) {
  try {
    const cache = await client.translateVideoCache({ videoData });
    return cache?.cloning ?? null;
  } catch (e) {
    log(`Не удалось проверить cloning-cache: ${e?.message ?? e}`);
    return null;
  }
}

async function bootstrapRequestedAudio(videoData, translationId) {
  // status=6 встречается при первом переводе нового YouTube-видео. Не даём
  // @vot.js/node 2.4.12 делать свой recursive retry: он забывает extraOpts и
  // превращает Live Voices в обычный перевод. Повтор после bootstrap делает
  // внешний цикл ниже и снова передаёт useLivelyVoice=true.
  await client.requestVtransFailAudio(videoData.url);
  await client.requestVtransAudio(videoData.url, translationId, {
    audioFile: new Uint8Array(),
    fileId: AUDIO_DOWNLOAD_TYPE,
  });
  log("AUDIO_REQUESTED: audio bootstrap отправлен; продолжаю только с Live Voices");
}

try {
  const videoData = await getVideoData(args.url);
  if (knownDuration > 0) {
    // Для YouTube duration входит в cache-key Яндекса.
    videoData.duration = knownDuration;
    log(`duration=${knownDuration}s передан в VOT cache-key`);
  }
  if (sourceLang) {
    videoData.language = sourceLang;
    log(`language=${sourceLang} форсирован для запроса`);
  }

  const deadline = Date.now() + timeoutSec * 1000;
  let result = null;
  let audioBootstrapDone = false;

  while (Date.now() < deadline) {
    let res;
    try {
      res = await client.translateVideo({
        videoData,
        extraOpts: {
          useLivelyVoice: useLively,
          videoTitle: videoData.title ?? "",
        },
        // Встроенный AUDIO_REQUESTED recursion в @vot.js/node 2.4.12 теряет
        // extraOpts.useLivelyVoice. Bootstrap ниже выполняем сами.
        shouldSendFailedAudio: false,
      });
    } catch (e) {
      const data = e?.data ?? {};
      if (data.status === 7 || /auth required/i.test(e?.message ?? "")) {
        console.error("LIVEDUB_AUTH_REQUIRED");
        log(`Яндекс требует авторизацию для живых голосов. ${token ? "Токен отклонён (протух?)." : "VOT_API_TOKEN/YANDEX_OAUTH_TOKEN не задан."}`);
        process.exit(3);
      }
      if (data.status === 0 || /couldn't translate/i.test(e?.message ?? "")) {
        console.error("LIVEDUB_NOT_AVAILABLE");
        log(`Яндекс не смог перевести: ${e.message} ${JSON.stringify(data)}`);
        process.exit(4);
      }
      throw e;
    }

    if (res.status === AUDIO_REQUESTED && !audioBootstrapDone) {
      try {
        await bootstrapRequestedAudio(videoData, res.translationId);
        audioBootstrapDone = true;
      } catch (e) {
        console.error("LIVEDUB_NOT_AVAILABLE");
        log(`Не удалось инициировать подготовку аудио для Live Voices: ${e?.message ?? e}`);
        process.exit(4);
      }
      await sleep(5000);
      continue;
    }

    if (res.translated && res.url) {
      if (!useLively) {
        result = res;
        break;
      }

      // Yandex cache хранит обычный перевод (default) и Live Voices (cloning)
      // раздельно. Не принимаем просто "translated + url": именно так раньше
      // обычный cached voice ошибочно сохранялся как *.live.mp3.
      const cloning = await liveCacheState(videoData);
      if (cloning?.status === CACHE_FINISHED) {
        log("Live Voices подтверждены: cloning-cache=FINISHED");
        result = res;
        break;
      }

      const cloneStatus = cloning?.status ?? "missing";
      const cloneRemaining = cloning?.remainingTime ?? res.remainingTime ?? 30;
      const wait = Math.min(Math.max(Number(cloneRemaining) || 30, 10), 60);
      log(`Получен URL, но Live Voices ещё не подтверждены (cloning=${cloneStatus}) — жду ${wait}с`);
      await sleep(wait * 1000);
      continue;
    }

    const wait = Math.min(Math.max(res.remainingTime ?? 30, 10), 60);
    log(`status=${res.status} remaining=${res.remainingTime}s — жду ${wait}с (перевод готовится у Яндекса)`);
    await sleep(wait * 1000);
  }

  if (!result) {
    console.error("LIVEDUB_NOT_AVAILABLE");
    log(`Live Voices не успели подготовиться за ${timeoutSec}с; обычный голос не используется`);
    process.exit(4);
  }

  log(`Аудио готово: ${result.url.slice(0, 80)}...`);
  let resp = null;
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      resp = await fetch(result.url);
      if (resp.ok) break;
    } catch (e) {
      log(`Скачивание попытка ${attempt} failed: ${e.message}`);
    }
    if (attempt < 3) await sleep(2000);
  }

  if (!resp || !resp.ok) {
    console.error("LIVEDUB_NOT_AVAILABLE");
    log(`Скачивание аудио не удалось после 3 попыток: HTTP ${resp ? resp.status : "ERR"}`);
    process.exit(4);
  }
  const buf = Buffer.from(await resp.arrayBuffer());
  if (!buf || buf.length < 4096) {
    console.error("LIVEDUB_NOT_AVAILABLE");
    log(`Скачанный аудиофайл подозрительно мал: ${buf ? buf.length : 0} байт — считаю загрузку битой`);
    process.exit(4);
  }

  fs.mkdirSync(args.output, { recursive: true });
  const safeId = (videoData.videoId ?? "translation").toString().replace(/[^\w-]/g, "_");
  const outPath = path.resolve(args.output, `${safeId}.${useLively ? "live" : "tts"}.mp3`);
  fs.writeFileSync(outPath, buf);
  log(`Сохранено ${buf.length} байт (${useLively ? "живые голоса подтверждены" : "обычные голоса"})`);
  console.log(outPath);
  process.exit(0);
} catch (e) {
  console.error("LIVEDUB_NOT_AVAILABLE");
  log(`Ошибка: ${e?.message ?? e}`);
  process.exit(4);
}
