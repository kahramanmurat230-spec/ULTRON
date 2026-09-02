import { useEffect } from "react";
import { cameraFaceTracker } from "./CameraFaceTracker";

/** Bridges the workspace camera element to the optional face-tracking adapter. */
export function CameraTrackingLayer() {
  useEffect(() => {
    let stopped = false;
    let timer = 0;

    const connect = async () => {
      const video = document.getElementById("ultron-holo-camera") as HTMLVideoElement | null;
      if (!video) {
        timer = window.setTimeout(() => void connect(), 250);
        return;
      }
      if (video.srcObject && video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) {
        await cameraFaceTracker.start(video);
        return;
      }
      timer = window.setTimeout(() => void connect(), 250);
    };

    void connect();
    return () => {
      stopped = true;
      if (timer) window.clearTimeout(timer);
      cameraFaceTracker.stop();
      void stopped;
    };
  }, []);

  return null;
}
