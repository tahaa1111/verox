import type { Medication } from "../types";
import { ConfidenceBadge } from "./ConfidenceBadge";

interface Props {
  medications: Medication[];
}

/** Color-coded badge for specialty–drug coherence result */
function SpecialtyBadge({ match, note }: { match: Medication["specialty_match"]; note: string | null }) {
  if (!match || match === "neutral") {
    return <span className="text-gray-300 text-xs">—</span>;
  }
  const styles: Record<string, string> = {
    confirmed: "bg-green-100 text-green-800 border border-green-200",
    possible:  "bg-yellow-100 text-yellow-800 border border-yellow-200",
    mismatch:  "bg-red-100 text-red-800 border border-red-200",
  };
  const labels: Record<string, string> = {
    confirmed: "✓ Match",
    possible:  "~ Possible",
    mismatch:  "✗ Mismatch",
  };
  return (
    <span
      className={`inline-block px-2 py-0.5 rounded text-xs font-semibold cursor-default ${styles[match] ?? ""}`}
      title={note ?? match}
    >
      {labels[match] ?? match}
    </span>
  );
}

export function MedicationTable({ medications }: Props) {
  if (!medications.length) {
    return <p className="text-gray-500 text-sm italic">No medications extracted.</p>;
  }
  return (
    <div className="overflow-x-auto rounded-lg border border-gray-200">
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="bg-gray-50 text-left text-xs font-semibold text-gray-500 uppercase tracking-wide">
            <th className="px-4 py-3">Drug Name</th>
            <th className="px-4 py-3">Normalized</th>
            <th className="px-4 py-3">Dosage</th>
            <th className="px-4 py-3">Frequency</th>
            <th className="px-4 py-3">Duration</th>
            <th className="px-4 py-3">Qty</th>
            <th className="px-4 py-3 text-center">CNAM</th>
            <th className="px-4 py-3 text-center">Specialty</th>
            <th className="px-4 py-3">Confidence</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {medications.map((med, i) => (
            <tr key={i} className="hover:bg-gray-50 transition-colors">
              <td className="px-4 py-3">
                {/* Show corrected name when spell correction was applied */}
                {med.drug_name_spell_corrected && med.drug_name_spell_corrected !== med.drug_name ? (
                  <span className="flex flex-col gap-0.5">
                    <span className="font-semibold text-gray-900">{med.drug_name_spell_corrected}</span>
                    <span className="text-xs text-gray-400 line-through">{med.drug_name}</span>
                  </span>
                ) : (
                  <span className="font-semibold text-gray-900">{med.drug_name}</span>
                )}
              </td>
              <td className="px-4 py-3 text-gray-500">{med.drug_name_normalized ?? "—"}</td>
              <td className="px-4 py-3">{med.dosage ?? "—"}</td>
              <td className="px-4 py-3">{med.frequency ?? "—"}</td>
              <td className="px-4 py-3">{med.duration ?? "—"}</td>
              <td className="px-4 py-3">{med.quantity ?? "—"}</td>
              <td className="px-4 py-3 text-center">
                {med.cnam ? (
                  <span className="inline-flex items-center gap-1 bg-green-100 text-green-700 text-xs font-semibold px-2 py-0.5 rounded-full">
                    <svg className="w-3 h-3" viewBox="0 0 20 20" fill="currentColor">
                      <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414L8.414 15l-4.121-4.121a1 1 0 011.414-1.414L8.414 12.172l7.879-7.879a1 1 0 011.414 0z" clipRule="evenodd"/>
                    </svg>
                    CNAM
                  </span>
                ) : (
                  <span className="text-gray-300 text-xs">—</span>
                )}
              </td>
              <td className="px-4 py-3 text-center">
                <SpecialtyBadge match={med.specialty_match} note={med.specialty_note} />
              </td>
              <td className="px-4 py-3"><ConfidenceBadge value={med.confidence} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
