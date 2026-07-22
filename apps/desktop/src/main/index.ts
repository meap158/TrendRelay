import { promises as fs } from "node:fs";
import { hostname } from "node:os";
import { join } from "node:path";

import { app, BrowserWindow, ipcMain, safeStorage, shell } from "electron";
import type { IpcMainInvokeEvent } from "electron";

const apiOrigin = process.env.TRENDRELAY_API_URL ?? "http://127.0.0.1:8080";
const webOrigin = process.env.TRENDRELAY_WEB_URL ?? "http://localhost:3000";

type DeviceClaims = { sub?: string; email?: string; exp?: number };
type ApiRequest = { path: string; method?: "GET" | "POST"; body?: string };

function sessionPath() {
  return join(app.getPath("userData"), "device-session.bin");
}

function decodeClaims(token: string): DeviceClaims {
  try {
    return JSON.parse(Buffer.from(token.split(".")[1], "base64url").toString("utf8")) as DeviceClaims;
  } catch {
    return {};
  }
}

async function clearToken(): Promise<void> {
  await fs.rm(sessionPath(), { force: true });
}

async function readToken(): Promise<string | null> {
  try {
    if (!safeStorage.isEncryptionAvailable()) return null;
    const encrypted = await fs.readFile(sessionPath());
    const token = safeStorage.decryptString(encrypted);
    const claims = decodeClaims(token);
    if (!claims.exp || claims.exp * 1000 <= Date.now()) {
      await clearToken();
      return null;
    }
    return token;
  } catch {
    return null;
  }
}

async function storeToken(token: string): Promise<void> {
  if (!safeStorage.isEncryptionAvailable()) {
    throw new Error("Secure operating-system storage is unavailable.");
  }
  const target = sessionPath();
  const temporary = `${target}.tmp`;
  await fs.mkdir(app.getPath("userData"), { recursive: true });
  await fs.writeFile(temporary, safeStorage.encryptString(token));
  await fs.rm(target, { force: true });
  await fs.rename(temporary, target);
}

async function desktopStatus() {
  const token = await readToken();
  if (!token) return { paired: false as const };
  const claims = decodeClaims(token);
  return {
    paired: true as const,
    userId: claims.sub ?? "",
    email: claims.email ?? null,
    expiresAt: claims.exp ? new Date(claims.exp * 1000).toISOString() : null,
  };
}

async function pairDesktop() {
  const started = await fetch(`${apiOrigin}/api/device-pairings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ device_name: `TrendRelay Desktop on ${hostname()}` }),
  });
  if (!started.ok) throw new Error("Could not start desktop pairing.");
  const pairing = await started.json() as {
    device_code: string;
    verification_path: string;
    expires_at: string;
    interval_seconds: number;
  };
  const verificationUrl = new URL(pairing.verification_path, webOrigin);
  if (!["http:", "https:"].includes(verificationUrl.protocol)) {
    throw new Error("Unsafe pairing verification URL.");
  }
  await shell.openExternal(verificationUrl.toString());
  const deadline = new Date(pairing.expires_at).getTime();
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, pairing.interval_seconds * 1000));
    const exchange = await fetch(`${apiOrigin}/api/device-pairings/token/exchange`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_code: pairing.device_code }),
    });
    if (exchange.status === 428) continue;
    const payload = await exchange.json() as { access_token?: string; detail?: string };
    if (!exchange.ok || !payload.access_token) {
      throw new Error(payload.detail ?? "Desktop pairing failed.");
    }
    await storeToken(payload.access_token);
    return desktopStatus();
  }
  throw new Error("Desktop pairing expired before approval.");
}

async function authorizedApiRequest(input: ApiRequest) {
  const token = await readToken();
  if (!token) throw new Error("Pair TrendRelay Desktop to continue.");
  const method = input.method ?? "GET";
  if (!["GET", "POST"].includes(method)) throw new Error("API method is not allowed.");
  if (!input.path.startsWith("/api/") || input.path.startsWith("//")) {
    throw new Error("API path is not allowed.");
  }
  const target = new URL(input.path, apiOrigin);
  if (target.origin !== new URL(apiOrigin).origin) throw new Error("API origin is not allowed.");
  const response = await fetch(target, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(input.body ? { "Content-Type": "application/json" } : {}),
    },
    body: input.body,
  });
  if (response.status === 401) await clearToken();
  return {
    ok: response.ok,
    status: response.status,
    body: await response.text(),
    contentType: response.headers.get("content-type"),
  };
}

function assertTrustedSender(event: IpcMainInvokeEvent): void {
  const senderUrl = event.senderFrame?.url;
  if (!senderUrl) throw new Error("Desktop request has no renderer origin.");
  const senderOrigin = new URL(senderUrl).origin;
  if (senderOrigin !== new URL(webOrigin).origin) {
    throw new Error("Desktop request came from an untrusted renderer origin.");
  }
}

function registerDesktopIpc() {
  ipcMain.handle("trendrelay:status", (event) => {
    assertTrustedSender(event);
    return desktopStatus();
  });
  ipcMain.handle("trendrelay:pair", (event) => {
    assertTrustedSender(event);
    return pairDesktop();
  });
  ipcMain.handle("trendrelay:sign-out", async (event) => {
    assertTrustedSender(event);
    await clearToken();
    return { paired: false as const };
  });
  ipcMain.handle("trendrelay:api", (event, input: ApiRequest) => {
    assertTrustedSender(event);
    return authorizedApiRequest(input);
  });
}

function createWindow() {
  const window = new BrowserWindow({
    width: 1280,
    height: 800,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: join(__dirname, "../preload/index.js"),
    },
  });
  void window.loadURL(webOrigin);
  window.webContents.on("will-navigate", (event, url) => {
    if (new URL(url).origin !== new URL(webOrigin).origin) event.preventDefault();
  });
  window.webContents.setWindowOpenHandler(({ url }) => {
    const protocol = new URL(url).protocol;
    if (protocol === "http:" || protocol === "https:") void shell.openExternal(url);
    return { action: "deny" };
  });
}

app.whenReady().then(() => {
  registerDesktopIpc();
  createWindow();
});
app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); });
