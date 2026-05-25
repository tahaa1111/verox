import { useEffect, useRef, useCallback } from "react";
import { useStore } from "../store";
import { pollJob, getWsToken } from "../api";
import type { JobPollResponse } from "../types";

// Derive WebSocket host from VITE_API_BASE_URL so we don't need a separate env var.
// VITE_API_BASE_URL = "https://medibox-api-XXX.us-central1.run.app/v1"
// → WS_HOST         = "wss://medibox-api-XXX.us-central1.run.app"
// In dev (VITE_API_BASE_URL = "/v1" or unset) → WS_HOST = "" → relative WS URL
const _API_BASE = import.meta.env.VITE_API_BASE_URL as string | undefined ?? "";
const WS_HOST = _API_BASE.startsWith("http")
  ? _API_BASE.replace(/^http/, "ws").replace(/\/v1\/?$/, "")
  : (import.meta.env.VITE_WS_BASE_URL as string | undefined ?? "");

const POLL_INTERVAL_MS = 2_000;   // 2 s fallback polling (WS preferred)
const HEARTBEAT_TIMEOUT_MS = 20_000;

export function useJobWs(jobId: string | null) {
  const upsertJob = useStore((s) => s.upsertJob);
  const wsRef = useRef<WebSocket | null>(null);
  const heartbeatTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollTimer.current) {
      clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
  }, []);

  const startPolling = useCallback(() => {
    if (pollTimer.current) return;
    pollTimer.current = setInterval(async () => {
      if (!jobId) return;
      try {
        const job = await pollJob(jobId);
        upsertJob(job);
        if (job.status === "completed" || job.status === "failed" || job.status === "cancelled") {
          stopPolling();
        }
      } catch {
        // network error — keep polling
      }
    }, POLL_INTERVAL_MS);
  }, [jobId, upsertJob, stopPolling]);

  const resetHeartbeat = useCallback(() => {
    if (heartbeatTimer.current) clearTimeout(heartbeatTimer.current);
    heartbeatTimer.current = setTimeout(() => {
      // WS silent for too long — fall back to polling
      wsRef.current?.close();
      startPolling();
    }, HEARTBEAT_TIMEOUT_MS);
  }, [startPolling]);

  const handleWsMessage = useCallback((msg: Record<string, unknown>) => {
    if (msg.event === "heartbeat") return;

    if (msg.event === "completed") {
      stopPolling();
      upsertJob({
        job_id: jobId!,
        status: "completed",
        progress_pct: 100,
        result: (msg.result as JobPollResponse["result"]) ?? null,
        error_message: null,
        estimated_completion_s: null,
      } as JobPollResponse);
    } else if (msg.event === "failed") {
      stopPolling();
      upsertJob({
        job_id: jobId!,
        status: "failed",
        progress_pct: 0,
        result: null,
        error_message: (msg.error as string) ?? (msg.error_message as string) ?? "Processing failed",
        estimated_completion_s: null,
      } as JobPollResponse);
    } else if (msg.event === "inference_progress" || msg.event === "inference_started") {
      upsertJob({
        job_id: jobId!,
        status: "processing",
        progress_pct: Math.round(((msg.progress as number) ?? 0) * 100),
        result: null,
        error_message: null,
        estimated_completion_s: null,
      } as JobPollResponse);
    } else if (msg.event === "cancelled") {
      stopPolling();
      upsertJob({
        job_id: jobId!,
        status: "cancelled",
        progress_pct: 0,
        result: null,
        error_message: "Cancelled by user.",
        estimated_completion_s: null,
      } as JobPollResponse);
    } else if (msg.event === "status") {
      // Initial state sent by the server when WS first connects
      if (msg.status === "completed" && msg.result) {
        stopPolling();
        upsertJob({
          job_id: jobId!,
          status: "completed",
          progress_pct: 100,
          result: msg.result as JobPollResponse["result"],
          error_message: null,
          estimated_completion_s: null,
        } as JobPollResponse);
      }
    }
  }, [jobId, upsertJob, stopPolling]);

  useEffect(() => {
    if (!jobId) return;

    let cancelled = false;

    // Exchange Firebase JWT for a short-lived opaque WS token, then connect.
    // Falls back to HTTP polling immediately if token exchange fails or if the
    // browser has no Firebase token yet (unauthenticated flow / dev mode).
    getWsToken()
      .then((opaqueToken) => {
        if (cancelled) return;

        const rawUrl = `${WS_HOST}/v1/ws/jobs/${jobId}?token=${opaqueToken}`;
        const wsUrl = rawUrl.startsWith("ws")
          ? rawUrl
          : `ws://${location.host}${rawUrl}`;

        try {
          const ws = new WebSocket(wsUrl);
          wsRef.current = ws;

          ws.onopen = () => resetHeartbeat();

          ws.onmessage = (ev) => {
            resetHeartbeat();
            try {
              handleWsMessage(JSON.parse(ev.data as string));
            } catch {
              // ignore malformed WS message
            }
          };

          ws.onerror = () => startPolling();
          ws.onclose = () => startPolling();
        } catch {
          startPolling();
        }
      })
      .catch(() => {
        // No Firebase token or /ws/token endpoint unreachable — fall back to polling
        if (!cancelled) startPolling();
      });

    return () => {
      cancelled = true;
      wsRef.current?.close();
      if (heartbeatTimer.current) clearTimeout(heartbeatTimer.current);
      stopPolling();
    };
  }, [jobId, upsertJob, startPolling, resetHeartbeat, handleWsMessage, stopPolling]);
}
