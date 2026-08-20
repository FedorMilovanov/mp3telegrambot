#!/usr/bin/env node
/**
 * Loopback-only HTTP transport for the exact-source bgutil SessionManager.
 *
 * The token engine remains the pinned upstream build under .runtime/. This
 * repo-owned wrapper changes only transport ownership: it exposes the two
 * endpoints yt-dlp's bgutil HTTP provider needs on 127.0.0.1:4417 instead of
 * starting upstream 1.3.1's all-interface HTTP server.
 */

import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { SessionManager } from "../.runtime/bgutil-ytdlp-pot-provider/server/build/session_manager.js";
import { VERSION } from "../.runtime/bgutil-ytdlp-pot-provider/server/build/utils.js";

const HOST = "127.0.0.1";
const PORT = 4417;
const OWNER_POLICY = "mp3telegrambot-bgutil-loopback-v1";
const MAX_BODY_BYTES = 2 * 1024 * 1024;
const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const PROVIDER_ROOT = path.resolve(SCRIPT_DIR, "..", ".runtime", "bgutil-ytdlp-pot-provider");
const MARKER_PATH = path.join(PROVIDER_ROOT, ".mp3bot-bgutil-version");
const PROVIDER_MARKER = fs.readFileSync(MARKER_PATH, "utf8").trim();

const sessionManager = new SessionManager(false);

function sendJson(response, status, payload) {
    const body = Buffer.from(JSON.stringify(payload), "utf8");
    response.writeHead(status, {
        "Content-Type": "application/json; charset=utf-8",
        "Content-Length": String(body.length),
        "Cache-Control": "no-store",
    });
    response.end(body);
}

function errorText(error) {
    if (error instanceof Error) {
        return error.stack || error.message || error.name;
    }
    return String(error);
}

async function readJson(request) {
    let size = 0;
    const chunks = [];
    for await (const chunk of request) {
        size += chunk.length;
        if (size > MAX_BODY_BYTES) {
            throw new Error("request body exceeds 2 MiB");
        }
        chunks.push(chunk);
    }
    if (!chunks.length) return {};
    const parsed = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("request body must be a JSON object");
    }
    return parsed;
}

const server = http.createServer(async (request, response) => {
    try {
        const requestUrl = new URL(request.url || "/", `http://${HOST}:${PORT}`);

        if (request.method === "GET" && requestUrl.pathname === "/ping") {
            sendJson(response, 200, {
                server_uptime: process.uptime(),
                version: VERSION,
                owner: OWNER_POLICY,
                provider_marker: PROVIDER_MARKER,
            });
            return;
        }

        if (request.method === "POST" && requestUrl.pathname === "/get_pot") {
            const body = await readJson(request);
            if (body.data_sync_id) {
                sendJson(response, 400, {
                    error: "data_sync_id is deprecated, use content_binding instead",
                });
                return;
            }
            if (body.visitor_data) {
                sendJson(response, 400, {
                    error: "visitor_data is deprecated, use content_binding instead",
                });
                return;
            }
            if (body.disable_innertube) {
                sendJson(response, 400, {
                    error: "disable_innertube is deprecated because the /Create endpoint doesn't work anymore",
                });
                return;
            }

            const sessionData = await sessionManager.generatePoToken(
                body.content_binding,
                body.proxy || "",
                Boolean(body.bypass_cache),
                body.source_address,
                Boolean(body.disable_tls_verification),
                body.challenge,
                body.innertube_context,
            );
            sendJson(response, 200, sessionData);
            return;
        }

        sendJson(response, 404, { error: "not found" });
    } catch (error) {
        console.error(errorText(error));
        sendJson(response, 500, { error: errorText(error) });
    }
});

server.on("error", (error) => {
    console.error(`bgutil loopback server failed: ${errorText(error)}`);
    process.exitCode = 1;
});

function shutdown(signal) {
    server.close(() => process.exit(0));
    const timer = setTimeout(() => process.exit(1), 3000);
    timer.unref();
    console.log(`bgutil loopback server stopping: ${signal}`);
}

process.once("SIGINT", () => shutdown("SIGINT"));
process.once("SIGTERM", () => shutdown("SIGTERM"));

server.listen(PORT, HOST, () => {
    console.log(
        `bgutil loopback ready version=${VERSION} marker=${PROVIDER_MARKER} address=${HOST}:${PORT}`,
    );
});
