import { create } from "zustand";
import type { JobPollResponse } from "./types";

interface AppState {
  firebaseToken: string | null;
  setFirebaseToken: (token: string | null) => void;

  activeJobs: Record<string, JobPollResponse>;
  upsertJob: (job: JobPollResponse) => void;

  maintenanceMode: boolean;
  setMaintenanceMode: (v: boolean) => void;
}

export const useStore = create<AppState>((set) => ({
  firebaseToken: localStorage.getItem("firebase_token"),
  setFirebaseToken: (token) => {
    if (token) localStorage.setItem("firebase_token", token);
    else localStorage.removeItem("firebase_token");
    set({ firebaseToken: token });
  },

  activeJobs: {},
  upsertJob: (job) =>
    set((s) => ({ activeJobs: { ...s.activeJobs, [job.job_id]: job } })),

  maintenanceMode: false,
  setMaintenanceMode: (v) => set({ maintenanceMode: v }),
}));
