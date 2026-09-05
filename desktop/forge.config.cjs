module.exports = {
  packagerConfig: {
    asar: true,
    executableName: 'ULTRON',
    name: 'ULTRON',
    appBundleId: 'com.ultron.assistant',
    extraResource: [
      { from: '../frontend/dist', to: 'frontend-dist' },
      { from: '../backend/dist/ULTRON_BACKEND', to: 'backend' }
    ]
  },
  rebuildConfig: {},
  makers: [
    {
      name: '@electron-forge/maker-squirrel',
      config: {
        name: 'ULTRON',
        setupExe: 'ULTRON-Setup.exe',
        shortcutName: 'ULTRON'
      }
    }
  ]
};
