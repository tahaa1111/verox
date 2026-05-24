"""
Specialty–Drug Coherence Validator
====================================
Cross-checks each prescribed drug against the prescribing doctor's specialty.

Sources used (in priority order):
  1. drug_descriptions.json   — rich indication text imported from the user's CSV
                                 (run:  python3 referances/import_drug_descriptions.py your_file.csv)
  2. drug_registry.json       — AMM class / subclass / indications (already in pipeline)
  3. Built-in specialty map   — maps each TAHA specialty to expected AMM classes/subclasses

Logic per medication:
  CONFIRMED  (1.0) — drug class is in the specialty's expected scope, OR drug is universal
  NEUTRAL    (0.6) — specialty unknown / Médecin Généraliste (no restriction)
  POSSIBLE   (0.4) — drug class is adjacent but not typical for this specialty
  MISMATCH   (0.1) — drug class is very unlikely for this specialty

Output added to each medication dict:
  specialty_match        : "confirmed" | "neutral" | "possible" | "mismatch"
  specialty_match_score  : float 0.0–1.0
  specialty_note         : human-readable explanation

Output added to result dict:
  prescription_specialty_score   : float — average across all medications
  specialty_review_reason        : str | None — set if any mismatch found
"""

import json
import os
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

# ---------------------------------------------------------------------------
# Specialty → (primary_classes, primary_subclasses, secondary_subclasses)
#
# primary_classes    — AMM class names that are CORE to this specialty
# primary_subclasses — AMM subclass names that are expected for this specialty
# secondary_subclasses — subclasses that are plausible but not typical
# ---------------------------------------------------------------------------
_UNIVERSAL_SUBCLASSES = {
    "ANALGESIQUES",
    "ANTIBACTERIENS (USAGE SYSTEMIQUE)",
    "ANTIVIRAUX (USAGE SYSTEMIQUE)",
    "ANTIPARASITAIRES - INSECTICIDES",
    "ANTIHISTAMINIQUES (USAGE SYSTEMIQUE)",
    "VITAMINES",
    "COMPLEMENTS MINERAUX",
    "ANTIEMETIQUES ET ANTINAUSEEUX",
    "MEDICAMENTS DE LA TOUX ET DU RHUME",
    "MEDICAMENTS LIES A DES PROBLEMES D'ACIDITE",
    "LAXATIFS",
    "CORTICOSTEROIDES (USAGE SYSTEMIQUE)",
    "TONIQUES",
    "VACCINS",
    "SERUMS IMMUNISANTS ET IMMUNOGLOBULINES",
    "SUBSTITUTS DU SANG ET SOLUTIONS DE PERFUSION",
    "ANESTHESIQUES",
    "ANTISEPTIQUES ET DESINFECTANTS",
    "AGENTS DIAGNOSTIQUES",
}

_UNIVERSAL_CLASSES = {
    "DIVERS",
}

# Specialties that can prescribe ANYTHING — no mismatch ever flagged
_UNRESTRICTED_SPECIALTIES = {
    "MEDECINE GENERALE",
    "MEDECINE DE FAMILLE",
    "MEDECINE INTERNE",
    "MEDECINE D'URGENCE",
    "MEDECINE DU TRAVAIL",
    "PEDIATRIE",
    "CHIRURGIE PEDIATRIQUE",
    "CHIRURGIE GENERALE",
    "REANIMATION MEDICALE",
    "HEMATOLOGIE CLINIQUE",
    "MEDECINE CARCINOLOGIQUE",
    "CHIRURGIE CARCINOLOGIQUE",
    "RADIOTHERAPIE",
    "BIOLOGIE CLINIQUE",
}

