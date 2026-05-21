"""
Postprocessing pipeline (spec §5.3).
Steps in order:
1. JSON repair (json-repair library)
2. Schema validation (Pydantic v2)
3. Drug name normalization (real Tunisian formulary from DD-013)
4. Date normalization (French + Arabic → ISO 8601)
5. Dosage normalization (regex pipeline)
6. Hallucination filter (unregistered drugs → requires_review)
7. Confidence calibration (logprob + formulary score + completeness, DD-007)
8. Audit trail construction
"""

import json
import math
import re
from typing import Any, Optional

import structlog

from services.worker.utils.drug_normalizer import normalize_drug_name

logger = structlog.get_logger(__name__)

# Calibration weights (DD-007)
W_LOGPROB = 0.50
W_FORMULARY = 0.35
W_COMPLETENESS = 0.15

# Required fields for completeness scoring
REQUIRED_FIELDS = ["patient_name", "doctor_name", "issue_date", "medications"]

FRENCH_MONTH_MAP = {
    "janvier": "01", "février": "02", "fevrier": "02", "mars": "03",
    "avril": "04", "mai": "05", "juin": "06", "juillet": "07",
    "août": "08", "aout": "08", "septembre": "09", "octobre": "10",
    "novembre": "11", "décembre": "12", "decembre": "12",
}
ARABIC_MONTH_MAP = {
    "يناير": "01", "فبراير": "02", "مارس": "03", "أبريل": "04",
    "مايو": "05", "يونيو": "06", "يوليو": "07", "أغسطس": "08",
    "سبتمبر": "09", "أكتوبر": "10", "نوفمبر": "11", "ديسمبر": "12",
}

DOSAGE_UNITS_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(mg|g|ml|mcg|µg|iu|ui|cp|co|gél|gel|comprimé|sachet)\b",
    re.IGNORECASE,
)
UNIT_NORMALIZE = {
    "comprimé": "cp", "gél": "gel", "sachet": "sachet",
    "co": "cp", "mcg": "µg",
}


# ---------------------------------------------------------------------------
# Public utility functions (used by tests and external callers)
# ---------------------------------------------------------------------------

def repair_and_parse_json(raw: str) -> Optional[dict]:
    """Public wrapper around _repair_and_parse for testing and standalone use."""
    if not raw:
        return {}
    result = _repair_and_parse(raw, logger)
    return result if result else {}


def calibrate_confidence(
    logprob_conf: float,
    formulary_score: float,
    completeness: float,
) -> float:
    """Compute calibrated overall confidence from component scores (DD-007).

    Weights: 0.50 × logprob_conf + 0.35 × formulary_score + 0.15 × completeness.
    Result is clamped to [0.0, 1.0].  The full pipeline (run_postprocessing) applies
    its own [0.05, 0.98] clamp on top; this function is a pure weighted sum.
    """
    raw = W_LOGPROB * logprob_conf + W_FORMULARY * formulary_score + W_COMPLETENESS * completeness
    return round(max(0.0, min(1.0, raw)), 4)


def normalize_date(raw: Optional[str]) -> Optional[str]:
    """Public wrapper around _normalize_date for testing and standalone use."""
    return _normalize_date(raw) if raw else None


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_postprocessing(
    raw_output: str,
    logprobs: list[dict],
    job_id: str,
    session_id: str,
    model_version: str,
    timings_ms: dict,
) -> dict:
    """Full postprocessing pipeline. Returns the §6 output schema dict."""

    log = logger.bind(job_id=job_id)

    # ---- Step 1: JSON repair ----
    parsed = _repair_and_parse(raw_output, log)

    # ---- Step 2: Schema validation (lenient — we fill defaults) ----
    result = _normalize_schema(parsed, job_id, session_id)

    # ---- Step 3–5: Medication enrichment ----
    review_reasons: list[str] = []
    for med in result.get("medications", []):
        # Drug name normalization
        raw_drug = med.get("drug_name", "")
        inn, formulary_score, registry_meta = normalize_drug_name(raw_drug)
        med["drug_name_normalized"] = inn
        med["formulary_match_score"] = formulary_score

        if formulary_score < 0.85:
            med["requires_review"] = True
            if "formulary_miss" not in review_reasons:
                review_reasons.append("formulary_miss")
        else:
            med["requires_review"] = False

        # Dosage normalization
        raw_dosage = med.get("dosage", "")
        if raw_dosage:
            med["dosage_normalized"] = _normalize_dosage(raw_dosage)

        # Date in medication context (duration normalization)
        # No ISO date needed here; just clean nulls
        for field in ("dosage", "frequency", "duration", "instructions", "warnings"):
            med[field] = _clean_null(med.get(field))

    # ---- Step 4: Issue date normalization ----
    raw_date = result.get("issue_date")
    if raw_date:
        result["issue_date"] = _normalize_date(raw_date)

    # ---- Step 5: Clean PII nulls ----
    result["patient_name"] = _clean_null(result.get("patient_name"))
    result["doctor_name"] = _clean_null(result.get("doctor_name"))

    # ---- Step 6: Hallucination filter ----
    # Already flagged per-medication above; check aggregate
    flagged_count = sum(1 for m in result.get("medications", []) if m.get("requires_review"))
    total_meds = len(result.get("medications", []))
    if total_meds > 0 and flagged_count / total_meds > 0.5:
        review_reasons.append("high_hallucination_rate")

    # ---- Step 7: Confidence calibration (DD-007) ----
    logprob_conf = _extract_logprob_confidence(logprobs)
    formulary_scores = [m.get("formulary_match_score", 0.0) for m in result.get("medications", [])]
    avg_formulary = sum(formulary_scores) / len(formulary_scores) if formulary_scores else 0.0
    completeness = _compute_completeness(result)

    calibrated_conf = (
        W_LOGPROB * logprob_conf +
        W_FORMULARY * avg_formulary +
        W_COMPLETENESS * completeness
    )
    calibrated_conf = round(max(0.05, min(0.98, calibrated_conf)), 4)
    result["overall_confidence"] = calibrated_conf

    # Per-medication field confidences (approximation from logprobs)
    for med in result.get("medications", []):
        med.setdefault("field_confidences", {
            "drug_name": round(med.get("formulary_match_score", 0.0), 4),
            "dosage": logprob_conf,
            "frequency": logprob_conf,
        })

    # ---- Step 8: Final metadata ----
    requires_review = bool(review_reasons) or calibrated_conf < 0.5
    if requires_review and "low_overall_confidence" not in review_reasons:
        if calibrated_conf < 0.5:
            review_reasons.append("low_overall_confidence")

    result["requires_human_review"] = requires_review
    result["review_reasons"] = review_reasons
    result["model_version"] = model_version
    result["timings_ms"] = timings_ms

    log.info(
        "postprocessing_done",
        confidence=calibrated_conf,
        med_count=total_meds,
        requires_review=requires_review,
    )
    return result


