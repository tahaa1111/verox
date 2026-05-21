import { useParams, Link, useNavigate } from "react-router-dom";
import { useState, useEffect } from "react";
import { useStore } from "../store";
import { pollJob, submitCorrection } from "../api";
import type { PrescriptionResult } from "../types";

export function CorrectionsPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const navigate = useNavigate();
  const job = useStore((s) => (jobId ? s.activeJobs[jobId] : null));
  const upsertJob = useStore((s) => s.upsertJob);

  const [original, setOriginal] = useState<PrescriptionResult | null>(null);
  const [editedJson, setEditedJson] = useState("");
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!jobId) return;
    const load = async () => {
      let j = job;
      if (!j) {
        j = await pollJob(jobId);
        upsertJob(j);
      }
      if (j.result) {
        setOriginal(j.result);
        setEditedJson(JSON.stringify(j.result, null, 2));
      }
    };
    load().catch(() => {});
  }, [jobId, job, upsertJob]);

  const onTextChange = (value: string) => {
    setEditedJson(value);
    try {
      JSON.parse(value);
      setJsonError(null);
    } catch (e) {
      setJsonError(String(e));
    }
  };

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!jobId || jsonError) return;
    setSubmitting(true);
    try {
      const corrected = JSON.parse(editedJson);
      await submitCorrection(jobId, corrected);
      setDone(true);
      setTimeout(() => navigate(`/jobs/${jobId}`), 1500);
    } catch (err) {
      setJsonError(String(err));
    } finally {
      setSubmitting(false);
    }
  };

  if (!jobId) return <p className="text-red-600">No job ID.</p>;
  if (done) return <div className="text-green-700 font-medium">Correction submitted. Redirecting…</div>;

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Submit Correction</h1>
        <Link to={`/jobs/${jobId}`} className="text-sm text-brand-600 hover:underline">
          ← Back to result
        </Link>
      </div>

      <p className="text-sm text-gray-600">
        Edit the extracted JSON below to correct any errors. Your correction
        will be stored and used to improve the model at the next retraining cycle.
      </p>

      <form onSubmit={onSubmit} className="space-y-4">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* Original (read-only) */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Original extraction (read-only)
            </label>
            <textarea
              readOnly
              className="w-full h-96 font-mono text-xs border border-gray-200 rounded-md p-3 bg-gray-50 resize-none"
              value={original ? JSON.stringify(original, null, 2) : "Loading…"}
            />
          </div>

          {/* Editable */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Corrected JSON
            </label>
            <textarea
              className="w-full h-96 font-mono text-xs border border-gray-300 rounded-md p-3 resize-none focus:outline-none focus:ring-2 focus:ring-brand-500"
              value={editedJson}
              onChange={(e) => onTextChange(e.target.value)}
              spellCheck={false}
            />
            {jsonError && (
              <p className="text-red-600 text-xs mt-1">{jsonError}</p>
            )}
          </div>
        </div>

        <div className="bg-amber-50 border border-amber-200 rounded px-4 py-2 text-sm text-amber-900">
          Pharmacist verification required. Medibox assists, it does not dispense.
          Your correction will be reviewed before being used as training data.
        </div>

        <button
          type="submit"
          disabled={submitting || !!jsonError}
          className="bg-brand-600 hover:bg-brand-700 text-white font-semibold py-2.5 px-6 rounded-md transition-colors disabled:opacity-50"
        >
          {submitting ? "Submitting…" : "Submit Correction"}
        </button>
      </form>
    </div>
  );
}
