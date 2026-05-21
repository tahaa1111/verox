export interface Medication {
  drug_name: string;
  drug_name_normalized: string | null;
  dosage: string | null;
  frequency: string | null;
  duration: string | null;
  drug_class: string | null;
  confidence: number;
}

export interface PrescriptionResult {
  job_id: string;
  status: "queued" | "processing" | "completed" | "failed";
  patient_name: string | null;
  doctor_name: string | null;
  issue_date: string | null;
  medications: Medication[];
  overall_confidence: number;
  low_confidence_fields: string[];
  disclaimer: string;
  processing_time_ms: number | null;
  error_message: string | null;
}

export interface JobPollResponse {
  job_id: string;
  status: "queued" | "processing" | "completed" | "failed";
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
