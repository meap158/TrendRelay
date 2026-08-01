import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";

const candidates = process.platform === "win32"
  ? [".venv\\Scripts\\python.exe", "python"]
  : [".venv/bin/python", "python3", "python"];

const python = candidates.find(
  (candidate) => candidate === "python" || candidate === "python3" || existsSync(candidate),
);
if (!python) {
  console.error("Python was not found. Install Python 3.12+ or create .venv first.");
  process.exit(1);
}

const result = spawnSync(
  python,
  ["-m", "pytest", "services/api/tests", "tests", ...process.argv.slice(2)],
  { cwd: process.cwd(), env: process.env, stdio: "inherit" },
);

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}
process.exit(result.status ?? 1);
