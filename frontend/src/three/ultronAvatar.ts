/**
 * ULTRON 3D avatar.
 * - Procedural holographic robot head (always available, the fallback).
 * - Optional GLB/glTF drop-in via /models/manifest.json → AnimationMixer,
 *   morph-target lip-sync, named head/jaw/eye node wiring.
 * Head micro-movement, gaze, blink and mouth are driven by the backend
 * agent state machine + real TTS amplitude (ttsLevel) / mic amplitude.
 */
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import type { AgentState } from "../lib/types";

export interface AvatarInput {
  state: AgentState;
  lip: number; // 0..1 real TTS amplitude
  mic: number; // 0..1 real microphone amplitude
  dt: number;
  t: number;
}

const clamp = (v: number, a: number, b: number) => Math.max(a, Math.min(b, v));

export class UltronAvatar {
  group = new THREE.Group();
  kind: "procedural" | "glb" = "procedural";

  private disposables: { dispose: () => void }[] = [];
  private head = new THREE.Group(); // procedural head pivot
  private eyeGroup = new THREE.Group();
  private jaw = new THREE.Group();
  private eyeMat!: THREE.MeshBasicMaterial;
  private eyeGlowMatL!: THREE.SpriteMaterial;
  private eyeGlowMatR!: THREE.SpriteMaterial;
  private mouthMat!: THREE.MeshBasicMaterial;
  private mouth!: THREE.Mesh;

  private mixer: THREE.AnimationMixer | null = null;
  private glbRoot: THREE.Object3D | null = null;
  private glbHead: THREE.Object3D | null = null;
  private glbJawNode: THREE.Object3D | null = null;
  private glbEyeMats: THREE.Material[] = [];
  private glbJawMorphs: { mesh: THREE.Mesh; idx: number }[] = [];

  // behaviour
  private yaw = 0;
  private pitch = 0;
  private blinkTimer = 2.5;
  private blink = 0;
  private nodT = -1;
  private shakeT = -1;
  private prevState: AgentState = "IDLE";
  private sacX = 0;
  private sacY = 0;
  private sacNext = 0;

  constructor(glowTex: THREE.Texture) {
    this.buildProcedural(glowTex);
    this.group.add(this.head);
  }

  private track<T extends { dispose: () => void }>(d: T): T {
    this.disposables.push(d);
    return d;
  }

  // ------------------------------------------------------------- procedural
  private buildProcedural(glowTex: THREE.Texture): void {
    const gun = () =>
      this.track(
        new THREE.MeshStandardMaterial({ color: 0x151a22, metalness: 0.92, roughness: 0.34, flatShading: true })
      );
    const dark = () =>
      this.track(
        new THREE.MeshStandardMaterial({ color: 0x0b0e14, metalness: 0.85, roughness: 0.5, flatShading: true })
      );

    const skull = new THREE.Mesh(this.track(new THREE.SphereGeometry(0.42, 24, 18)), gun());
    skull.scale.set(0.8, 1.0, 0.88);
    skull.position.y = 0.14;
    this.head.add(skull);

    const crest = new THREE.Mesh(this.track(new THREE.BoxGeometry(0.05, 0.16, 0.5)), dark());
    crest.position.set(0, 0.5, -0.02);
    this.head.add(crest);

    const face = new THREE.Mesh(this.track(new THREE.BoxGeometry(0.4, 0.34, 0.2)), gun());
    face.position.set(0, 0.02, 0.26);
    this.head.add(face);

    const brow = new THREE.Mesh(this.track(new THREE.BoxGeometry(0.36, 0.06, 0.1)), dark());
    brow.position.set(0, 0.2, 0.32);
    brow.rotation.x = -0.18;
    this.head.add(brow);

    for (const s of [-1, 1]) {
      const cheek = new THREE.Mesh(this.track(new THREE.BoxGeometry(0.1, 0.22, 0.16)), dark());
      cheek.position.set(0.2 * s, -0.06, 0.22);
      cheek.rotation.y = -0.35 * s;
      this.head.add(cheek);
      const ear = new THREE.Mesh(this.track(new THREE.CylinderGeometry(0.09, 0.09, 0.05, 16)), gun());
      ear.rotation.z = Math.PI / 2;
      ear.position.set(0.36 * s, 0.08, 0);
      this.head.add(ear);
      const earGlow = new THREE.Mesh(
        this.track(new THREE.CylinderGeometry(0.045, 0.045, 0.06, 12)),
        this.track(new THREE.MeshBasicMaterial({ color: 0xff2438 }))
      );
      earGlow.rotation.z = Math.PI / 2;
      earGlow.position.set(0.365 * s, 0.08, 0);
      this.head.add(earGlow);
    }

    // eyes (emissive red, glow sprites)
    this.eyeMat = this.track(new THREE.MeshBasicMaterial({ color: 0xff2438 }));
    for (const s of [-1, 1]) {
      const eye = new THREE.Mesh(this.track(new THREE.SphereGeometry(0.05, 12, 10)), this.eyeMat);
      eye.scale.set(1.5, 0.7, 0.5);
      eye.position.set(0.11 * s, 0.12, 0.36);
      this.eyeGroup.add(eye);
      const gm = this.track(
        new THREE.SpriteMaterial({ map: glowTex, color: 0xff2438, transparent: true, opacity: 0.6, blending: THREE.AdditiveBlending, depthWrite: false })
      );
      if (s < 0) this.eyeGlowMatL = gm;
      else this.eyeGlowMatR = gm;
      const spr = new THREE.Sprite(gm);
      spr.scale.setScalar(0.34);
      spr.position.copy(eye.position);
      this.eyeGroup.add(spr);
    }
    this.head.add(this.eyeGroup);

    // jaw pivot (mouth)
    this.jaw.position.set(0, -0.1, 0.14);
    const chin = new THREE.Mesh(this.track(new THREE.BoxGeometry(0.3, 0.12, 0.22)), gun());
    chin.position.set(0, -0.1, 0.16);
    this.jaw.add(chin);
    this.mouthMat = this.track(
      new THREE.MeshBasicMaterial({ color: 0xff2438, transparent: true, opacity: 0.35, blending: THREE.AdditiveBlending, depthWrite: false })
    );
    this.mouth = new THREE.Mesh(this.track(new THREE.BoxGeometry(0.22, 0.02, 0.05)), this.mouthMat);
    this.mouth.position.set(0, -0.02, 0.31);
    this.jaw.add(this.mouth);
    this.head.add(this.jaw);

    // neck
    const neck = new THREE.Mesh(this.track(new THREE.CylinderGeometry(0.11, 0.15, 0.3, 12)), dark());
    neck.position.y = -0.45;
    this.head.add(neck);
  }

