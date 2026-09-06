"use strict";

const { contextBridge } = require("electron");

// The Cockpit communicates with the Python backend over loopback HTTP/WS.
// No Node/Electron primitives are exposed to untrusted renderer content.
contextBridge.exposeInMainWorld("ultronDesktop", Object.freeze({
  platform: process.platform,
  packaged: process.env.NODE_ENV === "production",
}));
