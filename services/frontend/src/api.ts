import axios from "axios";
import type { CameraSnapshotResponse, JobPollResponse, SubmitResponse } from "./types";

const BASE = import.meta.env.VITE_API_BASE_URL || "/v1";

const http = axios.create({ baseURL: BASE, timeout: 30_000 });

http.interceptors.request.use((config) => {
  const token = localStorage.getItem("firebase_token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

/** Poll a job by ID for status + result. */
export async function pollJob(jobId: string): Promise<JobPollResponse> {
  const { data } = await http.get<JobPollResponse>(`/result/${jobId}`);
  return data;
}

export async function submitCorrection(
  jobId: string,
  correctedJson: object
): Promise<void> {
  // Backend CorrectionRequest expects corrected_data (matches DB column name)
  await http.post(`/corrections/${jobId}`, { corrected_data: correctedJson });
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

/**
 * Get the latest camera snapshot plus stability progress and latest job_id.
 * Returns stable_progress (0→1) from Pi stability tracker, and latest_job_id
 * once the Pi auto-submits after a stable hold.
 */
export async function getCameraSnapshot(
  deviceId = "pi-0001"
): Promise<CameraSnapshotResponse> {
  const { data } = await http.get<CameraSnapshotResponse>(
    `/camera/snapshot?device_id=${deviceId}`
  );
  return data;
}

/** Manual capture: take the current frame from Redis and submit as a job. */
export async function submitCapture(deviceId = "pi-0001"): Promise<SubmitResponse> {
  const { data } = await http.post<SubmitResponse>(
    `/camera/capture?device_id=${deviceId}`
  );
  return data;
}

/** Signal the Pi to start the camera (via Redis command relay). */
export async function signalCameraStart(deviceId = "pi-0001"): Promise<void> {
  await http.post(`/camera/start?device_id=${deviceId}`);
}

/** Signal the Pi to stop the camera. */
export async function signalCameraStop(deviceId = "pi-0001"): Promise<void> {
  await http.post(`/camera/stop?device_id=${deviceId}`);
}

/** Cancel a queued or in-progress job. */
export async function cancelJob(jobId: string): Promise<void> {
  await http.post(`/jobs/${jobId}/cancel`);
}

/**
 * Submit one or more base64-encoded images directly to the pipeline,
 * bypassing the Pi/YOLO layer. Each image is treated as a full-frame crop.
 * Returns the job_id for tracking via WebSocket / results page.
 */
export async function submitImages(
  imagesB64: string[],
  filename = "upload"
): Promise<string> {
  const crops = imagesB64.map((b64, i) => ({
    image_base64: b64,
    bbox:         [0, 0, 1, 1],
    confidence:   1.0,
    track_id:     i,
  }));
  const { data } = await http.post<SubmitResponse>("/submit", {
    device_id:  "upload",
    session_id: `upload-${Date.now()}-${filename.replace(/[^a-z0-9]/gi, "")}`,
    crops,
  });
  return data.job_id;
}

/**
 * Exchange the Firebase JWT (Authorization header) for a short-lived opaque
 * WebSocket token (TTL: 30s). The opaque token is passed as ?token= in the
 * WebSocket URL so the Firebase JWT never appears in server logs or browser history.
 */
export async function getWsToken(): Promise<string> {
  const { data } = await http.post<{ ws_token: string; expires_in: number }>("/ws/token");
  return data.ws_token;
}
