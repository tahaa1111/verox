import type { Medication } from "../types";
import { ConfidenceBadge } from "./ConfidenceBadge";

interface Props {
  medications: Medication[];
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
            <th className="px-4 py-3">Confidence</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {medications.map((med, i) => (
            <tr key={i} className="hover:bg-gray-50 transition-colors">
              <td className="px-4 py-3 font-semibold text-gray-900">{med.drug_name}</td>
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
              <td className="px-4 py-3"><ConfidenceBadge value={med.confidence} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
