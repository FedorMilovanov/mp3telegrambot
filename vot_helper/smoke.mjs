#!/usr/bin/env node
/**
 * Runtime contract smoke test for `vot_live.mjs`.
 *
 * Why this file exists
 * --------------------
 * `@vot.js/node` 3.x moved the provider methods to `client.provider.*`. The
 * helper calls `client.translateVideoCache`, `client.requestVtransAudio` and
 * `client.requestVtransFailAudio`, so a major bump installs cleanly, passes
 * `npm audit`, and then fails at the first real translation with
 * `TypeError: client.translateVideoCache is not a function` — while every
 * Python test stays green, because nothing ever loads this ES module.
 *
 * The required surface is not written down here: it is **derived from
 * `vot_live.mjs` itself**, so the check cannot drift away from the code it is
 * supposed to protect. Adding a new `client.something(...)` call to the helper
 * automatically extends what this script verifies.
 *
 * Exit code 0 = the installed tree satisfies the helper. Any other code = the
 * tree must not ship.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const helperSource = readFileSync(join(here, "vot_live.mjs"), "utf8");

/** Every `client.method(` call the helper makes, deduplicated and sorted. */
function requiredClientMethods(source) {
  const found = new Set();
  for (const match of source.matchAll(/\bclient\.([A-Za-z_$][\w$]*)\s*\(/g)) {
    found.add(match[1]);
  }
  return [...found].sort();
}

/** Named imports from `@vot.js/node/...` sub-paths, e.g. getVideoData. */
function requiredModuleExports(source) {
  const wanted = [];
  for (const match of source.matchAll(
    /import\s*\{([^}]+)\}\s*from\s*"(@vot\.js\/node[^"]*)"/g,
  )) {
    const names = match[1]
      .split(",")
      .map((part) => part.trim().split(/\s+as\s+/)[0].trim())
      .filter(Boolean);
    wanted.push({ specifier: match[2], names });
  }
  return wanted;
}

const failures = [];

let VOTClient;
try {
  VOTClient = (await import("@vot.js/node")).default;
} catch (error) {
  console.error(`smoke: cannot import @vot.js/node: ${error.message}`);
  process.exit(1);
}

if (typeof VOTClient !== "function") {
  failures.push(`default export is ${typeof VOTClient}, expected a constructor`);
} else {
  const client = new VOTClient({ apiToken: "smoke-token" });
  const required = requiredClientMethods(helperSource);
  if (required.length === 0) {
    failures.push("no client.<method>( calls were found in vot_live.mjs");
  }
  for (const method of required) {
    if (typeof client[method] !== "function") {
      failures.push(
        `client.${method} is ${typeof client[method]}; ` +
          "the installed @vot.js/node does not match vot_live.mjs " +
          "(3.x moved it to client.provider.*)",
      );
    }
  }
  console.log(`smoke: checked client methods ${required.join(", ") || "(none)"}`);
}

for (const { specifier, names } of requiredModuleExports(helperSource)) {
  let namespace;
  try {
    namespace = await import(specifier);
  } catch (error) {
    failures.push(`cannot import ${specifier}: ${error.message}`);
    continue;
  }
  for (const name of names) {
    if (typeof namespace[name] !== "function") {
      failures.push(`${specifier} does not export ${name} as a function`);
    }
  }
  console.log(`smoke: checked ${specifier} exports ${names.join(", ")}`);
}

const pkg = JSON.parse(readFileSync(join(here, "node_modules", "undici", "package.json"), "utf8"));
console.log(`smoke: undici ${pkg.version}`);

if (failures.length > 0) {
  for (const failure of failures) console.error(`smoke: FAIL ${failure}`);
  process.exit(1);
}

console.log("smoke: vot_live.mjs runtime contract is satisfied");
