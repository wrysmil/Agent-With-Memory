"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("mira", {
  appName: "Mira",
  platform: process.platform,
  retryBoot: () => ipcRenderer.send("mira:retry"),
});
