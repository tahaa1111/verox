/**
 * UploadPage — direct image upload to vLLM, bypassing the Pi/YOLO pipeline.
 * Each uploaded image is sent as a full-frame crop to POST /v1/submit.
 * Supports multiple images (each becomes a separate job).
 */

import { useState, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { submitImages } from "../api";
import { useStore } from "../store";

interface Preview {
  file: File;
  url: string;
  b64: string | null;
}

export function UploadPage() {
  const navigate   = useNavigate();
  const addToQueue = useStore((s) => s.addToQueue);

  const [previews,    setPreviews]    = useState<Preview[]>([]);
  const [submitting,  setSubmitting]  = useState(false);
  const [dragging,    setDragging]    = useState(false);
  const [error,       setError]       = useState<string | null>(null);
  const [done,        setDone]        = useState<string[]>([]);  // submitted job IDs

  const inputRef = useRef<HTMLInputElement>(null);

  // ── Helpers ────────────────────────────────────────────────────────────────

  const toBase64 = (file: File): Promise<string> =>
    new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload  = () => resolve((reader.result as string).split(",")[1]);
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });

  const addFiles = useCallback(async (files: FileList | File[]) => {
    const arr = Array.from(files).filter((f) =>
      f.type === "image/jpeg" || f.type === "image/png"
    );
    if (!arr.length) return;
    const newPreviews = await Promise.all(
      arr.map(async (f) => ({
        file: f,
        url:  URL.createObjectURL(f),
        b64:  await toBase64(f),
      }))
    );
    setPreviews((prev) => [...prev, ...newPreviews]);
    setError(null);
    setDone([]);
  }, []);

  const removePreview = (idx: number) => {
    setPreviews((prev) => {
      URL.revokeObjectURL(prev[idx].url);
      return prev.filter((_, i) => i !== idx);
    });
  };

  // ── Drag-and-drop ──────────────────────────────────────────────────────────

  const onDragOver  = (e: React.DragEvent) => { e.preventDefault(); setDragging(true); };
  const onDragLeave = () => setDragging(false);
  const onDrop      = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    addFiles(e.dataTransfer.files);
  };

  // ── Submit ─────────────────────────────────────────────────────────────────

  const handleSubmit = async () => {
    if (!previews.length || submitting) return;
    setSubmitting(true);
    setError(null);

    const jobIds: string[] = [];
    for (const p of previews) {
      if (!p.b64) continue;
      try {
        const jobId = await submitImages([p.b64], p.file.name);
        jobIds.push(jobId);
        addToQueue(jobId);
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : "Submission failed";
        setError(msg);
        setSubmitting(false);
        return;
      }
    }

    setDone(jobIds);
    setSubmitting(false);

    if (jobIds.length === 1) {
      navigate(`/results/${jobIds[0]}`);
    } else {
      navigate("/queue");
    }
  };

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-xl font-bold text-gray-900">Upload Prescription</h1>
        <p className="text-sm text-gray-500 mt-1">
          Upload one or more prescription images — processed directly by the AI, no camera required.
        </p>
      </div>

      {/* Drop zone */}
      <div
        onClick={() => inputRef.current?.click()}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        className={`
          relative border-2 border-dashed rounded-xl p-10 text-center cursor-pointer
          transition-colors select-none
          ${dragging
            ? "border-brand-500 bg-brand-50"
            : "border-gray-200 hover:border-brand-400 hover:bg-gray-50"}
        `}
      >
        <input
          ref={inputRef}
          type="file"
          accept="image/jpeg,image/png"
          multiple
          className="hidden"
          onChange={(e) => e.target.files && addFiles(e.target.files)}
        />
        <div className="flex flex-col items-center gap-3 pointer-events-none">
          <div className="w-14 h-14 rounded-full bg-brand-50 flex items-center justify-center">
            <svg className="w-7 h-7 text-brand-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5"/>
            </svg>
          </div>
          <div>
            <p className="font-semibold text-gray-700">
              {dragging ? "Drop images here" : "Click or drag images here"}
            </p>
            <p className="text-xs text-gray-400 mt-1">JPEG or PNG — multiple files supported</p>
          </div>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Previews */}
      {previews.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium text-gray-700">
              {previews.length} image{previews.length > 1 ? "s" : ""} selected
            </p>
            <button
              onClick={() => { previews.forEach((p) => URL.revokeObjectURL(p.url)); setPreviews([]); setDone([]); }}
              className="text-xs text-gray-400 hover:text-red-500 transition-colors"
            >
              Clear all
            </button>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            {previews.map((p, i) => (
              <div key={i} className="relative group rounded-lg overflow-hidden border border-gray-200 bg-gray-50 aspect-[3/4]">
                <img
                  src={p.url}
                  alt={p.file.name}
                  className="w-full h-full object-contain"
                />
                {/* Remove button */}
                <button
                  onClick={(e) => { e.stopPropagation(); removePreview(i); }}
                  className="absolute top-1.5 right-1.5 w-6 h-6 rounded-full bg-black/60 text-white
                             flex items-center justify-center opacity-0 group-hover:opacity-100
                             transition-opacity hover:bg-red-600"
                >
                  <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M6 18L18 6M6 6l12 12"/>
                  </svg>
                </button>
                {/* File name */}
                <div className="absolute bottom-0 left-0 right-0 bg-black/50 px-2 py-1">
                  <p className="text-white text-xs truncate">{p.file.name}</p>
                </div>
              </div>
            ))}
          </div>

          {/* Submit button */}
          <button
            onClick={handleSubmit}
            disabled={submitting || previews.some((p) => !p.b64)}
            className="w-full py-3 px-6 rounded-xl bg-brand-600 hover:bg-brand-700 disabled:bg-gray-300
                       text-white font-semibold text-sm transition-colors flex items-center justify-center gap-2"
          >
            {submitting ? (
              <>
                <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin"/>
                Processing…
              </>
            ) : (
              <>
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                    d="M9 5l7 7-7 7"/>
                </svg>
                Process {previews.length} image{previews.length > 1 ? "s" : ""}
              </>
            )}
          </button>
        </div>
      )}

      {/* Submitted jobs */}
      {done.length > 1 && (
        <div className="rounded-lg bg-green-50 border border-green-200 px-4 py-3 text-sm text-green-700">
          {done.length} jobs queued — <a href="/queue" className="font-semibold underline">view queue</a>
        </div>
      )}
    </div>
  );
}