# Map TAHA specialty → (primary_classes set, primary_subclasses set)
_SPECIALTY_MAP: dict[str, dict] = {

    # ── Cardiovascular ────────────────────────────────────────────────────────
    "CARDIOLOGIE": {
        "classes":    {"SYSTEME CARDIOVASCULAIRE", "SANG ET ORGANES HEMATOPOIETIQUES"},
        "subclasses": {
            "THERAPIE CARDIAQUE", "ANTIHYPERTENSEURS", "DIURETIQUES",
            "AGENTS ANTITHROMBOTIQUES", "AGENTS REDUISANT LES LIPIDES SERIQUES",
            "AGENTS AGISSANT SUR LE SYSTEME RENINE-ANGIOTENSINE",
            "INHIBITEURS DES CANAUX DU CALCIUM", "AGENTS β-BLOQUANTS",
            "VASODILATATEURS PERIPHERIQUES", "VASOPROTECTEURS", "ANTIHEMORRAGIQUES",
        },
    },
    "CHIRURGIE CARDIO-VASCULAIRE & PERIPHERIQUE": {
        "classes":    {"SYSTEME CARDIOVASCULAIRE", "SANG ET ORGANES HEMATOPOIETIQUES"},
        "subclasses": {
            "THERAPIE CARDIAQUE", "ANTIHYPERTENSEURS", "AGENTS ANTITHROMBOTIQUES",
            "DIURETIQUES", "VASODILATATEURS PERIPHERIQUES",
        },
    },

    # ── Dermatology ───────────────────────────────────────────────────────────
    "DERMATOLOGIE": {
        "classes":    {"MEDICAMENTS DERMATOLOGIQUES"},
        "subclasses": {
            "PREPARATIONS DERMATOLOGIQUES DE CORTICOSTEROIDES",
            "ANTIFONGIQUES A USAGE DERMATOLOGIQUE",
            "ANTIBIOTIQUES ET CHIMIOTHERAPIE A USAGE DERMATOLOGIQUE",
            "AUTRES PREPARATIONS DERMATOLOGIQUES",
            "ANTI-PSORIASIS",
            "PREPARATIONS ANTI-ACNEIQUES",
            "EMOLLIENTS ET PROTECTEURS",
            "PREPARATIONS POUR LE TRAITEMENT DES PLAIES ET DES ULCERATIONS",
            "ANTIPRURIGINEUX, Y COMPRIS ANTIHISTAMINIQUES ET AN",
            "ECTOPARASITICIDES, Y COMPRIS LES SCABICIDES, INSECTICIDES ET REPULSIFS",
        },
    },

    # ── Neurology / Psychiatry ────────────────────────────────────────────────
    "NEUROLOGIE": {
        "classes":    {"SYSTEME NERVEUX"},
        "subclasses": {
            "ANTI-EPILEPTIQUES", "ANTI-PARKINSONIENS",
            "ANALGESIQUES", "ANESTHESIQUES", "MYORELAXANTS",
            "AUTRES MEDICAMENTS DU SYSTEME NERVEUX",
        },
    },
    "NEURO-CHIRURGIE": {
        "classes":    {"SYSTEME NERVEUX"},
        "subclasses": {
            "ANTI-EPILEPTIQUES", "ANALGESIQUES", "ANESTHESIQUES",
            "CORTICOSTEROIDES (USAGE SYSTEMIQUE)", "MYORELAXANTS",
        },
    },
    "PSYCHIATRIE": {
        "classes":    {"SYSTEME NERVEUX"},
        "subclasses": {
            "PSYCHOLEPTIQUES", "PSYCHOANALEPTIQUES",
            "AUTRES MEDICAMENTS DU SYSTEME NERVEUX",
        },
    },
    "PSYCHIATRIE INFANTILE": {
        "classes":    {"SYSTEME NERVEUX"},
        "subclasses": {
            "PSYCHOLEPTIQUES", "PSYCHOANALEPTIQUES",
            "ANTI-EPILEPTIQUES", "AUTRES MEDICAMENTS DU SYSTEME NERVEUX",
        },
    },

    # ── Gastroenterology ─────────────────────────────────────────────────────
    "GASTRO-ENTEROLOGIE": {
        "classes":    {"APPAREIL DIGESTIF ET METABOLISME"},
        "subclasses": {
            "MEDICAMENTS LIES A DES PROBLEMES D'ACIDITE",
            "ANTIDIARRHEIQUES, ANTI-INFLAMMATOIRES INTESTINAUX/AGENTS ANTI-INFECTIEUX",
            "DIGESTIFS, Y COMPRIS LES ENZYMES",
            "TRAITEMENT DE LA BILE ET DU FOIE",
            "MEDICAMENTS UTILISES EN CAS DE PROBLEMES FONCTIONNELS GASTRO-INTESTINAUX",
            "ANTIEMETIQUES ET ANTINAUSEEUX",
            "LAXATIFS",
            "AUTRES PRODUITS LIES AU TRACTUS DIGESTIF ET AU METABOLISME",
        },
    },

    # ── Endocrinology ─────────────────────────────────────────────────────────
    "ENDOCRINOLOGIE": {
        "classes":    {"HORMONES SYSTEMIQUES, HORMONES SEXUELLES EXCLUES",
                       "APPAREIL DIGESTIF ET METABOLISME"},
        "subclasses": {
            "MEDICAMENT UTILISE DANS LE TRAITEMENT DU DIABETE",
            "HORMONES PANCREATIQUES",
            "THERAPEUTIQUE DE LA THYROIDE",
            "THERAPEUTIQUE ENDOCRINE",
            "HORMONES HYPOPHYSAIRES, HYPOTHALAMIQUES ET ANALOGUES",
            "AGENTS REDUISANT LES LIPIDES SERIQUES",
        },
    },
    "NUTRITION": {
        "classes":    {"APPAREIL DIGESTIF ET METABOLISME",
                       "HORMONES SYSTEMIQUES, HORMONES SEXUELLES EXCLUES"},
        "subclasses": {
            "MEDICAMENT UTILISE DANS LE TRAITEMENT DU DIABETE",
            "HORMONES PANCREATIQUES",
            "VITAMINES", "COMPLEMENTS MINERAUX",
            "AUTRES PRODUITS LIES AU TRACTUS DIGESTIF ET AU METABOLISME",
        },
    },

    # ── Gynecology / Obstetrics ───────────────────────────────────────────────
    "GYNECOLOGIE-OBSTETRIQUE": {
        "classes":    {"SYSTEME GENITO URINAIRE ET HORMONES SEXUELLES"},
        "subclasses": {
            "HORMONES SEXUELLES ET MODULATEURS DU SYSTEME GENITAL",
            "ANTI-INFECTIEUX ET ANTISEPTIQUES GYNECOLOGIQUES",
            "AUTRES MEDICAMENTS GYNECOLOGIQUES",
            "AUTRES MEDICAMENTS EN CAS DE TROUBLES DU SYSTEME M",
        },
    },

    # ── Pulmonology ───────────────────────────────────────────────────────────
    "PNEUMO-PHTISIOLOGIE": {
        "classes":    {"SYSTEME RESPIRATOIRE"},
        "subclasses": {
            "MEDICAMENTS DES MALADIES RESPIRATOIRES OBSTRUCTIVE",
            "MEDICAMENTS DE LA TOUX ET DU RHUME",
            "AUTRES PRODUITS EN RELATION AVEC LE SYSTEME RESPIRATOIRE",
            "MEDICAMENTS CONTRE LES MYCOBACTERIES",
            "ANTIBACTERIENS (USAGE SYSTEMIQUE)",
        },
    },

    # ── Rheumatology ──────────────────────────────────────────────────────────
    "RHUMATOLOGIE": {
        "classes":    {"MUSCLE ET SQUELETTE"},
        "subclasses": {
            "ANTI-INFLAMMATOIRES ET ANTIRHUMATISMAUX",
            "ANTIGOUTTEUX",
            "MEDICAMENTS POUR LES MALADIES DES OS",
            "MYORELAXANTS",
            "IMMUNOSUPPRESSEURS",
            "CORTICOSTEROIDES (USAGE SYSTEMIQUE)",
            "TOPIQUES POUR LES DOULEURS ARTICULAIRES ET MUSCULA",
        },
    },
    "CHIRURGIE ORTHOPEDIQUE": {
        "classes":    {"MUSCLE ET SQUELETTE"},
        "subclasses": {
            "ANTI-INFLAMMATOIRES ET ANTIRHUMATISMAUX",
            "ANALGESIQUES", "ANESTHESIQUES", "MYORELAXANTS",
            "MEDICAMENTS POUR LES MALADIES DES OS",
        },
    },

    # ── Ophthalmology ─────────────────────────────────────────────────────────
    "OPHTALMOLOGIE": {
        "classes":    {"ORGANES SENSORIELS"},
        "subclasses": {
            "MEDICAMENTS OPHTALMOLOGIQUES",
        },
    },

    # ── ENT ───────────────────────────────────────────────────────────────────
    "O.R.L.": {
        "classes":    {"ORGANES SENSORIELS"},
        "subclasses": {
            "MEDICAMENTS OTOLOGIQUES",
            "MEDICAMENTS POUR LE NEZ",
            "MEDICAMENTS POUR LA GORGE",
            "ANTIBACTERIENS (USAGE SYSTEMIQUE)",
        },
    },

    # ── Urology ───────────────────────────────────────────────────────────────
    "UROLOGIE": {
        "classes":    {"SYSTEME GENITO URINAIRE ET HORMONES SEXUELLES"},
        "subclasses": {
            "MEDICAMENTS UROLOGIQUES",
            "HORMONES SEXUELLES ET MODULATEURS DU SYSTEME GENITAL",
        },
    },

    # ── Nephrology ────────────────────────────────────────────────────────────
    "NEPHROLOGIE": {
        "classes":    {"SYSTEME CARDIOVASCULAIRE", "SANG ET ORGANES HEMATOPOIETIQUES"},
        "subclasses": {
            "DIURETIQUES",
            "AGENTS AGISSANT SUR LE SYSTEME RENINE-ANGIOTENSINE",
            "MEDICAMENTS UROLOGIQUES",
            "ANTIHYPERTENSEURS",
            "ANTIANEMIANTS",
        },
    },

    # ── Oncology ──────────────────────────────────────────────────────────────
    "CHIRURGIE CARCINOLOGIE": {
        "classes":    {"ANTINEOPLASIQUES ET IMMUNOMODULATEURS"},
        "subclasses": {"ANTINEOPLASIQUES", "IMMUNOSUPPRESSEURS", "IMMUNOSTIMULANTS"},
    },
    "MEDECINE CARCINOLOGIQUE": {
        "classes":    {"ANTINEOPLASIQUES ET IMMUNOMODULATEURS"},
        "subclasses": {"ANTINEOPLASIQUES", "IMMUNOSUPPRESSEURS", "IMMUNOSTIMULANTS"},
    },

    # ── Infectious diseases ───────────────────────────────────────────────────
    "MALADIES INFECTIEUSES": {
        "classes":    {"ANTIINFECTIEUX GENERAUX A USAGE SYSTEMIQUE",
                       "ANTIPARASITAIRES - INSECTICIDES"},
        "subclasses": {
            "ANTIBACTERIENS (USAGE SYSTEMIQUE)",
            "ANTIVIRAUX (USAGE SYSTEMIQUE)",
            "ANTIMYCOTIQUES (USAGE SYSTEMIQUE)",
            "ANTIPROTOZOAIRES",
            "ANTHELMINTHIQUES",
            "MEDICAMENTS CONTRE LES MYCOBACTERIES",
        },
    },

    # ── Stomatology ───────────────────────────────────────────────────────────
    "STOMATOLOGIE ET CHIRURGIE MAXILLO-FACIALE": {
        "classes":    set(),
        "subclasses": {
            "PREPARATIONS STOMATOLOGIQUES",
            "ANTIBACTERIENS (USAGE SYSTEMIQUE)",
            "ANALGESIQUES",
            "ANTIFONGIQUES A USAGE DERMATOLOGIQUE",
        },
    },

    # ── Anesthesia ────────────────────────────────────────────────────────────
    "ANESTHESIE & ANESTHESIE REANIMATION": {
        "classes":    set(),
        "subclasses": {
            "ANESTHESIQUES", "ANALGESIQUES", "MYORELAXANTS",
            "PSYCHOLEPTIQUES",
        },
    },

    # ── Physical therapy ──────────────────────────────────────────────────────
    "MEDECINE PHYSIQUE": {
        "classes":    {"MUSCLE ET SQUELETTE"},
        "subclasses": {
            "ANTI-INFLAMMATOIRES ET ANTIRHUMATISMAUX",
            "ANALGESIQUES", "MYORELAXANTS",
            "TOPIQUES POUR LES DOULEURS ARTICULAIRES ET MUSCULA",
        },
    },
    "PHYSIOTHERAPIE": {
        "classes":    {"MUSCLE ET SQUELETTE"},
        "subclasses": {
            "ANTI-INFLAMMATOIRES ET ANTIRHUMATISMAUX",
            "ANALGESIQUES", "MYORELAXANTS",
        },
    },
}


