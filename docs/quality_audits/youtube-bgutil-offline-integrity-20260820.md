# YouTube bgutil offline integrity repair — 2026-08-20

## Observed production failure

On the production Windows machine, exact-source checkout, `npm ci`, and TypeScript build completed, but bootstrap failed on the post-build command:

`node build/generate_once.js --version`

The command exceeded the 20-second smoke timeout.

## Root cause

At upstream commit `a0be2352807e3bd6991f09d2cab685a0ab825b26`, `generate_once.ts` imports `SessionManager` before Commander parses `--version`. That import pulls the heavy provider graph (`axios`, `bgutils-js`, BotGuard/WebPO, `proxy-agent`, `jsdom`, `youtubei.js`) even though the requested operation is only version reporting.

Therefore `generate_once.js --version` is not a cheap version probe and its latency depends on cold Node/module loading, antivirus/filesystem behavior and local Windows/NVM conditions. Raising the timeout would preserve the wrong ownership contract.

## Repair

- keep exact source commit pinning, `npm ci`, local TypeScript build and process-tree ownership unchanged;
- use `node --check build/generate_once.js` for a non-executing compiled-entrypoint syntax check;
- validate `server/package.json` and source `package-lock.json` version `1.3.1`;
- require npm's hidden `node_modules/.package-lock.json`;
- prove every package recorded in the installed hidden lock still exists;
- for every direct production dependency, require source-lock, installed-lock and installed `package.json` versions to agree;
- keep the same fail-closed rebuild behavior when any integrity check fails.

The provider itself is not executed at startup and no YouTube request is added to startup. Real provider/GVS behavior remains covered by the manual production-network acceptance probe.

## Quality invariants

No model/Gemini/503 changes. No YouTube client or format change. No Chrome/WPC fallback. No manual PO token. No format 18/360p fallback. No weakening of Factory duration/fragment validation.
