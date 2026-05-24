/**
 * HistoryPage — shows all completed/failed scans stored in localStorage.
 * Entries are saved automatically by the store whenever a job reaches a terminal state.
 */

import { Link } from "react-router-dom";
import { useStore } from "../store";
import type { ScanHistoryEntry } from "../store";

// ── Helpers ─────────────────────────────────────────────────────────────────

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, {
      day: "2-digit", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function confidenceColor(c: number): string {
  if (c >= 0.85) return "bg-green-100 text-green-700";
  if (c >= 0.6)  return "bg-yellow-100 text-yellow-700";
  return "bg-red-100 text-red-700";
}

// ── Sub-components ───────────────────────────────────────────────────────────

function HistoryRow({ entry }: { entry: ScanHistoryEntry }) {
  const isCompleted = entry.status === "completed";

  return (
    <Link
      to={`/results/${entry.job_id}`}
      className="flex items-center gap-4 px-5 py-4 hover:bg-gray-50 transition-colors border-b border-gray-100 last:border-0 group"
    >
      {/* Status icon */}
      <div className={`flex-shrink-0 w-9 h-9 rounded-full flex items-center justify-center ${
        isCompleted ? "bg-green-100" : "bg-red-100"
      }`}>
        {isCompleted ? (
          <svg className="w-4 h-4 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>
          </svg>
        ) : (
          <svg className="w-4 h-4 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z"/>
          </svg>
        )}
      </div>

      {/* Date + patient */}
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-gray-900 truncate">
          {entry.patient_name ?? <span className="text-gray-400 italic">Unknown patient</span>}
        </p>
        <p className="text-xs text-gray-400 mt-0.5">{formatDate(entry.scanned_at)}</p>
      </div>

      {/* Doctor */}
      <div className="hidden sm:block w-36 truncate text-sm text-gray-500">
        {entry.doctor_name ?? <span className="text-gray-300">—</span>}
      </div>

      {/* Drug count */}
      <div className="hidden sm:flex items-center gap-1 w-20 text-sm text-gray-600">
        <svg className="w-3.5 h-3.5 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z"/>
        </svg>
        {isCompleted ? `${entry.drug_count} drug${entry.drug_count !== 1 ? "s" : ""}` : "—"}
      </div>

      {/* Confidence */}
      <div className="w-16 text-center">
        {isCompleted ? (
          <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-semibold ${confidenceColor(entry.confidence)}`}>
            {Math.round(entry.confidence * 100)}%
          </span>
        ) : (
          <span className="text-xs text-red-400 font-medium">Failed</span>
        )}
      </div>

      {/* Review flag */}
      <div className="w-6 flex-shrink-0">
        {entry.requires_review && (
          <span title="Requires pharmacist review">
            <svg className="w-4 h-4 text-amber-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/>
            </svg>
          </span>
        )}
      </div>

      {/* Chevron */}
      <svg className="w-4 h-4 text-gray-300 group-hover:text-gray-500 flex-shrink-0 transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7"/>
      </svg>
    </Link>
  );
}

// ── Main page ────────────────────────────────────────────────────────────────

export function HistoryPage() {
  const scanHistory = useStore((s) => s.scanHistory);
  const clearHistory = useStore((s) => s.clearHistory);

  const completed = scanHistory.filter((e) => e.status === "completed").length;
  const failed    = scanHistory.filter((e) => e.status === "failed").length;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Scan History</h1>
          <p className="text-sm text-gray-400 mt-0.5">
            {scanHistory.length === 0
              ? "No scans yet"
              : `${scanHistory.length} scan${scanHistory.length !== 1 ? "s" : ""} — ${completed} completed, ${failed} failed`}
          </p>
        </div>

        {scanHistory.length > 0 && (
          <button
            onClick={() => {
              if (confirm("Clear all scan history? This cannot be undone.")) clearHistory();
            }}
            className="text-xs text-gray-400 hover:text-red-500 transition-colors px-3 py-1.5 rounded-lg border border-gray-200 hover:border-red-200"
          >
            Clear history
          </button>
        )}
      </div>

      {/* Empty state */}
      {scanHistory.length === 0 && (
        <div className="bg-white rounded-2xl border border-gray-100 shadow-sm flex flex-col items-center justify-center py-20 gap-4 text-center">
          <div className="w-16 h-16 bg-gray-100 rounded-2xl flex items-center justify-center">
            <svg className="w-8 h-8 text-gray-300" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/>
            </svg>
          </div>
          <div>
            <p className="text-gray-600 font-medium">No scans yet</p>
            <p className="text-sm text-gray-400 mt-1">
              Completed scans will appear here automatically.
            </p>
          </div>
          <Link
            to="/"
            className="mt-2 bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-5 py-2 rounded-xl transition-colors"
          >
            Start a scan
          </Link>
        </div>
      )}

      {/* History table */}
      {scanHistory.length > 0 && (
        <div className="bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden">
          {/* Column headers */}
          <div className="flex items-center gap-4 px-5 py-2.5 bg-gray-50 border-b border-gray-100 text-xs font-medium text-gray-400 uppercase tracking-wide">
            <div className="w-9 flex-shrink-0" />
            <div className="flex-1">Patient</div>
            <div className="hidden sm:block w-36">Doctor</div>
            <div className="hidden sm:block w-20">Drugs</div>
            <div className="w-16 text-center">Confidence</div>
            <div className="w-6 flex-shrink-0" />
            <div className="w-4 flex-shrink-0" />
          </div>

          {scanHistory.map((entry) => (
            <HistoryRow key={entry.job_id} entry={entry} />
          ))}
        </div>
      )}

      {/* Note about persistence */}
      {scanHistory.length > 0 && (
        <p className="text-xs text-gray-400 text-center">
          History is stored locally on this device. Clearing browser data will remove it.
        </p>
      )}
    </div>
  );
}
