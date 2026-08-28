import { useEffect, useRef } from "react";
import * as THREE from "three";
import { ttsLevel } from "../lib/tts";
import { getState, useApp } from "../lib/store";
import { resolveTheme } from "../styles/themes";

/**
 * HoloHead3D — procedural cybernetic head (Three.js, light).
 * Mouse parallax look-at · state animations · theme-aware accent ·
 * SPEAKING jaw/eye sync via ttsLevel · DEEP_COMPUTE neuron particles.
 */
export function HoloHead3D() {
  const ref = useRef<HTMLDivElement>(null);
  const agentState = useApp((s) => s.agentState);
  const theme = useApp((s) => s.theme);

  useEffect(() => {
    const mount = ref.current;
    if (!mount) return;
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));
    renderer.setClearColor(0x000000, 0);
    mount.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 50);
    camera.position.set(0, 0.1, 4.4);

    scene.add(new THREE.AmbientLight(0x334455, 1.4));
    const key = new THREE.PointLight(0xffffff, 8, 10, 2);
    key.position.set(1.5, 1.5, 2.5);
    scene.add(key);

    const disposables: { dispose: () => void }[] = [];
    const T = <X extends { dispose: () => void }>(d: X): X => (disposables.push(d), d);

    const accent = { cur: new THREE.Color("#ff1e42"), cyan: new THREE.Color("#38e1ff") };
    const metal = T(new THREE.MeshStandardMaterial({ color: 0x141a24, metalness: 0.92, roughness: 0.3, flatShading: true }));
    const dark = T(new THREE.MeshStandardMaterial({ color: 0x0a0e16, metalness: 0.85, roughness: 0.5, flatShading: true }));
    const eyeMat = T(new THREE.MeshBasicMaterial({ color: 0xff1e42 }));
    const mouthMat = T(new THREE.MeshBasicMaterial({ color: 0xff1e42, transparent: true, opacity: 0.5 }));

    const head = new THREE.Group();
    const skull = new THREE.Mesh(T(new THREE.SphereGeometry(0.5, 24, 18)), metal);
    skull.scale.set(0.8, 1, 0.9);
    skull.position.y = 0.15;
    head.add(skull);
    const crest = new THREE.Mesh(T(new THREE.BoxGeometry(0.06, 0.18, 0.55)), dark);
    crest.position.set(0, 0.55, -0.02);
    head.add(crest);
    const face = new THREE.Mesh(T(new THREE.BoxGeometry(0.46, 0.4, 0.22)), metal);
    face.position.set(0, 0.02, 0.3);
    head.add(face);
    const brow = new THREE.Mesh(T(new THREE.BoxGeometry(0.42, 0.07, 0.1)), dark);
    brow.position.set(0, 0.24, 0.38);
    brow.rotation.x = -0.2;
    head.add(brow);
    const eyes: THREE.Mesh[] = [];
    for (const s of [-1, 1]) {
      const e = new THREE.Mesh(T(new THREE.SphereGeometry(0.055, 12, 10)), eyeMat);
      e.scale.set(1.6, 0.7, 0.5);
      e.position.set(0.12 * s, 0.14, 0.42);
      head.add(e);
      eyes.push(e);
      const ear = new THREE.Mesh(T(new THREE.CylinderGeometry(0.1, 0.1, 0.06, 14)), dark);
      ear.rotation.z = Math.PI / 2;
      ear.position.set(0.42 * s, 0.1, 0);
      head.add(ear);
    }
    const jaw = new THREE.Group();
    jaw.position.set(0, -0.12, 0.16);
    const chin = new THREE.Mesh(T(new THREE.BoxGeometry(0.34, 0.14, 0.24)), metal);
    chin.position.set(0, -0.1, 0.18);
    jaw.add(chin);
    const mouth = new THREE.Mesh(T(new THREE.BoxGeometry(0.24, 0.016, 0.05)), mouthMat);
    mouth.position.set(0, -0.02, 0.34);
    jaw.add(mouth);
    head.add(jaw);
    const neck = new THREE.Mesh(T(new THREE.CylinderGeometry(0.12, 0.17, 0.34, 12)), dark);
    neck.position.y = -0.52;
    head.add(neck);
    scene.add(head);

    // data rings (LISTENING spins faster)
    const rings: THREE.Mesh[] = [];
    const ringMats: THREE.MeshBasicMaterial[] = [];
    for (let i = 0; i < 3; i++) {
      const m = T(new THREE.MeshBasicMaterial({ color: i % 2 ? 0x38e1ff : 0xff1e42, transparent: true, opacity: 0.45, blending: THREE.AdditiveBlending, depthWrite: false }));
      ringMats.push(m);
      const r = new THREE.Mesh(T(new THREE.TorusGeometry(0.95 + i * 0.18, 0.008, 8, 90)), m);
      rings.push(r);
      scene.add(r);
    }
    // neuron particles (DEEP_COMPUTE accelerates)
    const N = 260;
    const base = new Float32Array(N * 3);
    for (let i = 0; i < N; i++) {
      const r = 0.28 + Math.random() * 0.2;
      const th = Math.random() * Math.PI * 2;
      const ph = Math.acos(2 * Math.random() - 1);
      base[i * 3] = r * Math.sin(ph) * Math.cos(th);
      base[i * 3 + 1] = 0.12 + r * Math.cos(ph);
      base[i * 3 + 2] = r * Math.sin(ph) * Math.sin(th);
    }
    const pGeo = T(new THREE.BufferGeometry());
    pGeo.setAttribute("position", new THREE.BufferAttribute(base.slice(), 3));
    const pMat = T(new THREE.PointsMaterial({ color: 0xff1e42, size: 0.02, transparent: true, opacity: 0.85, blending: THREE.AdditiveBlending, depthWrite: false }));
    const neurons = new THREE.Points(pGeo, pMat);
    scene.add(neurons);
    // glow sprite
    const gc = document.createElement("canvas");
    gc.width = gc.height = 128;
    const g2 = gc.getContext("2d")!;
    const grad = g2.createRadialGradient(64, 64, 0, 64, 64, 64);
    grad.addColorStop(0, "rgba(255,255,255,.9)");
    grad.addColorStop(0.4, "rgba(255,60,80,.35)");
    grad.addColorStop(1, "rgba(255,60,80,0)");
    g2.fillStyle = grad;
    g2.fillRect(0, 0, 128, 128);
    const glowTex = T(new THREE.CanvasTexture(gc));
    const glowMat = T(new THREE.SpriteMaterial({ map: glowTex, transparent: true, opacity: 0.5, blending: THREE.AdditiveBlending, depthWrite: false }));
    const glow = new THREE.Sprite(glowMat);
    glow.scale.setScalar(3.4);
    scene.add(glow);

    // mouse parallax
    const mouse = { x: 0, y: 0 };
    const onMove = (e: MouseEvent) => {
      mouse.x = (e.clientX / window.innerWidth) * 2 - 1;
      mouse.y = (e.clientY / window.innerHeight) * 2 - 1;
    };
    window.addEventListener("mousemove", onMove);

    const resize = () => {
      const w = mount.clientWidth || 1, h = mount.clientHeight || 1;
      renderer.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(mount);

    const clock = new THREE.Clock();
    let raf = 0;
    const loop = () => {
      raf = requestAnimationFrame(loop);
      const dt = Math.min(clock.getDelta(), 0.05);
      const t = clock.elapsedTime;
      const st = getState();
      const state = st.agentState;
      const alert = state === "ERROR";
      const th = resolveTheme(st.theme, alert);
      accent.cur.lerp(new THREE.Color(th.red), 0.08);
      accent.cyan.lerp(new THREE.Color(th.cyan), 0.08);
      eyeMat.color.copy(accent.cur);
      mouthMat.color.copy(accent.cur);
      pMat.color.copy(accent.cur);
      ringMats[0].color.copy(accent.cur);
      ringMats[1].color.copy(accent.cyan);
      ringMats[2].color.copy(accent.cur);
      key.color.copy(accent.cur).lerp(new THREE.Color("#ffffff"), 0.6);

      // IDLE floating bob + breathing glow
      const speaking = st.ttsSpeaking;
      const amp = speaking ? ttsLevel.current : 0;
      head.position.y = Math.sin(t * 1.1) * 0.05;
      glowMat.opacity = 0.35 + 0.12 * Math.sin(t * 1.6) + amp * 0.5;

      // mouse parallax look-at (smooth lerp)
      const tx = mouse.x * 0.55, ty = -mouse.y * 0.3;
      head.rotation.y += (tx - head.rotation.y) * Math.min(1, dt * 5);
      head.rotation.x += (ty - head.rotation.x) * Math.min(1, dt * 5);

      // state animations
      const listening = state === "LISTENING" || st.mic === "listening";
      const deep = ["THINKING", "PLANNING", "EXECUTING", "VERIFYING"].includes(state);
      const ringSpeed = listening ? 2.6 : deep ? 1.6 : alert ? 4 : 0.5;
      rings.forEach((r, i) => {
        r.rotation.z += dt * ringSpeed * (i % 2 ? -1 : 1);
        r.rotation.x = Math.PI / 2.4 + Math.sin(t * 0.5 + i) * 0.3;
      });
      const eyeBoost = listening ? 1.5 : alert ? 2.2 : 1 + amp * 2;
      eyes.forEach((e) => e.scale.set(1.6 * eyeBoost * 0.75, 0.7 * (listening ? 1.2 : 1), 0.5));
      // SPEAKING jaw sync
      jaw.rotation.x = amp * 0.45 + (speaking ? 0.05 * Math.sin(t * 30) : 0);
      mouthMat.opacity = 0.35 + amp * 0.65;
      // DEEP_COMPUTE neurons
      const nspeed = deep ? 3.4 : alert ? 5 : 0.6;
      neurons.rotation.y += dt * nspeed;
      const pos = pGeo.getAttribute("position") as THREE.BufferAttribute;
      const arr = pos.array as Float32Array;
      const ag = deep || alert ? 0.05 : 0.015;
      for (let i = 0; i < N; i++) {
        const k = 1 + Math.sin(t * (2 + nspeed) + i) * ag;
        arr[i * 3] = base[i * 3] * k;
        arr[i * 3 + 1] = base[i * 3 + 1] * k;
        arr[i * 3 + 2] = base[i * 3 + 2] * k;
      }
      pos.needsUpdate = true;

      renderer.render(scene, camera);
    };
    raf = requestAnimationFrame(loop);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      window.removeEventListener("mousemove", onMove);
      disposables.forEach((d) => d.dispose());
      renderer.dispose();
      if (renderer.domElement.parentElement === mount) mount.removeChild(renderer.domElement);
    };
  }, []);

  return (
    <div className="core-stage" style={{ position: "relative" }}>
      <div ref={ref} style={{ position: "absolute", inset: 0 }} />
      <div className="core-state-chip">{agentState}</div>
      <div className="core-label">
        <div className="t" style={{ color: "var(--red)" }}>ULTRON</div>
        <div className="s">V15.0.0 · HOLO-HEAD</div>
      </div>
    </div>
  );
}
