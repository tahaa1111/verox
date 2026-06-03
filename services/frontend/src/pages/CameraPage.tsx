/**
 * CameraPage — Live Pi camera feed over WebSocket.
 *
 * Flow:
 *  1. Press "Start Camera" → opens WS to /v1/camera/ws/feed/{device_id}?token=...
 *  2. WS send {"type":"start"} → Pi begins streaming frames back
 *  3. Pi detects stability (3 s ring) → auto-submits → sends {"type":"job_id","job_id":"..."}
 *  4. Frontend adds job to queue, closes WS, shows "Queued ✓" toast
 *  5. "Capture Now" calls POST /camera/capture (reads latest in-memory frame on server)
 *  6. "Stop" sends {"type":"stop"} and closes WS
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { Link } from "react-router-dom";
import { submitCapture, pollJob } from "../api";
import { useStore } from "../store";

type CameraState = "idle" | "streaming" | "error";

const START_TIMEOUT_MS   = 8000;
const STABILITY_WINDOW_S = 3;
const QUEUED_FLASH_MS    = 2500;
const DEVICE_ID          = "pi-0001";

// Derive WebSocket base URL from the HTTP API base URL env var.
function getWsFeedUrl(deviceId: string, token: string): string {
  const apiBase = import.meta.env.VITE_API_BASE_URL as string | undefined;
  if (apiBase && apiBase.startsWith("http")) {
    const wsBase = apiBase.replace(/^http/, "ws");
    return `${wsBase}/camera/ws/feed/${deviceId}?token=${encodeURIComponent(token)}`;
  }
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const base  = apiBase ?? "/v1";
  return `${proto}//${location.host}${base}/camera/ws/feed/${deviceId}?token=${encodeURIComponent(token)}`;
}

// ── Stability countdown ring ──────────────────────────────────────────────────

function StabilityRing({ progress }: { progress: number }) {
  const radius = 36;
  const circ   = 2 * Math.PI * radius;
  const remaining   = Math.max(0, 1 - progress);
  const secondsLeft = Math.ceil(remaining * STABILITY_WINDOW_S);
  const dashOffset  = circ * remaining;
  if (progress <= 0.01) return null;
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center bg-black/40 backdrop-blur-sm">
      <div className="relative flex items-center justify-center">
        <svg width="96" height="96" className="-rotate-90">
          <circle cx="48" cy="48" r={radius} fill="none" stroke="rgba(255,255,255,0.2)" strokeWidth="6" />
          <circle cx="48" cy="48" r={radius} fill="none"
            stroke={progress >= 1 ? "#22c55e" : "#f59e0b"} strokeWidth="6"
            strokeDasharray={circ} strokeDashoffset={dashOffset} strokeLinecap="round"
            style={{ transition: "stroke-dashoffset 0.15s linear, stroke 0.3s ease" }}
          />
        </svg>
        <div className="absolute flex flex-col items-center">
          <span className="text-white font-bold text-2xl leading-none">
            {progress >= 1 ? "✓" : secondsLeft}
          </span>
          {progress < 1 && <span className="text-white/70 text-xs">s</span>}
        </div>
      </div>
      <p className="mt-3 text-white font-semibold text-sm tracking-wide">
        {progress >= 1 ? "Submitting…" : "Hold steady…"}
      </p>
      {progress < 1 && (
        <p className="text-white/60 text-xs mt-1">{Math.round(progress * 100)}% stable</p>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function CameraPage() {
  const [cameraState,    setCameraState]    = useState<CameraState>("idle");
  const [error,          setError]          = useState<string | null>(null);
  const [frameSrc,       setFrameSrc]       = useState<string | null>(null);
  const [stableProgress, setStableProgress] = useState(0);
  const [capturing,      setCapturing]      = useState(false);
  const [queuedFlash,    setQueuedFlash]    = useState<string | null>(null);

  const { jobQueue, addToQueue, removeFromQueue, upsertJob } = useStore();

  const wsRef          = useRef<WebSocket | null>(null);
  const seenJobRef     = useRef<string | null>(null);
  const flashTimer     = useRef<ReturnType<typeof setTimeout> | null>(null);
  const frameLostTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const startTimer     = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Close WS and reset viewport ───────────────────────────────────────────
  const stopCamera = useCallback((sendStop = false) => {
    if (startTimer.current)     { clearTimeout(startTimer.current);     startTimer.current     = null; }
    if (frameLostTimer.current) { clearTimeout(frameLostTimer.current); frameLostTimer.current = null; }
    const ws = wsRef.current;
    if (ws) {
      wsRef.current = null;
      if (sendStop && ws.readyState === WebSocket.OPEN) {
        try { ws.send(JSON.stringify({ type: "stop" })); } catch { /* ignore */ }
      }
      ws.close();
    }
    setCameraState("idle");
    setStableProgress(0);
    setFrameSrc(null);
  }, []);

  useEffect(() => () => stopCamera(true), [stopCamera]);

  // ── Poll any queued jobs on mount to clear stale entries ─────────────────
  useEffect(() => {
    jobQueue.forEach((id) => {
      pollJob(id)
        .then((j) => upsertJob(j))
        .catch(() => removeFromQueue(id));
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── "Queued ✓" toast ─────────────────────────────────────────────────────
  const flashQueued = (jobId: string) => {
    setQueuedFlash(jobId);
    if (flashTimer.current) clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => setQueuedFlash(null), QUEUED_FLASH_MS);
  };

  // ── Start camera — open WebSocket ────────────────────────────────────────
  const startCamera = () => {
    setCameraState("streaming");
    setError(null);
    setFrameSrc(null);
    setStableProgress(0);
    seenJobRef.current = null;

    const token = localStorage.getItem("firebase_token") ?? "";
    const url   = getWsFeedUrl(DEVICE_ID, token);
    const ws    = new WebSocket(url);
    wsRef.current = ws;

    let gotFrame = false;

    startTimer.current = setTimeout(() => {
      if (!gotFrame) {
        stopCamera(false);
        setError(
          "No camera feed within 8 s.\n" +
          "Check the Pi (medibox-camera service) is running:\n" +
          "  sudo systemctl start medibox-camera"
        );
        setCameraState("error");
      }
    }, START_TIMEOUT_MS);

    ws.onopen = () => {
      ws.send(JSON.stringify({ type: "start" }));
    };

    ws.onmessage = (event: MessageEvent) => {
      let msg: Record<string, unknown>;
      try { msg = JSON.parse(event.data as string); } catch { return; }

      if (msg.type === "frame") {
        if (!gotFrame) {
          gotFrame = true;
          if (startTimer.current) { clearTimeout(startTimer.current); startTimer.current = null; }
        }
        setFrameSrc("data:image/jpeg;base64," + (msg.frame as string));
        setStableProgress((msg.stable_progress as number) ?? 0);

        if (frameLostTimer.current) clearTimeout(frameLostTimer.current);
        frameLostTimer.current = setTimeout(() => stopCamera(true), 3000);

      } else if (msg.type === "job_id") {
        const jobId = msg.job_id as string;
        if (jobId && jobId !== seenJobRef.current) {
          seenJobRef.current = jobId;
          addToQueue(jobId);
          flashQueued(jobId);
          stopCamera(false);
        }
      }
    };

    ws.onerror = () => {
      if (startTimer.current)     { clearTimeout(startTimer.current);     startTimer.current     = null; }
      if (frameLostTimer.current) { clearTimeout(frameLostTimer.current); frameLostTimer.current = null; }
      wsRef.current = null;
      setError("WebSocket error — check network connection and Pi status.");
      setCameraState("error");
    };

    ws.onclose = (e: CloseEvent) => {
      if (startTimer.current)     { clearTimeout(startTimer.current);     startTimer.current     = null; }
      if (frameLostTimer.current) { clearTimeout(frameLostTimer.current); frameLostTimer.current = null; }
      wsRef.current = null;
      if (e.code === 4401) {
        setError("Authentication failed — sign out and back in, then retry.");
        setCameraState("error");
      } else if (e.code === 4403) {
        setError("Camera access denied — invalid camera secret.");
        setCameraState("error");
      }
      // Normal close (1000 / 1001) or stopCamera() already set idle — do nothing extra.
    };
  };

  // ── Manual capture — server reads latest in-memory frame ─────────────────
  const captureNow = async () => {
    if (cameraState !== "streaming" || capturing) return;
    setCapturing(true);
    try {
      const result = await submitCapture(DEVICE_ID);
      seenJobRef.current = result.job_id;
      addToQueue(result.job_id);
      flashQueued(result.job_id);
      stopCamera(false);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      setCameraState("error");
    } finally {
      setCapturing(false);
    }
  };

  const isLive = cameraState === "streaming" && frameSrc !== null;

  return (
    <div className="space-y-5 max-w-2xl mx-auto">

      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Prescription Camera</h1>
        <p className="text-sm text-gray-500 mt-1">
          Press <strong>Start Camera</strong>, hold the prescription steady for 3 s — it submits automatically.
        </p>
      </div>

      {/* ── Camera viewport ─────────────────────────────────────────────────── */}
      {cameraState === "streaming" && (
        <div
          className="relative bg-black rounded-2xl overflow-hidden border border-gray-200 shadow-sm"
          style={{ aspectRatio: "4/3", maxHeight: "520px" }}
        >
          {frameSrc ? (
            <img src={frameSrc} alt="Pi camera live feed" className="w-full h-full object-contain" />
          ) : (
            <div
              className="flex flex-col items-center justify-center h-full text-white gap-3"
              style={{ minHeight: "320px" }}
            >
              <div className="animate-spin rounded-full h-10 w-10 border-2 border-white border-t-transparent" />
              <span className="text-sm text-gray-300">Waiting for Pi camera feed…</span>
            </div>
          )}

          {isLive && (
            <div className="absolute top-3 left-3 flex items-center gap-1.5 bg-black/60 rounded-full px-2.5 py-1">
              <div className="w-2 h-2 bg-red-500 rounded-full animate-pulse" />
              <span className="text-white text-xs font-semibold tracking-wide">LIVE</span>
            </div>
          )}

          {isLive && <StabilityRing progress={stableProgress} />}

          {capturing && (
            <div className="absolute inset-0 bg-black/60 flex flex-col items-center justify-center gap-3">
              <div className="animate-spin rounded-full h-12 w-12 border-4 border-white border-t-transparent" />
              <span className="text-white font-medium">Submitting…</span>
            </div>
          )}
        </div>
      )}

      {/* ── Error card ───────────────────────────────────────────────────────── */}
      {cameraState === "error" && error && (
        <div className="bg-red-50 border border-red-200 rounded-xl p-4 flex gap-3 items-start">
          <svg className="w-5 h-5 text-red-500 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>
          </svg>
          <pre className="text-red-700 text-xs whitespace-pre-wrap flex-1">{error}</pre>
        </div>
      )}

      {/* ── "Queued ✓" toast ─────────────────────────────────────────────────── */}
      {queuedFlash && (
        <div className="flex items-center gap-3 bg-green-50 border border-green-200 rounded-xl px-4 py-3">
          <div className="w-8 h-8 bg-green-100 rounded-full flex items-center justify-center shrink-0">
            <svg className="w-4 h-4 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7"/>
            </svg>
          </div>
          <div className="flex-1">
            <p className="text-green-800 font-semibold text-sm">Scan queued successfully!</p>
            <p className="text-green-600 text-xs mt-0.5">
              Job #{jobQueue.length} added — results will appear in the queue.
            </p>
          </div>
          <Link
            to="/queue"
            className="text-xs font-semibold text-green-700 hover:text-green-900 underline shrink-0"
          >
            View queue →
          </Link>
        </div>
      )}

      {/* ── Controls ─────────────────────────────────────────────────────────── */}
      <div className="flex gap-3 flex-wrap items-center">
        {(cameraState === "idle" || cameraState === "error") && (
          <button
            onClick={startCamera}
            className="bg-brand-600 hover:bg-brand-700 text-white font-semibold px-6 py-2.5 rounded-xl text-sm transition-colors flex items-center gap-2"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M15 10l4.553-2.069A1 1 0 0121 8.87v6.26a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z"/>
            </svg>
            Start Camera
          </button>
        )}

        {cameraState === "streaming" && (
          <>
            <button
              onClick={captureNow}
              disabled={!frameSrc || capturing}
              className="bg-green-600 hover:bg-green-700 disabled:opacity-40 disabled:cursor-not-allowed text-white font-semibold px-6 py-2.5 rounded-xl text-sm transition-colors flex items-center gap-2"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z"/>
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 13a3 3 0 11-6 0 3 3 0 016 0z"/>
              </svg>
              Capture Now
            </button>
            <button
              onClick={() => stopCamera(true)}
              className="border border-gray-300 text-gray-600 hover:bg-gray-50 font-medium px-4 py-2.5 rounded-xl text-sm transition-colors"
            >
              Stop
            </button>
          </>
        )}

        {jobQueue.length > 0 && (
          <Link
            to="/queue"
            className="ml-auto flex items-center gap-2 bg-brand-50 border border-brand-100
                       text-brand-700 text-sm font-medium px-4 py-2 rounded-xl hover:bg-brand-100 transition-colors"
          >
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-brand-400 opacity-75"/>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-brand-600"/>
            </span>
            {jobQueue.length} in queue — View results →
          </Link>
        )}
      </div>

      {/* Instructions */}
      <div className="bg-blue-50 border border-blue-100 rounded-xl p-4 text-sm text-blue-800 space-y-1">
        <p className="font-semibold">📋 How it works</p>
        <ol className="list-decimal list-inside space-y-0.5 text-blue-700">
          <li>Press <strong>Start Camera</strong> — live feed appears instantly over WebSocket</li>
          <li>Hold prescription still — 3 s countdown ring fills</li>
          <li>Camera submits automatically, feed closes, scan is queued</li>
          <li>Or press <strong>Capture Now</strong> to skip the countdown</li>
          <li>Press <strong>Start Camera</strong> again for the next prescription</li>
        </ol>
      </div>

      <p className="text-xs text-gray-400 border-t pt-3">
        ⚠️ Clinical decision-support only — pharmacist verification required before dispensing.
      </p>
    </div>
  );
}
