import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "../components/Feedback/notify";
import { WS_BASE } from "../api/wsBase";

// A flapping camera or scanner re-raises the same message on every retry.
// These dialogs stay dismissed for this long afterwards so a dead socket
// cannot bury the screen a guard is working on.
const STREAM_THROTTLE = 30000


// ── Bounding-box colours (must match backend CLASS_NAMES) ────────────────────
const TRACK_COLORS = {
  license_plate: "#14A374",
  vehicle:       "#14A374",
  motorcycle:    "#2E8CCB",
  _default:      "#F6CE11",
};

const VEHICLE_TYPE_LABELS = {
  motorcycle: "Motorcycle",
};

function getTrackColor(track) {
  if (track.vehicle_type) return TRACK_COLORS[track.vehicle_type] ?? TRACK_COLORS._default;
  if (track.class_name)   return TRACK_COLORS[track.class_name]   ?? TRACK_COLORS._default;
  return TRACK_COLORS._default;
}

const LERP = 0.25; // smoothing factor for bbox interpolation

/**
 * useRtspStream
 *
 * Connects to /ws/scan/rtsp/ on the Django backend.
 * The backend reads the RTSP stream via OpenCV and sends back:
 *   - {type:"frame", image_b64} → drawn onto canvasRef
 *   - {type:"tracks", tracks}   → bounding-box overlay
 *   - {type:"result", results}  → plate scan results
 *   - {type:"status", connected, message}
 *   - {type:"error",  message}
 *
 * Usage:
 *   const { connected, results, canvasRef, startStream, stopStream, ... } = useRtspStream(token)
 *   <canvas ref={canvasRef} />
 *   <button onClick={() => startStream("rtsp://...")}>Connect</button>
 */
