"""
Medical Named Entity Recognition (NER) for prescription text.

Uses spaCy blank("fr") + EntityRuler — NO model download required.
Patterns are built at runtime from the Tunisian AMM registry.

Labels:
  DRUG      — trade name (from drug_registry.json)
  DRUG_INN  — INN/DCI (from drug_dict.json)

Three validation functions:

1. run_ner_validation(medications, raw_text, doctor_name)
   - Checks each extracted medication name for NER entity support
   - Scans raw extracted_raw_text for drug mentions the LLM may have missed
   - Checks that doctor_name heuristically looks like a person name
   Returns a ner_validation dict that is stored in the pipeline result.

2. validate_drug_name(name)         — quick single-name check
3. validate_doctor_name(name)       — heuristic person-name check
"""

import os
import json
import re
from functools import lru_cache
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

_DEFAULT_REFERANCES = "/app/referances"
if not os.environ.get("REFERANCES_DIR") and not os.path.isdir(_DEFAULT_REFERANCES):
    _local = os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../..", "referances")
    )
    if os.path.isdir(_local):
        _DEFAULT_REFERANCES = _local
REFERANCES_DIR = os.getenv("REFERANCES_DIR", _DEFAULT_REFERANCES)

# Pre-serialised model path — baked into the Docker image at build time.
# Loading from disk takes < 5 s; building from patterns takes ~20 min.
_NER_MODEL_PATH = os.path.join(REFERANCES_DIR, "ner_model")

# Limit number of patterns when falling back to runtime build (local dev only)
MAX_DRUG_PATTERNS = 3000
MAX_INN_PATTERNS  = 1000

try:
    import spacy
    _SPACY_AVAILABLE = True
except ImportError:
    _SPACY_AVAILABLE = False
    logger.warning("spacy_not_installed",
                   msg="NER validation disabled — add spacy to requirements.txt")


