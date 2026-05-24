"""
Doctor name normalization using the Tunisian healthcare provider registry (TAHA.xlsx).

Reference files mounted at /app/referances/:
  - doctor_names.json:    [sorted list of "NOM PRENOM" strings]
  - doctor_registry.json: {"NOM PRENOM": {nom, prenom, full_name, type, specialite, adresse}}

Strategy (mirrors drug_normalizer.py):
1. Build DOCTOR_NAMES list (~31 k entries) from doctor_names.json
2. RapidFuzz WRatio match, threshold 80 (looser than drugs — names vary more in handwriting)
3. On match → return canonical full name + metadata (specialty, type, address)
4. Below threshold → flag requires_review, pass through raw name
"""

import json
import os
from functools import lru_cache
from typing import Optional

import structlog
from rapidfuzz import process, fuzz

logger = structlog.get_logger(__name__)

_DEFAULT_REFERANCES: str = "/app/referances"
if not os.environ.get("REFERANCES_DIR") and not os.path.isdir(_DEFAULT_REFERANCES):
    _local = os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../..", "referances")
    )
    if os.path.isdir(_local):
        _DEFAULT_REFERANCES = _local
REFERANCES_DIR = os.getenv("REFERANCES_DIR", _DEFAULT_REFERANCES)

MATCH_THRESHOLD = 80   # slightly lower than drug threshold — handwritten names vary more


@lru_cache(maxsize=1)
def _build_indexes() -> tuple[list[str], dict, list[str]]:
    """Returns (names_lower, registry, names_original). Cached after first call."""
    names_path    = os.path.join(REFERANCES_DIR, "doctor_names.json")
    registry_path = os.path.join(REFERANCES_DIR, "doctor_registry.json")

    names_list: list[str] = []
    registry:   dict      = {}

    if os.path.exists(names_path):
        with open(names_path, encoding="utf-8") as f:
            names_list = json.load(f)
    else:
        logger.warning("doctor_names_not_found", path=names_path)

    if os.path.exists(registry_path):
        with open(registry_path, encoding="utf-8") as f:
            registry = json.load(f)
    else:
        logger.warning("doctor_registry_not_found", path=registry_path)

    # Build lowercase lookup list for rapidfuzz
    names_lower = [n.lower() for n in names_list]
    logger.info("doctor_indexes_built", total_names=len(names_list))
    return names_lower, registry, names_list


def normalize_doctor_name(raw_name: str) -> tuple[Optional[str], float, dict]:
    """
    Normalize a raw OCR doctor name.

    Returns:
        (canonical_full_name, match_score_0_to_1, metadata_dict)

    metadata_dict keys: full_name, type, specialite, adresse
    """
    if not raw_name or not raw_name.strip():
        return None, 0.0, {}

    names_lower, registry, names_original = _build_indexes()
    if not names_lower:
        return raw_name.strip(), 0.0, {}

    cleaned = _clean_raw(raw_name)
    if not cleaned:
        return raw_name.strip(), 0.0, {}

    result = process.extractOne(
        cleaned.lower(),
        names_lower,
        scorer=fuzz.WRatio,
        score_cutoff=MATCH_THRESHOLD,
    )

    if result is None:
        logger.debug("doctor_no_match", raw=raw_name, cleaned=cleaned)
        return raw_name.strip(), 0.0, {}

    matched_lower, score, idx = result
    score_float       = round(score / 100.0, 4)
    canonical_name    = names_original[idx]  # original casing

    # Fetch metadata from registry (may be dict or list[dict]; take first)
    meta_raw = registry.get(canonical_name, {})
    if isinstance(meta_raw, list):
        meta = meta_raw[0] if meta_raw else {}
    else:
        meta = meta_raw

    logger.debug("doctor_matched", raw=raw_name, normalized=canonical_name, score=score)
    return canonical_name, score_float, meta


def _clean_raw(name: str) -> str:
    """Normalize OCR artifacts in doctor names."""
    import re
    name = name.strip()
    # Remove common title prefixes that won't appear in the registry
    name = re.sub(r"^(Dr\.?|Pr\.?|Prof\.?|Docteur|Professeur)\s+", "", name, flags=re.IGNORECASE)
    # Remove special chars except hyphens and apostrophes
    name = re.sub(r"[^\w\s\-\']", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name
