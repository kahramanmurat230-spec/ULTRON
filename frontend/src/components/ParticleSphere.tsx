import { useEffect, useRef } from "react";
import * as THREE from "three";
import { ttsLevel } from "../lib/tts";
import { getState } from "../lib/store";
import { resolveTheme } from "../styles/themes";

/**
 * ParticleSphere â€” ULTRON'un merkezi holografik Ã§ekirdeÄŸi.
 *
 * BÃ¼yÃ¼k partikÃ¼l kÃ¼resi (kÄ±rmÄ±zÄ± + beyaz), orbital halkalar ve merkezde
 * kÄ±rmÄ±zÄ± ULTRON Ã¼Ã§geni (SVG overlay). Ajan durumuna gÃ¶re canlanÄ±r:
 *   IDLE       â†’ sakin, yavaÅŸ nefes/pulse, dÃ¼ÅŸÃ¼k partikÃ¼l hÄ±zÄ±
 *   LISTENING  â†’ kÃ¼re bÃ¼yÃ¼yÃ¼p kÃ¼Ã§Ã¼lÃ¼r (nefes alma), partikÃ¼ller aktifleÅŸir
 *   THINKING   â†’ partikÃ¼ller tÃ¼rbÃ¼lansla hÄ±zlanÄ±r, halkalar hÄ±zlÄ± dÃ¶ner
 *   SPEAKING   â†’ ttsLevel ile ritimli pulse (ses senkron)
 * Backend/state mantÄ±ÄŸÄ±na dokunmaz; sadece getState() ile okur.
 */
