"""
import_drug_descriptions.py
============================
Reads referances/liste_amm.xls and generates referances/drug_descriptions.json.

Output structure:
{
  "DRUG_NAME_UPPER": {
    "trade_name": str,
    "dci": str,
    "class": str,
    "subclass": str,
    "indications": str,          # truncated to 500 chars
    "specialty_tags": [str, ...]  # inferred from class/subclass
  },
  ...
}

Both trade names (Nom) AND DCIs are indexed as keys so both amm_info.class
and drug_name_normalized lookups find a match.

Usage:
    python3 referances/import_drug_descriptions.py
    # or from project root:
    python3 referances/import_drug_descriptions.py referances/liste_amm.xls
"""

import json
import os
import sys

# ---------------------------------------------------------------------------
# Reverse-map: AMM class/subclass → list of TAHA specialties that use them
# ---------------------------------------------------------------------------
_CLASS_TO_SPECIALTIES: dict[str, list[str]] = {
    "ANTINEOPLASIQUES ET IMMUNOMODULATEURS": [
        "MEDECINE CARCINOLOGIQUE",
        "CHIRURGIE CARCINOLOGIQUE",
        "RADIOTHERAPIE",
        "HEMATOLOGIE CLINIQUE",
    ],
    "SYSTEME CARDIOVASCULAIRE": [
        "CARDIOLOGIE",
        "CHIRURGIE CARDIO-VASCULAIRE & PERIPHERIQUE",
        "NEPHROLOGIE",
    ],
    "SANG ET ORGANES HEMATOPOIETIQUES": [
        "CARDIOLOGIE",
        "HEMATOLOGIE CLINIQUE",
        "NEPHROLOGIE",
    ],
    "SYSTEME NERVEUX": [
        "NEUROLOGIE",
        "NEURO-CHIRURGIE",
        "PSYCHIATRIE",
        "PSYCHIATRIE INFANTILE",
        "ANESTHESIE & ANESTHESIE REANIMATION",
    ],
    "APPAREIL DIGESTIF ET METABOLISME": [
        "GASTRO-ENTEROLOGIE",
        "ENDOCRINOLOGIE",
        "NUTRITION",
    ],
    "HORMONES SYSTEMIQUES, HORMONES SEXUELLES EXCLUES": [
        "ENDOCRINOLOGIE",
        "NUTRITION",
        "GYNECOLOGIE-OBSTETRIQUE",
    ],
    "SYSTEME GENITO URINAIRE ET HORMONES SEXUELLES": [
        "GYNECOLOGIE-OBSTETRIQUE",
        "UROLOGIE",
    ],
    "SYSTEME RESPIRATOIRE": [
        "PNEUMO-PHTISIOLOGIE",
    ],
    "MUSCLE ET SQUELETTE": [
        "RHUMATOLOGIE",
        "CHIRURGIE ORTHOPEDIQUE",
        "MEDECINE PHYSIQUE",
        "PHYSIOTHERAPIE",
    ],
    "MEDICAMENTS DERMATOLOGIQUES": [
        "DERMATOLOGIE",
    ],
    "ORGANES SENSORIELS": [
        "OPHTALMOLOGIE",
        "O.R.L.",
    ],
    "ANTIINFECTIEUX GENERAUX A USAGE SYSTEMIQUE": [
        "MALADIES INFECTIEUSES",
        "PNEUMO-PHTISIOLOGIE",
        "MEDECINE INTERNE",
    ],
    "ANTIPARASITAIRES - INSECTICIDES": [
        "MALADIES INFECTIEUSES",
        "MEDECINE INTERNE",
    ],
}

