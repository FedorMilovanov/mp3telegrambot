from __future__ import annotations

from pathlib import Path

path = Path("provider/server/src/session_manager.ts")
source = path.read_text(encoding="utf-8")

old_import = 'import { buildURL, getHeaders, USER_AGENT } from "bgutils-js/utils";'
new_import = '''import {
    buildURL,
    getHeaders,
    parseLooseJSON,
    USER_AGENT,
} from "bgutils-js/utils";'''
if source.count(old_import) != 1:
    raise SystemExit("FORWARD243_IMPORT_ANCHOR_DRIFT")
source = source.replace(old_import, new_import, 1)

method_anchor = "    private async getDescrambledChallenge(\n"
if source.count(method_anchor) != 1:
    raise SystemExit("FORWARD243_METHOD_ANCHOR_DRIFT")

homepage_method = r'''    private async getChallengeFromHomepage(
        potCtx: PotContext,
    ): Promise<ChallengeData | undefined> {
        try {
            const pageResponse = await potCtx.fetch("https://www.youtube.com", {
                method: "GET",
                headers: {
                    accept: "*/*",
                    "accept-language": "en-US,en;q=0.7",
                    "user-agent": USER_AGENT,
                },
            });
            if (!pageResponse.ok) {
                this.logger.warn(
                    `homepage-challenge: HTTP ${pageResponse.status}; falling back`,
                );
                return undefined;
            }
            const pageHtml = await pageResponse.text();
            const ytcfgMatch = pageHtml.match(/ytcfg\.set\(({.+?})\);/s);
            const attMatch = pageHtml.match(
                /window\.ytAtN\(\s*({[\s\S]*?})\s*\)/,
            );
            if (!ytcfgMatch || !attMatch) {
                this.logger.warn(
                    "homepage-challenge: ytcfg/ytAtN pair missing; falling back",
                );
                return undefined;
            }

            const ytObj = { config_: JSON.parse(ytcfgMatch[1] as string) };
            const globalObj = potCtx.globalObj as typeof globalThis & {
                yt?: typeof ytObj;
                window?: { yt?: typeof ytObj };
            };
            globalObj.yt = ytObj;
            if (globalObj.window) globalObj.window.yt = ytObj;

            const attData = parseLooseJSON(attMatch[1] as string);
            const challengeResponse = attData.R as
                | { bgChallenge?: ChallengeData }
                | undefined;
            const bgChallenge = challengeResponse?.bgChallenge;
            if (!bgChallenge?.program || !bgChallenge?.interpreterUrl) {
                this.logger.warn(
                    "homepage-challenge: payload missing bgChallenge; falling back",
                );
                return undefined;
            }
            this.logger.debug("Using challenge from the homepage (forward243)");
            return bgChallenge;
        } catch (e) {
            const detail = e instanceof Error ? e.message : String(e);
            this.logger.warn(
                `homepage-challenge: failed (${detail}); falling back`,
            );
            return undefined;
        }
    }

'''
source = source.replace(method_anchor, homepage_method + method_anchor, 1)

try_anchor = "        try {\n            if (!challenge) {\n"
try_replacement = (
    "        try {\n"
    "            challenge =\n"
    "                (await this.getChallengeFromHomepage(potCtx)) ?? challenge;\n"
    "            if (!challenge) {\n"
)
if source.count(try_anchor) != 1:
    raise SystemExit("FORWARD243_TRY_ANCHOR_DRIFT")
source = source.replace(try_anchor, try_replacement, 1)

path.write_text(source, encoding="utf-8")
print("FORWARD243_APPLIED=1")
