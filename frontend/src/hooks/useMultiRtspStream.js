import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

const WS_BASE = import.meta.env.VITE_API_URL
  ? import.meta.env.VITE_API_URL.replace(/^http/, "ws")
  : `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}`;

const TRACK_COLORS = {
  license_plate: "#00ff88",
  vehicle:       "#00ff88",
  motor:         "#3b82f6",
  ebike:         "#8b5cf6",
  escooter:      "#10b981",
  _default:      "#facc15",
};
const VEHICLE_TYPE_LABELS = { ebike: "E-Bike", escooter: "E-Scooter" };
const LERP = 0.25;

function trackColor(t) {
  return TRACK_COLORS[t.vehicle_type] ?? TRACK_COLORS[t.class_name] ?? TRACK_COLORS._default;
}

let _seq = 0;
const genId = () => ++_seq;

/**
 * useMultiRtspStream
 *
 * Manages up to N simultaneous RTSP camera WebSocket connections.
 * Each camera connects to /ws/scan/rtsp/, receives JPEG frames and
 * plate-scan results, and draws frames + bbox overlays onto its own canvas.
 *
 * Canvas elements must be attached via the callback ref helper:
 *   <canvas ref={el => registerCanvas(cam.id, el)} />
 *
 * Exposed API:
 *   cameras          — array of camera state objects
 *   activeCamId      — currently focused camera id
 *   setActiveCamId   — switch focused camera
 *   activeCam        — camera object for active camera
 *   addCamera(name, url)     — open a new RTSP connection
 *   removeCamera(id)         — close + remove a camera
 *   disconnectCamera(id)     — close WS (keeps camera in list)
 *   disconnectAll()          — close all, clear list
 *   results          — latest scan results (any camera)
 *   flash            — true for ~450ms after a scan result
 *   registerCanvas   — callback ref for <canvas> elements
 */
