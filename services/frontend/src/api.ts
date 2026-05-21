import axios from "axios";
import type { JobPollResponse, SubmitResponse } from "./types";

const BASE = import.meta.env.VITE_API_BASE_URL || "/v1";

const http = axios.create({ baseURL: BASE, timeout: 30_000 });

http.interceptors.request.use((config) => {
  const token = localStorage.getItem("firebase_token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

export async function submitPrescription(
  deviceId: string,
  sessionId: string,
  crops: File[]
): Promise<SubmitResponse> {
  const form = new FormData();
  form.append("device_id", deviceId);
  form.append("session_id", sessionId);
  crops.forEach((f) => form.append("crops", f));
  const { data } = await http.post<SubmitResponse>("/submit", form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data;
}

export async function pollJob(jobId: string): Promise<JobPollResponse> {
  const { data } = await http.get<JobPollResponse>(`/result/${jobId}`);
  return data;
}

export async function submitCorrection(
  jobId: string,
  correctedJson: object
): Promise<void> {
  await http.post(`/corrections/${jobId}`, { corrected_json: correctedJson });
}

export async function getAdminModels(): Promise<unknown> {
  const { data } = await http.get("/admin/models");
  return data;
}

export async function rollbackModel(deployedModelId: string): Promise<void> {
  await http.post("/admin/model/rollback", { deployed_model_id: deployedModelId });
}

export async function setMaintenance(active: boolean, reason: string): Promise<void> {
  await http.post("/admin/maintenance", { active, reason });
}
