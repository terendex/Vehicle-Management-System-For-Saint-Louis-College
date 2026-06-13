import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

const WS_BASE =
  (import.meta.env.VITE_API_URL || "http://localhost:8000").replace(
    "http",
    "ws"
  );

export function useScanStream(token, cameraOn) {
  const [scanning, setScanning] = useState(false);
  const [results, setResults] = useState([]);
  const [flash, setFlash] = useState(false);
  const [activeTracks, setActiveTracks] = useState([]);
  const wsRef = useRef(null);
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const animationRef = useRef(null);
  const frameCounterRef = useRef(0);

  const drawOverlays = useCallback((tracks) => {
    const canvas = canvasRef.current;
    const webcam = videoRef.current;
    if (!canvas || !webcam || !tracks?.length) return;

    let video = null;
    if (typeof webcam.getVideo === 'function') {
      video = webcam.getVideo();
    }
    if (!video) {
      video = webcam.video;
    }
    
    const ctx = canvas.getContext("2d");
    const vw = video?.videoWidth || webcam.clientWidth || 640;
    const vh = video?.videoHeight || webcam.clientHeight || 480;

    canvas.width = vw;
    canvas.height = vh;

    ctx.clearRect(0, 0, vw, vh);
    ctx.font = "12px 'Courier New', monospace";
    ctx.textBaseline = "top";
    ctx.textAlign = "left";

    tracks.forEach((track) => {
      let px, py, pw, ph;
      if (track.bbox[0] > 1 || track.bbox[1] > 1) {
        px = track.bbox[0];
        py = track.bbox[1];
        pw = track.bbox[2];
        ph = track.bbox[3];
      } else {
        px = track.bbox[0] * vw;
        py = track.bbox[1] * vh;
        pw = track.bbox[2] * vw;
        ph = track.bbox[3] * vh;
      }

      ctx.strokeStyle = "#00ff88";
      ctx.lineWidth = 3;
      ctx.strokeRect(px, py, pw, ph);

      const text = track.plate_text || `Track #${track.track_id}`;
      const confText = track.detection_conf ? `conf: ${(track.detection_conf * 100).toFixed(0)}%` : "";
      const displayText = `${text}${confText ? ' | ' + confText : ''}`;
      ctx.fillStyle = "rgba(0, 0, 0, 0.8)";
      const textWidth = ctx.measureText(displayText).width + 12;
      const textHeight = 22;
      ctx.fillRect(px, py - textHeight, textWidth, textHeight);

      ctx.fillStyle = "#00ff88";
      ctx.fillText(displayText, px + 6, py - textHeight + 4);
    });
  }, []);

  useEffect(() => {
    if (!cameraOn || !token) return;

    let isCancelled = false;
    setScanning(true);
    setResults([]);

    const socket = new WebSocket(
      `${WS_BASE}/ws/scan/live/?token=${token}`
    );
    wsRef.current = socket;

    socket.onopen = () => {
      console.log("[WS] Connected");
    };

    socket.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        console.log("[WS] Received:", msg.type, msg);
        if (msg.type === "error") {
          toast.error(msg.message);
          return;
        }
        if (msg.type === "tracks" && msg.tracks) {
          setActiveTracks(msg.tracks);
          drawOverlays(msg.tracks);
        }
        if (msg.type === "ocr_update") {
          setActiveTracks(prev => prev.map(t =>
            t.track_id === msg.track_id
              ? { ...t, plate_text: msg.plate_text }
              : t
          ));
        }
        if (msg.type === "result" && msg.results && !isCancelled) {
          setFlash(true);
          setTimeout(() => setFlash(false), 450);
          setResults(msg.results);
          msg.results.forEach((r) => {
            if (r.vehicle) {
              const k = r.plate_number;
              if (!localStorage.getItem(`vehicle:${k}`)) {
                localStorage.setItem(`vehicle:${k}`, JSON.stringify(r.vehicle));
              }
            }
          });
        }
      } catch (e) {
        console.error("[WS] Parse error", e);
      }
    };

    socket.onerror = (err) => {
      console.error("[WS] Error:", err);
      toast.error("WebSocket error");
    };
    socket.onclose = (event) => {
      console.log("[WS] Disconnected:", event.code, event.reason);
    };

const sendFrame = () => {
      if (isCancelled) return;
      
      const webcam = videoRef.current;
      const ws = wsRef.current;

      if (!webcam || !ws || ws.readyState !== WebSocket.OPEN) {
        animationRef.current = requestAnimationFrame(sendFrame);
        return;
      }

      frameCounterRef.current++;

      if (frameCounterRef.current % 30 !== 0) {
        animationRef.current = requestAnimationFrame(sendFrame);
        return;
      }

      console.log(`[WS] Capturing frame ${frameCounterRef.current}`);

      try {
        let base64 = null;
        if (typeof webcam.getScreenshot === 'function') {
          base64 = webcam.getScreenshot({ width: 640, height: 480 });
        }
        if (!base64 && webcam.video) {
          const canvas = document.createElement("canvas");
          canvas.width = 640;
          canvas.height = 480;
          const ctx = canvas.getContext("2d");
          ctx.drawImage(webcam.video, 0, 0, 640, 480);
          base64 = canvas.toDataURL("image/jpeg", 0.85);
        }
        if (!base64) {
          console.log("[WS] Screenshot returned null");
          animationRef.current = requestAnimationFrame(sendFrame);
          return;
        }
        const jpegBase64 = base64.split(',')[1];
        if (jpegBase64 && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "frame", image_b64: jpegBase64 }));
        }
      } catch (e) {
        console.error("[WS] Screenshot failed:", e);
      }

      animationRef.current = requestAnimationFrame(sendFrame);
    };

    if (cameraOn) {
      sendFrame();
    }

    return () => {
      isCancelled = true;
      socket.close();
      wsRef.current = null;
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current);
      }
    };
}, [cameraOn, token, drawOverlays]);

  return {
    scanning,
    results,
    flash,
    activeTracks,
    videoRef,
    canvasRef,
  }
}