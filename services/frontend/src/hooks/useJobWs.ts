import { useEffect, useRef, useCallback } from "react";
import { useStore } from "../store";
import { pollJob } from "../api";
import type { JobPollResponse } from "../types";

const WS_BASE = import.meta.env.VITE_WS_BASE_URL || "";
const POLL_INTERVAL_MS = 3_000;
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
          if (msg.type === "heartbeat") return;
          if (msg.type === "completed" || msg.type === "failed" || msg.type === "progress") {
            const partial: Partial<JobPollResponse> = {
              job_id: jobId,
              status: msg.type === "completed" ? "completed" : msg.type === "failed" ? "failed" : "processing",
              progress_pct: msg.progress_pct ?? 0,
              result: msg.result ?? null,
              error_message: msg.error_message ?? null,
              estimated_completion_s: null,
            };
            upsertJob(partial as JobPollResponse);
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
