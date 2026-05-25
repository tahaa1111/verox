/**
 * ResultsPage — Prescription OCR results.
 *
 * Layout (always):
 *  1. Loading spinner (while processing)
 *  2. Detected Text — per YOLO crop, with YOLO + model confidence badges
 *  3. Structured Prescription (patient/doctor/medications) — only if looks like a prescription
 *  4. Raw text labeling panel — always shown if any text was found
 *  5. Submit Correction + New Scan
 */

import { useParams, Link, useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import { useStore } from "../store";
import { useJobWs } from "../hooks/useJobWs";
import { pollJob, cancelJob } from "../api";
import { ProgressBar } from "../components/ProgressBar";
import { MedicationTable } from "../components/MedicationTable";
import { ConfidenceBadge } from "../components/ConfidenceBadge";
import type { PrescriptionResult, CropText } from "../types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function confColor(v: number): string {
  if (v >= 0.75) return "bg-emerald-100 text-emerald-800 border-emerald-200";
  if (v >= 0.50) return "bg-amber-100 text-amber-800 border-amber-200";
  return "bg-red-100 text-red-800 border-red-200";
}

function confLabel(v: number): string {
  if (v >= 0.75) return "High";
  if (v >= 0.50) return "Medium";
  return "Low";
}

// ---------------------------------------------------------------------------
// CropTexts — the star of the show: per-YOLO-crop text + confidence
// ---------------------------------------------------------------------------