@lru_cache(maxsize=1)
def _build_nlp():
    """Load the pre-serialised spaCy NER pipeline (< 5 s).

    The model is baked into the Docker image by the Dockerfile RUN step that
    calls build_ner_model.py at image-build time.  Loading from disk avoids
    the ~20-minute cold-build cost that occurred when patterns were compiled
    at container startup.

    Falls back to runtime build if the serialised model is missing
    (local development without the Docker image).
    """
    if not _SPACY_AVAILABLE:
        return None

    # ── Fast path: load pre-serialised model from Docker image ──────────────
    if os.path.isdir(_NER_MODEL_PATH):
        try:
            nlp = spacy.load(_NER_MODEL_PATH)
            logger.info("ner_model_loaded", path=_NER_MODEL_PATH,
                        pipes=nlp.pipe_names)
            return nlp
        except Exception as exc:
            logger.warning("ner_model_load_failed", path=_NER_MODEL_PATH,
                           exc=str(exc), fallback="building_from_patterns")

    # ── Slow fallback: build from patterns (local dev / missing model) ───────
    logger.warning("ner_model_not_found",
                   path=_NER_MODEL_PATH,
                   msg="Building NER pipeline from patterns — this takes ~20 min on cold start. "
                       "Run build_ner_model.py and include the output in your Docker image.")
    try:
        nlp = spacy.blank("fr")
        ruler = nlp.add_pipe("entity_ruler", config={"phrase_matcher_attr": "LOWER"})
        patterns: list[dict] = []

        reg_path = os.path.join(REFERANCES_DIR, "drug_registry.json")
        if os.path.exists(reg_path):
            with open(reg_path, encoding="utf-8") as f:
                registry = json.load(f)
            for i, name in enumerate(registry):
                if i >= MAX_DRUG_PATTERNS:
                    break
                name = (name or "").strip()
                if len(name) >= 3:
                    patterns.append({"label": "DRUG", "pattern": name})
        else:
            logger.warning("ner_drug_registry_not_found", path=reg_path)

        dict_path = os.path.join(REFERANCES_DIR, "drug_dict.json")
        if os.path.exists(dict_path):
            with open(dict_path, encoding="utf-8") as f:
                drug_dict = json.load(f)
            for i, dci in enumerate(drug_dict):
                if i >= MAX_INN_PATTERNS:
                    break
                dci = (dci or "").strip()
                if len(dci) >= 3:
                    patterns.append({"label": "DRUG_INN", "pattern": dci})
        else:
            logger.warning("ner_drug_dict_not_found", path=dict_path)

        ruler.add_patterns(patterns)
        logger.info("ner_pipeline_built", pattern_count=len(patterns))
        return nlp

    except Exception as exc:
        logger.error("ner_build_failed", error=str(exc))
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_ner_validation(
    medications: list[dict],
    raw_text: Optional[str],
    doctor_name: Optional[str],
) -> dict:
    """
    Full NER validation pass for one prescription result.

    Returns:
    {
      "ner_available": bool,
      "drug_validation": {
        "confirmed":         list[str],  # drug names that matched NER patterns
        "unconfirmed":       list[str],  # drug names with no NER support
                                         # (may be novel/handwritten; NOT removed)
        "missed_candidates": list[str],  # drug-like text in raw_text not in meds list
                                         # flagged for pharmacist review
      },
      "doctor_name_valid":        bool,  # heuristic: looks like a person name
      "ner_added_review_reason":  bool,  # True if missed_candidates found
    }
    """
    result: dict = {
        "ner_available": _SPACY_AVAILABLE,
        "drug_validation": {
            "confirmed": [],
            "unconfirmed": [],
            "missed_candidates": [],
        },
        "doctor_name_valid": True,
        "ner_added_review_reason": False,
    }

    nlp = _build_nlp()
    if nlp is None:
        return result

    # ── Validate each extracted medication name ──────────────────────────────
    med_names_lower: set[str] = set()

    for med in medications or []:
        name: str = (med.get("drug_name") or "").strip()
        if not name:
            continue

        confirmed = _has_drug_entity(nlp, name)

        # Also try the spell-corrected or normalised name if available
        if not confirmed:
            alt = (med.get("drug_name_spell_corrected") or
                   med.get("drug_name_normalized") or "")
            if alt:
                confirmed = _has_drug_entity(nlp, alt)

        if confirmed:
            result["drug_validation"]["confirmed"].append(name)
        else:
            result["drug_validation"]["unconfirmed"].append(name)

        med_names_lower.add(name.lower())

    # ── Scan raw text for drug mentions the model may have missed ────────────
    if raw_text and raw_text.strip():
        latin_text = _strip_arabic(raw_text)
        if latin_text.strip():
            doc = nlp(latin_text)
            for ent in doc.ents:
                if ent.label_ in ("DRUG", "DRUG_INN"):
                    entity_text = ent.text.strip()
                    # Only report if it wasn't already in the medications list
                    if entity_text.lower() not in med_names_lower and len(entity_text) >= 3:
                        result["drug_validation"]["missed_candidates"].append(entity_text)

    # ── Validate doctor name heuristically ───────────────────────────────────
    if doctor_name:
        result["doctor_name_valid"] = validate_doctor_name(doctor_name)

    # Flag if there are missed drug candidates
    if result["drug_validation"]["missed_candidates"]:
        result["ner_added_review_reason"] = True
        logger.debug(
            "ner_missed_candidates",
            count=len(result["drug_validation"]["missed_candidates"]),
            candidates=result["drug_validation"]["missed_candidates"][:5],
        )

    logger.debug(
        "ner_validation_done",
        confirmed=len(result["drug_validation"]["confirmed"]),
        unconfirmed=len(result["drug_validation"]["unconfirmed"]),
        missed=len(result["drug_validation"]["missed_candidates"]),
    )
    return result


def validate_drug_name(name: str) -> bool:
    """Return True if the name matches at least one DRUG or DRUG_INN pattern."""
    if not name or not name.strip():
        return False
    nlp = _build_nlp()
    if nlp is None:
        return True  # can't validate without spaCy — assume valid
    return _has_drug_entity(nlp, name)


def validate_doctor_name(name: str) -> bool:
    """
    Heuristic: a valid doctor/patient name must have at least one alphabetic
    token of length >= 2. Rejects pure-digit strings, single-character names,
    and obvious placeholder values.
    """
    if not name or not name.strip():
        return False
    normalized = name.strip().lower()
    # Reject obvious placeholders
    if normalized in ("null", "none", "n/a", "na", "-", "unknown", "inconnu"):
        return False
    tokens = re.split(r"[\s,\-]+", name.strip())
    alpha_tokens = [t for t in tokens if t.isalpha() and len(t) >= 2]
    return len(alpha_tokens) >= 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_drug_entity(nlp, text: str) -> bool:
    """Return True if spaCy finds any DRUG or DRUG_INN entity in text."""
    doc = nlp(text)
    return any(ent.label_ in ("DRUG", "DRUG_INN") for ent in doc.ents)


def _strip_arabic(text: str) -> str:
    """Replace Arabic Unicode block characters with spaces (keep Latin portions)."""
    return "".join(" " if "؀" <= c <= "ۿ" else c for c in text)
