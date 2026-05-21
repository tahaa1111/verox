"""Unit tests for training evaluation metrics."""

import pytest
from src.training.evaluate import field_f1, drug_class_accuracy, json_validity_rate


class TestFieldF1:
    def test_perfect_match(self):
        preds = [{"drug_name": "paracetamol"}]
        refs = [{"drug_name": "paracetamol"}]
        assert field_f1(preds, refs, "drug_name") == pytest.approx(1.0)

    def test_zero_match(self):
        preds = [{"drug_name": "aspirine"}]
        refs = [{"drug_name": "paracetamol"}]
        assert field_f1(preds, refs, "drug_name") == pytest.approx(0.0)

    def test_partial_match(self):
        preds = [{"drug_name": "amoxicilline 500mg"}]
        refs = [{"drug_name": "amoxicilline tablet"}]
        score = field_f1(preds, refs, "drug_name")
        assert 0.0 < score < 1.0

    def test_both_empty_counts_as_perfect(self):
        preds = [{"drug_name": ""}]
        refs = [{"drug_name": ""}]
        assert field_f1(preds, refs, "drug_name") == pytest.approx(1.0)

    def test_empty_predictions_list(self):
        assert field_f1([], [], "drug_name") == pytest.approx(0.0)

    def test_missing_field_treated_as_empty(self):
        preds = [{}]
        refs = [{}]
        assert field_f1(preds, refs, "drug_name") == pytest.approx(1.0)


class TestDrugClassAccuracy:
    def test_correct_prediction_gives_1(self):
        predictions = [{"medications": [{"drug_name_normalized": "amoxicilline"}]}]
        references = [{"medications": [{"drug_name_normalized": "amoxicilline", "drug_class": "antibiotic"}]}]
        acc = drug_class_accuracy(predictions, references)
        assert "antibiotic" in acc
        assert acc["antibiotic"] == pytest.approx(1.0)

    def test_wrong_prediction_gives_0(self):
        predictions = [{"medications": [{"drug_name_normalized": "paracetamol"}]}]
        references = [{"medications": [{"drug_name_normalized": "amoxicilline", "drug_class": "antibiotic"}]}]
        acc = drug_class_accuracy(predictions, references)
        assert acc["antibiotic"] == pytest.approx(0.0)

    def test_empty_medications(self):
        predictions = [{"medications": []}]
        references = [{"medications": []}]
        acc = drug_class_accuracy(predictions, references)
        assert acc == {}


class TestJsonValidityRate:
    def test_all_valid(self):
        outputs = ['{"a": 1}', '{"b": 2}', '[]']
        assert json_validity_rate(outputs) == pytest.approx(1.0)

    def test_all_invalid(self):
        outputs = ["not json", "{{{{", ""]
        rate = json_validity_rate(outputs)
        assert rate < 1.0

    def test_empty_list(self):
        assert json_validity_rate([]) == pytest.approx(0.0)

    def test_repairable_json_counts_as_valid(self):
        outputs = ['{"a": 1,}']  # trailing comma — json_repair fixes this
        rate = json_validity_rate(outputs)
        assert rate == pytest.approx(1.0)
