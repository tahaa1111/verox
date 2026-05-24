export interface Medication {
  drug_name: string;
  drug_name_normalized: string | null;
  dosage: string | null;
  frequency: string | null;
  duration: string | null;
  quantity: string | null;
  drug_class: string | null;
  confidence: number;
  cnam: boolean;
}

export interface PatientInfo {
  name: string | null;
  last_name: string | null;
  address: string | null;
  profession: string | null;
}

export interface DoctorInfo {
  name: string | null;
  stamp: string | null;
}

export interface CropText {
  cell: number;
  track_id: number;
  text: string;
  model_confidence: number;   // LLM's confidence for reading this cell (0–1)
  yolo_confidence: number;    // YOLO's detection confidence for this crop (0–1)
}

export interface PrescriptionResult {
  job_id: string;
  status: "queued" | "processing" | "completed" | "failed";
  // Structured patient/doctor blocks (new schema)
  patient: PatientInfo | null;
  doctor: DoctorInfo | null;
  // Legacy flat fields (old schema — kept for backwards compat)
  patient_name: string | null;
  doctor_name: string | null;
  issue_date: string | null;
  medications: Medication[];
  overall_confidence: number;
  low_confidence_fields: string[];
  disclaimer: string;
  processing_time_ms: number | null;
  error_message: string | null;
  /**
   * Verbatim transcription of ALL text the model detected on the prescription.
   * Parsed into contextual blocks in the UI so the pharmacist can label them.
   */
  extracted_raw_text: string;
  additional_notes: string | null;
  /**
   * Per-YOLO-crop text with detection + model confidence scores.
   * Populated from the model's cell_texts output merged with Pi YOLO confidence.
   */
  crop_texts: CropText[];
  /** True if the structured fields look like a prescription. */
  requires_human_review?: boolean;
  review_reasons?: string[];
}

export interface JobPollResponse {
  job_id: string;
  status: "queued" | "processing" | "completed" | "failed" | "cancelled";
  progress_pct: number;
  estimated_completion_s: number | null;
  result: PrescriptionResult | null;
  error_message: string | null;
}

export interface SubmitResponse {
  job_id: string;
  status: string;
  estimated_completion_s: number;
  ws_url: string;
}

export interface ModelVersion {
  id: string;
  display_name: string;
  vertex_model_resource_name: string;
  vertex_deployed_model_id: string | null;
  eval_drug_f1: number | null;
  eval_json_validity: number | null;
  deployed_at: string | null;
  is_current: boolean;
}

/** Snapshot response now includes stable_progress and latest_job_id from Pi */
export interface CameraSnapshotResponse {
  frame: string;
  device_id: string;
  stable_progress: number;       // 0.0 → 1.0 from Pi stability tracker
  latest_job_id: string | null;  // set after Pi auto-submit
}
