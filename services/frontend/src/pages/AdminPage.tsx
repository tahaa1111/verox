import { useState, useEffect } from "react";
import { getAdminModels, rollbackModel, setMaintenance } from "../api";
import { ConfidenceBadge } from "../components/ConfidenceBadge";
import type { ModelVersion } from "../types";
import clsx from "clsx";

export function AdminPage() {
  const [models, setModels] = useState<ModelVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [maintenanceActive, setMaintenanceActive] = useState(false);
  const [maintenanceReason, setMaintenanceReason] = useState("");
  const [actionMsg, setActionMsg] = useState<string | null>(null);

  useEffect(() => {
    getAdminModels()
      .then((data) => setModels(data as ModelVersion[]))
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  const handleRollback = async (deployedModelId: string, name: string) => {
    if (!confirm(`Roll back to model: ${name}?`)) return;
    try {
      await rollbackModel(deployedModelId);
      setActionMsg(`Rollback to ${name} initiated.`);
    } catch (e) {
      setActionMsg(`Error: ${String(e)}`);
    }
  };

  const handleMaintenance = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await setMaintenance(maintenanceActive, maintenanceReason);
      setActionMsg(`Maintenance mode ${maintenanceActive ? "enabled" : "disabled"}.`);
    } catch (e) {
      setActionMsg(`Error: ${String(e)}`);
    }
  };

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-bold text-gray-900">Admin Dashboard</h1>

      {/* Maintenance Mode */}
      <section className="bg-white rounded-lg border border-gray-200 p-5">
        <h2 className="font-semibold text-gray-800 mb-3">Maintenance Mode</h2>
        <form onSubmit={handleMaintenance} className="space-y-3">
          <label className="flex items-center gap-3">
            <input
              type="checkbox"
              checked={maintenanceActive}
              onChange={(e) => setMaintenanceActive(e.target.checked)}
              className="h-4 w-4 accent-brand-600"
            />
            <span className="text-sm text-gray-700">Enable maintenance mode (blocks new submissions)</span>
          </label>
          <div>
            <input
              type="text"
              placeholder="Reason for maintenance"
              value={maintenanceReason}
              onChange={(e) => setMaintenanceReason(e.target.value)}
              className="w-full border border-gray-300 rounded-md px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            />
          </div>
          <button
            type="submit"
            className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-medium px-4 py-2 rounded-md"
          >
            Apply
          </button>
        </form>
      </section>

      {/* Model Versions */}
      <section className="bg-white rounded-lg border border-gray-200 p-5">
        <h2 className="font-semibold text-gray-800 mb-3">Model Registry</h2>
        {loading && <div className="text-gray-400 text-sm animate-pulse">Loading models…</div>}
        {error && <div className="text-red-600 text-sm">{error}</div>}
        {!loading && !error && (
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="bg-gray-100 text-left">
                <th className="px-3 py-2 text-gray-700 font-semibold">Name</th>
                <th className="px-3 py-2 text-gray-700 font-semibold">drug_f1</th>
                <th className="px-3 py-2 text-gray-700 font-semibold">json_validity</th>
                <th className="px-3 py-2 text-gray-700 font-semibold">Deployed At</th>
                <th className="px-3 py-2 text-gray-700 font-semibold">Status</th>
                <th className="px-3 py-2 text-gray-700 font-semibold">Actions</th>
              </tr>
            </thead>
            <tbody>
              {models.map((m) => (
                <tr
                  key={m.id}
                  className={clsx(
                    "border-t border-gray-200",
                    m.is_current && "bg-green-50"
                  )}
                >
                  <td className="px-3 py-2 font-mono text-xs truncate max-w-[180px]">
                    {m.display_name}
                  </td>
                  <td className="px-3 py-2">
                    {m.eval_drug_f1 != null ? (
                      <ConfidenceBadge value={m.eval_drug_f1} />
                    ) : "—"}
                  </td>
                  <td className="px-3 py-2">
                    {m.eval_json_validity != null ? (
                      <span className={clsx(
                        "text-xs font-semibold",
                        m.eval_json_validity >= 0.995 ? "text-green-700" : "text-red-600"
                      )}>
                        {(m.eval_json_validity * 100).toFixed(1)}%
                      </span>
                    ) : "—"}
                  </td>
                  <td className="px-3 py-2 text-gray-500 text-xs">
                    {m.deployed_at
                      ? new Date(m.deployed_at).toLocaleString()
                      : "—"}
                  </td>
                  <td className="px-3 py-2">
                    {m.is_current ? (
                      <span className="text-xs bg-green-100 text-green-800 px-2 py-0.5 rounded font-semibold">
                        Current
                      </span>
                    ) : (
                      <span className="text-xs text-gray-400">Inactive</span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    {!m.is_current && m.vertex_deployed_model_id && (
                      <button
                        onClick={() =>
                          handleRollback(m.vertex_deployed_model_id!, m.display_name)
                        }
                        className="text-xs text-brand-600 hover:underline font-medium"
                      >
                        Rollback
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* Action feedback */}
      {actionMsg && (
        <div className="bg-blue-50 border border-blue-200 text-blue-800 text-sm px-4 py-2 rounded">
          {actionMsg}
        </div>
      )}

      <p className="text-xs text-gray-400">
        All admin actions are audited. For traffic split changes, use the Vertex AI console or
        the rollback endpoint. Pharmacist verification required on all prescriptions.
      </p>
    </div>
  );
}
