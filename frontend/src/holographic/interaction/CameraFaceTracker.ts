export interface FaceTrackingTarget {
  x: number;
  y: number;
  confidence: number;
  detected: boolean;
  timestamp: number;
}

export interface FaceTracker {
  start(video: HTMLVideoElement): Promise<boolean>;
  stop(): void;
  getTarget(): FaceTrackingTarget;
  isSupported(): boolean;
}

const target: FaceTrackingTarget = { x: 0, y: 0, confidence: 0, detected: false, timestamp: 0 };
let detector: { detect(video: HTMLVideoElement): Promise<Array<{ boundingBox: { x: number; y: number; width: number; height: number } }>> } | null = null;
let timer = 0;
let active = false;

function resetTarget(): void {
  target.x = 0;
  target.y = 0;
  target.confidence = 0;
  target.detected = false;
  target.timestamp = performance.now();
}

/** Browser-native face tracking adapter. Keeps vision optional and isolated from agents. */
export const cameraFaceTracker: FaceTracker = {
  isSupported(): boolean {
    return typeof window !== "undefined" && "FaceDetector" in window;
  },

  async start(video: HTMLVideoElement): Promise<boolean> {
    this.stop();
    if (!this.isSupported()) {
      resetTarget();
      return false;
    }

    const FaceDetectorCtor = (window as Window & {
      FaceDetector?: new (options?: { maxDetectedFaces?: number; fastMode?: boolean }) => typeof detector;
    }).FaceDetector;
    if (!FaceDetectorCtor) return false;

    detector = new FaceDetectorCtor({ maxDetectedFaces: 1, fastMode: true });
    active = true;

    const tick = async () => {
      if (!active || !detector) return;
      try {
        if (video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA && video.videoWidth > 0) {
          const faces = await detector.detect(video);
          const face = faces[0];
          if (face) {
            const box = face.boundingBox;
            const cx = (box.x + box.width / 2) / video.videoWidth;
            const cy = (box.y + box.height / 2) / video.videoHeight;
            target.x = Math.max(-1, Math.min(1, (cx - 0.5) * 2));
            target.y = Math.max(-1, Math.min(1, (cy - 0.5) * 2));
            target.confidence = 1;
            target.detected = true;
            target.timestamp = performance.now();
          } else {
            target.confidence *= 0.8;
            if (target.confidence < 0.05) resetTarget();
          }
        }
      } catch {
        // Vision is optional; never let camera tracking break the render loop.
      }
      if (active) timer = window.setTimeout(() => void tick(), 50);
    };

    void tick();
    return true;
  },

  stop(): void {
    active = false;
    if (timer) window.clearTimeout(timer);
    timer = 0;
    detector = null;
    resetTarget();
  },

  getTarget(): FaceTrackingTarget {
    return { ...target };
  },
};
