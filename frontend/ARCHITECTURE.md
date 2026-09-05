
## 3D Avatar upgrade (v15.1)
- `src/three/ultronAvatar.ts`: procedural gunmetal robot head (fallback) +
  GLB/glTF drop-in (`/models/manifest.json` → `/models/<file>.glb`) with
  `THREE.AnimationMixer` (first clip), morph-target lip-sync (`*jaw|mouth|open*`),
  named-node wiring (`*head*`, `*jaw*`, `*eye*`). No model → procedural head, never crashes.
- Head: state-driven gaze targets + micro-saccades + blink timer + nod (DONE) + shake (ERROR).
- Eyes: red emissive + glow sprites; brightness per agent state; mic amplitude boosts LISTENING.
- Mouth: driven ONLY by real TTS RMS (`src/lib/tts.ts`):
  Microsoft Edge Neural TTS (`edge-tts`) → MP3 → `AudioBufferSourceNode` → `AnalyserNode` → speakers + `ttsLevel` → jaw/morphs.
  No robotic fallback is used. If the neural backend is unavailable, ULTRON remains silent and reports the failure honestly.
  Mode shown honestly in the footer (SPEECH: NEURAL/OFF).
- TTS trigger: backend agent DONE/ERROR messages over WS. Audio unlocked on first user gesture.
- Perf: pixelRatio cap 1.75 with adaptive steps [1.75,1.25,1,0.75] protecting a 30 FPS floor;
  full dispose of avatar/mixer/geometries/materials on unmount.