# ---------------------------------------------------------------------------
# Drug descriptions from CSV (loaded lazily)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _load_drug_descriptions() -> dict:
    """
    Load drug_descriptions.json if present.
    Generated by:  python3 referances/import_drug_descriptions.py your_drugs.csv
    Structure:  { "DRUG_NAME_UPPER": { "description": str, "indications": str,
                                        "specialty_tags": [str, ...] } }
    """
    path = os.path.join(REFERANCES_DIR, "drug_descriptions.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    logger.info("drug_descriptions_loaded", count=len(data))
    return data


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_specialty_coherence(
    medications: list[dict],
    specialty: Optional[str],
    doctor_name: Optional[str] = None,
) -> dict:
    """
    Check each drug against the doctor's specialty.

    Args:
        medications : list of medication dicts (must have amm_info.class / amm_info.subclass)
        specialty   : doctor specialty string from TAHA registry (or None)
        doctor_name : optional, for logging only

    Returns a dict:
    {
      "specialty_checked":        bool,
      "specialty_normalized":     str | None,
      "prescription_score":       float,   # 0.0–1.0 avg across all meds
      "any_mismatch":             bool,
      "medications_scored":       list[dict],  # same order as input, with added fields
    }
    Each medication dict gets:
      "specialty_match":       "confirmed" | "neutral" | "possible" | "mismatch"
      "specialty_match_score": float
      "specialty_note":        str
    """
    result: dict = {
        "specialty_checked":    bool(specialty),
        "specialty_normalized": None,
        "prescription_score":   1.0,
        "any_mismatch":         False,
        "medications_scored":   [],
    }

    if not specialty or not specialty.strip():
        # No specialty info — mark everything neutral
        for med in medications or []:
            med = dict(med)
            med["specialty_match"]       = "neutral"
            med["specialty_match_score"] = 0.6
            med["specialty_note"]        = "Doctor specialty unknown — cannot validate"
            result["medications_scored"].append(med)
        result["prescription_score"] = 0.6
        return result

    specialty_norm = _normalize_specialty(specialty)
    result["specialty_normalized"] = specialty_norm

    # Unrestricted specialties — no mismatch possible
    if specialty_norm in _UNRESTRICTED_SPECIALTIES:
        for med in medications or []:
            med = dict(med)
            med["specialty_match"]       = "neutral"
            med["specialty_match_score"] = 1.0
            med["specialty_note"]        = f"{specialty_norm} can prescribe any medication"
            result["medications_scored"].append(med)
        result["prescription_score"] = 1.0
        return result

    spec_map = _SPECIALTY_MAP.get(specialty_norm)
    drug_descriptions = _load_drug_descriptions()
    scores: list[float] = []

    for med in medications or []:
        med = dict(med)
        amm     = med.get("amm_info") or {}
        drug_cl = (amm.get("class") or "").upper().strip()
        drug_sc = (amm.get("subclass") or "").upper().strip()
        drug_nm = (med.get("drug_name_normalized") or med.get("drug_name") or "").upper().strip()

        # --- check universal drugs first ---
        if drug_sc in _UNIVERSAL_SUBCLASSES or drug_cl in _UNIVERSAL_CLASSES:
            med["specialty_match"]       = "confirmed"
            med["specialty_match_score"] = 1.0
            med["specialty_note"]        = f"Universal drug ({drug_sc or drug_cl}) — valid for all specialties"
            scores.append(1.0)
            result["medications_scored"].append(med)
            continue

        # --- check drug_descriptions CSV tags if available ---
        desc_entry = drug_descriptions.get(drug_nm, {})
        csv_tags   = [t.upper() for t in desc_entry.get("specialty_tags", [])]
        if csv_tags and specialty_norm in csv_tags:
            med["specialty_match"]       = "confirmed"
            med["specialty_match_score"] = 1.0
            med["specialty_note"]        = f"Drug indication matches {specialty_norm} (from drug descriptions)"
            scores.append(1.0)
            result["medications_scored"].append(med)
            continue

        # --- no specialty map for this specialty → neutral ---
        if not spec_map:
            med["specialty_match"]       = "neutral"
            med["specialty_match_score"] = 0.6
            med["specialty_note"]        = f"No specialty map for {specialty_norm}"
            scores.append(0.6)
            result["medications_scored"].append(med)
            continue

        # --- check against specialty map ---
        expected_classes    = spec_map.get("classes", set())
        expected_subclasses = spec_map.get("subclasses", set())

        if drug_cl in expected_classes or drug_sc in expected_subclasses:
            match, score, note = "confirmed", 1.0, (
                f"Drug class/subclass ({drug_sc or drug_cl}) matches {specialty_norm}"
            )
        elif _is_adjacent(drug_cl, drug_sc, specialty_norm):
            match, score, note = "possible", 0.4, (
                f"Drug ({drug_sc or drug_cl}) is plausible but not typical for {specialty_norm}"
            )
        else:
            match, score, note = "mismatch", 0.1, (
                f"Drug class '{drug_sc or drug_cl}' is unexpected for {specialty_norm} — verify"
            )
            result["any_mismatch"] = True

        med["specialty_match"]       = match
        med["specialty_match_score"] = score
        med["specialty_note"]        = note
        scores.append(score)
        result["medications_scored"].append(med)

        logger.debug(
            "specialty_check",
            drug=drug_nm,
            specialty=specialty_norm,
            match=match,
            score=score,
        )

    result["prescription_score"] = round(sum(scores) / len(scores), 4) if scores else 1.0

    if result["any_mismatch"]:
        logger.info(
            "specialty_mismatch_found",
            specialty=specialty_norm,
            doctor=doctor_name,
            mismatch_count=sum(1 for m in result["medications_scored"]
                               if m.get("specialty_match") == "mismatch"),
        )

    return result


def enrich_medications_with_specialty(
    medications: list[dict],
    specialty_result: dict,
) -> list[dict]:
    """
    Merge specialty scores back into the original medication dicts.
    (Call this after validate_specialty_coherence.)
    """
    scored = specialty_result.get("medications_scored", [])
    for i, med in enumerate(medications):
        if i < len(scored):
            med["specialty_match"]       = scored[i].get("specialty_match", "neutral")
            med["specialty_match_score"] = scored[i].get("specialty_match_score", 0.6)
            med["specialty_note"]        = scored[i].get("specialty_note", "")
    return medications


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_specialty(specialty: str) -> str:
    """Upper-case and strip for consistent lookup."""
    return specialty.strip().upper()


def _is_adjacent(drug_class: str, drug_subclass: str, specialty: str) -> bool:
    """
    Return True if the drug is plausibly (but not typically) prescribed by
    this specialty. Handles common cross-specialty patterns:
      - Any specialist can give corticosteroids, immunosuppressants
      - Surgeons prescribe analgesics, antibiotics (already in universal)
      - Oncologists prescribe almost everything
    """
    adjacent_always = {
        "IMMUNOSUPPRESSEURS", "IMMUNOSTIMULANTS",
        "CORTICOSTEROIDES (USAGE SYSTEMIQUE)",
        "ANTIANEMIANTS", "ANTIHEMORRAGIQUES",
        "AGENTS DIAGNOSTIQUES", "AGENTS REDUISANT LES LIPIDES SERIQUES",
    }
    if drug_subclass in adjacent_always:
        return True

    # Surgeons (any) can prescribe pain meds and peri-op drugs
    if "CHIRURGIE" in specialty:
        return drug_subclass in {
            "ANALGESIQUES", "ANESTHESIQUES", "MYORELAXANTS",
            "ANTIBACTERIENS (USAGE SYSTEMIQUE)",
        }

    return False