_SUBCLASS_TO_SPECIALTIES: dict[str, list[str]] = {
    "ANTINEOPLASIQUES": [
        "MEDECINE CARCINOLOGIQUE", "CHIRURGIE CARCINOLOGIQUE",
        "RADIOTHERAPIE", "HEMATOLOGIE CLINIQUE",
    ],
    "IMMUNOSUPPRESSEURS": [
        "RHUMATOLOGIE", "NEPHROLOGIE", "MEDECINE CARCINOLOGIQUE",
        "HEMATOLOGIE CLINIQUE", "GASTRO-ENTEROLOGIE",
    ],
    "IMMUNOSTIMULANTS": [
        "MALADIES INFECTIEUSES", "MEDECINE CARCINOLOGIQUE",
    ],
    "THERAPIE CARDIAQUE": ["CARDIOLOGIE"],
    "ANTIHYPERTENSEURS": ["CARDIOLOGIE", "NEPHROLOGIE"],
    "DIURETIQUES": ["CARDIOLOGIE", "NEPHROLOGIE"],
    "AGENTS ANTITHROMBOTIQUES": ["CARDIOLOGIE", "HEMATOLOGIE CLINIQUE"],
    "AGENTS REDUISANT LES LIPIDES SERIQUES": ["CARDIOLOGIE", "ENDOCRINOLOGIE"],
    "AGENTS AGISSANT SUR LE SYSTEME RENINE-ANGIOTENSINE": ["CARDIOLOGIE", "NEPHROLOGIE"],
    "INHIBITEURS DES CANAUX DU CALCIUM": ["CARDIOLOGIE"],
    "AGENTS β-BLOQUANTS": ["CARDIOLOGIE"],
    "VASODILATATEURS PERIPHERIQUES": ["CARDIOLOGIE", "CHIRURGIE CARDIO-VASCULAIRE & PERIPHERIQUE"],
    "ANTIHEMORRAGIQUES": ["CARDIOLOGIE", "HEMATOLOGIE CLINIQUE"],
    "ANTIANEMIANTS": ["HEMATOLOGIE CLINIQUE", "NEPHROLOGIE"],
    "ANTI-EPILEPTIQUES": ["NEUROLOGIE", "NEURO-CHIRURGIE", "PSYCHIATRIE INFANTILE"],
    "ANTI-PARKINSONIENS": ["NEUROLOGIE"],
    "PSYCHOLEPTIQUES": ["PSYCHIATRIE", "PSYCHIATRIE INFANTILE"],
    "PSYCHOANALEPTIQUES": ["PSYCHIATRIE", "PSYCHIATRIE INFANTILE"],
    "MYORELAXANTS": ["NEUROLOGIE", "RHUMATOLOGIE", "CHIRURGIE ORTHOPEDIQUE",
                     "MEDECINE PHYSIQUE", "ANESTHESIE & ANESTHESIE REANIMATION"],
    "AUTRES MEDICAMENTS DU SYSTEME NERVEUX": ["NEUROLOGIE", "PSYCHIATRIE"],
    "MEDICAMENT UTILISE DANS LE TRAITEMENT DU DIABETE": ["ENDOCRINOLOGIE", "NUTRITION"],
    "HORMONES PANCREATIQUES": ["ENDOCRINOLOGIE", "NUTRITION"],
    "THERAPEUTIQUE DE LA THYROIDE": ["ENDOCRINOLOGIE"],
    "THERAPEUTIQUE ENDOCRINE": ["ENDOCRINOLOGIE"],
    "HORMONES HYPOPHYSAIRES, HYPOTHALAMIQUES ET ANALOGUES": ["ENDOCRINOLOGIE"],
    "MEDICAMENTS LIES A DES PROBLEMES D'ACIDITE": ["GASTRO-ENTEROLOGIE"],
    "ANTIDIARRHEIQUES, ANTI-INFLAMMATOIRES INTESTINAUX/AGENTS ANTI-INFECTIEUX": ["GASTRO-ENTEROLOGIE"],
    "DIGESTIFS, Y COMPRIS LES ENZYMES": ["GASTRO-ENTEROLOGIE"],
    "TRAITEMENT DE LA BILE ET DU FOIE": ["GASTRO-ENTEROLOGIE"],
    "MEDICAMENTS UTILISES EN CAS DE PROBLEMES FONCTIONNELS GASTRO-INTESTINAUX": ["GASTRO-ENTEROLOGIE"],
    "LAXATIFS": ["GASTRO-ENTEROLOGIE"],
    "HORMONES SEXUELLES ET MODULATEURS DU SYSTEME GENITAL": [
        "GYNECOLOGIE-OBSTETRIQUE", "UROLOGIE", "ENDOCRINOLOGIE",
    ],
    "ANTI-INFECTIEUX ET ANTISEPTIQUES GYNECOLOGIQUES": ["GYNECOLOGIE-OBSTETRIQUE"],
    "AUTRES MEDICAMENTS GYNECOLOGIQUES": ["GYNECOLOGIE-OBSTETRIQUE"],
    "MEDICAMENTS UROLOGIQUES": ["UROLOGIE", "NEPHROLOGIE"],
    "MEDICAMENTS DES MALADIES RESPIRATOIRES OBSTRUCTIVE": ["PNEUMO-PHTISIOLOGIE"],
    "MEDICAMENTS DE LA TOUX ET DU RHUME": ["PNEUMO-PHTISIOLOGIE"],
    "AUTRES PRODUITS EN RELATION AVEC LE SYSTEME RESPIRATOIRE": ["PNEUMO-PHTISIOLOGIE"],
    "MEDICAMENTS CONTRE LES MYCOBACTERIES": ["PNEUMO-PHTISIOLOGIE", "MALADIES INFECTIEUSES"],
    "ANTI-INFLAMMATOIRES ET ANTIRHUMATISMAUX": [
        "RHUMATOLOGIE", "CHIRURGIE ORTHOPEDIQUE", "MEDECINE PHYSIQUE",
    ],
    "ANTIGOUTTEUX": ["RHUMATOLOGIE"],
    "MEDICAMENTS POUR LES MALADIES DES OS": ["RHUMATOLOGIE", "CHIRURGIE ORTHOPEDIQUE"],
    "TOPIQUES POUR LES DOULEURS ARTICULAIRES ET MUSCULA": ["RHUMATOLOGIE", "MEDECINE PHYSIQUE"],
    "PREPARATIONS DERMATOLOGIQUES DE CORTICOSTEROIDES": ["DERMATOLOGIE"],
    "ANTIFONGIQUES A USAGE DERMATOLOGIQUE": ["DERMATOLOGIE"],
    "ANTIBIOTIQUES ET CHIMIOTHERAPIE A USAGE DERMATOLOGIQUE": ["DERMATOLOGIE"],
    "AUTRES PREPARATIONS DERMATOLOGIQUES": ["DERMATOLOGIE"],
    "ANTI-PSORIASIS": ["DERMATOLOGIE"],
    "PREPARATIONS ANTI-ACNEIQUES": ["DERMATOLOGIE"],
    "EMOLLIENTS ET PROTECTEURS": ["DERMATOLOGIE"],
    "PREPARATIONS POUR LE TRAITEMENT DES PLAIES ET DES ULCERATIONS": ["DERMATOLOGIE"],
    "ANTIPRURIGINEUX, Y COMPRIS ANTIHISTAMINIQUES ET AN": ["DERMATOLOGIE"],
    "ECTOPARASITICIDES, Y COMPRIS LES SCABICIDES, INSECTICIDES ET REPULSIFS": ["DERMATOLOGIE"],
    "MEDICAMENTS OPHTALMOLOGIQUES": ["OPHTALMOLOGIE"],
    "MEDICAMENTS OTOLOGIQUES": ["O.R.L."],
    "MEDICAMENTS POUR LE NEZ": ["O.R.L."],
    "MEDICAMENTS POUR LA GORGE": ["O.R.L."],
    "ANTIBACTERIENS (USAGE SYSTEMIQUE)": [
        "MALADIES INFECTIEUSES", "PNEUMO-PHTISIOLOGIE", "MEDECINE INTERNE",
        "O.R.L.", "STOMATOLOGIE ET CHIRURGIE MAXILLO-FACIALE",
    ],
    "ANTIVIRAUX (USAGE SYSTEMIQUE)": ["MALADIES INFECTIEUSES", "MEDECINE INTERNE"],
    "ANTIMYCOTIQUES (USAGE SYSTEMIQUE)": ["MALADIES INFECTIEUSES", "DERMATOLOGIE"],
    "ANTIPROTOZOAIRES": ["MALADIES INFECTIEUSES"],
    "ANTHELMINTHIQUES": ["MALADIES INFECTIEUSES"],
    "PREPARATIONS STOMATOLOGIQUES": ["STOMATOLOGIE ET CHIRURGIE MAXILLO-FACIALE"],
    "ANESTHESIQUES": ["ANESTHESIE & ANESTHESIE REANIMATION", "CHIRURGIE GENERALE"],
    "ANALGESIQUES": [
        "ANESTHESIE & ANESTHESIE REANIMATION", "NEUROLOGIE",
        "RHUMATOLOGIE", "CHIRURGIE ORTHOPEDIQUE", "MEDECINE PHYSIQUE",
        "STOMATOLOGIE ET CHIRURGIE MAXILLO-FACIALE",
    ],
}


