/**
 * CorrectionsPage — structured form instead of raw JSON editor.
 * The pharmacist corrects patient / doctor / medication fields directly.
 * The form state is serialised back to JSON only on submit.
 */

import { useParams, Link, useNavigate } from "react-router-dom";
import { useState, useEffect } from "react";
import { useStore } from "../store";
import { pollJob, submitCorrection } from "../api";
import type { PrescriptionResult } from "../types";

// ── Types for the editable form state ────────────────────────────────────────

interface MedRow {
  drug_name: string;
  dosage: string;
  frequency: string;
  duration: string;
  quantity: string;
  cnam: boolean;
}

interface FormState {
  patient_first_name: string;
  patient_last_name: string;
  patient_address: string;
  patient_profession: string;
  doctor_name: string;
  doctor_stamp: string;
  issue_date: string;
  medications: MedRow[];
  additional_notes: string;
}

function blankMed(): MedRow {
  return { drug_name: "", dosage: "", frequency: "", duration: "", quantity: "", cnam: false };
}

function resultToForm(r: PrescriptionResult): FormState {
  return {
    patient_first_name: r.patient?.name ?? r.patient_name ?? "",
    patient_last_name:  r.patient?.last_name ?? "",
    patient_address:    r.patient?.address ?? "",
    patient_profession: r.patient?.profession ?? "",
    doctor_name:        r.doctor?.name ?? r.doctor_name ?? "",
    doctor_stamp:       r.doctor?.stamp ?? "",
    issue_date:         r.issue_date ?? "",
    medications: (r.medications ?? []).map((m) => ({
      drug_name:  m.drug_name ?? "",
      dosage:     m.dosage ?? "",
      frequency:  m.frequency ?? "",
      duration:   m.duration ?? "",
      quantity:   m.quantity ?? "",
      cnam:       m.cnam ?? false,
    })),
    additional_notes: r.additional_notes ?? "",
  };
}

function formToPayload(f: FormState, original: PrescriptionResult): object {
  // Merge corrected fields back onto the original result object
  return {
    ...original,
    patient_name:  [f.patient_first_name, f.patient_last_name].filter(Boolean).join(" ") || null,
    doctor_name:   f.doctor_name || null,
    issue_date:    f.issue_date || null,
    patient: {
      name:       f.patient_first_name || null,
      last_name:  f.patient_last_name  || null,
      address:    f.patient_address    || null,
      profession: f.patient_profession || null,
    },
    doctor: {
      name:  f.doctor_name  || null,
      stamp: f.doctor_stamp || null,
    },
    additional_notes: f.additional_notes || null,
    medications: f.medications.map((m) => ({
      drug_name:             m.drug_name  || null,
      drug_name_normalized:  null,
      dosage:                m.dosage     || null,
      frequency:             m.frequency  || null,
      duration:              m.duration   || null,
      quantity:              m.quantity   || null,
      cnam:                  m.cnam,
      drug_class:            null,
      confidence:            1.0,   // human-verified → full confidence
    })),
  };
}

// ── Small reusable field components ──────────────────────────────────────────

