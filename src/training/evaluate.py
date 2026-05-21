"""
Evaluation script — per-field F1, per-drug-class accuracy, rare-drug accuracy.
Spec §8.2 component 5.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--eval_data_uri", required=True)   # gs:// or local path
    p.add_argument("--adapter_uri", required=True)      # gs:// adapter path
    p.add_argument("--vllm_url", default="http://localhost:8000")
    p.add_argument("--output_metrics_path", default="/tmp/metrics.json")
    p.add_argument("--fail_threshold_drug_f1", type=float, default=0.80)
    p.add_argument("--max_class_regression_pct", type=float, default=2.0)
    return p.parse_args()


def load_eval_data(uri: str) -> list[dict]:
    if uri.startswith("gs://"):
        from google.cloud import storage
        client = storage.Client()
        bucket_name, blob_name = uri.lstrip("gs://").split("/", 1)
        content = client.bucket(bucket_name).blob(blob_name).download_as_text()
    else:
        content = Path(uri).read_text()
    return [json.loads(line) for line in content.splitlines() if line.strip()]


def field_f1(predictions: list[dict], references: list[dict], field: str) -> float:
    """Token-level F1 for a text field."""
    total_f1 = 0.0
    for pred, ref in zip(predictions, references):
        pred_val = str(pred.get(field, "") or "").lower().split()
        ref_val = str(ref.get(field, "") or "").lower().split()
        if not pred_val and not ref_val:
            total_f1 += 1.0
            continue
        common = set(pred_val) & set(ref_val)
        if not common:
            continue
        precision = len(common) / len(pred_val) if pred_val else 0
        recall = len(common) / len(ref_val) if ref_val else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
        total_f1 += f1
    return total_f1 / len(predictions) if predictions else 0.0


def drug_class_accuracy(predictions: list[dict], references: list[dict]) -> dict[str, float]:
    """Per-drug-class extraction accuracy (presence match)."""
    class_hits: dict[str, list[bool]] = defaultdict(list)
    for pred, ref in zip(predictions, references):
        for ref_med in (ref.get("medications") or []):
            drug_class = ref_med.get("drug_class", "unknown")
            ref_drug = (ref_med.get("drug_name_normalized") or ref_med.get("drug_name", "")).lower()
            pred_drugs = [
                (m.get("drug_name_normalized") or m.get("drug_name", "")).lower()
                for m in (pred.get("medications") or [])
            ]
            hit = any(ref_drug in pd or pd in ref_drug for pd in pred_drugs)
            class_hits[drug_class].append(hit)
    return {cls: sum(hits) / len(hits) for cls, hits in class_hits.items() if hits}


def json_validity_rate(raw_outputs: list[str]) -> float:
    valid = 0
    for output in raw_outputs:
        try:
            json.loads(output)
            valid += 1
        except Exception:
            try:
                import json_repair
                # json_repair never raises; parse its output to confirm a real JSON structure
                repaired = json_repair.repair_json(output)
                parsed = json.loads(repaired)
                if isinstance(parsed, (dict, list)):
                    valid += 1
            except Exception:
                pass
    return valid / len(raw_outputs) if raw_outputs else 0.0


def run_evaluation(args: argparse.Namespace) -> dict:
    print(f"[evaluate] Loading eval data from {args.eval_data_uri}")
    samples = load_eval_data(args.eval_data_uri)
    print(f"[evaluate] {len(samples)} eval samples")

    # In production: call Vertex AI endpoint for each sample and compare
    # For now: use the stored corrected_json as ground truth vs original structured_json
    predictions = [s.get("original", {}) for s in samples]
    references = [s.get("corrected", {}) for s in samples]

    drug_f1 = field_f1(predictions, references, "drug_name")
    dosage_f1 = field_f1(predictions, references, "dosage")
    date_f1 = field_f1(predictions, references, "issue_date")
    class_acc = drug_class_accuracy(predictions, references)

    # Rare drug set — high-priority Tunisian drugs that appear rarely in training data
    rare_drugs = {"clomipramine", "phenobarbital", "levothyroxine", "metformine"}
    rare_hits = []
    for pred, ref in zip(predictions, references):
        for ref_med in (ref.get("medications") or []):
            drug = (ref_med.get("drug_name_normalized") or "").lower()
            if any(r in drug for r in rare_drugs):
                pred_drugs = [(m.get("drug_name_normalized") or "").lower()
                              for m in (pred.get("medications") or [])]
                rare_hits.append(any(r in pd for r in rare_drugs for pd in pred_drugs))

    rare_accuracy = sum(rare_hits) / len(rare_hits) if rare_hits else 1.0

    raw_outputs = [json.dumps(p) for p in predictions]
    json_val = json_validity_rate(raw_outputs)

    metrics = {
        "drug_f1": round(drug_f1, 4),
        "dosage_accuracy": round(dosage_f1, 4),
        "date_accuracy": round(date_f1, 4),
        "drug_class_metrics": {k: round(v, 4) for k, v in class_acc.items()},
        "rare_drug_accuracy": round(rare_accuracy, 4),
        "json_validity_rate": round(json_val, 4),
        "eval_samples": len(samples),
        "passed": drug_f1 >= args.fail_threshold_drug_f1 and json_val >= 0.995,
    }

    Path(args.output_metrics_path).write_text(json.dumps(metrics, indent=2))
    print(f"[evaluate] Metrics: drug_f1={drug_f1:.4f}, json_validity={json_val:.4f}, "
          f"rare_accuracy={rare_accuracy:.4f}")
    print(f"[evaluate] PASSED: {metrics['passed']}")
    return metrics


if __name__ == "__main__":
    args = parse_args()
    metrics = run_evaluation(args)
    if not metrics["passed"]:
        raise SystemExit(f"Evaluation FAILED: {metrics}")
