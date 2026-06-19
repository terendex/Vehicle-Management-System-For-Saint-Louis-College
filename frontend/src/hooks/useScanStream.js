import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

const WS_BASE =
  (import.meta.env.VITE_API_URL || "http://localhost:8000").replace(
    "http",
    "ws"
  );

// ── Vehicle-type bounding box colours (matches VEHICLE_TYPE_META in the UI) ──
const TRACK_COLORS = {
  license_plate: "#00ff88",   // green  — plate detections
  vehicle:       "#00ff88",   // green  — car/jeep/bus body
  motor:         "#3b82f6",   // blue   — motorcycle/tricycle
  ebike:         "#8b5cf6",   // purple
  escooter:      "#10b981",   // emerald
  _default:      "#facc15",   // yellow — unknown / future classes
};

const VEHICLE_TYPE_LABELS = {
  ebike:   "E-Bike",
  escooter: "E-Scooter",
};

function getTrackColor(track) {
  if (track.vehicle_type) return TRACK_COLORS[track.vehicle_type] ?? TRACK_COLORS._default;
  if (track.class_name)   return TRACK_COLORS[track.class_name]   ?? TRACK_COLORS._default;
  return TRACK_COLORS._default;
}

export function useScanStream(token, cameraOn) {
  const [scanning, setScanning] = useState(false);
  const [connected, setConnected] = useState(false);
  const [results, setResults] = useState([]);
  const [flash, setFlash] = useState(false);
  const [activeTracks, setActiveTracks] = useState([]);
  const [idRequiredTracks, setIdRequiredTracks] = useState({});
  const wsRef = useRef(null);
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const animationRef = useRef(null);
  const frameCounterRef = useRef(0);

  // Track IDs that have already triggered an id_required modal this session,
  // so we don't re-open the modal every frame for the same detected vehicle.
  const promptedTrackIds = useRef(new Set());

  const drawOverlays = useCallback((tracks) => {
    const canvas = canvasRef.current;
    const webcam = videoRef.current;
    if (!canvas || !webcam || !tracks?.length) return;

    let video = null;
    if (typeof webcam.getVideo === "function") {
      video = webcam.getVideo();
    }
    if (!video) {
      video = webcam.video;
    }

    const ctx = canvas.getContext("2d");
    const vw = video?.videoWidth  || webcam.clientWidth  || 640;
    const vh = video?.videoHeight || webcam.clientHeight || 480;

    canvas.width  = vw;
    canvas.height = vh;
    ctx.clearRect(0, 0, vw, vh);
    ctx.font      = "12px 'Courier New', monospace";
    ctx.textBaseline = "top";
    ctx.textAlign    = "left";

    tracks.forEach((track) => {
      // ── Resolve pixel coordinates ───────────────────────────────────────────
      let px, py, pw, ph;
      if (track.bbox[0] > 1 || track.bbox[1] > 1) {
        // Already in pixel coords (absolute)
        px = track.bbox[0];
        py = track.bbox[1];
        pw = track.bbox[2];
        ph = track.bbox[3];
      } else {
        // Normalised 0–1
        px = track.bbox[0] * vw;
        py = track.bbox[1] * vh;
        pw = track.bbox[2] * vw;
        ph = track.bbox[3] * vh;
      }

      const color = getTrackColor(track);

      // ── Bounding box ────────────────────────────────────────────────────────
      ctx.strokeStyle = color;
      ctx.lineWidth   = track.vehicle_type ? 3 : 2;  // thicker for unplated

      // Dashed box for unplated vehicles — visually distinct from plate boxes
      if (track.vehicle_type) {
        ctx.setLineDash([8, 4]);
      } else {
        ctx.setLineDash([]);
      }
      ctx.strokeRect(px, py, pw, ph);
      ctx.setLineDash([]);  // reset

      // ── Label text ──────────────────────────────────────────────────────────
      let labelText;
      if (track.vehicle_type) {
        const typeLabel = VEHICLE_TYPE_LABELS[track.vehicle_type] ?? track.vehicle_type;
        const confText  = track.detection_conf
          ? ` ${(track.detection_conf * 100).toFixed(0)}%`
          : "";
        labelText = `${typeLabel}${confText}`;
      } else {
        const plateText = track.plate_text || `Track #${track.track_id}`;
        const confText  = track.detection_conf
          ? ` | ${(track.detection_conf * 100).toFixed(0)}%`
          : "";
        labelText = `${plateText}${confText}`;
      }

      const PADDING = 6;
      const TEXT_H  = 22;
      const textWidth = ctx.measureText(labelText).width + PADDING * 2;

      // Label background
      ctx.fillStyle = "rgba(0, 0, 0, 0.75)";
      ctx.fillRect(px, py - TEXT_H, textWidth, TEXT_H);

      // Label border strip in track colour
      ctx.fillStyle = color;
      ctx.fillRect(px, py - TEXT_H, 3, TEXT_H);

      // Label text
      ctx.fillStyle = "#ffffff";
      ctx.fillText(labelText, px + PADDING + 2, py - TEXT_H + 4);

      // ── "ID Required" badge ─────────────────────────────────────────────────
      if (track.vehicle_type) {
        const badge    = "⚠ ID Required";
        const badgeW   = ctx.measureText(badge).width + PADDING * 2;
        const badgeY   = py + ph + 2;
        ctx.fillStyle  = "rgba(239,68,68,0.85)";
        ctx.fillRect(px, badgeY, badgeW, TEXT_H);
        ctx.fillStyle  = "#ffffff";
        ctx.fillText(badge, px + PADDING, badgeY + 4);
      }
    });
  }, []);

  useEffect(() => {
    if (!cameraOn || !token) return;

    let isCancelled = false;
    setScanning(true);
    setResults([]);
    // Reset prompted-track memory on each camera session
    promptedTrackIds.current = new Set();

    const socket = new WebSocket(`${WS_BASE}/ws/scan/live/?token=${token}`);
    wsRef.current = socket;

    socket.onopen = () => {
      console.log("[WS] Connected");
      setConnected(true);
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
          setActiveTracks((prev) =>
            prev.map((t) =>
              t.track_id === msg.track_id
                ? { ...t, plate_text: msg.plate_text }
                : t
            )
          );
        }

        if (msg.type === "id_required") {
          const trackId = msg.track_id;

          // Only surface the modal once per track per camera session
          if (promptedTrackIds.current.has(trackId)) return;
          promptedTrackIds.current.add(trackId);

          setIdRequiredTracks((prev) => ({
            ...prev,
            [trackId]: {
              vehicle_type: msg.vehicle_type,
              message:      msg.message,
            },
          }));
        }

        if (msg.type === "result" && msg.results && !isCancelled) {
          setFlash(true);
          setTimeout(() => setFlash(false), 450);
          setResults(msg.results);
          msg.results.forEach((r) => {
            if (r.vehicle) {
              const k = r.plate_number;
              if (k && !localStorage.getItem(`vehicle:${k}`)) {
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
      if (!isCancelled) {
        setScanning(false);
        setConnected(false);
      }
    };

    const sendFrame = () => {
      if (isCancelled) return;

      const webcam = videoRef.current;
      const ws     = wsRef.current;

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
        if (typeof webcam.getScreenshot === "function") {
          base64 = webcam.getScreenshot({ width: 1280, height: 720 });
        }
        if (!base64 && webcam.video) {
          const canvas = document.createElement("canvas");
          canvas.width  = 1280;
          canvas.height = 720;
          canvas.getContext("2d").drawImage(webcam.video, 0, 0, 1280, 720);
          base64 = canvas.toDataURL("image/jpeg", 0.85);
        }
        if (!base64) {
          console.log("[WS] Screenshot returned null");
          animationRef.current = requestAnimationFrame(sendFrame);
          return;
        }
        const jpegBase64 = base64.split(",")[1];
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

  /**
   * Dismiss a track from the id_required state after its modal is handled.
   * Call this from SecurityEntryManagement after verify/close.
   */
  const dismissIdRequired = useCallback((trackId) => {
    setIdRequiredTracks((prev) => {
      const next = { ...prev };
      delete next[trackId];
      return next;
    });
  }, []);

  return {
    scanning,
    connected,
    results,
    flash,
    activeTracks,
    idRequiredTracks,
    videoRef,
    canvasRef,
    dismissIdRequired,
  };
}