def _repair_and_parse(raw: str, log) -> dict:
    """Try json-repair → direct parse → regex extraction."""
    # Primary: json-repair (handles trailing commas, unquoted keys, etc.)
    try:
        import json_repair
        repaired = json_repair.repair_json(raw)
        return json.loads(repaired)
    except Exception:
        pass
    # Fallback: find first {...} block
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    log.warning("json_parse_failed", raw_length=len(raw))
    return {}


def _normalize_schema(parsed: dict, job_id: str, session_id: str) -> dict:
    """Ensure all §6 required keys exist with correct types."""
    meds_raw = parsed.get("medications", [])
    if not isinstance(meds_raw, list):
        meds_raw = []
    meds = []
    for m in meds_raw:
        if isinstance(m, dict) and m.get("drug_name"):
            meds.append({
                "drug_name": str(m.get("drug_name", "")),
                "drug_name_normalized": None,
                "formulary_match_score": 0.0,
                "dosage": m.get("dosage"),
                "dosage_normalized": None,
                "frequency": m.get("frequency"),
                "duration": m.get("duration"),
                "instructions": m.get("instructions"),
                "warnings": m.get("warnings"),
                "track_id": int(m.get("track_id", 0)),
                "field_confidences": {"drug_name": 0.0, "dosage": 0.0, "frequency": 0.0},
                "requires_review": False,
            })
    return {
        "job_id": job_id,
        "session_id": session_id,
        "prescription_id": parsed.get("prescription_id") or None,
        "patient_name": parsed.get("patient_name") or None,
        "doctor_name": parsed.get("doctor_name") or None,
        "issue_date": parsed.get("issue_date") or None,
        "medications": meds,
        "additional_notes": parsed.get("additional_notes") or None,
        "extracted_raw_text": str(parsed.get("extracted_raw_text", "")),
        "overall_confidence": float(parsed.get("overall_confidence", 0.0)),
        "requires_human_review": False,
        "review_reasons": [],
    }


def _normalize_date(raw: str) -> Optional[str]:
    if not raw:
        return None
    raw = raw.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw
    lower = raw.lower()
    for fr, num in FRENCH_MONTH_MAP.items():
        if fr in lower:
            lower = lower.replace(fr, num)
            break
    for ar, num in ARABIC_MONTH_MAP.items():
        if ar in raw:
            raw = raw.replace(ar, num)
            break
    try:
        from dateutil import parser as dp
        return dp.parse(lower, dayfirst=True).strftime("%Y-%m-%d")
    except Exception:
        pass
    for pat in (r"(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})", r"(\d{4})[/\-\.](\d{1,2})[/\-\.](\d{1,2})"):
        m = re.search(pat, raw)
        if m:
            g = m.groups()
            try:
                if len(g[0]) == 4:
                    return f"{g[0]}-{g[1].zfill(2)}-{g[2].zfill(2)}"
                return f"{g[2]}-{g[1].zfill(2)}-{g[0].zfill(2)}"
            except Exception:
                pass
    return raw  # return original if all parsing fails


def _normalize_dosage(raw: str) -> Optional[dict]:
    m = DOSAGE_UNITS_RE.search(raw)
    if not m:
        return None
    value_str = m.group(1).replace(",", ".")
    unit = UNIT_NORMALIZE.get(m.group(2).lower(), m.group(2).lower())
    try:
        return {"value": float(value_str), "unit": unit}
    except ValueError:
        return None


def _clean_null(val: Any) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    if s.lower() in ("null", "none", "n/a", "na", "-", "", "unknown"):
        return None
    return s


def _extract_logprob_confidence(logprobs: list[dict]) -> float:
    """Average min-logprob over output tokens → confidence proxy (DD-006)."""
    if not logprobs:
        return 0.5  # neutral default
    lps = []
    for token_data in logprobs:
        lp = token_data.get("logprob")
        if lp is not None:
            lps.append(lp)
    if not lps:
        return 0.5
    avg_lp = sum(lps) / len(lps)
    # Convert log-prob (typically -10..0) to 0..1 range
    conf = math.exp(max(avg_lp, -5.0))
    return round(min(max(conf, 0.0), 1.0), 4)


def _compute_completeness(result: dict) -> float:
    """Fraction of required fields that are non-null."""
    filled = sum(1 for f in REQUIRED_FIELDS if result.get(f))
    return filled / len(REQUIRED_FIELDS)
