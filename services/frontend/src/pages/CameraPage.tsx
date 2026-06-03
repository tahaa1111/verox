/**
 * CameraPage — Live Pi camera feed with multi-scan queue support.
 *
 * Flow:
 *  1. Press "Start Camera" → viewport appears, signals Pi via POST /v1/camera/start
 *  2. Frontend polls GET /v1/camera/snapshot every 200 ms for live feed
 *  3. Pi detects document stability (3 s countdown ring) → auto-submits → pushes latest_job_id
 *  4. Frontend adds job to queue, viewport closes, "Queued ✓" toast appears
 *  5. "Capture Now" does the same (manual capture without waiting for stability)
 *  6. "Stop" closes the viewport without capturing
 *
 * Viewport lifecycle:  hidden → (Start Camera) → streaming → (Capture/Stop/Auto-submit) → hidden
 * Multi-scan: press "Start Camera" again after each capture — camera hardware is always warm.
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { Link } from "react-router-dom";
import {
  getCameraSnapshot, submitCapture,
  signalCameraStart, signalCameraStop,
} from "../api";
import { useStore } from "../store";
import { pollJob } from "../api";

type CameraState = "idle" | "streaming" | "error";

const POLL_INTERVAL_MS   = 200;
const START_TIMEOUT_MS   = 8000;
const STABILITY_WINDOW_S = 3;
const QUEUED_FLASH_MS    = 2500;  // how long to show the "Queued ✓" toast

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

  const pollRef    = useRef<ReturnType<typeof setInterval> | null>(null);
  const startTimer = useRef<ReturnType<typeof setTimeout>  | null>(null);
  const seenJobRef = useRef<string | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout>  | null>(null);

  // ── stopPolling: hides the viewport and optionally stops the Pi ────────────
  const stopPolling = useCallback((sendStop = false) => {
    if (pollRef.current)    { clearInterval(pollRef.current);  pollRef.current  = null; }
    if (startTimer.current) { clearTimeout(startTimer.current); startTimer.current = null; }
    setCameraState("idle");
    setStableProgress(0);
    setFrameSrc(null);
    if (sendStop) signalCameraStop().catch(() => {});
  }, []);
  useEffect(() => () => stopPolling(true), [stopPolling]);

  // ── On mount: poll any queued jobs to clear stale entries ─────────────────
  useEffect(() => {
    jobQueue.forEach((id) => {
      pollJob(id)
        .then((j) => upsertJob(j))
        .catch(() => removeFromQueue(id));
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── "Queued ✓" toast ──────────────────────────────────────────────────────
  const flashQueued = (jobId: string) => {
    setQueuedFlash(jobId);
    if (flashTimer.current) clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => setQueuedFlash(null), QUEUED_FLASH_MS);
  };

  // ── Start camera ──────────────────────────────────────────────────────────
  const startCamera = async () => {
    setCameraState("streaming");
    setError(null);
    setFrameSrc(null);
    setStableProgress(0);
    seenJobRef.current = null;
    let gotFrame = false;

    // Pre-seed seenJobRef to avoid stale job_id from Redis (5-min TTL)
    // triggering an unwanted queue addition on the very first poll.
    try {
      const init = await getCameraSnapshot();
      seenJobRef.current = init.latest_job_id ?? null;
    } catch { /* 404 = Pi hasn't pushed yet — fine */ }

    try { await signalCameraStart(); } catch { /* non-fatal */ }

    startTimer.current = setTimeout(() => {
      if (!gotFrame) {
        stopPolling();
        setError(
          "No camera feed received within 8 s.\n" +
          "Ensure the Pi (medibox-camera service) is running:\n" +
          "  sudo systemctl start medibox-camera"
        );
        setCameraState("error");
      }
    }, START_TIMEOUT_MS);

    pollRef.current = setInterval(async () => {
      try {
        const snap = await getCameraSnapshot();
        if (!gotFrame) {
          gotFrame = true;
          if (startTimer.current) { clearTimeout(startTimer.current); startTimer.current = null; }
        }

        setFrameSrc("data:image/jpeg;base64," + snap.frame);
        setStableProgress(snap.stable_progress ?? 0);

        // Pi auto-submitted a new job — add to queue, close viewport
        if (snap.latest_job_id && snap.latest_job_id !== seenJobRef.current) {
          seenJobRef.current = snap.latest_job_id;
          addToQueue(snap.latest_job_id);
          flashQueued(snap.latest_job_id);
          stopPolling(false); // close viewport; Pi manages its own push state
        }
      } catch { /* 404 = no frame yet */ }
    }, POLL_INTERVAL_MS);
  };

  // ── Manual capture ─────────────────────────────────────────────────────────
  const captureNow = async () => {
    if (cameraState !== "streaming" || capturing) return;
    setCapturing(true);
    try {
      const result = await submitCapture();
      seenJobRef.current = result.job_id;
      signalCameraStop().catch(() => {});
      addToQueue(result.job_id);
      flashQueued(result.job_id);
      stopPolling(false); // close viewport after capture
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

      {/* ── Camera viewport — only rendered while streaming ────────────────── */}
      {cameraState === "streaming" && (
        <div
          className="relative bg-black rounded-2xl overflow-hidden border border-gray-200 shadow-sm"
          style={{ aspectRatio: "4/3", maxHeight: "520px" }}
        >
          {/* Live frame or waiting spinner */}
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

          {/* LIVE badge */}
          {isLive && (
            <div className="absolute top-3 left-3 flex items-center gap-1.5 bg-black/60 rounded-full px-2.5 py-1">
              <div className="w-2 h-2 bg-red-500 rounded-full animate-pulse" />
              <span className="text-white text-xs font-semibold tracking-wide">LIVE</span>
            </div>
          )}

          {/* 3-second stability countdown ring */}
          {isLive && <StabilityRing progress={stableProgress} />}

          {/* Manual capture submitting overlay */}
          {capturing && (
            <div className="absolute inset-0 bg-black/60 flex flex-col items-center justify-center gap-3">
              <div className="animate-spin rounded-full h-12 w-12 border-4 border-white border-t-transparent" />
              <span className="text-white font-medium">Submitting…</span>
            </div>
          )}
        </div>
      )}

      {/* ── Error card (shown when viewport is closed after a timeout) ─────── */}
      {cameraState === "error" && error && (
        <div className="bg-red-50 border border-red-200 rounded-xl p-4 flex gap-3 items-start">
          <svg className="w-5 h-5 text-red-500 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>
          </svg>
          <pre className="text-red-700 text-xs whitespace-pre-wrap flex-1">{error}</pre>
        </div>
      )}

      {/* ── "Queued ✓" toast — shown AFTER viewport closes ─────────────────── */}
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

      {/* ── Controls ────────────────────────────────────────────────────────── */}
      <div className="flex gap-3 flex-wrap items-center">
        {/* Start button — shown when idle or after error */}
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

        {/* Capture + Stop — shown while streaming */}
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
              onClick={() => stopPolling(true)}
              className="border border-gray-300 text-gray-600 hover:bg-gray-50 font-medium px-4 py-2.5 rounded-xl text-sm transition-colors"
            >
              Stop
            </button>
          </>
        )}

        {/* Queue summary pill */}
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
          <li>Press <strong>Start Camera</strong> — live feed appears</li>
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