function CropTexts({ crops, overallConf }: { crops: CropText[]; overallConf: number }) {
  const [expanded, setExpanded] = useState(true);
  if (!crops || crops.length === 0) return null;

  return (
    <div className="space-y-3">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center justify-between w-full group"
      >
        <div className="flex items-center gap-2">
          <h2 className="text-base font-semibold text-gray-900">Detected Text</h2>
          <span className="text-xs bg-blue-50 text-blue-700 px-2 py-0.5 rounded-full font-medium border border-blue-100">
            {crops.length} crop{crops.length !== 1 ? "s" : ""}
          </span>
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium border ${confColor(overallConf)}`}>
            {confLabel(overallConf)} confidence
          </span>
        </div>
        <svg
          className={`w-4 h-4 text-gray-400 transition-transform ${expanded ? "rotate-180" : ""}`}
          fill="none" viewBox="0 0 24 24" stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {expanded && (
        <div className="space-y-3">
          {crops.map((crop, i) => (
            <div key={i} className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden">
              {/* Crop header */}
              <div className="flex items-center gap-3 px-4 py-2.5 bg-gray-50 border-b border-gray-100">
                <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
                  Crop {crop.cell}
                </span>
                {/* YOLO confidence */}
                <div className="flex items-center gap-1">
                  <span className="text-xs text-gray-400">YOLO:</span>
                  <span className={`text-xs px-1.5 py-0.5 rounded font-mono font-medium border ${confColor(crop.yolo_confidence)}`}>
                    {(crop.yolo_confidence * 100).toFixed(0)}%
                  </span>
                </div>
                {/* Model read confidence */}
                <div className="flex items-center gap-1">
                  <span className="text-xs text-gray-400">Read:</span>
                  <span className={`text-xs px-1.5 py-0.5 rounded font-mono font-medium border ${confColor(crop.model_confidence)}`}>
                    {(crop.model_confidence * 100).toFixed(0)}%
                  </span>
                </div>
              </div>
              {/* Detected text */}
              <pre className="text-sm text-gray-800 font-mono whitespace-pre-wrap break-words leading-relaxed p-4">
                {crop.text || <span className="text-gray-300 italic">No text detected in this region</span>}
              </pre>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// PrescriptionDetails — Patient + Doctor + Medications in one unified section.
// Only renders rows / cards that have actual extracted values.
// ---------------------------------------------------------------------------

function InfoRow({ label, value }: { label: string; value: string | null | undefined }) {
  if (!value) return null;  // hide rows with no data — never show "—"
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs font-medium text-gray-400 uppercase tracking-wide">{label}</span>
      <span className="text-sm font-medium text-gray-900">{value}</span>
    </div>
  );
}

function PrescriptionDetails({
  result,
  needsReview,
}: {
  result: PrescriptionResult;
  needsReview: boolean;
}) {
  const firstName   = result.patient?.name       ?? result.patient_name ?? null;
  const lastName    = result.patient?.last_name  ?? null;
  const fullName    = [firstName, lastName].filter(Boolean).join(" ") || null;
  const address     = result.patient?.address    ?? null;
  const profession  = result.patient?.profession ?? null;
  const date        = result.issue_date          ?? null;
  const doctorName  = result.doctor?.name        ?? result.doctor_name  ?? null;
  const doctorStamp = result.doctor?.stamp       ?? null;

  const hasPatient = !!(fullName || address || profession || date);
  const hasDoctor  = !!(doctorName || doctorStamp);

  return (
    <div className="bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden">
      {/* Section header */}
      <div className="flex items-center gap-3 px-5 py-3.5 border-b border-gray-100 bg-gray-50 flex-wrap">
        <h2 className="text-base font-semibold text-gray-800">Prescription</h2>
        {needsReview ? (
          <span className="text-xs bg-amber-50 text-amber-700 px-2 py-0.5 rounded-full border border-amber-200 font-medium">
            ⚠ Needs review
          </span>
        ) : (
          <span className="text-xs bg-emerald-50 text-emerald-700 px-2 py-0.5 rounded-full border border-emerald-100 font-medium">
            ✓ Prescription detected
          </span>
        )}
        {result.overall_confidence != null && (
          <ConfidenceBadge value={result.overall_confidence} />
        )}
      </div>

      <div className="p-5 space-y-5">
        {/* Patient + Doctor — side by side, only shown if they have data */}
        {(hasPatient || hasDoctor) && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {hasPatient && (
              <div className="space-y-3">
                <div className="flex items-center gap-2">
                  <div className="w-7 h-7 bg-blue-100 rounded-full flex items-center justify-center flex-shrink-0">
                    <svg className="w-3.5 h-3.5 text-blue-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                        d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                    </svg>
                  </div>
                  <span className="text-sm font-semibold text-gray-700">Patient</span>
                </div>
                <div className="grid grid-cols-2 gap-2.5 pl-9">
                  <InfoRow label="Full Name"   value={fullName}   />
                  <InfoRow label="Profession"  value={profession} />
                  <InfoRow label="Address"     value={address}    />
                  <InfoRow label="Date"        value={date}       />
                </div>
              </div>
            )}

            {hasDoctor && (
              <div className="space-y-3">
                <div className="flex items-center gap-2">
                  <div className="w-7 h-7 bg-emerald-100 rounded-full flex items-center justify-center flex-shrink-0">
                    <svg className="w-3.5 h-3.5 text-emerald-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                        d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                  </div>
                  <span className="text-sm font-semibold text-gray-700">Prescribing Doctor</span>
                </div>
                <div className="grid grid-cols-1 gap-2.5 pl-9">
                  <InfoRow label="Name"             value={doctorName}  />
                  <InfoRow label="Clinic / Hospital" value={doctorStamp} />
                </div>
              </div>
            )}
          </div>
        )}

        {/* Low-confidence field warnings */}
        {(result.low_confidence_fields?.length ?? 0) > 0 && (
          <div className="flex items-start gap-2 bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 text-sm text-amber-800">
            <svg className="w-4 h-4 mt-0.5 flex-shrink-0 text-amber-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M12 9v2m0 4h.01m-6.938 4h13.856C19 19 20 17.657 20 16.19c0-.98-.503-1.84-1.263-2.37L12 3 5.263 13.82C4.503 14.35 4 15.21 4 16.19 4 17.657 4.982 19 6.144 19z"/>
            </svg>
            <span>
              Low confidence on: <strong>{result.low_confidence_fields.join(", ")}</strong> — verify before dispensing.
            </span>
          </div>
        )}

        {/* Medications — inside the same card */}
        {(result.medications?.length ?? 0) > 0 && (
          <div className="space-y-2">
            <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Medications</span>
            <MedicationTable medications={result.medications} />
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// RawTextLabelPanel — labeling only, shown below crop texts
// ---------------------------------------------------------------------------

const FIELD_PRESETS = [
  "Patient Information",
  "Doctor / Prescriber",
  "Medication",
  "Dosage & Instructions",
  "Date / Reference",
  "CNAM / Insurance",
  "Additional Notes",
  "Other",
];

function RawTextLabelPanel({ rawText }: { rawText: string }) {
  const chunks = rawText
    ? rawText.split(/\n{2,}/).map((c) => c.trim()).filter(Boolean)
    : [];
  const [labels, setLabels] = useState<Record<number, string>>({});
  const [expanded, setExpanded] = useState(false);

  if (chunks.length === 0) return null;

  return (
    <div className="space-y-3">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center justify-between w-full group"
      >
        <div className="flex items-center gap-2">
          <h2 className="text-base font-semibold text-gray-800">Label Text Blocks</h2>
          <span className="text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded-full font-medium">
            {chunks.length} block{chunks.length !== 1 ? "s" : ""}
          </span>
        </div>
        <svg
          className={`w-4 h-4 text-gray-400 transition-transform ${expanded ? "rotate-180" : ""}`}
          fill="none" viewBox="0 0 24 24" stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {expanded && (
        <>
          <p className="text-xs text-gray-500">
            Assign headers to text blocks not captured in the structured fields.
          </p>
          <div className="space-y-3">
            {chunks.map((chunk, i) => (
              <div key={i} className="bg-white rounded-xl border border-gray-100 shadow-sm p-4 space-y-3">
                <div className="flex items-center gap-2 flex-wrap">
                  <select
                    value={labels[i] ?? ""}
                    onChange={(e) => setLabels((prev) => ({ ...prev, [i]: e.target.value }))}
                    className="text-xs border border-gray-200 rounded-lg px-2.5 py-1.5 text-gray-600 bg-gray-50 focus:outline-none focus:ring-2 focus:ring-brand-500 transition"
                  >
                    <option value="">— assign header —</option>
                    {FIELD_PRESETS.map((p) => <option key={p} value={p}>{p}</option>)}
                  </select>
                  {labels[i] && (
                    <span className="text-xs bg-brand-50 text-brand-700 px-2 py-0.5 rounded-full font-medium border border-brand-100">
                      {labels[i]}
                    </span>
                  )}
                  <span className="ml-auto text-xs text-gray-300">Block {i + 1}</span>
                </div>
                <pre className="text-sm text-gray-700 font-mono whitespace-pre-wrap break-words leading-relaxed bg-gray-50 rounded-lg p-3 border border-gray-100">
                  {chunk}
                </pre>
              </div>
            ))}
          </div>
          <p className="text-xs text-gray-400 pt-1">
            ℹ️ Labels are local only — use <span className="font-medium text-gray-500">Submit Correction</span> to send to the system.
          </p>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function ResultsPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const navigate = useNavigate();
  const job = useStore((s) => (jobId ? s.activeJobs[jobId] : null));
  const upsertJob = useStore((s) => s.upsertJob);
  const [cancelling, setCancelling] = useState(false);

  const handleCancel = async () => {
    if (!jobId) return;
    setCancelling(true);
    try {
      await cancelJob(jobId);
      upsertJob({ job_id: jobId, status: "cancelled", progress_pct: 0, result: null, error_message: "Cancelled by user.", estimated_completion_s: null });
      navigate("/");
    } catch {
      setCancelling(false);
    }
  };

  useJobWs(jobId ?? null);

  // Initial load if not yet in store
  useEffect(() => {
    if (!jobId || job) return;
    pollJob(jobId).then(upsertJob).catch(() => {});
  }, [jobId, job, upsertJob]);

  if (!jobId) return <p className="text-red-600">No job ID.</p>;

  const isLoading = !job || (job.status !== "completed" && job.status !== "failed" && job.status !== "cancelled");
  const result: PrescriptionResult | null = job?.result as PrescriptionResult | null;

  const needsReview = !!(result?.requires_human_review);
  const reviewReasons: string[] = result?.review_reasons ?? [];
  // image_type from model. Old results without the field default to "prescription".
  const imageType = result?.image_type ?? "prescription";

  // "Prescription detected" section only shows for actual prescriptions with content,
  // not for drug boxes, unknown images, blank images, or all-zero-confidence results.
  const isPrescription = result
    ? imageType === "prescription" &&
      ((result.medications?.length ?? 0) > 0 ||
      !!(result.patient?.name ?? result.patient_name) ||
      !!(result.doctor?.name ?? result.doctor_name)) &&
      !reviewReasons.includes("all_drugs_low_confidence")
    : false;

  const cropTexts = result?.crop_texts ?? [];
  const overallConf = result?.overall_confidence ?? 0;

  return (
    <div className="space-y-6 max-w-3xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">OCR Results</h1>
          <p className="text-xs text-gray-400 font-mono mt-0.5">{jobId}</p>
        </div>
        <button
          onClick={() => navigate("/")}
          className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800 transition-colors"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
          New scan
        </button>
      </div>

      {/* Loading spinner */}
      {isLoading && (
        <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5 space-y-3">
          <div className="flex items-center gap-3">
            <div className="animate-spin rounded-full h-5 w-5 border-2 border-brand-600 border-t-transparent flex-shrink-0" />
            <span className="text-sm font-medium text-gray-700 capitalize">
              {job?.status ?? "Loading"}…
            </span>
            {job?.progress_pct != null && (
              <span className="text-xs text-gray-400">{job.progress_pct}%</span>
            )}
            <button
              onClick={handleCancel}
              disabled={cancelling}
              className="ml-auto flex items-center gap-1.5 text-xs text-red-500 hover:text-red-700 hover:bg-red-50 border border-red-200 hover:border-red-300 disabled:opacity-50 disabled:cursor-not-allowed px-3 py-1.5 rounded-lg transition-colors"
            >
              {cancelling ? (
                <div className="w-3 h-3 border border-red-400 border-t-transparent rounded-full animate-spin" />
              ) : (
                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12"/>
                </svg>
              )}
              {cancelling ? "Cancelling…" : "Cancel"}
            </button>
          </div>
          {job && <ProgressBar pct={job.progress_pct} />}
        </div>
      )}

      {/* Cancelled */}
      {job?.status === "cancelled" && (
        <div className="bg-gray-50 border border-gray-200 rounded-2xl p-4 text-sm text-gray-600 flex items-center gap-3">
          <svg className="w-5 h-5 flex-shrink-0 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636"/>
          </svg>
          <div>
            <p className="font-semibold text-gray-700">Job cancelled</p>
            <p className="mt-0.5 text-gray-500">This scan was cancelled. Start a new scan to try again.</p>
          </div>
          <button
            onClick={() => navigate("/")}
            className="ml-auto bg-brand-600 hover:bg-brand-700 text-white font-medium text-sm px-4 py-2 rounded-xl transition-colors"
          >
            New Scan
          </button>
        </div>
      )}

      {/* Error */}
      {job?.status === "failed" && (
        <div className="bg-red-50 border border-red-200 rounded-2xl p-4 text-sm text-red-700">
          <p className="font-semibold">Processing failed</p>
          <p className="mt-1 text-red-600">{job.error_message ?? "An unknown error occurred."}</p>
          <button
            onClick={() => navigate("/")}
            className="mt-3 bg-brand-600 hover:bg-brand-700 text-white font-medium text-sm px-4 py-2 rounded-xl transition-colors"
          >
            New Scan
          </button>
        </div>
      )}

      {result && (
        <>
          {/* ① DETECTED TEXT — always shown first */}
          {cropTexts.length > 0 ? (
            <CropTexts crops={cropTexts} overallConf={overallConf} />
          ) : result.extracted_raw_text ? (
            /* Fallback: model returned raw text but no per-crop breakdown */
            <div className="bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden">
              <div className="flex items-center gap-3 px-4 py-3 bg-gray-50 border-b border-gray-100">
                <h2 className="text-base font-semibold text-gray-900">Detected Text</h2>
                <span className={`text-xs px-2 py-0.5 rounded-full font-medium border ${confColor(overallConf)}`}>
                  {confLabel(overallConf)} confidence · {(overallConf * 100).toFixed(0)}%
                </span>
              </div>
              <pre className="text-sm text-gray-800 font-mono whitespace-pre-wrap break-words leading-relaxed p-4">
                {result.extracted_raw_text}
              </pre>
            </div>
          ) : null}

          {/* ── Non-prescription image type banners ── */}
          {imageType === "drug_box" && (
            <div className="bg-blue-50 border border-blue-200 rounded-xl px-4 py-4 flex items-start gap-3">
              <span className="text-2xl">💊</span>
              <div>
                <p className="text-sm font-semibold text-blue-800">Drug Box / Medication Packaging</p>
                <p className="text-xs text-blue-700 mt-0.5">
                  This image shows a medication box or packaging — not a prescription.
                  No prescription data was extracted. Please upload a doctor's prescription instead.
                </p>
                {result?.extracted_raw_text && (
                  <p className="text-xs text-blue-600 mt-1">
                    Text on box: <span className="font-mono">{result.extracted_raw_text}</span>
                  </p>
                )}
              </div>
            </div>
          )}

          {imageType === "blank" && (
            <div className="bg-gray-50 border border-gray-200 rounded-xl px-4 py-4 flex items-start gap-3">
              <span className="text-2xl">🖼️</span>
              <div>
                <p className="text-sm font-semibold text-gray-700">Blank / Unreadable Image</p>
                <p className="text-xs text-gray-600 mt-0.5">
                  The image appears to be empty or unreadable. Please upload a clear photo of the prescription.
                </p>
              </div>
            </div>
          )}

          {imageType === "unknown" && (
            <div className="bg-orange-50 border border-orange-200 rounded-xl px-4 py-4 flex items-start gap-3">
              <span className="text-2xl">❓</span>
              <div>
                <p className="text-sm font-semibold text-orange-800">Unrecognized Image</p>
                <p className="text-xs text-orange-700 mt-0.5">
                  This image does not appear to be a medical prescription.
                  Please upload a doctor's prescription to extract medication data.
                </p>
                {result?.extracted_raw_text && (
                  <p className="text-xs text-orange-600 mt-1 font-mono">{result.extracted_raw_text}</p>
                )}
              </div>
            </div>
          )}

          {/* Low confidence fallback (old model, no image_type) */}
          {imageType === "prescription" && !isPrescription && overallConf < 0.3 && (
            <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 text-sm text-amber-800 flex items-start gap-2">
              <svg className="w-4 h-4 mt-0.5 flex-shrink-0 text-amber-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M12 9v2m0 4h.01m-6.938 4h13.856C19 19 20 17.657 20 16.19c0-.98-.503-1.84-1.263-2.37L12 3 5.263 13.82C4.503 14.35 4 15.21 4 16.19 4 17.657 4.982 19 6.144 19z"/>
              </svg>
              <span>
                <strong>Low confidence ({(overallConf * 100).toFixed(0)}%).</strong> The image may not be a prescription,
                or the text was not legible enough to extract structured data.
              </span>
            </div>
          )}

          {/* ② PRESCRIPTION STRUCTURE — Patient + Doctor + Medications in one card */}
          {isPrescription && (
            <PrescriptionDetails result={result} needsReview={needsReview} />
          )}

          {/* ③ LABEL TEXT BLOCKS — collapsed by default */}
          {result.extracted_raw_text && (
            <RawTextLabelPanel rawText={result.extracted_raw_text} />
          )}

          {/* Additional notes */}
          {result.additional_notes && (
            <div className="bg-gray-50 border border-gray-200 rounded-xl px-4 py-3 text-sm text-gray-700">
              <span className="font-semibold text-gray-600 block text-xs uppercase tracking-wide mb-1">
                Additional Notes
              </span>
              {result.additional_notes}
            </div>
          )}

          {/* Disclaimer */}
          <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 text-sm text-amber-900">
            {result.disclaimer ?? "⚠️ Clinical decision-support only. Pharmacist verification required before dispensing."}
          </div>

          {/* Actions */}
          <div className="flex gap-3 pt-1">
            <Link
              to={`/corrections/${jobId}`}
              className="border border-gray-300 hover:bg-gray-50 text-gray-700 font-medium text-sm px-4 py-2 rounded-xl transition-colors"
            >
              Submit Correction
            </Link>
            <button
              onClick={() => navigate("/")}
              className="bg-brand-600 hover:bg-brand-700 text-white font-semibold text-sm px-5 py-2 rounded-xl transition-colors"
            >
              New Scan
            </button>
          </div>
        </>
      )}
    </div>
  );
}
