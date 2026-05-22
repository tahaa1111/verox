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

export async function getCameraSnapshot(deviceId = "pi-0001"): Promise<{ frame: string }> {
  const { data } = await http.get<{ frame: string }>(`/camera/snapshot?device_id=${deviceId}`);
  return data;
}

export async function submitCapture(deviceId = "pi-0001"): Promise<SubmitResponse> {
  const { data } = await http.post<SubmitResponse>(`/camera/capture?device_id=${deviceId}`);
  return data;
}

/** Signal the Pi to start the camera (frontend-triggered via Redis command relay). */
export async function signalCameraStart(deviceId = "pi-0001"): Promise<void> {
  await http.post(`/camera/start?device_id=${deviceId}`);
}

/** Signal the Pi to stop the camera. */
export async function signalCameraStop(deviceId = "pi-0001"): Promise<void> {
  await http.post(`/camera/stop?device_id=${deviceId}`);
}
