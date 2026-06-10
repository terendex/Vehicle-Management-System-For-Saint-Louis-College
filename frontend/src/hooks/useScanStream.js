import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

const WS_BASE =
  (import.meta.env.VITE_API_URL || "http://localhost:8000").replace(
    "http",
    "ws"
  );

export function useScanStream(token, cameraOn, autoScan) {
  const [scanning, setScanning] = useState(false);
  const [results, setResults] = useState([]);
  const [bbox, setBbox] = useState([]);
  const [flash, setFlash] = useState(false);
  const wsRef = useRef(null);
  const camerasRef = useRef(null);
  const activeCamIdRef = useRef(null);
  const processingRef = useRef(false);
  const INTERVAL_MS = 500;

  const getLatestPlate = useCallback((cam) => {
    if (!cam) return null;
    try {
      return cam.getScreenshot();
    } catch {
      return null;
    }
  }, []);

  useEffect(() => {
    if (!cameraOn || !token) return;

    setScanning(true);
    setResults([]);
    setBbox([]);

    const socket = new WebSocket(
      `${WS_BASE}/ws/scan/live/?token=${token}`
    );
    wsRef.current = socket;

    socket.onopen = () => {
      setScanning(false);
      console.log("[WS] Connected");
    };

    socket.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "error") {
          toast.error(msg.message);
          return;
        }
        if (msg.type !== "result") return;

        if (!msg.results || msg.results.length === 0) {
          setBbox([]);
          setResults([]);
          return;
        }

        setResults(msg.results);
        const boxes = msg.results.map((r) => r.bbox).filter(Boolean);
        setBbox(boxes);


        msg.results.forEach((r) => {
          if (r.vehicle) {
            const k = r.plate_number;
            if (!localStorage.getItem(`vehicle:${k}`)) {
              localStorage.setItem(`vehicle:${k}`, JSON.stringify(r.vehicle));
            }
          }
        });
      } catch (e) {
        console.error("[WS] Parse error", e);
      }
    };

    socket.onerror = () => toast.error("WebSocket error");
    socket.onclose = () => {
      console.log("[WS] Disconnected");
      setScanning(false);
    };

    return () => {
      socket.close();
      wsRef.current = null;
    };
  }, [cameraOn, token]);

  useEffect(() => {
    if (!cameraOn || !autoScan) return;
    console.log("[WS-DBG] Interval effect started, cameraOn:", cameraOn, "autoScan:", autoScan);

    const interval = setInterval(() => {
      if (processingRef.current) { return; }
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
        console.log("[WS-DBG] tick skipped — WS not open, readyState:", wsRef.current?.readyState);
        return;
      }

      const cams = camerasRef.current;
      const camId = activeCamIdRef.current;
      const cam = cams?.[camId];
      if (!cam) {
        console.log("[WS-DBG] tick skipped — no cam ref. camId:", camId, "cams keys:", cams ? Object.keys(cams) : "null", "cams type:", typeof cams, Array.isArray(cams) ? "ARRAY" : "obj");
        return;
      }

      const imgSrc = getLatestPlate(cam);
      if (!imgSrc) {
        console.log("[WS-DBG] tick skipped — getScreenshot returned null");
        return;
      }

      console.log("[WS-DBG] ✅ Sending frame, base64 length:", imgSrc.length);
      processingRef.current = true;
      setScanning(true);

      // Simplify: the imgSrc is already a data URL, just extract base64 directly
      const base64 = imgSrc.split(",")[1];
      if (base64 && wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: "frame", image_b64: base64 }));
      }
      processingRef.current = false;
      setScanning(false);
    }, INTERVAL_MS);

    return () => clearInterval(interval);
  }, [cameraOn, autoScan, getLatestPlate]);

  const setCameras = useCallback((cams) => {
    camerasRef.current = cams;
  }, []);

  const setActiveCamId = useCallback((id) => {
    activeCamIdRef.current = id;
  }, []);

  return {
    scanning,
    results,
    bbox,
    flash,
    setResults,
    setBbox,
    setCameras,
    setActiveCamId,
  };
}
