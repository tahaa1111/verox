"""
Medical spell correction for OCR-extracted drug names.

Uses SymSpell (Damerau-Levenshtein edit distance) with a custom dictionary
built from the Tunisian AMM drug registry (trade names + DCIs).

Designed to run BEFORE the fuzzy matcher so that OCR errors like
  "Amoxiciline"   → "Amoxicilline"
  "Paracetamoi"   → "Paracetamol"
  "AUGMENTIN 1MG" → "AUGMENTIN 1G"    (unit typo)
are fixed before scoring, improving AMM match quality.

Arabic-script tokens are skipped (no Latin dictionary for them).
Short words (<4 chars), digits, and dosage units are skipped.
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

MAX_EDIT_DISTANCE = 2   # max Damerau-Levenshtein distance to accept as a correction

# Tokens that should never be spell-corrected
_SKIP_RE = re.compile(
    r"""
    ^\d+([.,]\d+)?$         |  # pure number
    ^\d*(mg|g|ml|µg|mcg|iu |   # dosage unit (with optional leading digit)
          ui|cp|co|cpr|gél|gel|comprimé|sachet|mL)$
    """,
    re.IGNORECASE | re.VERBOSE,
)

try:
    from symspellpy import SymSpell, Verbosity
    _SYMSPELL_AVAILABLE = True
except ImportError:
    _SYMSPELL_AVAILABLE = False
    logger.warning("symspellpy_not_installed",
                   msg="Spell correction disabled — add symspellpy to requirements.txt")


@lru_cache(maxsize=1)
def _build_spell() -> Optional["SymSpell"]:  # type: ignore[name-defined]
    """Build the SymSpell instance once from AMM vocabulary. Cached after first call."""
    if not _SYMSPELL_AVAILABLE:
        return None

    sym = SymSpell(max_dictionary_edit_distance=MAX_EDIT_DISTANCE, prefix_length=7)
    word_counts: dict[str, int] = {}

    # Trade names → words (lower weight, brand names vary more)
    reg_path = os.path.join(REFERANCES_DIR, "drug_registry.json")
    if os.path.exists(reg_path):
        with open(reg_path, encoding="utf-8") as f:
            registry = json.load(f)
        for name in registry:
            for word in name.split():
                w = word.lower().rstrip(".,;:()")
                if w and len(w) >= 3 and not _SKIP_RE.match(w) and not _is_arabic(w):
                    word_counts[w] = word_counts.get(w, 0) + 1
    else:
        logger.warning("spell_drug_registry_not_found", path=reg_path)

    # DCIs (INN names) → higher weight — more authoritative spelling
    dict_path = os.path.join(REFERANCES_DIR, "drug_dict.json")
    if os.path.exists(dict_path):
        with open(dict_path, encoding="utf-8") as f:
            drug_dict = json.load(f)
        for dci in drug_dict:
            for word in dci.split():
                w = word.lower().rstrip(".,;:()")
                if w and len(w) >= 3 and not _SKIP_RE.match(w) and not _is_arabic(w):
                    word_counts[w] = word_counts.get(w, 0) + 10
    else:
        logger.warning("spell_drug_dict_not_found", path=dict_path)

    for word, count in word_counts.items():
        sym.create_dictionary_entry(word, count)

    logger.info("spell_dictionary_built", unique_words=len(word_counts))
    return sym


def spell_correct_drug(name: str) -> tuple[str, bool, int]:
    """
    Spell-correct an OCR-extracted drug name token-by-token.

    Returns:
        (corrected_name, was_corrected, max_edit_distance_applied)

    Tokens that are too short, pure numbers, dosage units, or Arabic-script
    are passed through unchanged.
    """
    if not name or not name.strip():
        return name, False, 0

    sym = _build_spell()
    if sym is None:
        return name, False, 0

    tokens = name.strip().split()
    corrected_tokens: list[str] = []
    any_corrected = False
    max_dist = 0

    for token in tokens:
        clean = token.lower().strip(".,;:()")

        # --- skip rules ---
        if len(clean) < 4 or _SKIP_RE.match(clean) or _is_arabic(clean):
            corrected_tokens.append(token)
            continue

        suggestions = sym.lookup(
            clean,
            Verbosity.CLOSEST,
            max_edit_distance=MAX_EDIT_DISTANCE,
            include_unknown=False,
        )

        if not suggestions or suggestions[0].distance == 0:
            # already a known word or no suggestion found — keep as-is
            corrected_tokens.append(token)
            continue

        suggestion = suggestions[0].term  # best (closest) suggestion

        # Preserve original casing style (ALL-CAPS, Title, lower)
        if token.isupper():
            corrected_tokens.append(suggestion.upper())
        elif token[0].isupper():
            corrected_tokens.append(suggestion.capitalize())
        else:
            corrected_tokens.append(suggestion)

        any_corrected = True
        max_dist = max(max_dist, suggestions[0].distance)
        logger.debug(
            "spell_token_corrected",
            original=token,
            corrected=suggestion,
            distance=suggestions[0].distance,
        )

    corrected = " ".join(corrected_tokens)
    return corrected, any_corrected, max_dist


def _is_arabic(text: str) -> bool:
    """Return True if more than half the characters are in the Arabic Unicode block."""
    arabic = sum(1 for c in text if "؀" <= c <= "ۿ")
    return len(text) > 0 and arabic / len(text) > 0.5
