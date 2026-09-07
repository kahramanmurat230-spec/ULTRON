const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('ultronDesktop', {
  platform: process.platform,
  version: process.versions.electron
});