function Field({
  label, value, onChange, placeholder, hint,
}: {
  label: string; value: string; onChange: (v: string) => void;
  placeholder?: string; hint?: string;
}) {
  return (
    <div className="space-y-1">
      <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide">
        {label}
      </label>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder ?? label}
        className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-800 placeholder-gray-300 focus:outline-none focus:ring-2 focus:ring-brand-500 transition"
      />
      {hint && <p className="text-xs text-gray-400">{hint}</p>}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function CorrectionsPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const navigate  = useNavigate();
  const job       = useStore((s) => (jobId ? s.activeJobs[jobId] : null));
  const upsertJob = useStore((s) => s.upsertJob);

  const [original,   setOriginal]   = useState<PrescriptionResult | null>(null);
  const [form,       setForm]       = useState<FormState | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [done,       setDone]       = useState(false);

  // Load the job result on mount
  useEffect(() => {
    if (!jobId) return;
    const load = async () => {
      let j = job;
      if (!j) { j = await pollJob(jobId); upsertJob(j); }
      if (j.result) {
        setOriginal(j.result);
        setForm(resultToForm(j.result));
      }
    };
    load().catch(() => {});
  }, [jobId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Helpers to update parts of the form state
  const setField = (key: keyof Omit<FormState, "medications">, value: string) =>
    setForm((prev) => prev ? { ...prev, [key]: value } : prev);

  const setMed = (idx: number, key: keyof MedRow, value: string | boolean) =>
    setForm((prev) => {
      if (!prev) return prev;
      const meds = prev.medications.map((m, i) =>
        i === idx ? { ...m, [key]: value } : m
      );
      return { ...prev, medications: meds };
    });

  const addMed = () =>
    setForm((prev) => prev ? { ...prev, medications: [...prev.medications, blankMed()] } : prev);

  const removeMed = (idx: number) =>
    setForm((prev) => {
      if (!prev) return prev;
      return { ...prev, medications: prev.medications.filter((_, i) => i !== idx) };
    });

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!jobId || !form || !original) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await submitCorrection(jobId, formToPayload(form, original));
      setDone(true);
      setTimeout(() => navigate(`/results/${jobId}`), 1500);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  if (!jobId) return <p className="text-red-600">No job ID.</p>;

  if (done) {
    return (
      <div className="flex flex-col items-center justify-center py-20 gap-4 text-center">
        <div className="w-14 h-14 bg-green-100 rounded-full flex items-center justify-center">
          <svg className="w-7 h-7 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7"/>
          </svg>
        </div>
        <p className="text-lg font-semibold text-gray-800">Correction submitted!</p>
        <p className="text-sm text-gray-500">Redirecting back to results…</p>
      </div>
    );
  }

  if (!form) {
    return (
      <div className="flex items-center justify-center py-20 gap-3 text-gray-400">
        <div className="animate-spin rounded-full h-6 w-6 border-2 border-brand-500 border-t-transparent" />
        Loading result…
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-2xl mx-auto">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Submit Correction</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Fix any extraction errors below. Your corrections improve the model.
          </p>
        </div>
        <Link to={`/results/${jobId}`} className="text-sm text-brand-600 hover:underline flex items-center gap-1">
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7"/>
          </svg>
          Back to result
        </Link>
      </div>

      <form onSubmit={onSubmit} className="space-y-6">

        {/* ── Patient ── */}
        <section className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5 space-y-4">
          <div className="flex items-center gap-2 pb-1 border-b border-gray-100">
            <div className="w-7 h-7 bg-blue-100 rounded-full flex items-center justify-center">
              <svg className="w-3.5 h-3.5 text-blue-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"/>
              </svg>
            </div>
            <h2 className="font-semibold text-gray-800 text-sm">Patient</h2>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <Field label="First Name"  value={form.patient_first_name}  onChange={(v) => setField("patient_first_name", v)} />
            <Field label="Last Name"   value={form.patient_last_name}   onChange={(v) => setField("patient_last_name", v)} />
            <Field label="Address"     value={form.patient_address}     onChange={(v) => setField("patient_address", v)} />
            <Field label="Profession"  value={form.patient_profession}  onChange={(v) => setField("patient_profession", v)} />
          </div>
        </section>

        {/* ── Doctor ── */}
        <section className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5 space-y-4">
          <div className="flex items-center gap-2 pb-1 border-b border-gray-100">
            <div className="w-7 h-7 bg-emerald-100 rounded-full flex items-center justify-center">
              <svg className="w-3.5 h-3.5 text-emerald-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>
              </svg>
            </div>
            <h2 className="font-semibold text-gray-800 text-sm">Prescribing Doctor</h2>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <Field label="Doctor Name"      value={form.doctor_name}  onChange={(v) => setField("doctor_name", v)} />
            <Field label="Clinic / Hospital" value={form.doctor_stamp} onChange={(v) => setField("doctor_stamp", v)} placeholder="Clinic or hospital stamp" />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Issue Date" value={form.issue_date} onChange={(v) => setField("issue_date", v)} placeholder="e.g. 2026-05-20" />
          </div>
        </section>

        {/* ── Medications ── */}
        <section className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5 space-y-4">
          <div className="flex items-center justify-between pb-1 border-b border-gray-100">
            <div className="flex items-center gap-2">
              <div className="w-7 h-7 bg-violet-100 rounded-full flex items-center justify-center">
                <svg className="w-3.5 h-3.5 text-violet-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                    d="M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z"/>
                </svg>
              </div>
              <h2 className="font-semibold text-gray-800 text-sm">
                Medications
                <span className="ml-2 text-xs text-gray-400 font-normal normal-case">
                  {form.medications.length} item{form.medications.length !== 1 ? "s" : ""}
                </span>
              </h2>
            </div>
            <button
              type="button"
              onClick={addMed}
              className="flex items-center gap-1 text-xs text-brand-600 hover:text-brand-800 font-medium px-2.5 py-1.5 rounded-lg hover:bg-brand-50 transition-colors"
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4"/>
              </svg>
              Add medication
            </button>
          </div>

          {form.medications.length === 0 && (
            <p className="text-sm text-gray-400 italic text-center py-4">
              No medications — click "Add medication" to add one.
            </p>
          )}

          {form.medications.map((med, idx) => (
            <div key={idx} className="rounded-xl border border-gray-100 bg-gray-50 p-4 space-y-3 relative">
              {/* Remove button */}
              <button
                type="button"
                onClick={() => removeMed(idx)}
                className="absolute top-3 right-3 text-gray-300 hover:text-red-500 transition-colors"
                title="Remove medication"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12"/>
                </svg>
              </button>

              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">
                Medication {idx + 1}
              </p>

              <div className="grid grid-cols-2 gap-3">
                <div className="col-span-2">
                  <Field
                    label="Drug Name"
                    value={med.drug_name}
                    onChange={(v) => setMed(idx, "drug_name", v)}
                    placeholder="e.g. Metformine"
                  />
                </div>
                <Field label="Dosage"     value={med.dosage}     onChange={(v) => setMed(idx, "dosage", v)}     placeholder="e.g. 850 mg" />
                <Field label="Frequency"  value={med.frequency}  onChange={(v) => setMed(idx, "frequency", v)}  placeholder="e.g. 2×/day" />
                <Field label="Duration"   value={med.duration}   onChange={(v) => setMed(idx, "duration", v)}   placeholder="e.g. 30 days" />
                <Field label="Quantity"   value={med.quantity}   onChange={(v) => setMed(idx, "quantity", v)}   placeholder="e.g. 1 box" />
              </div>

              {/* CNAM toggle */}
              <label className="flex items-center gap-2 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={med.cnam}
                  onChange={(e) => setMed(idx, "cnam", e.target.checked)}
                  className="w-4 h-4 rounded accent-brand-600"
                />
                <span className="text-sm text-gray-600">
                  CNAM covered{" "}
                  <span className="text-xs text-gray-400">(national insurance reimbursement)</span>
                </span>
              </label>
            </div>
          ))}
        </section>

        {/* ── Additional notes ── */}
        <section className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5 space-y-3">
          <h2 className="font-semibold text-gray-800 text-sm pb-1 border-b border-gray-100">
            Additional Notes
          </h2>
          <textarea
            value={form.additional_notes}
            onChange={(e) => setField("additional_notes", e.target.value)}
            placeholder="Any extra instructions, warnings, or context…"
            rows={3}
            className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-800 placeholder-gray-300 focus:outline-none focus:ring-2 focus:ring-brand-500 transition resize-none"
          />
        </section>

        {/* ── Disclaimer ── */}
        <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 text-sm text-amber-900">
          ⚠️ Pharmacist verification required. Corrections will be reviewed before being used as training data.
        </div>

        {/* ── Submit error ── */}
        {submitError && (
          <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3 text-sm text-red-700">
            {submitError}
          </div>
        )}

        {/* ── Actions ── */}
        <div className="flex gap-3">
          <button
            type="submit"
            disabled={submitting}
            className="bg-brand-600 hover:bg-brand-700 disabled:opacity-50 text-white font-semibold text-sm px-6 py-2.5 rounded-xl transition-colors flex items-center gap-2"
          >
            {submitting ? (
              <>
                <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                Submitting…
              </>
            ) : (
              <>
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7"/>
                </svg>
                Submit Correction
              </>
            )}
          </button>
          <Link
            to={`/results/${jobId}`}
            className="border border-gray-200 text-gray-600 hover:bg-gray-50 font-medium text-sm px-4 py-2.5 rounded-xl transition-colors"
          >
            Cancel
          </Link>
        </div>
      </form>
    </div>
  );
}