  // ------------------------------------------------------------- GLB loader
  async tryLoadGLB(url: string): Promise<boolean> {
    return new Promise<boolean>((resolve) => {
      const loader = new GLTFLoader();
      loader.load(
        url,
        (gltf) => {
          const model = gltf.scene;
          const box = new THREE.Box3().setFromObject(model);
          const size = box.getSize(new THREE.Vector3());
          const center = box.getCenter(new THREE.Vector3());
          const s = 1.25 / Math.max(size.y, 1e-6);
          model.scale.setScalar(s);
          model.position.sub(center.multiplyScalar(s));

          this.glbHead = null;
          model.traverse((o) => {
            if (/head/i.test(o.name) && !this.glbHead) this.glbHead = o;
            if (/jaw|mandible/i.test(o.name) && !this.glbJawNode) this.glbJawNode = o;
            const mesh = o as THREE.Mesh;
            if (mesh.isMesh) {
              const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
              for (const m of mats) {
                if (/eye/i.test(m.name) || /eye/i.test(mesh.name)) this.glbEyeMats.push(m);
              }
              if (mesh.morphTargetDictionary) {
                for (const [key, idx] of Object.entries(mesh.morphTargetDictionary)) {
                  if (/jaw|mouth|open/i.test(key)) this.glbJawMorphs.push({ mesh, idx });
                }
              }
            }
          });
          if (!this.glbHead) this.glbHead = model;
          if (gltf.animations.length > 0) {
            this.mixer = new THREE.AnimationMixer(model);
            this.mixer.clipAction(gltf.animations[0]).play();
          }
          this.glbRoot = model;
          this.group.add(model);
          this.head.visible = false; // procedural fallback retired
          this.kind = "glb";
          resolve(true);
        },
        undefined,
        () => {
          console.info("[ULTRON] model unavailable — procedural avatar active");
          resolve(false);
        }
      );
    });
  }

  /** Called by HoloHead3D/legacy avatar layer after checking /models/manifest.json */
  async loadFromManifest(): Promise<void> {
    try {
      const res = await fetch("/models/manifest.json", { cache: "no-store" });
      if (!res.ok) return;
      const manifest = (await res.json()) as { model?: string | null };
      if (manifest.model) await this.tryLoadGLB(`/models/${manifest.model}`);
    } catch {
      /* manifest unreadable — keep procedural */
    }
  }

