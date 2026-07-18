import { app, BrowserWindow, shell } from "electron";

function createWindow() {
  const window = new BrowserWindow({
    width: 1280,
    height: 800,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: undefined,
    },
  });
  const webUrl = process.env.TRENDRELAY_WEB_URL ?? "http://localhost:3000";
  void window.loadURL(webUrl);
  window.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: "deny" };
  });
}

app.whenReady().then(createWindow);
app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); });
