import { useState, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { submitPrescription } from "../api";
import clsx from "clsx";

const MAX_FILES = 30;
const MAX_FILE_BYTES = 2 * 1024 * 1024; // 2 MB
const ALLOWED_TYPES = ["image/jpeg", "image/png"];

function generateSessionId() {
  return crypto.randomUUID();
}

export function SubmitPage() {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [deviceId, setDeviceId] = useState("pi-0001");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const addFiles = useCallback((incoming: FileList | null) => {
    if (!incoming) return;
    const valid: File[] = [];
    const errs: string[] = [];
    for (const f of Array.from(incoming)) {
      if (!ALLOWED_TYPES.includes(f.type)) { errs.push(`${f.name}: not JPEG/PNG`); continue; }
      if (f.size > MAX_FILE_BYTES) { errs.push(`${f.name}: exceeds 2 MB`); continue; }
      valid.push(f);
    }
    setFiles((prev) => {
      const merged = [...prev, ...valid].slice(0, MAX_FILES);
      return merged;
    });
    if (errs.length) setError(errs.join("; "));
  }, []);

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    addFiles(e.dataTransfer.files);
  }, [addFiles]);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!files.length) { setError("Select at least one crop image."); return; }
    if (!/^pi-\d{2,4}$/.test(deviceId)) { setError("Device ID format: pi-XXXX"); return; }
    setError(null);
    setLoading(true);
    try {
      const resp = await submitPrescription(deviceId, generateSessionId(), files);
      navigate(`/jobs/${resp.job_id}`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Submit failed";
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-2xl mx-auto">
      <h1 className="text-2xl font-bold text-gray-900 mb-6">Submit Prescription</h1>

      <form onSubmit={onSubmit} className="space-y-5">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Device ID
          </label>
          <input
            type="text"
            value={deviceId}
            onChange={(e) => setDeviceId(e.target.value)}
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            placeholder="pi-0001"
            pattern="^pi-\d{2,4}$"
            required
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Crop Images (JPEG/PNG, max {MAX_FILES} files, 2 MB each)
          </label>
          <div
            onDrop={onDrop}
            onDragOver={(e) => e.preventDefault()}
            onClick={() => inputRef.current?.click()}
            className={clsx(
              "border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors",
              files.length ? "border-brand-400 bg-brand-50" : "border-gray-300 hover:border-brand-400"
            )}
          >
            <input
              ref={inputRef}
              type="file"
              accept=".jpg,.jpeg,.png"
              multiple
              className="hidden"
              onChange={(e) => addFiles(e.target.files)}
            />
            {files.length === 0 ? (
              <p className="text-gray-500 text-sm">
                Drag & drop crop images here, or click to browse
              </p>
            ) : (
              <p className="text-brand-700 font-medium text-sm">
                {files.length} file{files.length !== 1 ? "s" : ""} selected
              </p>
            )}
          </div>

          {files.length > 0 && (
            <ul className="mt-2 space-y-1 max-h-40 overflow-y-auto text-xs text-gray-600">
              {files.map((f, i) => (
                <li key={i} className="flex items-center justify-between">
                  <span className="truncate">{f.name}</span>
                  <button
                    type="button"
                    onClick={() => setFiles((prev) => prev.filter((_, j) => j !== i))}
                    className="ml-2 text-red-400 hover:text-red-600 font-bold flex-shrink-0"
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {error && (
          <div className="bg-red-50 border border-red-300 text-red-700 text-sm px-4 py-2 rounded">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={loading}
          className="w-full bg-brand-600 hover:bg-brand-700 text-white font-semibold py-2.5 rounded-md transition-colors disabled:opacity-50"
        >
          {loading ? "Submitting…" : "Submit for Extraction"}
        </button>
      </form>
    </div>
  );
}
