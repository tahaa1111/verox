"""
Drug name normalization using the real Tunisian reference files (DD-013).

Reference files mounted at /app/referances/:
  - drug_dict.json:     {INN: [trade_name, ...]}  — maps active ingredient → brands
  - drug_registry.json: {trade_name: {dci, dosage, form, amm, class, ...}}

Strategy (§5.3 step 3):
1. Build FORMULARY_NAMES: all lowercase keys from both files (~15,000+ entries)
2. Build TRADE_TO_INN: inverted drug_dict.json for INN lookup
3. Build REGISTRY: full metadata from drug_registry.json
4. RapidFuzz WRatio match, threshold 85 (spec §5.3)
5. Below threshold → flag requires_review: True, pass through raw name
"""

import json
import os
from functools import lru_cache
from typing import Optional

import structlog
from rapidfuzz import process, fuzz

logger = structlog.get_logger(__name__)

# In production the reference files are at /app/referances (baked into the container).
# For local development they live at <project-root>/referances/.
# The env var REFERANCES_DIR always wins when set explicitly.
_DEFAULT_REFERANCES: str = "/app/referances"
if not os.environ.get("REFERANCES_DIR") and not os.path.isdir(_DEFAULT_REFERANCES):
    _local = os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../..", "referances")
    )
    if os.path.isdir(_local):
        _DEFAULT_REFERANCES = _local
REFERANCES_DIR = os.getenv("REFERANCES_DIR", _DEFAULT_REFERANCES)
MATCH_THRESHOLD = 85


@lru_cache(maxsize=1)
def _build_indexes() -> tuple[dict, dict, list[str]]:
    """Returns (trade_to_inn, registry, formulary_names_list). Cached after first call."""
    drug_dict_path = os.path.join(REFERANCES_DIR, "drug_dict.json")
    drug_registry_path = os.path.join(REFERANCES_DIR, "drug_registry.json")

    trade_to_inn: dict[str, str] = {}       # lowercase trade name → INN
    registry: dict[str, dict] = {}          # lowercase trade name → full metadata
    formulary_names: set[str] = set()

    # ---- drug_dict.json: {INN: [trade_name, ...]} ----
    if os.path.exists(drug_dict_path):
        with open(drug_dict_path, encoding="utf-8") as f:
            drug_dict: dict = json.load(f)
        for inn, trade_names in drug_dict.items():
            inn_lower = inn.lower().strip()
            formulary_names.add(inn_lower)
            for trade in trade_names:
                t_lower = trade.lower().strip()
                formulary_names.add(t_lower)
                trade_to_inn[t_lower] = inn  # canonical INN
    else:
        logger.warning("drug_dict_not_found", path=drug_dict_path)

    # ---- drug_registry.json: {trade_name: {dci, dosage, ...}} ----
    if os.path.exists(drug_registry_path):
        with open(drug_registry_path, encoding="utf-8") as f:
            drug_reg: dict = json.load(f)
        for trade_name, meta in drug_reg.items():
            t_lower = trade_name.lower().strip()
            formulary_names.add(t_lower)
            # drug_registry entries can be dict or list[dict]
            if isinstance(meta, list):
                meta_item = meta[0] if meta else {}
            else:
                meta_item = meta
            registry[t_lower] = meta_item
            # Also index by DCI (INN) from registry
            dci = meta_item.get("dci", "")
            if dci:
                formulary_names.add(dci.lower().strip())
    else:
        logger.warning("drug_registry_not_found", path=drug_registry_path)

    names_list = sorted(formulary_names)
    logger.info("drug_indexes_built", total_names=len(names_list), trade_to_inn_count=len(trade_to_inn))
    return trade_to_inn, registry, names_list


def normalize_drug_name(raw_name: str) -> tuple[Optional[str], float, dict]:
    """
    Normalize a raw OCR drug name string.

    Returns:
        (normalized_inn, match_score_0_to_1, metadata_dict)
    """
    if not raw_name or not raw_name.strip():
        return None, 0.0, {}

    trade_to_inn, registry, formulary_names = _build_indexes()
    if not formulary_names:
        return raw_name.strip(), 0.0, {}

    cleaned = _clean_raw(raw_name)
    if not cleaned:
        return raw_name.strip(), 0.0, {}

    result = process.extractOne(
        cleaned.lower(),
        formulary_names,
        scorer=fuzz.WRatio,
        score_cutoff=MATCH_THRESHOLD,
    )

    if result is None:
        logger.debug("drug_no_match", raw=raw_name, cleaned=cleaned)
        return raw_name.strip(), 0.0, {}

    matched_name, score, _ = result
    score_float = round(score / 100.0, 4)

    # Resolve to canonical INN
    inn = trade_to_inn.get(matched_name, None)
    if inn is None:
        # matched directly on INN or DCI
        inn = matched_name.title()

    # Fetch metadata from registry
    meta = registry.get(matched_name, {})

    logger.debug("drug_matched", raw=raw_name, normalized=inn, score=score)
    return inn, score_float, meta


def load_drug_dict() -> list[str]:
    """Return the sorted list of all known drug/INN/trade names from the formulary.

    Calls _build_indexes() (which is LRU-cached) and returns the names list.
    Primarily used for health-checks and unit tests; see _build_indexes() for
    the full trade-to-INN and registry dicts.
    """
    _, _, formulary_names = _build_indexes()
    return formulary_names


def _clean_raw(name: str) -> str:
    """Remove OCR artifacts and dosage fragments before matching."""
    import re
    name = name.strip()
    # Remove dosage fused to name (e.g. "Amox500mg" → "Amox")
    name = re.sub(r"\b\d+\s*(mg|g|ml|mcg|iu|ui|µg|cp|co|gél|gel)\b", "", name, flags=re.IGNORECASE)
    # Remove special chars except hyphens
    name = re.sub(r"[^\w\s\-]", " ", name)
    # Collapse whitespace
    name = re.sub(r"\s+", " ", name).strip()
    return name
