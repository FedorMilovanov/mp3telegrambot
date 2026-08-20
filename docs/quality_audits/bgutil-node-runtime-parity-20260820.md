# bgutil runtime parity incident — 2026-08-20

## Production evidence

A real Windows/TUN Shorts Factory request reached the exact-source bgutil runtime but failed before token generation. Startup had reported:

`bgutil 1.3.1@a0be2352; node=22.23.1; browserless=on; source-only=on`

The real yt-dlp request then executed Deno against `server/src/generate_once.ts --version` and hit the upstream 15-second script-version timeout. yt-dlp consequently skipped mweb HTTPS formats because no GVS PO Token was available.

## Root cause

The readiness contract and execution contract selected different JavaScript runtimes:

1. MP3Bot provisioning builds and validates `server/build/generate_once.js` with Node >=22.
2. `services.ffmpeg._supported_js_runtimes()` enabled Deno and Node when both were installed.
3. yt-dlp enables Deno by default and ranks Deno above Node.
4. bgutil 1.3.1 also gives its Deno script provider a higher preference than its Node provider.
5. The Deno provider validates `server/src/generate_once.ts` by executing `--version` with a fixed 15-second timeout.
6. Therefore startup could prove the Node path healthy while the first real PO-token request chose an unproved Deno path and failed.

Increasing the timeout would only mask the split contract. The correct repair is to make production execute the same runtime that startup proves.

## Repair

- Require the existing Node >=22 production runtime.
- Explicitly pass `--no-js-runtimes` before enabling Node, because yt-dlp otherwise keeps its default Deno runtime enabled.
- Enable only `--js-runtimes node` for production yt-dlp commands.
- Ignore an installed Deno binary for this path; it remains installed but cannot win provider selection.
- Add regression coverage for a machine where both Deno and Node exist, and for the exact argument ordering that clears default Deno before enabling Node.

## Quality invariants

This does not lower source quality, change `mweb`, weaken fragment/duration integrity, reintroduce Chrome/WPC, or add a larger timeout. It removes an execution-path ambiguity so bootstrap and real GVS token generation share one Node-owned route.
