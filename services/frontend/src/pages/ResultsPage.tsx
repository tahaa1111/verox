/**
 * ResultsPage — Prescription OCR results.
 *
 * Shows:
 *  - Real-time job progress (polling + WebSocket)
 *  - Structured patient + doctor block
 *  - Medication table with CNAM reimbursement column
 *  - Overall confidence + review flags
 *  - Submit Correction link
 */

import { useParams, Link, useNavigate } from "react-router-dom";
import { useEffect } from "react";
import { useStore } from "../store";
import { useJobWs } from "../hooks/useJobWs";
import { pollJob } from "../api";
import { ProgressBar } from "../components/ProgressBar";
import { MedicationTable } from "../components/MedicationTable";
import { ConfidenceBadge } from "../components/ConfidenceBadge";
import type { PrescriptionResult } from "../types";

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function InfoRow({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs font-medium text-gray-400 uppercase tracking-wide">{label}</span>
      <span className="text-sm font-medium text-gray-900">{value ?? <span className="text-gray-300 italic">—</span>}</span>
    </div>
  );
}

function PatientCard({ result }: { result: PrescriptionResult }) {
  // Support both new structured schema and legacy flat fields
  const firstName = result.patient?.name ?? result.patient_name ?? null;
  const lastName = result.patient?.last_name ?? null;
  const fullName = [firstName, lastName].filter(Boolean).join(" ") || null;
  const address = result.patient?.address ?? null;
  const profession = result.patient?.profession ?? null;
  const doctorName = result.doctor?.name ?? result.doctor_name ?? null;
  const doctorStamp = result.doctor?.stamp ?? null;

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
      {/* Patient */}
      <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5 space-y-3">
        <div className="flex items-center gap-2 mb-1">
          <div className="w-8 h-8 bg-blue-100 rounded-full flex items-center justify-center">
            <svg className="w-4 h-4 text-blue-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
            </svg>
          </div>
          <h3 className="font-semibold text-gray-800">Patient</h3>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <InfoRow label="Full Name" value={fullName} />
          <InfoRow label="Profession" value={profession} />
          <InfoRow label="Address" value={address} />
          <InfoRow label="Date" value={result.issue_date} />
        </div>
      </div>

      {/* Doctor */}
      <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5 space-y-3">
        <div className="flex items-center gap-2 mb-1">
          <div className="w-8 h-8 bg-emerald-100 rounded-full flex items-center justify-center">
            <svg className="w-4 h-4 text-emerald-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </div>
          <h3 className="font-semibold text-gray-800">Prescribing Doctor</h3>
        </div>
        <div className="grid grid-cols-1 gap-3">
          <InfoRow label="Name" value={doctorName} />
          <InfoRow label="Clinic / Stamp" value={doctorStamp} />
        </div>
        {/* Confidence */}
        <div className="pt-2 border-t border-gray-100 flex items-center gap-2">
          <span className="text-xs text-gray-400 uppercase tracking-wide">Overall Confidence</span>
          <ConfidenceBadge value={result.overall_confidence} />
        </div>
      </div>
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

  useJobWs(jobId ?? null);

  // Initial load if not yet in store
  useEffect(() => {
    if (!jobId || job) return;
    pollJob(jobId).then(upsertJob).catch(() => {});
  }, [jobId, job, upsertJob]);

  if (!jobId) return <p className="text-red-600">No job ID.</p>;

  const isLoading = !job || (job.status !== "completed" && job.status !== "failed");
  const result = job?.result;

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

      {/* Progress card */}
      {isLoading && (
        <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5 space-y-3">
          <div className="flex items-center gap-3">
            <div className="animate-spin rounded-full h-5 w-5 border-2 border-brand-600 border-t-transparent" />
            <span className="text-sm font-medium text-gray-700 capitalize">
              {job?.status ?? "Loading"}…
            </span>
            {job?.progress_pct != null && (
              <span className="text-xs text-gray-400 ml-auto">{job.progress_pct}%</span>
            )}
          </div>
          {job && <ProgressBar pct={job.progress_pct} />}
        </div>
      )}

      {/* Error */}
      {job?.status === "failed" && (
        <div className="bg-red-50 border border-red-200 rounded-2xl p-4 text-sm text-red-700">
          <p className="font-semibold">Processing failed</p>
          <p className="mt-1 text-red-600">{job.error_message ?? "An unknown error occurred."}</p>
        </div>
      )}

      {/* Results */}
      {result && (
        <>
          {/* Low-confidence warning */}
          {result.low_confidence_fields?.length > 0 && (
            <div className="flex items-start gap-2 bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 text-sm text-amber-800">
              <svg className="w-4 h-4 mt-0.5 flex-shrink-0 text-amber-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M12 9v2m0 4h.01m-6.938 4h13.856C19.0 19 20 17.657 20 16.19c0-.98-.503-1.84-1.263-2.37L12 3 5.263 13.82C4.503 14.35 4 15.21 4 16.19 4 17.657 4.982 19 6.144 19z"/>
              </svg>
              <span>Low confidence on: <strong>{result.low_confidence_fields.join(", ")}</strong> — verify before dispensing.</span>
            </div>
          )}

          {/* Patient + Doctor cards */}
          <PatientCard result={result} />

          {/* Medications */}
          <div className="space-y-2">
            <h2 className="text-base font-semibold text-gray-800">Medications</h2>
            <MedicationTable medications={result.medications} />
          </div>

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
