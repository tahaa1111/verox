"""
Postprocessing pipeline (spec §5.3).
Steps in order:
1.  JSON repair (json-repair library)
2.  Schema validation (Pydantic v2)
2.5 Spell correction (SymSpell + AMM vocabulary) — fixes OCR typos before fuzzy match
3.  Drug name normalization   (Tunisian AMM formulary — liste_amm.xls)
4.  Doctor name normalization (Tunisian provider registry — TAHA.xlsx)
4c. NER validation (spaCy EntityRuler) — confirms entities + scans for missed drugs
4d. Specialty–drug coherence check — cross-references each drug against the doctor's
    specialty using AMM class/subclass + drug_descriptions.json indications
5.  Date normalization (French + Arabic → ISO 8601)
6.  Dosage normalization (regex pipeline)
7.  Hallucination filter (unregistered drugs → requires_review)
8.  Confidence calibration (logprob + formulary score + completeness, DD-007)
9.  Audit trail construction
"""

import json
import math
import re
from typing import Any, Optional

import structlog

from services.worker.utils.drug_normalizer import normalize_drug_name
from services.worker.utils.doctor_normalizer import normalize_doctor_name
from services.worker.utils.medical_spellcheck import spell_correct_drug
from services.worker.utils.medical_ner import run_ner_validation
from services.worker.utils.specialty_validator import (
    validate_specialty_coherence,
    enrich_medications_with_specialty,
)

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
    crop_meta: dict | None = None,
    slot_map: dict | None = None,
) -> dict:
    """Full postprocessing pipeline. Returns the §6 output schema dict.

    crop_meta: {track_id: {yolo_confidence, bbox}} — YOLO confidence per crop from Pi.
    slot_map:  {cell_number: track_id} — maps model cell index back to original crop.
    """

    log = logger.bind(job_id=job_id)

    # ---- Step 1: JSON repair ----
    parsed = _repair_and_parse(raw_output, log)

    # ---- Step 2: Schema validation (lenient — we fill defaults) ----
    result = _normalize_schema(parsed, job_id, session_id)

    # ---- Step 2.5: Spell correction on extracted drug names ----
    # Fix OCR typos in drug names BEFORE sending to the AMM fuzzy matcher.
    # Original OCR value is always preserved in drug_name; corrected form stored
    # in drug_name_spell_corrected and used as fuzzy-match input.
    spell_correction_count = 0
    for med in result.get("medications", []):
        raw = med.get("drug_name", "") or ""
        corrected, was_corrected, edit_dist = spell_correct_drug(raw)
        med["drug_name_spell_corrected"] = corrected if was_corrected else None
        med["spell_correction_distance"] = edit_dist if was_corrected else 0
        if was_corrected:
            spell_correction_count += 1
            log.debug("spell_correction", original=raw, corrected=corrected, distance=edit_dist)

    if spell_correction_count:
        log.info("spell_corrections_applied", count=spell_correction_count)

    # ---- Step 3: Medication enrichment against AMM registry ----
    # OCR extracts all detected text. References enrich and validate — nothing is dropped.
    # Unmatched drugs stay in the list but are flagged requires_review=True.
    review_reasons: list[str] = []

    for med in result.get("medications", []):
        # Use spell-corrected name as fuzzy-match input when available (better match quality)
        raw_drug = med.get("drug_name_spell_corrected") or med.get("drug_name", "")
        inn, formulary_score, registry_meta = normalize_drug_name(raw_drug)
        med["drug_name_normalized"] = inn
        med["formulary_match_score"] = formulary_score

        # Enrich with AMM metadata when available
        med["amm_info"] = {
            "dci":        registry_meta.get("dci"),
            "form":       registry_meta.get("form"),
            "lab":        registry_meta.get("lab"),
            "amm":        registry_meta.get("amm"),
            "class":      registry_meta.get("class"),
            "dosage_ref": registry_meta.get("dosage"),
        } if registry_meta else {}

        if formulary_score >= 0.85:
            med["requires_review"] = False
        else:
            # Not found in registry — flag for pharmacist, keep as-is in output
            med["requires_review"] = True
            if "formulary_miss" not in review_reasons:
                review_reasons.append("formulary_miss")

        # Dosage normalization
        raw_dosage = med.get("dosage", "")
        if raw_dosage:
            med["dosage_normalized"] = _normalize_dosage(raw_dosage)

        for field in ("dosage", "frequency", "duration", "instructions", "warnings"):
            med[field] = _clean_null(med.get(field))

    # ---- Step 4: Issue date normalization ----
    raw_date = result.get("issue_date")
    if raw_date:
        result["issue_date"] = _normalize_date(raw_date)

    # ---- Step 4b: Doctor name enrichment against TAHA registry ----
    # Raw name always kept. If matched → enrich with specialty/address.
    # If not matched → flag doctor_unregistered but still show the raw name.
    raw_doctor = _clean_null(result.get("doctor_name"))
    result["doctor_name"] = raw_doctor
    if raw_doctor:
        canonical_doctor, doctor_score, doctor_meta = normalize_doctor_name(raw_doctor)
        result["doctor_match_score"] = doctor_score
        if doctor_score >= 0.80:
            # Confirmed — replace display name with canonical registry name + enrich
            result["doctor_name"]            = canonical_doctor
            result["doctor_name_normalized"] = canonical_doctor
            result["doctor_info"]            = {
                "specialite": doctor_meta.get("specialite"),
                "type":       doctor_meta.get("type"),
                "adresse":    doctor_meta.get("adresse"),
            } if doctor_meta else {}
            # Sync canonical name into nested doctor object (used by frontend PatientCard)
            if result.get("doctor") and isinstance(result["doctor"], dict):
                result["doctor"]["name"] = canonical_doctor
        else:
            # Not found — keep raw name, surface to pharmacist
            result["doctor_name_normalized"] = None
            result["doctor_info"]            = {}
            review_reasons.append("doctor_unregistered")
    else:
        result["doctor_name_normalized"] = None
        result["doctor_match_score"]     = 0.0
        result["doctor_info"]            = {}

    # ---- Step 4c: NER validation ----
    # Run named-entity recognition over medication names and raw extracted text.
    # • confirmed    — drug names that matched AMM entity patterns (high confidence)
    # • unconfirmed  — drug names with no NER support (possible OCR noise or novel drug)
    # • missed_candidates — drug-like text found in raw_text but NOT in medications list
    #   → flagged for pharmacist review (model may have missed them)
    ner_result = run_ner_validation(
        medications=result.get("medications", []),
        raw_text=result.get("extracted_raw_text"),
        doctor_name=result.get("doctor_name"),
    )
    result["ner_validation"] = ner_result

    if ner_result.get("ner_added_review_reason"):
        review_reasons.append("ner_missed_drug_candidates")

    if not ner_result.get("doctor_name_valid", True) and result.get("doctor_name"):
        review_reasons.append("doctor_name_suspicious")

    # ---- Step 4d: Specialty–drug coherence check ----
    # Cross-reference each drug's AMM class/subclass against the prescribing
    # doctor's specialty.  Drugs outside the specialty's typical scope are
    # flagged as "mismatch" so a pharmacist can verify the prescription.
    # Requires doctor_info.specialite — present only when doctor was matched
    # against the TAHA registry in step 4b.
    doctor_specialty = (result.get("doctor_info") or {}).get("specialite") or None
    specialty_result = validate_specialty_coherence(
        medications=result.get("medications", []),
        specialty=doctor_specialty,
        doctor_name=result.get("doctor_name"),
    )
    # Merge specialty scores directly into each medication dict
    enrich_medications_with_specialty(result.get("medications", []), specialty_result)
    # Surface aggregate specialty score at the result level
    result["prescription_specialty_score"] = specialty_result.get("prescription_score", 1.0)
    result["specialty_checked"] = specialty_result.get("specialty_checked", False)
    result["specialty_normalized"] = specialty_result.get("specialty_normalized")

    if specialty_result.get("any_mismatch"):
        review_reasons.append("specialty_drug_mismatch")
        log.info(
            "specialty_mismatch",
            specialty=specialty_result.get("specialty_normalized"),
            prescription_score=specialty_result.get("prescription_score"),
        )

    # ---- Step 5: Clean PII nulls ----
    result["patient_name"] = _clean_null(result.get("patient_name"))
    result["doctor_name"]  = _clean_null(result.get("doctor_name"))

    # ---- Step 6: Hallucination filter ----
    flagged_count = sum(1 for m in result.get("medications", []) if m.get("requires_review"))
    total_meds    = len(result.get("medications", []))
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
        # Top-level per-drug confidence used by the UI (weighted blend of field scores)
        fc = med["field_confidences"]
        med.setdefault("confidence", round(
            0.50 * fc.get("drug_name", 0.0) +
            0.30 * fc.get("dosage", logprob_conf) +
            0.20 * fc.get("frequency", logprob_conf),
            4,
        ))

    # ---- Step 8a: Build crop_texts — per-crop text with YOLO + model confidence ----
    # Merges model's cell_texts (per-cell verbatim text) with YOLO detection confidence
    # from the Pi. Falls back gracefully if either is missing.
    cell_texts_raw = parsed.get("cell_texts", [])
    crop_texts: list[dict] = []
    _slot_map: dict = slot_map or {}
    _crop_meta: dict = crop_meta or {}

    if cell_texts_raw and isinstance(cell_texts_raw, list):
        for ct in cell_texts_raw:
            if not isinstance(ct, dict):
                continue
            cell_num = int(ct.get("cell", 0))
            text = str(ct.get("text", "")).strip()
            model_conf = float(ct.get("model_confidence", logprob_conf))
            track_id = _slot_map.get(cell_num, cell_num)
            crop_info: dict = _crop_meta.get(track_id) or {}
            yolo_conf = float(crop_info.get("yolo_confidence", 1.0))
            if text:
                crop_texts.append({
                    "cell": cell_num,
                    "track_id": track_id,
                    "text": text,
                    "model_confidence": round(model_conf, 3),
                    "yolo_confidence": round(yolo_conf, 3),
                })
    elif not cell_texts_raw and result.get("extracted_raw_text"):
        # Fallback: model didn't return cell_texts but has raw text — wrap as single crop
        first_crop: dict = next(iter(_crop_meta.values()), {}) if _crop_meta else {}
        yolo_conf = float(first_crop.get("yolo_confidence", 1.0))
        first_track_id = next(iter(_slot_map.values()), 1) if _slot_map else 1
        crop_texts.append({
            "cell": 1,
            "track_id": first_track_id,
            "text": result["extracted_raw_text"],
            "model_confidence": round(logprob_conf, 3),
            "yolo_confidence": round(yolo_conf, 3),
        })

    result["crop_texts"] = crop_texts

    # Ensure extracted_raw_text is never empty — aggregate from cell_texts if needed
    if not result.get("extracted_raw_text") and crop_texts:
        result["extracted_raw_text"] = "\n\n".join(ct["text"] for ct in crop_texts)

    # ---- Step 8: Final metadata ----
    requires_review = bool(review_reasons) or calibrated_conf < 0.5
    if requires_review and "low_overall_confidence" not in review_reasons:
        if calibrated_conf < 0.5:
            review_reasons.append("low_overall_confidence")

    # Flag hallucination risk: all extracted drugs have near-zero per-drug confidence
    # (model's own logprobs were effectively zero — likely hallucinated from a blank/noisy image).
    meds_list = result.get("medications", [])
    if meds_list:
        low_conf_drugs = [
            m for m in meds_list
            if (m.get("confidence") or 0.0) < 0.05
        ]
        if len(low_conf_drugs) == len(meds_list):
            requires_review = True
            if "all_drugs_low_confidence" not in review_reasons:
                review_reasons.append("all_drugs_low_confidence")

    # Non-prescription image types always require review and clear any medication results
    image_type = result.get("image_type", "prescription")
    if image_type in ("drug_box", "unknown", "blank"):
        requires_review = True
        reason_key = f"image_type_{image_type}"
        if reason_key not in review_reasons:
            review_reasons.append(reason_key)
        # Ensure medications list is empty for non-prescription images
        result["medications"] = []

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
                # Spell correction (step 2.5)
                "drug_name_spell_corrected": None,
                "spell_correction_distance": 0,
                # AMM fuzzy match (step 3)
                "drug_name_normalized": None,
                "formulary_match_score": 0.0,
                "amm_info": {},
                "requires_review": False,
                # Specialty coherence (step 4d)
                "specialty_match":       "neutral",
                "specialty_match_score": 0.6,
                "specialty_note":        "",
                # Dosage / usage
                "dosage": m.get("dosage"),
                "dosage_normalized": None,
                "frequency": m.get("frequency"),
                "duration": m.get("duration"),
                "quantity": m.get("quantity"),       # number of boxes/units
                "instructions": m.get("instructions"),
                "warnings": m.get("warnings"),
                "cnam": bool(m.get("cnam", False)),  # reimbursement flag
                "track_id": int(m.get("track_id", 0)),
                "field_confidences": {"drug_name": 0.0, "dosage": 0.0, "frequency": 0.0},
            })

    # ── Patient / Doctor: support both new nested schema and old flat fields ──
    # New model schema: patient.name / patient.last_name / doctor.name / doctor.stamp
    # Old model schema: patient_name (flat) / doctor_name (flat)
    patient_raw = parsed.get("patient") or {}
    if not isinstance(patient_raw, dict):
        patient_raw = {}
    doctor_raw = parsed.get("doctor") or {}
    if not isinstance(doctor_raw, dict):
        doctor_raw = {}

    # Flat patient_name: prefer explicit flat field, fall back to nested patient.name
    patient_name_val = (
        parsed.get("patient_name")
        or patient_raw.get("name")
        or None
    )
    # Flat doctor_name: prefer explicit flat field, fall back to nested doctor.name
    doctor_name_val = (
        parsed.get("doctor_name")
        or doctor_raw.get("name")
        or None
    )

    # Structured patient object (new schema) — None when both name and last_name are absent
    patient_obj = None
    if patient_raw:
        patient_obj = {
            "name":       patient_raw.get("name") or patient_name_val,
            "last_name":  patient_raw.get("last_name") or None,
            "address":    patient_raw.get("address") or None,
            "profession": patient_raw.get("profession") or None,
        }

    # Structured doctor object (new schema) — None when name is absent
    doctor_obj = None
    if doctor_raw:
        doctor_obj = {
            "name":  doctor_raw.get("name") or doctor_name_val,
            "stamp": doctor_raw.get("stamp") or None,
        }

    return {
        "job_id": job_id,
        "session_id": session_id,
        "prescription_id": parsed.get("prescription_id") or None,
        # Flat legacy fields (kept for backward compat + PII encryption pipeline)
        "patient_name": patient_name_val,
        "doctor_name": doctor_name_val,
        # Structured patient / doctor objects (new schema — used by frontend PatientCard)
        "patient": patient_obj,
        "doctor": doctor_obj,
        "issue_date": parsed.get("issue_date") or None,
        "medications": meds,
        "additional_notes": parsed.get("additional_notes") or None,
        "extracted_raw_text": str(parsed.get("extracted_raw_text", "")),
        "overall_confidence": float(parsed.get("overall_confidence", 0.0)),
        # image_type: model-classified image category
        # Values: "prescription" | "drug_box" | "unknown" | "blank"
        # Defaults to "prescription" for backward compat with old model outputs.
        "image_type": parsed.get("image_type") or "prescription",
        "requires_human_review": False,
        "review_reasons": [],
        # Doctor normalization fields (populated in step 4b)
        "doctor_name_normalized":  None,
        "doctor_match_score":      0.0,
        "doctor_info":             {},
        # NER validation (populated in step 4c)
        "ner_validation":          {},
        # Specialty coherence (populated in step 4d)
        "prescription_specialty_score": 1.0,
        "specialty_checked":            False,
        "specialty_normalized":         None,
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
