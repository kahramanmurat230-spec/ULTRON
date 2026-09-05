import * as THREE from "three";
import type { SystemSnapshot } from "../../lib/types";

/** Lightweight live telemetry bars for the holographic scene. */
export class TelemetryVisualizer {
  readonly group = new THREE.Group();
  private bars = new Map<string, THREE.Mesh>();
  private values = new Map<string, number>();

  constructor() {
    const labels = ["CPU", "RAM", "GPU", "DISK"];
    labels.forEach((label, index) => {
      const mesh = new THREE.Mesh(
        new THREE.BoxGeometry(0.08, 1, 0.08),
        new THREE.MeshBasicMaterial({ color: 0x38e1ff, transparent: true, opacity: 0.45, blending: THREE.AdditiveBlending, depthWrite: false }),
      );
      mesh.position.set(-0.18 + index * 0.12, 0, 0);
      this.group.add(mesh);
      this.bars.set(label, mesh);
      this.values.set(label, 0);
    });
  }

  update(snapshot: SystemSnapshot | null, delta: number): void {
    const target = {
      CPU: snapshot?.cpu.percent ?? 0,
      RAM: snapshot?.ram.percent ?? 0,
      GPU: snapshot?.gpu?.util ?? 0,
      DISK: snapshot?.disk.percent ?? 0,
    };
    for (const [label, mesh] of this.bars) {
      const current = this.values.get(label) ?? 0;
      const next = THREE.MathUtils.lerp(current, target[label as keyof typeof target], Math.min(1, delta * 6));
      this.values.set(label, next);
      mesh.scale.y = 0.15 + next / 100;
      mesh.position.y = mesh.scale.y * 0.5 - 0.5;
      (mesh.material as THREE.MeshBasicMaterial).opacity = 0.25 + next / 180;
    }
  }

  dispose(): void {
    this.group.traverse((object) => {
      const mesh = object as THREE.Mesh;
      mesh.geometry?.dispose();
      const material = mesh.material;
      if (Array.isArray(material)) material.forEach((item) => item.dispose()); else material?.dispose();
    });
  }
}