  // ------------------------------------------------------------- behaviour
  update(inp: AvatarInput): void {
    const { state, lip, mic, dt, t } = inp;

    if (state !== this.prevState) {
      if (state === "DONE") this.nodT = 0;
      if (state === "ERROR") this.shakeT = 0;
      this.prevState = state;
    }
    if (this.nodT >= 0 && (this.nodT += dt) > 0.9) this.nodT = -1;
    if (this.shakeT >= 0 && (this.shakeT += dt) > 1.0) this.shakeT = -1;

    // gaze target per agent state (robotic but natural)
    let gy = 0;
    let gp = 0;
    switch (state) {
      case "IDLE":
        gy = Math.sin(t * 0.23) * 0.26;
        gp = Math.sin(t * 0.17 + 1.3) * 0.1;
        break;
      case "LISTENING":
        gy = Math.sin(t * 0.5) * 0.08;
        gp = -0.06 - mic * 0.06;
        break;
      case "THINKING":
        gy = -0.28 + Math.sin(t * 0.8) * 0.05;
        gp = -0.2;
        break;
      case "PLANNING":
        gy = Math.sin(t * 1.1) * 0.42;
        gp = -0.04;
        break;
      case "EXECUTING":
        gy = Math.sin(t * 31) * 0.007;
        gp = 0.02;
        break;
      case "VERIFYING":
        gy = Math.sin(t * 6) > 0 ? 0.22 : -0.22;
        break;
      case "DONE":
        gp = 0.04;
        break;
      case "ERROR":
        gp = 0.12;
        break;
    }
    if (t > this.sacNext) {
      this.sacNext = t + 0.5 + Math.random() * 1.8;
      this.sacX = (Math.random() - 0.5) * 0.09;
      this.sacY = (Math.random() - 0.5) * 0.05;
    }
    gy += this.sacX;
    gp += this.sacY;
    if (this.nodT >= 0) gp += Math.sin(this.nodT * 10) * 0.12 * Math.exp(-this.nodT * 3);
    if (this.shakeT >= 0) gy += Math.sin(this.shakeT * 28) * 0.22 * Math.exp(-this.shakeT * 4);

    const k = 1 - Math.pow(0.002, dt);
    this.yaw += (gy - this.yaw) * k;
    this.pitch += (gp - this.pitch) * k;

    const headNode = this.kind === "glb" && this.glbHead ? this.glbHead : this.head;
    headNode.rotation.y = this.yaw;
    headNode.rotation.x = this.pitch;
    this.eyeGroup.position.x = this.yaw * 0.05;
    this.eyeGroup.position.y = -this.pitch * 0.04;

    // blink
    this.blinkTimer -= dt;
    if (this.blinkTimer <= 0) {
      this.blinkTimer = 2 + Math.random() * 4;
      this.blink = 0.12;
    }
    if (this.blink > 0) this.blink -= dt;
    const blinkScale = this.blink > 0 ? 0.15 : 1;

    // eye energy per state
    let bright = 0.8;
    switch (state) {
      case "IDLE": bright = 0.7; break;
      case "LISTENING": bright = 1.25 + mic * 0.8; break;
      case "THINKING": bright = 1.0 + Math.sin(t * 23) * 0.2; break;
      case "PLANNING": bright = 1.0; break;
      case "EXECUTING": bright = 1.2; break;
      case "VERIFYING": bright = 1.2; break;
      case "DONE": bright = 1.6; break;
      case "ERROR": bright = 1.4 + Math.sin(t * 18) * 1.0; break;
    }
    const b = clamp(bright, 0.15, 2.2) * blinkScale;
    this.eyeMat.color.setRGB(clamp(0.95 * b, 0, 1), clamp(0.12 * b, 0, 1), clamp(0.16 * b, 0, 1));
    const glowOp = clamp(0.42 * b, 0, 1);
    this.eyeGlowMatL.opacity = glowOp;
    this.eyeGlowMatR.opacity = glowOp;
    for (const m of this.glbEyeMats) {
      const std = m as THREE.MeshStandardMaterial;
      if (std.emissive) {
        std.emissive.setRGB(1, 0.1, 0.14);
        std.emissiveIntensity = b;
      }
    }
    this.eyeGroup.children.forEach((c) => {
      if ((c as THREE.Mesh).isMesh) c.scale.y = 0.7 * blinkScale;
    });

    // mouth — driven ONLY by real TTS amplitude
    const open = clamp(lip, 0, 1);
    this.jaw.rotation.x = open * 0.38;
    this.mouth.scale.y = 1 + open * 7;
    this.mouthMat.opacity = 0.4 + 0.6 * open;
    for (const jm of this.glbJawMorphs) {
      if (jm.mesh.morphTargetInfluences) jm.mesh.morphTargetInfluences[jm.idx] = open;
    }
    if (this.glbJawNode) this.glbJawNode.rotation.x = open * 0.3;

    this.mixer?.update(dt);
  }

  dispose(): void {
    this.mixer?.stopAllAction();
    this.mixer = null;
    const kill = (root: THREE.Object3D) =>
      root.traverse((o) => {
        const mesh = o as THREE.Mesh;
        if (mesh.geometry) mesh.geometry.dispose();
        const mats = Array.isArray(mesh.material) ? mesh.material : mesh.material ? [mesh.material] : [];
        mats.forEach((m) => m.dispose());
      });
    if (this.glbRoot) kill(this.glbRoot);
    kill(this.head);
    this.disposables.forEach((d) => d.dispose());
  }
}
