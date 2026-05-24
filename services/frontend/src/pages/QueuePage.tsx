/**
 * QueuePage — shows all scans currently in the processing pipeline.
 * Each row polls once on mount, then stays updated via the store.
 */

import { useEffect } from "react";
import { Link } from "react-router-dom";
import { useStore } from "../store";
import { pollJob } from "../api";

const STATUS_LABEL: Record<string, string> = {
  queued:         "Queued",
  preprocessing:  "Preprocessing",
  inferring:      "Inferring",
  processing:     "Processing",
  postprocessing: "Postprocessing",
  retrying:       "Retrying…",
  completed:      "Completed",
  failed:         "Failed",
  cancelled:      "Cancelled",
};

const STATUS_COLOR: Record<string, string> = {
  queued:         "bg-gray-100 text-gray-500",
  preprocessing:  "bg-blue-100 text-blue-600",
  inferring:      "bg-violet-100 text-violet-600",
  processing:     "bg-blue-100 text-blue-600",
  postprocessing: "bg-indigo-100 text-indigo-600",
  retrying:       "bg-amber-100 text-amber-700",
  completed:      "bg-green-100 text-green-700",
  failed:         "bg-red-100 text-red-600",
  cancelled:      "bg-gray-100 text-gray-400",
};

const TERMINAL = new Set(["completed", "failed", "cancelled"]);

function StatusBadge({ status }: { status: string }) {
  const color = STATUS_COLOR[status] ?? "bg-gray-100 text-gray-500";
  const label = STATUS_LABEL[status] ?? status;
  const isActive = !TERMINAL.has(status) && status !== "queued";
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-semibold px-2.5 py-1 rounded-full ${color}`}>
      {isActive && (
        <span className="relative flex h-1.5 w-1.5">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 bg-current"/>
          <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-current"/>
        </span>
      )}
      {label}
    </span>
  );
}

export function QueuePage() {
  const { jobQueue, activeJobs, upsertJob, removeFromQueue } = useStore();

  // Poll every queued job once on mount to get current status
  useEffect(() => {
    jobQueue.forEach((id) => {
      pollJob(id)
        .then((j) => upsertJob(j))
        .catch(() => removeFromQueue(id));
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="space-y-6 max-w-2xl mx-auto">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Scan Queue</h1>
          <p className="text-sm text-gray-400 mt-0.5">
            {jobQueue.length === 0
              ? "No scans in progress"
              : `${jobQueue.length} scan${jobQueue.length !== 1 ? "s" : ""} in the pipeline`}
          </p>
        </div>
        <Link to="/"
          className="bg-brand-600 hover:bg-brand-700 text-white font-semibold text-sm px-4 py-2 rounded-xl transition-colors flex items-center gap-1.5">
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M15 10l4.553-2.069A1 1 0 0121 8.87v6.26a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z"/>
          </svg>
          New scan
        </Link>
      </div>

      {/* Empty state */}
      {jobQueue.length === 0 && (
        <div className="bg-white rounded-2xl border border-gray-100 shadow-sm
                        flex flex-col items-center justify-center py-20 gap-4 text-center">
          <div className="w-14 h-14 bg-gray-100 rounded-2xl flex items-center justify-center">
            <svg className="w-7 h-7 text-gray-300" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/>
            </svg>
          </div>
          <div>
            <p className="text-gray-600 font-medium">Queue is empty</p>
            <p className="text-sm text-gray-400 mt-1">Start a scan to add jobs here.</p>
          </div>
          <Link to="/"
            className="mt-2 bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-5 py-2 rounded-xl transition-colors">
            Start a scan
          </Link>
        </div>
      )}

      {/* Queue list */}
      {jobQueue.length > 0 && (
        <div className="bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden divide-y divide-gray-50">
          {jobQueue.map((id, idx) => {
            const job    = activeJobs[id];
            const status = job?.status ?? "queued";
            const isDone = TERMINAL.has(status);
            const result = job?.result;
            const drugs  = result?.medications?.length ?? 0;
            const conf   = result?.overall_confidence;

            return (
              <div key={id} className="flex items-center gap-4 px-5 py-4">
                {/* Index */}
                <div className="w-7 h-7 rounded-full bg-brand-50 flex items-center justify-center flex-shrink-0">
                  <span className="text-xs font-bold text-brand-600">{idx + 1}</span>
                </div>

                {/* ID + status */}
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-mono text-gray-400 truncate">{id}</p>
                  <div className="flex items-center gap-2 mt-1">
                    <StatusBadge status={status} />
                    {isDone && drugs > 0 && (
                      <span className="text-xs text-gray-400">
                        {drugs} drug{drugs !== 1 ? "s" : ""}
                        {conf != null ? ` · ${Math.round(conf * 100)}% conf` : ""}
                      </span>
                    )}
                  </div>
                </div>

                {/* Action */}
                <Link to={`/results/${id}`}
                  className={`flex-shrink-0 text-xs font-semibold px-3 py-1.5 rounded-lg transition-colors ${
                    isDone
                      ? "bg-brand-600 hover:bg-brand-700 text-white"
                      : "border border-brand-200 text-brand-600 hover:bg-brand-50"
                  }`}>
                  {isDone ? "View results" : "Track"}
                </Link>
              </div>
            );
          })}
        </div>
      )}

      <p className="text-xs text-gray-400 text-center">
        Completed scans also appear in{" "}
        <Link to="/history" className="underline hover:text-gray-600">Scan History</Link>.
      </p>
    </div>
  );
}