export function useRtspStream(token) {
  const [wsActive,        setWsActive]        = useState(false);
  const [streamConnected, setStreamConnected] = useState(false);
  const [results,         setResults]         = useState([]);
  const [flash,           setFlash]           = useState(false);
  const [activeTracks,    setActiveTracks]    = useState([]);
  const [statusMsg,       setStatusMsg]       = useState("");

  const wsRef             = useRef(null);
  const canvasRef         = useRef(null);  // single canvas: frames + bbox overlay
  const renderLoopRef     = useRef(null);
  const targetTracksRef   = useRef(new Map());
  const smoothBoxRef      = useRef(new Map());
  const latestFrameRef    = useRef(null);  // latest decoded HTMLImageElement

  // ── 60-fps render loop ───────────────────────────────────────────────────
  const startRenderLoop = useCallback(() => {
    if (renderLoopRef.current) return;

    const draw = () => {
      const canvas = canvasRef.current;
      if (!canvas) { renderLoopRef.current = requestAnimationFrame(draw); return; }

      const img = latestFrameRef.current;
      const vw  = img?.naturalWidth  || canvas.clientWidth  || 1280;
      const vh  = img?.naturalHeight || canvas.clientHeight || 720;

      if (canvas.width !== vw)  canvas.width  = vw;
      if (canvas.height !== vh) canvas.height = vh;

      const ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, vw, vh);

      // Draw latest JPEG frame
      if (img && img.complete && img.naturalWidth > 0) {
        ctx.drawImage(img, 0, 0, vw, vh);
      } else {
        ctx.fillStyle = "#0B2340";
        ctx.fillRect(0, 0, vw, vh);
      }

      // Draw bounding-box overlays
      const targets = targetTracksRef.current;
      const smooth  = smoothBoxRef.current;

      for (const tid of smooth.keys()) {
        if (!targets.has(tid)) smooth.delete(tid);
      }

      if (targets.size > 0) {
        ctx.font         = "12px 'Courier New', monospace";
        ctx.textBaseline = "top";
        ctx.textAlign    = "left";

        for (const [tid, track] of targets) {
          const tx1 = track.bbox[0] * vw;
          const ty1 = track.bbox[1] * vh;
          const tx2 = track.bbox[2] * vw;
          const ty2 = track.bbox[3] * vh;

          if (!smooth.has(tid)) smooth.set(tid, { x1: tx1, y1: ty1, x2: tx2, y2: ty2 });
          const s = smooth.get(tid);
          s.x1 += (tx1 - s.x1) * LERP;
          s.y1 += (ty1 - s.y1) * LERP;
          s.x2 += (tx2 - s.x2) * LERP;
          s.y2 += (ty2 - s.y2) * LERP;

          const px = s.x1, py = s.y1, pw = s.x2 - s.x1, ph = s.y2 - s.y1;
          const color = getTrackColor(track);

          ctx.strokeStyle = color;
          ctx.lineWidth   = track.vehicle_type ? 3 : 2;
          ctx.setLineDash(track.vehicle_type ? [8, 4] : []);
          ctx.strokeRect(px, py, pw, ph);
          ctx.setLineDash([]);

          const PAD = 6, TH = 22;
          let labelText;
          if (track.vehicle_type) {
            const typeLabel = VEHICLE_TYPE_LABELS[track.vehicle_type] ?? track.vehicle_type;
            const confText  = track.detection_conf ? ` ${(track.detection_conf * 100).toFixed(0)}%` : "";
            labelText = `${typeLabel}${confText}`;
          } else {
            const plateText = track.plate_text || `Track #${tid}`;
            const confText  = track.detection_conf ? ` | ${(track.detection_conf * 100).toFixed(0)}%` : "";
            labelText = `${plateText}${confText}`;
          }

          const tw = ctx.measureText(labelText).width + PAD * 2;
          ctx.fillStyle = "rgba(0,0,0,0.75)";
          ctx.fillRect(px, py - TH, tw, TH);
          ctx.fillStyle = color;
          ctx.fillRect(px, py - TH, 3, TH);
          ctx.fillStyle = "#ffffff";
          ctx.fillText(labelText, px + PAD + 2, py - TH + 4);

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
    latestFrameRef.current  = null;
  }, []);

  // ── startStream ──────────────────────────────────────────────────────────
  const startStream = useCallback((rtspUrl) => {
    if (!token) { toast.error("Not authenticated."); return; }
    if (!rtspUrl?.trim().startsWith("rtsp://")) {
      toast.error("RTSP URL must start with rtsp://");
      return;
    }

    // Close any previous connection
    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.close();
      wsRef.current = null;
    }

    stopRenderLoop();
    startRenderLoop();
    setResults([]);
    setActiveTracks([]);
    setStreamConnected(false);
    setStatusMsg("Connecting…");

    const ws = new WebSocket(`${WS_BASE}/ws/scan/rtsp/?token=${token}`);
    wsRef.current = ws;

    ws.onopen = () => {
      setWsActive(true);
      ws.send(JSON.stringify({ type: "start", rtsp_url: rtspUrl }));
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);

        if (msg.type === "frame") {
          // Decode base64 JPEG and store as Image for the render loop
          const img = new Image();
          img.src = `data:image/jpeg;base64,${msg.image_b64}`;
          latestFrameRef.current = img;
          return;
        }

        if (msg.type === "status") {
          setStatusMsg(msg.message || "");
          setStreamConnected(!!msg.connected);
          return;
        }

        if (msg.type === "error") {
          toast.error(msg.message, { title: "Camera error", throttleMs: STREAM_THROTTLE });
          setStreamConnected(false);
          return;
        }

        if (msg.type === "tracks" && msg.tracks) {
          const map = new Map();
          for (const t of msg.tracks) map.set(t.track_id, t);
          targetTracksRef.current = map;
          setActiveTracks(msg.tracks);
          return;
        }

        if (msg.type === "ocr_update") {
          const current = targetTracksRef.current.get(msg.track_id);
          if (current) {
            targetTracksRef.current.set(msg.track_id, { ...current, plate_text: msg.plate_text });
          }
          setActiveTracks((prev) =>
            prev.map((t) => t.track_id === msg.track_id ? { ...t, plate_text: msg.plate_text } : t)
          );
          return;
        }

        if (msg.type === "result" && msg.results) {
          setFlash(true);
          setTimeout(() => setFlash(false), 450);
          setResults(msg.results);
        }

      } catch (e) {
        console.error("[RTSP WS] Parse error:", e);
      }
    };

    ws.onerror = () => toast.error("RTSP WebSocket connection error", { title: "Camera error", throttleMs: STREAM_THROTTLE });
    ws.onclose = () => {
      setWsActive(false);
      setStreamConnected(false);
    };
  }, [token, startRenderLoop, stopRenderLoop]);

  // ── stopStream ───────────────────────────────────────────────────────────
  const stopStream = useCallback(() => {
    if (wsRef.current) {
      if (wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: "stop" }));
      }
      wsRef.current.onclose = null;
      wsRef.current.close();
      wsRef.current = null;
    }
    setWsActive(false);
    setStreamConnected(false);
    setResults([]);
    setActiveTracks([]);
    setStatusMsg("");
    stopRenderLoop();
  }, [stopRenderLoop]);

  // Cleanup on unmount
  useEffect(() => () => stopStream(), [stopStream]);

  return {
    /** WebSocket is open */
    wsActive,
    /** Backend has successfully opened the RTSP stream */
    streamConnected,
    /** Both WS and RTSP stream are active */
    connected: wsActive && streamConnected,
    results,
    flash,
    activeTracks,
    statusMsg,
    /** Attach to the <canvas> element you want frames drawn into */
    canvasRef,
    startStream,
    stopStream,
  };
}
