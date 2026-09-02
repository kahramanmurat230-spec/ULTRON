import * as THREE from "three";

export interface HolographicEngineOptions {
  maxPixelRatio?: number;
  clearColor?: number;
}

/** Lightweight Three.js host for the Holographic Computing surface.
 * Owns renderer/scene/camera lifecycle; domain data stays outside the engine.
 */
export class HolographicEngine {
  readonly scene = new THREE.Scene();
  readonly camera = new THREE.PerspectiveCamera(50, 1, 0.1, 200);
  readonly renderer: THREE.WebGLRenderer;

  private readonly mount: HTMLElement;
  private readonly resizeObserver: ResizeObserver;
  private frame = 0;
  private running = false;
  private last = performance.now();

  constructor(mount: HTMLElement, options: HolographicEngineOptions = {}) {
    this.mount = mount;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, options.maxPixelRatio ?? 1.75));
    this.renderer.setClearColor(options.clearColor ?? 0x000000, 0);
    this.camera.position.set(0, 0, 8);
    mount.appendChild(this.renderer.domElement);

    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(mount);
    this.resize();
  }

  start(render: (deltaSeconds: number, elapsedSeconds: number) => void): void {
    if (this.running) return;
    this.running = true;
    this.last = performance.now();
    const tick = (now: number) => {
      if (!this.running) return;
      const delta = Math.min((now - this.last) / 1000, 0.05);
      this.last = now;
      render(delta, now / 1000);
      this.renderer.render(this.scene, this.camera);
      this.frame = requestAnimationFrame(tick);
    };
    this.frame = requestAnimationFrame(tick);
  }

  resize(): void {
    const width = Math.max(1, this.mount.clientWidth);
    const height = Math.max(1, this.mount.clientHeight);
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
  }

  stop(): void {
    this.running = false;
    cancelAnimationFrame(this.frame);
  }

  dispose(): void {
    this.stop();
    this.resizeObserver.disconnect();
    this.scene.traverse((object) => {
      const mesh = object as THREE.Mesh;
      if (mesh.geometry) mesh.geometry.dispose();
      const material = mesh.material;
      if (Array.isArray(material)) material.forEach((m) => m.dispose());
      else if (material) material.dispose();
    });
    this.renderer.dispose();
    if (this.renderer.domElement.parentElement === this.mount) this.mount.removeChild(this.renderer.domElement);
  }
}
