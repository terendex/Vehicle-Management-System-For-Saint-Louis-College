import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

const WS_BASE =
  (import.meta.env.VITE_API_URL || "http://127.0.0.1:8000").replace("http", "ws");

// ── Bounding box colours ───────────────────────────────────────────────────────
const TRACK_COLORS = {
  license_plate: "#00ff88",   // green  — plate detections
  vehicle:       "#00ff88",   // green  — car/jeep/bus body
  motor:         "#3b82f6",   // blue   — motorcycle/tricycle
  ebike:         "#8b5cf6",   // purple
  escooter:      "#10b981",   // emerald
  _default:      "#facc15",   // yellow — unknown / future classes
};

const VEHICLE_TYPE_LABELS = {
  ebike:    "E-Bike",
  escooter: "E-Scooter",
};

function getTrackColor(track) {
  if (track.vehicle_type) return TRACK_COLORS[track.vehicle_type] ?? TRACK_COLORS._default;
  if (track.class_name)   return TRACK_COLORS[track.class_name]   ?? TRACK_COLORS._default;
  return TRACK_COLORS._default;
}

// How fast the rendered box chases the backend target per 60fps frame.
// 0.25 → smooth, ~200ms to close 95% of the gap.
// 0.4  → snappier, ~120ms.
const LERP = 0.25;

