import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("trendrelayDesktop", {
  status: () => ipcRenderer.invoke("trendrelay:status"),
  pair: () => ipcRenderer.invoke("trendrelay:pair"),
  signOut: () => ipcRenderer.invoke("trendrelay:sign-out"),
  apiRequest: (input: { path: string; method?: "GET" | "POST"; body?: string }) =>
    ipcRenderer.invoke("trendrelay:api", input),
});
