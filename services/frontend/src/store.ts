import { create } from "zustand";
import type { JobPollResponse } from "./types";

export interface ScanHistoryEntry {
  job_id: string;
  scanned_at: string;
  patient_name: string | null;
  doctor_name: string | null;
  drug_count: number;
  confidence: number;
  requires_review: boolean;
  status: "completed" | "failed";
  error_message: string | null;
}

const HISTORY_KEY    = "medibox_scan_history";
const QUEUE_KEY      = "medibox_job_queue";   // array of in-flight job IDs
const MAX_HISTORY    = 100;
const TERMINAL       = new Set(["completed", "failed", "cancelled"]);

// ── Persistence helpers ───────────────────────────────────────────────────────

function loadHistory(): ScanHistoryEntry[] {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY) ?? "[]"); }
  catch { return []; }
}
function saveHistory(e: ScanHistoryEntry[]) {
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(e.slice(0, MAX_HISTORY))); }
  catch { /* full */ }
}

function loadQueue(): string[] {
  try { return JSON.parse(localStorage.getItem(QUEUE_KEY) ?? "[]"); }
  catch { return []; }
}
function saveQueue(q: string[]) {
  try { localStorage.setItem(QUEUE_KEY, JSON.stringify(q)); }
  catch { /* full */ }
}

// ── State interface ───────────────────────────────────────────────────────────

interface AppState {
  firebaseToken: string | null;
  setFirebaseToken: (token: string | null) => void;

  isAdmin: boolean;
  setIsAdmin: (v: boolean) => void;

  activeJobs: Record<string, JobPollResponse>;
  upsertJob: (job: JobPollResponse) => void;

  maintenanceMode: boolean;
  setMaintenanceMode: (v: boolean) => void;

  scanHistory: ScanHistoryEntry[];
  clearHistory: () => void;

  /**
   * Ordered list of job IDs currently in the pipeline (queued → processing).
   * Multiple scans can be submitted back-to-back; each gets tracked here.
   * Cleared automatically when a job reaches a terminal state.
   * Persisted in localStorage so page-refresh keeps the queue.
   */
  jobQueue: string[];
  addToQueue: (jobId: string) => void;
  removeFromQueue: (jobId: string) => void;   // manual removal (e.g. cancel)
}

// ── Store ─────────────────────────────────────────────────────────────────────

export const useStore = create<AppState>((set) => ({
  firebaseToken: localStorage.getItem("firebase_token"),
  setFirebaseToken: (token) => {
    if (token) localStorage.setItem("firebase_token", token);
    else localStorage.removeItem("firebase_token");
    set({ firebaseToken: token });
  },

  isAdmin: false,
  setIsAdmin: (v) => set({ isAdmin: v }),

  activeJobs: {},
  upsertJob: (job) =>
    set((s) => {
      const updated = { activeJobs: { ...s.activeJobs, [job.job_id]: job } };
      let extra: Partial<AppState> = {};

      // Auto-remove from queue when job reaches a terminal state
      if (TERMINAL.has(job.status) && s.jobQueue.includes(job.job_id)) {
        const newQueue = s.jobQueue.filter((id) => id !== job.job_id);
        saveQueue(newQueue);
        extra = { ...extra, jobQueue: newQueue };
      }

      // Auto-save to scan history on first terminal state (completed / failed)
      if (
        (job.status === "completed" || job.status === "failed") &&
        s.scanHistory.findIndex((h) => h.job_id === job.job_id) === -1
      ) {
        const entry: ScanHistoryEntry = {
          job_id:          job.job_id,
          scanned_at:      new Date().toISOString(),
          patient_name:    job.result?.patient_name ?? null,
          doctor_name:     job.result?.doctor_name  ?? null,
          drug_count:      job.result?.medications?.length ?? 0,
          confidence:      job.result?.overall_confidence ?? 0,
          requires_review: job.result?.requires_human_review ?? false,
          status:          job.status as "completed" | "failed",
          error_message:   job.error_message ?? null,
        };
        const newHistory = [entry, ...s.scanHistory];
        saveHistory(newHistory);
        extra = { ...extra, scanHistory: newHistory };
      }

      return { ...updated, ...extra };
    }),

  maintenanceMode: false,
  setMaintenanceMode: (v) => set({ maintenanceMode: v }),

  scanHistory: loadHistory(),
  clearHistory: () => { saveHistory([]); set({ scanHistory: [] }); },

  jobQueue: loadQueue(),
  addToQueue: (jobId) =>
    set((s) => {
      if (s.jobQueue.includes(jobId)) return s;
      const newQueue = [...s.jobQueue, jobId];
      saveQueue(newQueue);
      return { jobQueue: newQueue };
    }),
  removeFromQueue: (jobId) =>
    set((s) => {
      const newQueue = s.jobQueue.filter((id) => id !== jobId);
      saveQueue(newQueue);
      return { jobQueue: newQueue };
    }),
}));
