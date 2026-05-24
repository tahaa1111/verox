import { useEffect, useRef, useCallback } from "react";
import { useStore } from "../store";
import { pollJob } from "../api";
import type { JobPollResponse } from "../types";

const WS_BASE = import.meta.env.VITE_WS_BASE_URL || "";
const POLL_INTERVAL_MS = 1_000;   // 1 s — fast enough to catch completion quickly
const HEARTBEAT_TIMEOUT_MS = 20_000;

export function useJobWs(jobId: string | null) {
  const upsertJob = useStore((s) => s.upsertJob);
  const token = useStore((s) => s.firebaseToken);
  const wsRef = useRef<WebSocket | null>(null);
  const heartbeatTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const startPolling = useCallback(() => {
    if (pollTimer.current) return;
    pollTimer.current = setInterval(async () => {
      if (!jobId) return;
      try {
        const job = await pollJob(jobId);
        upsertJob(job);
        if (job.status === "completed" || job.status === "failed") {
          clearInterval(pollTimer.current!);
          pollTimer.current = null;
        }
      } catch {
        // network error — keep polling
      }
    }, POLL_INTERVAL_MS);
  }, [jobId, upsertJob]);

  const resetHeartbeat = useCallback(() => {
    if (heartbeatTimer.current) clearTimeout(heartbeatTimer.current);
    heartbeatTimer.current = setTimeout(() => {
      // WS silent for too long — fall back to polling
      wsRef.current?.close();
      startPolling();
    }, HEARTBEAT_TIMEOUT_MS);
  }, [startPolling]);

  useEffect(() => {
    if (!jobId) return;

    const wsUrl = `${WS_BASE}/v1/ws/jobs/${jobId}${token ? `?token=${token}` : ""}`;

    try {
      const ws = new WebSocket(wsUrl.startsWith("ws") ? wsUrl : `ws://${location.host}${wsUrl}`);
      wsRef.current = ws;

      ws.onopen = () => resetHeartbeat();

      ws.onmessage = (ev) => {
        resetHeartbeat();
        try {
          const msg = JSON.parse(ev.data as string);
          // Backend uses `event` field (not `type`) — see WsCompletedEvent / WsHeartbeatEvent
          if (msg.event === "heartbeat") return;

          if (msg.event === "completed") {
            upsertJob({
              job_id: jobId,
              status: "completed",
              progress_pct: 100,
              result: msg.result ?? null,
              error_message: null,
              estimated_completion_s: null,
            } as JobPollResponse);
          } else if (msg.event === "failed") {
            upsertJob({
              job_id: jobId,
              status: "failed",
              progress_pct: 0,
              result: null,
              error_message: msg.error ?? msg.error_message ?? "Processing failed",
              estimated_completion_s: null,
            } as JobPollResponse);
          } else if (msg.event === "inference_progress" || msg.event === "inference_started") {
            upsertJob({
              job_id: jobId,
              status: "processing",
              progress_pct: Math.round((msg.progress ?? 0) * 100),
              result: null,
              error_message: null,
              estimated_completion_s: null,
            } as JobPollResponse);
          } else if (msg.event === "cancelled") {
            upsertJob({
              job_id: jobId,
              status: "cancelled",
              progress_pct: 0,
              result: null,
              error_message: "Cancelled by user.",
              estimated_completion_s: null,
            } as JobPollResponse);
          } else if (msg.event === "status") {
            // Initial state sent by the server when WS first connects
            if (msg.status === "completed" && msg.result) {
              upsertJob({
                job_id: jobId,
                status: "completed",
                progress_pct: 100,
                result: msg.result,
                error_message: null,
                estimated_completion_s: null,
              } as JobPollResponse);
            }
          }
        } catch {
          // ignore malformed WS message
        }
      };

      ws.onerror = () => startPolling();
      ws.onclose = () => startPolling();
    } catch {
      startPolling();
    }

    return () => {
      wsRef.current?.close();
      if (heartbeatTimer.current) clearTimeout(heartbeatTimer.current);
      if (pollTimer.current) clearInterval(pollTimer.current);
    };
  }, [jobId, token, upsertJob, startPolling, resetHeartbeat]);
}