export function useScanStream(token, cameraOn) {
  const [scanning, setScanning]               = useState(false);
  const [connected, setConnected]             = useState(false);
  const [results, setResults]                 = useState([]);
  const [flash, setFlash]                     = useState(false);
  const [activeTracks, setActiveTracks]       = useState([]);
  const [idRequiredTracks, setIdRequiredTracks] = useState({});

  const wsRef           = useRef(null);
  const videoRef        = useRef(null);
  const canvasRef       = useRef(null);
  const sendLoopRef     = useRef(null);   // rAF handle for the frame-sender
  const renderLoopRef   = useRef(null);   // rAF handle for the canvas renderer
  const frameCounterRef = useRef(0);
  const promptedTrackIds = useRef(new Set());

  // Mutable maps shared between the WS handler and the render loop.
  // Using refs (not state) so reads/writes are synchronous — no stale closures.
  const targetTracksRef = useRef(new Map()); // track_id → track object from backend
  const smoothBoxRef    = useRef(new Map()); // track_id → {x1,y1,x2,y2} interpolated

  // ── 60fps render loop ──────────────────────────────────────────────────────
  const startRenderLoop = useCallback(() => {
    if (renderLoopRef.current) return;

    const draw = () => {
      const canvas = canvasRef.current;
      const webcam = videoRef.current;

      if (canvas && webcam) {
        const video = typeof webcam.getVideo === "function"
          ? webcam.getVideo()
          : webcam.video;

        const vw = video?.videoWidth  || webcam.clientWidth  || 640;
        const vh = video?.videoHeight || webcam.clientHeight || 480;
        canvas.width  = vw;
        canvas.height = vh;

        const ctx = canvas.getContext("2d");
        ctx.clearRect(0, 0, vw, vh);

        const targets = targetTracksRef.current;
        const smooth  = smoothBoxRef.current;

        // Remove interpolated entries for tracks the backend no longer reports
        for (const tid of smooth.keys()) {
          if (!targets.has(tid)) smooth.delete(tid);
        }

        if (targets.size > 0) {
          ctx.font         = "12px 'Courier New', monospace";
          ctx.textBaseline = "top";
          ctx.textAlign    = "left";

          for (const [tid, track] of targets) {
            // bbox from backend is [x1_norm, y1_norm, x2_norm, y2_norm]
            const tx1 = track.bbox[0] * vw;
            const ty1 = track.bbox[1] * vh;
            const tx2 = track.bbox[2] * vw;
            const ty2 = track.bbox[3] * vh;

            // First appearance — snap to target so there's no slide-in from origin
            if (!smooth.has(tid)) {
              smooth.set(tid, { x1: tx1, y1: ty1, x2: tx2, y2: ty2 });
            }

            // Lerp each corner toward the target
            const s = smooth.get(tid);
            s.x1 += (tx1 - s.x1) * LERP;
            s.y1 += (ty1 - s.y1) * LERP;
            s.x2 += (tx2 - s.x2) * LERP;
            s.y2 += (ty2 - s.y2) * LERP;

            const px = s.x1;
            const py = s.y1;
            const pw = s.x2 - s.x1;   // width  = x2 - x1
            const ph = s.y2 - s.y1;   // height = y2 - y1

            const color = getTrackColor(track);

            // ── Bounding box ─────────────────────────────────────────────────
            ctx.strokeStyle = color;
            ctx.lineWidth   = track.vehicle_type ? 3 : 2;
            ctx.setLineDash(track.vehicle_type ? [8, 4] : []);
            ctx.strokeRect(px, py, pw, ph);
            ctx.setLineDash([]);

            // ── Label text ───────────────────────────────────────────────────
            let labelText;
            if (track.vehicle_type) {
              const typeLabel = VEHICLE_TYPE_LABELS[track.vehicle_type] ?? track.vehicle_type;
              const confText  = track.detection_conf
                ? ` ${(track.detection_conf * 100).toFixed(0)}%` : "";
              labelText = `${typeLabel}${confText}`;
            } else {
              const plateText = track.plate_text || `Track #${tid}`;
              const confText  = track.detection_conf
                ? ` | ${(track.detection_conf * 100).toFixed(0)}%` : "";
              labelText = `${plateText}${confText}`;
            }

            const PAD = 6;
            const TH  = 22;
            const tw  = ctx.measureText(labelText).width + PAD * 2;

            ctx.fillStyle = "rgba(0,0,0,0.75)";
            ctx.fillRect(px, py - TH, tw, TH);
            ctx.fillStyle = color;
            ctx.fillRect(px, py - TH, 3, TH);
            ctx.fillStyle = "#ffffff";
            ctx.fillText(labelText, px + PAD + 2, py - TH + 4);

            // ── "⚠ ID Required" badge ────────────────────────────────────────
            if (track.vehicle_type) {
              const badge  = "⚠ ID Required";
              const badgeW = ctx.measureText(badge).width + PAD * 2;
              const badgeY = py + ph + 2;
              ctx.fillStyle = "rgba(239,68,68,0.85)";
              ctx.fillRect(px, badgeY, badgeW, TH);
              ctx.fillStyle = "#ffffff";
              ctx.fillText(badge, px + PAD, badgeY + 4);
            }
          }
        }
      }

      renderLoopRef.current = requestAnimationFrame(draw);
    };

    renderLoopRef.current = requestAnimationFrame(draw);
  }, []);

  const stopRenderLoop = useCallback(() => {
    if (renderLoopRef.current) {
      cancelAnimationFrame(renderLoopRef.current);
      renderLoopRef.current = null;
    }
    const canvas = canvasRef.current;
    if (canvas) canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
    targetTracksRef.current = new Map();
    smoothBoxRef.current    = new Map();
  }, []);

  // ── WebSocket + frame-sender lifecycle ─────────────────────────────────────
  useEffect(() => {
    if (!cameraOn || !token) return;

    let isCancelled = false;
    setScanning(true);
    setResults([]);
    promptedTrackIds.current = new Set();
    startRenderLoop();

    const socket = new WebSocket(`${WS_BASE}/ws/scan/live/?token=${token}`);
    wsRef.current = socket;

    socket.onopen = () => setConnected(true);

    socket.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);

        if (msg.type === "error") {
          toast.error(msg.message);
          return;
        }

        if (msg.type === "tracks" && msg.tracks) {
          // Rebuild the target map — render loop reads it every frame
          const map = new Map();
          for (const t of msg.tracks) map.set(t.track_id, t);
          targetTracksRef.current = map;
          setActiveTracks(msg.tracks);
        }

        if (msg.type === "ocr_update") {
          // Update plate text in the target map so the label updates smoothly
          const current = targetTracksRef.current.get(msg.track_id);
          if (current) {
            targetTracksRef.current.set(msg.track_id, {
              ...current, plate_text: msg.plate_text,
            });
          }
          setActiveTracks((prev) =>
            prev.map((t) =>
              t.track_id === msg.track_id ? { ...t, plate_text: msg.plate_text } : t
            )
          );
        }

        if (msg.type === "id_required") {
          const trackId = msg.track_id;
          if (promptedTrackIds.current.has(trackId)) return;
          promptedTrackIds.current.add(trackId);
          setIdRequiredTracks((prev) => ({
            ...prev,
            [trackId]: { vehicle_type: msg.vehicle_type, message: msg.message },
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

    socket.onerror = () => toast.error("WebSocket error");
    socket.onclose = () => {
      if (!isCancelled) {
        setScanning(false);
        setConnected(false);
      }
    };

    // ── Frame sender — every 6th rAF tick ≈ 10fps ──────────────────────────
    const sendFrame = () => {
      if (isCancelled) return;
      const webcam = videoRef.current;
      const ws     = wsRef.current;

      if (!webcam || !ws || ws.readyState !== WebSocket.OPEN) {
        sendLoopRef.current = requestAnimationFrame(sendFrame);
        return;
      }

      frameCounterRef.current++;
      if (frameCounterRef.current % 6 !== 0) {
        sendLoopRef.current = requestAnimationFrame(sendFrame);
        return;
      }

      try {
        let base64 = null;
        if (typeof webcam.getScreenshot === "function") {
          base64 = webcam.getScreenshot({ width: 1280, height: 720 });
        }
        if (!base64 && webcam.video) {
          const c = document.createElement("canvas");
          c.width  = 1280;
          c.height = 720;
          c.getContext("2d").drawImage(webcam.video, 0, 0, 1280, 720);
          base64 = c.toDataURL("image/jpeg", 0.75);
        }
        if (base64) {
          const jpegBase64 = base64.split(",")[1];
          if (jpegBase64 && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "frame", image_b64: jpegBase64 }));
          }
        }
      } catch (e) {
        console.error("[WS] Screenshot failed:", e);
      }

      sendLoopRef.current = requestAnimationFrame(sendFrame);
    };

    sendLoopRef.current = requestAnimationFrame(sendFrame);

    return () => {
      isCancelled = true;
      socket.close();
      wsRef.current = null;
      if (sendLoopRef.current) {
        cancelAnimationFrame(sendLoopRef.current);
        sendLoopRef.current = null;
      }
      stopRenderLoop();
    };
  }, [cameraOn, token, startRenderLoop, stopRenderLoop]);

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
