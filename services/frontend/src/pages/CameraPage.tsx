/**
 * CameraPage — Live Pi camera feed with automatic stability-triggered OCR.
 *
 * Flow:
 *  1. Press "Start Camera" → signals Pi via POST /v1/camera/start
 *  2. Frontend polls GET /v1/camera/snapshot every 200 ms for live feed
 *  3. stable_progress (0→1) from Pi stability tracker shown as countdown ring (3→0 s)
 *  4. When Pi reaches full stability it auto-submits → pushes latest_job_id to API
 *  5. Frontend detects latest_job_id → navigates to /results/:jobId
 *
 * Manual fallback: "Capture Now" button submits the current frame immediately.
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { getCameraSnapshot, submitCapture, signalCameraStart, signalCameraStop } from "../api";

type CameraState = "idle" | "streaming" | "submitting" | "error";

const POLL_INTERVAL_MS = 200;   // ~5 fps polling
const START_TIMEOUT_MS = 8000;  // give up after 8 s of no frames
const STABILITY_WINDOW_S = 3;   // must match Pi's STABILITY_WINDOW_S

function StabilityRing({ progress }: { progress: number }) {
  const radius = 36;
  const circ = 2 * Math.PI * radius;
  const remaining = Math.max(0, 1 - progress);
  const secondsLeft = Math.ceil(remaining * STABILITY_WINDOW_S);
  const dashOffset = circ * remaining;

  if (progress <= 0.01) return null;

  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center bg-black/40 backdrop-blur-sm">
      <div className="relative flex items-center justify-center">
        <svg width="96" height="96" className="-rotate-90">
          {/* track */}
          <circle cx="48" cy="48" r={radius} fill="none" stroke="rgba(255,255,255,0.2)" strokeWidth="6" />
          {/* progress arc */}
          <circle
            cx="48" cy="48" r={radius}
            fill="none"
            stroke={progress >= 1 ? "#22c55e" : "#f59e0b"}
            strokeWidth="6"
            strokeDasharray={circ}
            strokeDashoffset={dashOffset}
            strokeLinecap="round"
            style={{ transition: "stroke-dashoffset 0.15s linear, stroke 0.3s ease" }}
          />
        </svg>
        <div className="absolute flex flex-col items-center">
          <span className="text-white font-bold text-2xl leading-none">
            {progress >= 1 ? "✓" : secondsLeft}
          </span>
          {progress < 1 && (
            <span className="text-white/70 text-xs">s</span>
          )}
        </div>
      </div>
      <p className="mt-3 text-white font-semibold text-sm tracking-wide">
        {progress >= 1 ? "Submitting…" : "Hold steady…"}
      </p>
      {progress < 1 && (
        <p className="text-white/60 text-xs mt-1">
          {Math.round(progress * 100)}% stable
        </p>
      )}
    </div>
  );
}

