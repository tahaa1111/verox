import type { Medication } from "../types";
import { ConfidenceBadge } from "./ConfidenceBadge";

interface Props {
  medications: Medication[];
}

export function MedicationTable({ medications }: Props) {
  if (!medications.length) {
    return <p className="text-gray-500 text-sm">No medications extracted.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="bg-gray-100 text-left">
            <th className="px-3 py-2 font-semibold text-gray-700">Drug Name</th>
            <th className="px-3 py-2 font-semibold text-gray-700">Normalized</th>
            <th className="px-3 py-2 font-semibold text-gray-700">Dosage</th>
            <th className="px-3 py-2 font-semibold text-gray-700">Frequency</th>
            <th className="px-3 py-2 font-semibold text-gray-700">Duration</th>
            <th className="px-3 py-2 font-semibold text-gray-700">Class</th>
            <th className="px-3 py-2 font-semibold text-gray-700">Confidence</th>
          </tr>
        </thead>
        <tbody>
          {medications.map((med, i) => (
            <tr key={i} className="border-t border-gray-200 hover:bg-gray-50">
              <td className="px-3 py-2 font-medium">{med.drug_name}</td>
              <td className="px-3 py-2 text-gray-600">{med.drug_name_normalized ?? "—"}</td>
              <td className="px-3 py-2">{med.dosage ?? "—"}</td>
              <td className="px-3 py-2">{med.frequency ?? "—"}</td>
              <td className="px-3 py-2">{med.duration ?? "—"}</td>
              <td className="px-3 py-2 text-gray-500 text-xs">{med.drug_class ?? "—"}</td>
              <td className="px-3 py-2"><ConfidenceBadge value={med.confidence} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
