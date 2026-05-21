/**
 * CameraPage — Live Pi camera feed with one-click prescription capture.
 *
 * Architecture:
 *  - Pi runs app.py which pushes frames to Cloud API POST /v1/camera/push
 *  - Frontend polls GET /v1/camera/snapshot every 200ms for live feed
 *  - "Capture & Submit" → POST /v1/camera/capture → returns job_id
 *  - Navigate to /jobs/:id for result tracking
 *
 * To start the Pi camera: SSH to Pi and run:
 *   cd ~/yolo-ws && source venv/bin/activate && python app.py
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { getCameraSnapshot, submitCapture } from "../api";

type CameraState = "idle" | "streaming" | "capturing" | "submitting" | "error";

const POLL_INTERVAL_MS = 200;   // ~5 fps
const START_TIMEOUT_MS = 8000;  // how long to wait for first frame

export function CameraPage() {
  const [cameraState, setCameraState] = useState<CameraState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [frameSrc, setFrameSrc] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const navigate = useNavigate();

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    if (startTimerRef.current) { clearTimeout(startTimerRef.current); startTimerRef.current = null; }
    setCameraState("idle");
  }, []);

  useEffect(() => () => stopPolling(), [stopPolling]);

  const startCamera = () => {
    setCameraState("streaming");
    setError(null);
    let gotFrame = false;

    // Timeout if no frame arrives
    startTimerRef.current = setTimeout(() => {
      if (!gotFrame) {
        stopPolling();
        setError(
          "No camera feed received. Make sure the Pi camera is running:\n" +
          "  ssh verox@100.84.95.114\n" +
          "  cd ~/yolo-ws && source venv/bin/activate && python app.py"
        );
        setCameraState("error");
      }
    }, START_TIMEOUT_MS);

    pollRef.current = setInterval(async () => {
      try {
        const { frame } = await getCameraSnapshot();
        gotFrame = true;
        if (startTimerRef.current) {
          clearTimeout(startTimerRef.current);
          startTimerRef.current = null;
        }
        setFrameSrc("data:image/jpeg;base64," + frame);
      } catch {
        // 404 = Pi not sending — keep waiting until timeout
      }
    }, POLL_INTERVAL_MS);
  };

  const captureAndSubmit = async () => {
    if (cameraState !== "streaming") return;
    stopPolling();
    setCameraState("capturing");

    try {
      setCameraState("submitting");
      const result = await submitCapture();
      navigate(`/jobs/${result.job_id}`);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      setCameraState("error");
    }
  };

  const isLive = cameraState === "streaming" && frameSrc !== null;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Prescription Camera</h1>
        <p className="text-sm text-gray-500 mt-1">
          Live feed from the Pi camera. Position the prescription and press&nbsp;
          <strong>Capture &amp; Submit</strong> to start OCR.
        </p>
      </div>

      {/* Camera viewport */}
      <div
        className="relative bg-black rounded-xl overflow-hidden border border-gray-200"
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
          <div className="absolute top-3 left-3 flex items-center gap-1.5 bg-black/70 rounded-full px-2.5 py-1">
            <div className="w-2 h-2 bg-red-500 rounded-full animate-pulse" />
            <span className="text-white text-xs font-semibold tracking-wide">LIVE</span>
          </div>
        )}

        {/* Submitting overlay */}
        {(cameraState === "capturing" || cameraState === "submitting") && (
          <div className="absolute inset-0 bg-black/60 flex flex-col items-center justify-center gap-3">
            <div className="animate-spin rounded-full h-12 w-12 border-4 border-white border-t-transparent" />
            <span className="text-white font-medium">
              {cameraState === "capturing" ? "Capturing…" : "Submitting to OCR pipeline…"}
            </span>
          </div>
        )}
      </div>

      {/* Controls */}
      <div className="flex gap-3 flex-wrap items-center">
        {(cameraState === "idle" || cameraState === "error") && (
          <button
            onClick={startCamera}
            className="bg-brand-600 hover:bg-brand-700 text-white font-medium px-6 py-2.5 rounded-lg text-sm transition-colors flex items-center gap-2"
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
              onClick={captureAndSubmit}
              disabled={!frameSrc}
              className="bg-green-600 hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold px-8 py-2.5 rounded-lg text-sm transition-colors flex items-center gap-2"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z"/>
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 13a3 3 0 11-6 0 3 3 0 016 0z"/>
              </svg>
              Capture &amp; Submit
            </button>
            <button
              onClick={stopPolling}
              className="border border-gray-300 text-gray-600 hover:bg-gray-50 font-medium px-4 py-2.5 rounded-lg text-sm transition-colors"
            >
              Stop
            </button>
          </>
        )}
      </div>

      {/* Pi start instructions */}
      <div className="bg-gray-50 border border-gray-200 rounded-lg p-4 text-xs text-gray-600">
        <p className="font-semibold mb-1">How to start the Pi camera:</p>
        <code className="block bg-gray-100 rounded px-3 py-2 font-mono whitespace-pre">
{`ssh verox@100.84.95.114     # password: verox1
cd ~/yolo-ws
source venv/bin/activate
python app.py`}
        </code>
        <p className="mt-2 text-gray-500">The Pi will push frames automatically when the camera starts.</p>
      </div>

      <p className="text-xs text-gray-400 border-t pt-3">
        ⚠️ Clinical decision-support tool only. Pharmacist verification required.
      </p>
    </div>
  );
}
