import { useParams, Link } from "react-router-dom";
import { useEffect } from "react";
import { useStore } from "../store";
import { useJobWs } from "../hooks/useJobWs";
import { pollJob } from "../api";
import { ProgressBar } from "../components/ProgressBar";
import { MedicationTable } from "../components/MedicationTable";
import { ConfidenceBadge } from "../components/ConfidenceBadge";

export function JobTrackerPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const job = useStore((s) => (jobId ? s.activeJobs[jobId] : null));
  const upsertJob = useStore((s) => s.upsertJob);

  useJobWs(jobId ?? null);

  // Initial load if not yet in store
  useEffect(() => {
    if (!jobId || job) return;
    pollJob(jobId).then(upsertJob).catch(() => {});
  }, [jobId, job, upsertJob]);

  if (!jobId) return <p className="text-red-600">No job ID.</p>;
  if (!job) return <div className="animate-pulse text-gray-500">Loading job {jobId}…</div>;

  const result = job.result;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Job Result</h1>
        <span className="text-xs text-gray-400 font-mono">{jobId}</span>
      </div>

      {/* Status + progress */}
      <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-3">
        <div className="flex items-center justify-between text-sm">
          <span className="font-medium capitalize text-gray-700">Status: {job.status}</span>
          {job.status === "processing" && (
            <span className="text-gray-400">{job.progress_pct}%</span>
          )}
        </div>
        {job.status !== "completed" && job.status !== "failed" && (
          <ProgressBar pct={job.progress_pct} />
        )}
        {job.status === "failed" && (
          <p className="text-red-600 text-sm">{job.error_message ?? "Unknown error"}</p>
        )}
      </div>

      {/* Result */}
      {result && (
        <>
          <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-3">
            <h2 className="font-semibold text-gray-800">Prescription Details</h2>
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <span className="text-gray-500">Patient:</span>{" "}
                <span className="font-medium">{result.patient_name ?? "—"}</span>
              </div>
              <div>
                <span className="text-gray-500">Doctor:</span>{" "}
                <span className="font-medium">{result.doctor_name ?? "—"}</span>
              </div>
              <div>
                <span className="text-gray-500">Issue Date:</span>{" "}
                <span className="font-medium">{result.issue_date ?? "—"}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-gray-500">Overall Confidence:</span>
                <ConfidenceBadge value={result.overall_confidence} />
              </div>
            </div>

            {result.low_confidence_fields.length > 0 && (
              <div className="bg-yellow-50 border border-yellow-200 rounded px-3 py-2 text-xs text-yellow-800">
                Low confidence: {result.low_confidence_fields.join(", ")}
              </div>
            )}
          </div>

          <div className="bg-white rounded-lg border border-gray-200 p-4">
            <h2 className="font-semibold text-gray-800 mb-3">Medications</h2>
            <MedicationTable medications={result.medications} />
          </div>

          <div className="bg-amber-50 border border-amber-200 rounded px-4 py-2 text-sm text-amber-900">
            {result.disclaimer}
          </div>

          <div className="flex gap-3">
            <Link
              to={`/corrections/${jobId}`}
              className="bg-white border border-gray-300 hover:bg-gray-50 text-gray-700 font-medium text-sm px-4 py-2 rounded-md transition-colors"
            >
              Submit Correction
            </Link>
            <Link
              to="/"
              className="bg-brand-600 hover:bg-brand-700 text-white font-medium text-sm px-4 py-2 rounded-md transition-colors"
            >
              New Scan
            </Link>
          </div>
        </>
      )}
    </div>
  );
}
