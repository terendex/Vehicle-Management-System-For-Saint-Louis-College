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
    const video = videoRef.current;
    if (!canvas || !video || !tracks?.length) return;

    const ctx = canvas.getContext("2d");
    const vw = video.videoWidth || video.offsetWidth;
    const vh = video.videoHeight || video.offsetHeight;

    canvas.width = vw;
    canvas.height = vh;

    ctx.clearRect(0, 0, vw, vh);
    ctx.font = "16px 'Courier New', monospace";
    ctx.textBaseline = "top";
    ctx.textAlign = "left";

    tracks.forEach((track) => {
      const [x, y, w, h] = track.bbox;
      const px = x * vw;
      const py = y * vh;
      const pw = w * vw;
      const ph = h * vh;

      ctx.strokeStyle = "#00ff88";
      ctx.lineWidth = 3;
      ctx.strokeRect(px, py, pw, ph);

      ctx.fillStyle = "rgba(0, 0, 0, 0.7)";
      const text = track.plate_text || `Track #${track.track_id}`;
      const textWidth = ctx.measureText(text).width + 12;
      const textHeight = 24;
      ctx.fillRect(px, py - textHeight, textWidth, textHeight);

      ctx.fillStyle = "#00ff88";
      ctx.fillText(text, px + 6, py - textHeight + 4);
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

    socket.onerror = () => toast.error("WebSocket error");
    socket.onclose = () => {
      console.log("[WS] Disconnected");
    };

    const sendFrame = () => {
      if (isCancelled) return;
      
      const video = videoRef.current;
      const ws = wsRef.current;

      if (!video || !ws || ws.readyState !== WebSocket.OPEN) {
        animationRef.current = requestAnimationFrame(sendFrame);
        return;
      }

      frameCounterRef.current++;

      if (video.readyState < 2) {
        animationRef.current = requestAnimationFrame(sendFrame);
        return;
      }

      const canvas = document.createElement("canvas");
      canvas.width = video.videoWidth || 640;
      canvas.height = video.videoHeight || 480;
      const ctx = canvas.getContext("2d");
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

      const base64 = canvas.toDataURL("image/jpeg", 0.85).split(",")[1];
      if (base64 && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "frame", image_b64: base64 }));
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