# ULTRON avatar model drop-in

Place a GLB here (e.g. `ultron.glb`) and set `manifest.json` → `"model": "ultron.glb"`.
While `manifest.model` is `null`, the built-in **procedural holographic head** is used —
the system never crashes on a missing model.

What the loader wires up automatically when a GLB is present:

| Feature        | Looks for                                                    |
|----------------|--------------------------------------------------------------|
| head rotation  | node whose name matches /head/i (falls back to model root)   |
| eyes           | materials/meshes matching /eye/i → emissive + glow drive     |
| lip-sync       | morph targets matching /jaw\|mouth\|open/i, or node /jaw/i   |
| idle animation | first animation clip via `THREE.AnimationMixer`              |

Export uncompressed (no Draco) for maximum compatibility.
The energy core, holographic rings, particles and pedestal always remain active
behind/around the avatar.
