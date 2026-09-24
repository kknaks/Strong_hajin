#!/usr/bin/env node

/**
 * Run the product shell against a local web origin without touching the
 * checked-in production shell.config.json or product-shell capability.
 *
 * Rust uses include_str! for both files, so a config overlay alone cannot
 * work: this command builds a disposable copy of src-tauri, patches only the
 * local origin in that copy, and runs Tauri from there. The temporary tree is
 * removed when the process exits.
 */

import { cpSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import os from "node:os";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const sourceShell = join(frontendRoot, "src-tauri");
const origin = process.env.TAURI_LOCAL_ORIGIN ?? "http://127.0.0.1:5176";

function originOf(raw) {
  const parsed = new URL(raw);
  if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) {
    throw new Error("TAURI_LOCAL_ORIGIN must be an http(s) URL without credentials");
  }
  if (parsed.pathname !== "/" || parsed.search || parsed.hash) {
    throw new Error("TAURI_LOCAL_ORIGIN must contain only scheme, host and optional port");
  }
  if (!["127.0.0.1", "localhost", "::1"].includes(parsed.hostname)) {
    throw new Error("TAURI_LOCAL_ORIGIN is local-only (use 127.0.0.1, localhost, or ::1)");
  }
  return parsed.origin;
}

let localOrigin;
try {
  localOrigin = originOf(origin);
} catch (error) {
  console.error(`tauri-local: ${error.message}`);
  process.exit(2);
}
const tempRoot = mkdtempSync(join(os.tmpdir(), "strong-hajin-tauri-local-"));
const tempShell = join(tempRoot, "src-tauri");
let child;
let shuttingDown = false;
const cleanup = () => {
  try {
    rmSync(tempRoot, { recursive: true, force: true });
  } catch (error) {
    console.error(`tauri-local: temporary tree cleanup failed: ${error.message}`);
  }
};
process.once("exit", cleanup);
const forwardSignal = (signal) => {
  if (shuttingDown) return;
  shuttingDown = true;
  child?.kill(signal);
};
process.once("SIGINT", () => forwardSignal("SIGINT"));
process.once("SIGTERM", () => forwardSignal("SIGTERM"));

// Do not copy target/: it is build output and can be very large. Everything
// needed by include_str!, Cargo and Tauri's generated schemas is retained.
cpSync(sourceShell, tempShell, {
  recursive: true,
  filter: (source) => !relative(sourceShell, source).split(sep).includes("target"),
});
// tauri.conf.json's schema path is relative to the project root.
symlinkSync(join(frontendRoot, "node_modules"), join(tempRoot, "node_modules"), "junction");

const shellConfigPath = join(tempShell, "shell.config.json");
const shellConfig = JSON.parse(readFileSync(shellConfigPath, "utf8"));
shellConfig.operationalOrigin = localOrigin;
shellConfig.navigationAllowlist = [localOrigin];
writeFileSync(shellConfigPath, `${JSON.stringify(shellConfig, null, 2)}\n`);

const capabilityPath = join(tempShell, "capabilities", "product-shell.json");
const capability = JSON.parse(readFileSync(capabilityPath, "utf8"));
capability.remote.urls = [`${localOrigin}/*`];
writeFileSync(capabilityPath, `${JSON.stringify(capability, null, 2)}\n`);

console.log(`tauri-local: opening ${localOrigin}`);
console.log(`tauri-local: disposable Rust tree ${tempShell}`);
console.log("tauri-local: production shell.config.json and product-shell.json are untouched");

child = spawn(
  "npx",
  ["--prefix", frontendRoot, "--no-install", "tauri", "dev", "--no-dev-server"],
  { cwd: tempRoot, stdio: "inherit", env: { ...process.env, TAURI_LOCAL_ORIGIN: localOrigin } },
);

child.once("error", (error) => {
  console.error(`tauri-local: could not start Tauri: ${error.message}`);
  process.exitCode = 1;
});
child.once("exit", (code, signal) => {
  cleanup();
  if (signal) {
    process.exitCode = 128 + (signal === "SIGINT" ? 2 : 15);
  } else {
    process.exitCode = code ?? 1;
  }
});
