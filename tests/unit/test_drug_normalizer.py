"""Unit tests for drug normalizer — RapidFuzz WRatio against real reference files."""

from services.worker.utils.drug_normalizer import normalize_drug_name, load_drug_dict


class TestLoadDrugDict:
    def test_dict_loads_without_error(self):
        d = load_drug_dict()
        assert isinstance(d, (list, dict))
        assert len(d) > 0

    def test_dict_contains_known_tunisian_drug(self):
        d = load_drug_dict()
        names = d if isinstance(d, list) else list(d.keys())
        lowered = [n.lower() for n in names]
        # At least one common drug should be present
        assert any("amoxicillin" in n or "paracetamol" in n or "metformin" in n for n in lowered)


class TestNormalizeDrugName:
    """normalize_drug_name returns (inn, score_0_to_1, metadata_dict)."""

    def test_exact_match(self):
        inn, score, meta = normalize_drug_name("paracetamol")
        assert inn is not None
        assert "paracetamol" in inn.lower()
        assert score > 0.0

    def test_fuzzy_match_with_typo(self):
        # "paracetamoll" (double l) should still fuzzy-match
        inn, score, meta = normalize_drug_name("paracetamoll")
        assert inn is not None
        assert score > 0.0

    def test_unrecognized_drug_returns_low_score(self):
        # No formulary match → score == 0.0 (raw name returned, not None)
        inn, score, meta = normalize_drug_name("xyzunknowndrug99999")
        assert score == 0.0

    def test_arabic_or_french_variant(self):
        # Case-insensitive match
        inn, score, meta = normalize_drug_name("AMOXICILLINE")
        assert inn is not None
        assert score > 0.0

    def test_empty_string_returns_none_inn(self):
        inn, score, meta = normalize_drug_name("")
        assert inn is None

    def test_none_input_returns_none_inn(self):
        inn, score, meta = normalize_drug_name(None)  # type: ignore[arg-type]
        assert inn is None