export function useMultiRtspStream(token) {
  const [cameras, setCameras] = useState([]); // [{id, name, url, wsActive, streamConnected, statusMsg}]
  const [activeCamId, _setActive] = useState(null);
  const [results, setResults]     = useState([]);
  const [flash,   setFlash]       = useState(false);

  // Keep a ref for activeCamId so callbacks always see the latest value
  const activeRef = useRef(null);
  const setActiveCamId = useCallback((id) => {
    activeRef.current = id;
    _setActive(id);
  }, []);

  // Per-camera mutable refs (keyed by camera id, never cause re-renders)
  const wsMap     = useRef({}); // id → WebSocket
  const canvasMap = useRef({}); // id → HTMLCanvasElement
  const frameMap  = useRef({}); // id → HTMLImageElement (latest decoded frame)
  const trackMap  = useRef({}); // id → Map<trackId, trackObj>
  const smoothMap = useRef({}); // id → Map<trackId, {x1,y1,x2,y2}>
  const rafMap    = useRef({}); // id → animationFrame handle

  // ── Canvas callback ref registration ────────────────────────────────────
  const registerCanvas = useCallback((camId, el) => {
    if (el) canvasMap.current[camId] = el;
    else    delete canvasMap.current[camId];
  }, []);

  // ── 60-fps render loop for one camera ───────────────────────────────────
  const startRenderLoop = useCallback((camId) => {
    if (rafMap.current[camId]) return;
    if (!trackMap.current[camId]) trackMap.current[camId] = new Map();
    if (!smoothMap.current[camId]) smoothMap.current[camId] = new Map();

    const draw = () => {
      const canvas = canvasMap.current[camId];
      if (!canvas) { rafMap.current[camId] = requestAnimationFrame(draw); return; }

      const img = frameMap.current[camId];
      const vw  = img?.naturalWidth  || canvas.clientWidth  || 1280;
      const vh  = img?.naturalHeight || canvas.clientHeight || 720;
      if (canvas.width !== vw)  canvas.width  = vw;
      if (canvas.height !== vh) canvas.height = vh;

      const ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, vw, vh);

      if (img && img.complete && img.naturalWidth > 0) {
        ctx.drawImage(img, 0, 0, vw, vh);
      } else {
        ctx.fillStyle = "#0d1117";
        ctx.fillRect(0, 0, vw, vh);
      }

      const targets = trackMap.current[camId]  || new Map();
      const smooth  = smoothMap.current[camId] || new Map();

      // Remove stale smooth entries
      for (const tid of smooth.keys()) if (!targets.has(tid)) smooth.delete(tid);

      if (targets.size > 0) {
        ctx.font = "12px 'Courier New', monospace";
        ctx.textBaseline = "top";
        ctx.textAlign    = "left";

        for (const [tid, track] of targets) {
          const tx1 = track.bbox[0] * vw, ty1 = track.bbox[1] * vh;
          const tx2 = track.bbox[2] * vw, ty2 = track.bbox[3] * vh;

          if (!smooth.has(tid)) smooth.set(tid, { x1: tx1, y1: ty1, x2: tx2, y2: ty2 });
          const s = smooth.get(tid);
          s.x1 += (tx1 - s.x1) * LERP; s.y1 += (ty1 - s.y1) * LERP;
          s.x2 += (tx2 - s.x2) * LERP; s.y2 += (ty2 - s.y2) * LERP;

          const px = s.x1, py = s.y1, pw = s.x2 - s.x1, ph = s.y2 - s.y1;
          const color = trackColor(track);

          ctx.strokeStyle = color;
          ctx.lineWidth   = track.vehicle_type ? 3 : 2;
          ctx.setLineDash(track.vehicle_type ? [8, 4] : []);
          ctx.strokeRect(px, py, pw, ph);
          ctx.setLineDash([]);

          const PAD = 6, TH = 21;
          const labelText = track.vehicle_type
            ? (VEHICLE_TYPE_LABELS[track.vehicle_type] ?? track.vehicle_type)
            : `${track.plate_text || `T#${tid}`}${track.detection_conf ? ` ${(track.detection_conf * 100).toFixed(0)}%` : ""}`;

          const tw = ctx.measureText(labelText).width + PAD * 2;
          ctx.fillStyle = "rgba(0,0,0,0.75)"; ctx.fillRect(px, py - TH, tw, TH);
          ctx.fillStyle = color;              ctx.fillRect(px, py - TH, 3, TH);
          ctx.fillStyle = "#fff";             ctx.fillText(labelText, px + PAD + 2, py - TH + 4);
        }
      }

      rafMap.current[camId] = requestAnimationFrame(draw);
    };
    rafMap.current[camId] = requestAnimationFrame(draw);
  }, []);

  const stopRenderLoop = useCallback((camId) => {
    if (rafMap.current[camId]) {
      cancelAnimationFrame(rafMap.current[camId]);
      delete rafMap.current[camId];
    }
    const canvas = canvasMap.current[camId];
    if (canvas) canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
    delete frameMap.current[camId];
    delete trackMap.current[camId];
    delete smoothMap.current[camId];
  }, []);

  // ── Open WebSocket for one camera ────────────────────────────────────────
  const _connect = useCallback((camId, rtspUrl) => {
    if (!token) return;
    const ws = new WebSocket(`${WS_BASE}/ws/scan/rtsp/?token=${token}`);
    wsMap.current[camId] = ws;
    startRenderLoop(camId);

    ws.onopen = () => {
      setCameras(p => p.map(c => c.id === camId ? { ...c, wsActive: true, statusMsg: "Connecting…" } : c));
      ws.send(JSON.stringify({ type: "start", rtsp_url: rtspUrl }));
    };

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);

        if (msg.type === "frame") {
          const img = new Image();
          img.src = `data:image/jpeg;base64,${msg.image_b64}`;
          frameMap.current[camId] = img;
          return;
        }
        if (msg.type === "status") {
          setCameras(p => p.map(c => c.id === camId
            ? { ...c, streamConnected: !!msg.connected, statusMsg: msg.message || "" } : c));
          return;
        }
        if (msg.type === "error") {
          toast.error(`Camera error: ${msg.message}`);
          // Backend closed the WS — clean up the frontend side too
          const wsErr = wsMap.current[camId];
          if (wsErr) { try { wsErr.onclose = null; wsErr.close(); } catch {} delete wsMap.current[camId]; }
          stopRenderLoop(camId);
          setCameras(p => p.map(c => c.id === camId
            ? { ...c, wsActive: false, streamConnected: false, statusMsg: "Failed — check URL" } : c));
          return;
        }
        if (msg.type === "tracks" && msg.tracks) {
          const map = new Map();
          for (const t of msg.tracks) map.set(t.track_id, t);
          trackMap.current[camId] = map;
          return;
        }
        if (msg.type === "ocr_update") {
          const cur = trackMap.current[camId]?.get(msg.track_id);
          if (cur) trackMap.current[camId].set(msg.track_id, { ...cur, plate_text: msg.plate_text });
          return;
        }
        if (msg.type === "result" && msg.results) {
          setFlash(true);
          setTimeout(() => setFlash(false), 450);
          setResults(msg.results);
        }
      } catch { /* ignore parse errors */ }
    };

    ws.onerror = () => toast.error("Camera WebSocket connection error");
    ws.onclose = () => {
      setCameras(p => p.map(c => c.id === camId
        ? { ...c, wsActive: false, streamConnected: false } : c));
    };
  }, [token, startRenderLoop]);

  // ── Close WebSocket for one camera ───────────────────────────────────────
  const disconnectCamera = useCallback((camId) => {
    const ws = wsMap.current[camId];
    if (ws) {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "stop" }));
      ws.onclose = null;
      ws.close();
      delete wsMap.current[camId];
    }
    stopRenderLoop(camId);
    setCameras(p => p.map(c => c.id === camId
      ? { ...c, wsActive: false, streamConnected: false, statusMsg: "" } : c));
  }, [stopRenderLoop]);

  // ── Add a new camera ─────────────────────────────────────────────────────
  const addCamera = useCallback((name, url) => {
    const trimUrl = (url || "").trim();
    if (!trimUrl.startsWith("rtsp://")) { toast.error("URL must start with rtsp://"); return null; }
    const id  = genId();
    const cam = { id, name: (name || "").trim() || `Camera ${id}`, url: trimUrl, wsActive: false, streamConnected: false, statusMsg: "" };
    setCameras(p => [...p, cam]);
    setActiveCamId(id);
    _connect(id, trimUrl);
    return id;
  }, [_connect, setActiveCamId]);

  // ── Remove a camera (close WS + remove from list) ────────────────────────
  const removeCamera = useCallback((camId) => {
    disconnectCamera(camId);
    setCameras(p => {
      const next = p.filter(c => c.id !== camId);
      if (activeRef.current === camId) setActiveCamId(next[0]?.id ?? null);
      return next;
    });
  }, [disconnectCamera, setActiveCamId]);

  // ── Disconnect and clear all cameras ─────────────────────────────────────
  const disconnectAll = useCallback(() => {
    Object.keys(wsMap.current).forEach(id => {
      const ws = wsMap.current[id];
      if (ws) { try { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "stop" })); ws.onclose = null; ws.close(); } catch {} }
    });
    Object.keys(rafMap.current).forEach(id => cancelAnimationFrame(rafMap.current[id]));
    wsMap.current     = {};
    rafMap.current    = {};
    frameMap.current  = {};
    trackMap.current  = {};
    smoothMap.current = {};
    setCameras([]);
    setActiveCamId(null);
    setResults([]);
    setFlash(false);
  }, [setActiveCamId]);

  // Cleanup on unmount
  useEffect(() => () => {
    Object.values(wsMap.current).forEach(ws => { try { ws?.close(); } catch {} });
    Object.values(rafMap.current).forEach(h => cancelAnimationFrame(h));
  }, []);

  const activeCam = cameras.find(c => c.id === activeCamId) ?? cameras[0] ?? null;

  return {
    cameras,
    activeCamId: activeCam?.id ?? null,
    setActiveCamId,
    activeCam,
    addCamera,
    removeCamera,
    disconnectCamera,
    disconnectAll,
    results,
    flash,
    registerCanvas,
  };
}
