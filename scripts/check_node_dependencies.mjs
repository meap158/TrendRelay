import { existsSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const failures = [];

function load(name) {
  try {
    return require(name);
  } catch (error) {
    failures.push(`${name}: ${error instanceof Error ? error.message : String(error)}`);
    return undefined;
  }
}

function requireFile(name, path) {
  if (typeof path !== "string" || !existsSync(path)) {
    failures.push(`${name}: installed runtime file is missing`);
  }
}

requireFile("Electron", load("electron"));
requireFile("FFmpeg", load("ffmpeg-static"));
requireFile("FFprobe", load("@derhuerst/ffprobe-static"));
load("@swc/core");
load("esbuild");
load("../node_modules/vite/node_modules/esbuild");
load("unrs-resolver");

if (failures.length > 0) {
  console.error("Application dependency check failed:");
  for (const failure of failures) console.error(`  - ${failure}`);
  process.exit(1);
}