def _infer_specialty_tags(drug_class: str, drug_subclass: str) -> list[str]:
    """Infer specialty tags from drug class and subclass."""
    tags: set[str] = set()
    cl = (drug_class or "").upper().strip()
    sc = (drug_subclass or "").upper().strip()
    tags.update(_CLASS_TO_SPECIALTIES.get(cl, []))
    tags.update(_SUBCLASS_TO_SPECIALTIES.get(sc, []))
    return sorted(tags)


def build_drug_descriptions(xls_path: str) -> dict:
    """Read liste_amm.xls and return the drug_descriptions dict."""
    try:
        import xlrd
    except ImportError:
        print("ERROR: xlrd not installed. Run: pip install xlrd")
        sys.exit(1)

    wb = xlrd.open_workbook(xls_path, encoding_override="cp1252")
    ws = wb.sheet_by_index(0)

    # Column indices from header row:
    # 0: Nom  4: DCI  5: Classe  6: Sous Classe  14: Indications
    descriptions: dict = {}
    seen_dcis: dict = {}  # DCI → aggregated indications

    for row_idx in range(1, ws.nrows):
        row = ws.row_values(row_idx)
        trade_name = str(row[0]).strip()
        dci        = str(row[4]).strip()
        drug_class = str(row[5]).strip()
        drug_sub   = str(row[6]).strip()
        indications_raw = str(row[14]).strip() if row[14] else ""

        if not trade_name or trade_name == "None":
            continue

        # Truncate indications at 500 chars
        indications = indications_raw[:500] if indications_raw else ""
        # Normalize line endings
        indications = indications.replace("\r\n", " ").replace("\r", " ")

        specialty_tags = _infer_specialty_tags(drug_class, drug_sub)

        entry = {
            "trade_name":    trade_name,
            "dci":           dci,
            "class":         drug_class,
            "subclass":      drug_sub,
            "indications":   indications,
            "specialty_tags": specialty_tags,
        }

        # Index by trade name (UPPER)
        descriptions[trade_name.upper()] = entry

        # Also index by DCI (UPPER) — use first encountered indications for DCI
        if dci and dci.upper() not in seen_dcis:
            seen_dcis[dci.upper()] = entry
            descriptions[dci.upper()] = {
                **entry,
                "trade_name": dci,  # DCI entry uses DCI as trade_name
            }
        elif dci and dci.upper() in seen_dcis:
            # Append additional indications if not duplicate
            existing = descriptions[dci.upper()]
            if indications and indications not in (existing.get("indications") or ""):
                combined = ((existing["indications"] or "") + " | " + indications)[:500]
                existing["indications"] = combined

    return descriptions


def main():
    root = os.path.dirname(os.path.abspath(__file__))
    xls_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(root, "liste_amm.xls")
    out_path  = os.path.join(root, "drug_descriptions.json")

    if not os.path.exists(xls_path):
        print(f"ERROR: XLS not found at {xls_path}")
        sys.exit(1)

    print(f"Reading {xls_path} ...")
    descriptions = build_drug_descriptions(xls_path)

    print(f"Writing {out_path} ({len(descriptions)} entries) ...")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(descriptions, f, ensure_ascii=False, indent=2)

    print(f"Done. {len(descriptions)} drug/DCI entries written to {out_path}")


if __name__ == "__main__":
    main()
