"""Unit tests for postprocessing — JSON repair, confidence calibration, date normalization."""

from services.worker.tasks.postprocessing import (
    repair_and_parse_json,
    calibrate_confidence,
    normalize_date,
)


class TestRepairAndParseJson:
    def test_valid_json_passes_through(self):
        raw = '{"drug_name": "aspirine", "dosage": "500mg"}'
        result = repair_and_parse_json(raw)
        assert result["drug_name"] == "aspirine"

    def test_trailing_comma_repaired(self):
        raw = '{"drug_name": "aspirine", "dosage": "500mg",}'
        result = repair_and_parse_json(raw)
        assert result is not None
        assert "drug_name" in result

    def test_missing_quote_repaired(self):
        raw = '{drug_name: "aspirine"}'
        result = repair_and_parse_json(raw)
        assert result is not None

    def test_empty_string_returns_empty_dict(self):
        result = repair_and_parse_json("")
        assert result == {} or result is None

    def test_completely_invalid_returns_none_or_empty(self):
        result = repair_and_parse_json("not json at all £££")
        assert result is None or result == {}

    def test_nested_medications_list_preserved(self):
        raw = '{"medications": [{"drug_name": "amoxicilline", "dosage": "500mg"}]}'
        result = repair_and_parse_json(raw)
        assert len(result["medications"]) == 1


class TestCalibrateConfidence:
    def test_high_logprob_gives_high_confidence(self):
        score = calibrate_confidence(logprob_conf=0.95, formulary_score=0.9, completeness=1.0)
        assert score >= 0.85

    def test_low_logprob_gives_low_confidence(self):
        score = calibrate_confidence(logprob_conf=0.1, formulary_score=0.1, completeness=0.2)
        assert score < 0.5

    def test_score_is_clamped_0_to_1(self):
        score = calibrate_confidence(logprob_conf=1.5, formulary_score=1.5, completeness=2.0)
        assert 0.0 <= score <= 1.0

    def test_weights_sum_correctly(self):
        # 0.50*logprob + 0.35*formulary + 0.15*completeness
        score = calibrate_confidence(logprob_conf=1.0, formulary_score=1.0, completeness=1.0)
        assert abs(score - 1.0) < 0.001


class TestNormalizeDate:
    def test_iso_date_passes_through(self):
        assert normalize_date("2024-03-15") == "2024-03-15"

    def test_french_format_normalized(self):
        result = normalize_date("15/03/2024")
        assert "2024" in result

    def test_arabic_numerals_handled(self):
        # Should not crash
        result = normalize_date("١٥/٠٣/٢٠٢٤")
        assert result is not None

    def test_none_returns_none(self):
        assert normalize_date(None) is None  # type: ignore[arg-type]

    def test_empty_string_returns_none(self):
        result = normalize_date("")
        assert result is None or result == ""