export function ParticleSphere() {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const mount = ref.current;
    if (!mount) return;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0x000000, 0);
    mount.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
    camera.position.set(0, 0, 8.5);

    const disposables: { dispose: () => void }[] = [];
    const T = <X extends { dispose: () => void }>(d: X): X => (disposables.push(d), d);

    // ---- renk paleti (referans gÃ¶rsel) ----
    const RED = new THREE.Color("#ff1a1a");
    const RED2 = new THREE.Color("#ff3b3b");
    const WHITE = new THREE.Color("#f2f2f2");

    // ------------------------------------------------------------------
    // 1) PartikÃ¼l kÃ¼resi â€” ~3800 partikÃ¼l, kÄ±rmÄ±zÄ± + beyaz karÄ±ÅŸÄ±m
    // ------------------------------------------------------------------
    const RADIUS = 2.25;
    const N = 7200;
    const positions = new Float32Array(N * 3);
    const colors = new Float32Array(N * 3);
    const dirs = new Float32Array(N * 3); // birim yÃ¶n vektÃ¶rleri (pulse iÃ§in)
    const seeds = new Float32Array(N);    // partikÃ¼l baÅŸÄ±na faz
    for (let i = 0; i < N; i++) {
      // dÃ¼zgÃ¼n kÃ¼re daÄŸÄ±lÄ±mÄ±
      const u = Math.random();
      const v = Math.random();
      const th = u * Math.PI * 2;
      const ph = Math.acos(2 * v - 1);
      const sx = Math.sin(ph) * Math.cos(th);
      const sy = Math.sin(ph) * Math.sin(th);
      const sz = Math.cos(ph);
      // kabuk kalÄ±nlÄ±ÄŸÄ± iÃ§in hafif radyal jitter
      const r = RADIUS * (0.84 + Math.random() * 0.20);
      dirs[i * 3] = sx;
      dirs[i * 3 + 1] = sy;
      dirs[i * 3 + 2] = sz;
      positions[i * 3] = sx * r;
      positions[i * 3 + 1] = sy * r;
      positions[i * 3 + 2] = sz * r;
      seeds[i] = Math.random() * Math.PI * 2;
      // ~%78 kÄ±rmÄ±zÄ± tonlarÄ±, ~%22 beyaz "parÄ±ltÄ±" partikÃ¼lleri
      const c = Math.random() < 0.22 ? WHITE : (Math.random() < 0.5 ? RED : RED2);
      colors[i * 3] = c.r;
      colors[i * 3 + 1] = c.g;
      colors[i * 3 + 2] = c.b;
    }
    const geo = T(new THREE.BufferGeometry());
    geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geo.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    const sprite = makeDiscTexture();
    const pMat = T(new THREE.PointsMaterial({
      size: 0.04,
      map: sprite,
      vertexColors: true,
      transparent: true,
      opacity: 0.95,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      sizeAttenuation: true,
    }));
    disposables.push(sprite);
    const points = new THREE.Points(geo, pMat);
    const core = new THREE.Group();
    core.add(points);
    scene.add(core);

    // iÃ§ "Ã§ekirdek" Ä±ÅŸÄ±masÄ± (merkez Ã¼Ã§genin arkasÄ±nda kÄ±zÄ±l hale)
    const glowTex = makeGlowTexture();
    disposables.push(glowTex);
    const glowMat = T(new THREE.SpriteMaterial({ map: glowTex, color: RED, transparent: true, opacity: 0.55, blending: THREE.AdditiveBlending, depthWrite: false }));
    const glow = new THREE.Sprite(glowMat);
    glow.scale.setScalar(1.9);
    scene.add(glow);

    // ------------------------------------------------------------------
    // resize
    // ------------------------------------------------------------------
    const resize = () => {
      const w = mount.clientWidth || 1;
      const h = mount.clientHeight || 1;
      renderer.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(mount);

    // ------------------------------------------------------------------
    // animasyon dÃ¶ngÃ¼sÃ¼
    // ------------------------------------------------------------------
    const clock = new THREE.Clock();
    const posAttr = geo.getAttribute("position") as THREE.BufferAttribute;
    const arr = posAttr.array as Float32Array;
    let curScale = 1;
    let curTurb = 0;
    let raf = 0;

    const loop = () => {
      raf = requestAnimationFrame(loop);
      const dt = Math.min(clock.getDelta(), 0.05);
      const t = clock.elapsedTime;
      const st = getState();
      const state = st.agentState;
      const alert = state === "ERROR";
      const listening = state === "LISTENING" || st.mic === "listening";
      const thinking = ["THINKING", "PLANNING", "EXECUTING", "VERIFYING"].includes(state);
      const speaking = st.ttsSpeaking;
      const amp = speaking ? ttsLevel.current : 0;

      // tema kÄ±zÄ±lÄ±nÄ± uygula (varsayÄ±lan CRIMSON â†’ #ff1e42'e yakÄ±n)
      const th = resolveTheme(st.theme, alert);
      RED.set(th.red);
      glowMat.color.copy(RED);

      // hedef Ã¶lÃ§ek (LISTENING nefes alÄ±r, SPEAKING sesle pulse eder)
      let targetScale = 1;
      if (listening) targetScale = 1.08 + Math.sin(t * 2.4) * 0.09;
      else if (speaking) targetScale = 1.02 + amp * 0.22;
      else if (alert) targetScale = 1.05 + Math.sin(t * 8) * 0.05;
      else targetScale = 1 + Math.sin(t * 0.9) * 0.02; // IDLE nazik nefes
      curScale += (targetScale - curScale) * Math.min(1, dt * 6);
      core.scale.setScalar(curScale);

      // dÃ¶nÃ¼ÅŸ hÄ±zÄ± â€” THINKING hÄ±zlanÄ±r
      const rot = thinking ? 0.5 : alert ? 0.9 : listening ? 0.18 : 0.07;
      core.rotation.y += dt * rot;
      core.rotation.x = Math.sin(t * 0.12) * 0.12;

      // partikÃ¼l tÃ¼rbÃ¼lansÄ± â€” THINKING'te belirginleÅŸir
      const targetTurb = thinking ? 0.22 : alert ? 0.32 : listening ? 0.09 : 0.035;
      curTurb += (targetTurb - curTurb) * Math.min(1, dt * 4);
      const freq = thinking ? 3.4 : 1.4;
      for (let i = 0; i < N; i++) {
        const wob = 1 + Math.sin(t * freq + seeds[i]) * curTurb + amp * 0.12;
        const r = RADIUS * wob;
        arr[i * 3] = dirs[i * 3] * r;
        arr[i * 3 + 1] = dirs[i * 3 + 1] * r;
        arr[i * 3 + 2] = dirs[i * 3 + 2] * r;
      }
      posAttr.needsUpdate = true;

      // partikÃ¼l parlaklÄ±ÄŸÄ±
      pMat.opacity = 0.8 + (listening ? 0.15 : 0) + amp * 0.25 + (thinking ? 0.1 : 0);
      pMat.size = 0.05 + amp * 0.02 + (listening ? 0.008 : 0);

      // halkalar

      // merkez Ä±ÅŸÄ±masÄ± nefes alÄ±r + sesle titreÅŸir
      glowMat.opacity = 0.22 + 0.08 * Math.sin(t * 1.6) + amp * 0.42 + (thinking ? 0.10 : 0);
      glow.scale.setScalar(1.78 + Math.sin(t * 1.3) * 0.10 + amp * 0.42);

      renderer.render(scene, camera);
    };
    raf = requestAnimationFrame(loop);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      disposables.forEach((d) => d.dispose());
      renderer.dispose();
      if (renderer.domElement.parentElement === mount) mount.removeChild(renderer.domElement);
    };
  }, []);

  return (
    <div className="sphere-stage">
      <div ref={ref} className="sphere-canvas" />
      {/* Merkez ULTRON Ã¼Ã§geni â€” kesin/keskin gÃ¶rÃ¼ntÃ¼ iÃ§in SVG overlay */}
      <div className="sphere-logo" aria-hidden>
        <svg viewBox="0 0 120 108" width="108" height="98">
          <defs>
            <linearGradient id="ultri" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="#ff6a6a" />
              <stop offset="0.55" stopColor="#ff1a1a" />
              <stop offset="1" stopColor="#8a0000" />
            </linearGradient>
          </defs>
          {/* ana Ã¼Ã§gen */}
          <path d="M60 6 L112 100 L8 100 Z" fill="url(#ultri)" opacity="0.95" />
          {/* iÃ§ negatif kesim (Ultron delta) */}
          <path d="M60 30 L92 92 L58 92 L74 62 Z" fill="#000000" opacity="0.92" />
          <path d="M60 30 L28 92 L50 92 L60 54 Z" fill="#000000" opacity="0.55" />
        </svg>
      </div>
    </div>
  );
}

/** YumuÅŸak kenarlÄ± yuvarlak partikÃ¼l dokusu (kare piksel yerine parÄ±ltÄ±). */
function makeDiscTexture(): THREE.Texture {
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const g = c.getContext("2d")!;
  const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  grad.addColorStop(0, "rgba(255,255,255,1)");
  grad.addColorStop(0.35, "rgba(255,255,255,0.85)");
  grad.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grad;
  g.beginPath();
  g.arc(32, 32, 32, 0, Math.PI * 2);
  g.fill();
  const tex = new THREE.CanvasTexture(c);
  tex.needsUpdate = true;
  return tex;
}

/** Merkez kÄ±zÄ±l hale dokusu. */
function makeGlowTexture(): THREE.Texture {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d")!;
  const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grad.addColorStop(0, "rgba(255,255,255,0.9)");
  grad.addColorStop(0.35, "rgba(255,40,60,0.5)");
  grad.addColorStop(1, "rgba(255,40,60,0)");
  g.fillStyle = grad;
  g.fillRect(0, 0, 128, 128);
  const tex = new THREE.CanvasTexture(c);
  tex.needsUpdate = true;
  return tex;
}