export function CameraPage() {
  const [cameraState, setCameraState] = useState<CameraState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [frameSrc, setFrameSrc] = useState<string | null>(null);
  const [stableProgress, setStableProgress] = useState(0);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const seenJobRef = useRef<string | null>(null);   // track which job we already navigated to
  const navigate = useNavigate();

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    if (startTimerRef.current) { clearTimeout(startTimerRef.current); startTimerRef.current = null; }
    setCameraState("idle");
    setStableProgress(0);
    signalCameraStop().catch(() => {});
  }, []);

  useEffect(() => () => stopPolling(), [stopPolling]);

  const startCamera = async () => {
    setCameraState("streaming");
    setError(null);
    setStableProgress(0);
    seenJobRef.current = null;
    let gotFrame = false;

    try { await signalCameraStart(); } catch { /* non-fatal */ }

    startTimerRef.current = setTimeout(() => {
      if (!gotFrame) {
        stopPolling();
        setError(
          "No camera feed received within 8 s.\n" +
          "Ensure the Pi (medibox-camera service) is running:\n" +
          "  ssh verox@100.84.95.114\n" +
          "  sudo systemctl start medibox-camera"
        );
        setCameraState("error");
      }
    }, START_TIMEOUT_MS);

    pollRef.current = setInterval(async () => {
      try {
        const snap = await getCameraSnapshot();
        gotFrame = true;

        if (startTimerRef.current) {
          clearTimeout(startTimerRef.current);
          startTimerRef.current = null;
        }

        setFrameSrc("data:image/jpeg;base64," + snap.frame);
        setStableProgress(snap.stable_progress ?? 0);

        // Auto-navigate when Pi pushed a new job_id
        if (
          snap.latest_job_id &&
          snap.latest_job_id !== seenJobRef.current &&
          !isSubmitting
        ) {
          seenJobRef.current = snap.latest_job_id;
          if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
          navigate(`/results/${snap.latest_job_id}`);
        }
      } catch {
        // 404 = Pi not sending yet — keep waiting
      }
    }, POLL_INTERVAL_MS);
  };

  const captureNow = async () => {
    if (cameraState !== "streaming") return;
    setIsSubmitting(true);
    try {
      const result = await submitCapture();
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
      navigate(`/results/${result.job_id}`);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      setCameraState("error");
    } finally {
      setIsSubmitting(false);
    }
  };

  const isLive = cameraState === "streaming" && frameSrc !== null;

  return (
    <div className="space-y-6 max-w-2xl mx-auto">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Prescription Camera</h1>
        <p className="text-sm text-gray-500 mt-1">
          Point the camera at a prescription. It submits automatically once steady for 3 s.
        </p>
      </div>

      {/* Camera viewport */}
      <div
        className="relative bg-black rounded-2xl overflow-hidden border border-gray-200 shadow-sm"
        style={{ aspectRatio: "4/3", maxHeight: "520px" }}
      >
        {frameSrc ? (
          <img
            src={frameSrc}
            alt="Pi camera live feed"
            className="w-full h-full object-contain"
          />
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-white gap-3"
               style={{ minHeight: "320px" }}>
            {cameraState === "streaming" ? (
              <>
                <div className="animate-spin rounded-full h-10 w-10 border-2 border-white border-t-transparent" />
                <span className="text-sm text-gray-300">Waiting for Pi camera feed…</span>
              </>
            ) : cameraState === "error" ? (
              <pre className="text-red-400 text-xs text-center px-6 whitespace-pre-wrap">{error}</pre>
            ) : (
              <>
                <svg className="w-20 h-20 text-gray-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1}
                    d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z"/>
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M15 13a3 3 0 11-6 0 3 3 0 016 0z"/>
                </svg>
                <span className="text-gray-500 text-sm">Camera not started</span>
              </>
            )}
          </div>
        )}

        {/* LIVE badge */}
        {isLive && (
          <div className="absolute top-3 left-3 flex items-center gap-1.5 bg-black/60 rounded-full px-2.5 py-1">
            <div className="w-2 h-2 bg-red-500 rounded-full animate-pulse" />
            <span className="text-white text-xs font-semibold tracking-wide">LIVE</span>
          </div>
        )}

        {/* Stability countdown overlay */}
        {isLive && <StabilityRing progress={stableProgress} />}

        {/* Manual submitting overlay */}
        {isSubmitting && (
          <div className="absolute inset-0 bg-black/60 flex flex-col items-center justify-center gap-3">
            <div className="animate-spin rounded-full h-12 w-12 border-4 border-white border-t-transparent" />
            <span className="text-white font-medium">Submitting to OCR pipeline…</span>
          </div>
        )}
      </div>

      {/* Controls */}
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
              disabled={!frameSrc || isSubmitting}
              title="Submit current frame immediately (bypasses stability wait)"
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
              onClick={stopPolling}
              className="border border-gray-300 text-gray-600 hover:bg-gray-50 font-medium px-4 py-2.5 rounded-xl text-sm transition-colors"
            >
              Stop
            </button>
          </>
        )}
      </div>

      {/* Instructions */}
      <div className="bg-blue-50 border border-blue-100 rounded-xl p-4 text-sm text-blue-800 space-y-1">
        <p className="font-semibold">📋 How it works</p>
        <ol className="list-decimal list-inside space-y-0.5 text-blue-700">
          <li>Press <strong>Start Camera</strong> — the Pi begins streaming</li>
          <li>Hold the prescription still — countdown ring appears as it detects the document</li>
          <li>After 3 s steady hold the system submits automatically</li>
          <li>Results appear on the next screen</li>
        </ol>
        <p className="text-blue-500 text-xs pt-1">
          Use <strong>Capture Now</strong> to skip the countdown and submit immediately.
        </p>
      </div>

      <p className="text-xs text-gray-400 border-t pt-3">
        ⚠️ Clinical decision-support only — pharmacist verification required before dispensing.
      </p>
    </div>
  );
}
