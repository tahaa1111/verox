# Liability Statement — Medibox

## AI-Assisted Clinical Tool

Medibox uses artificial intelligence to extract structured data from handwritten Tunisian prescriptions. AI models can make errors, including:

- Misreading drug names, especially handwritten Arabic or French text
- Incorrect dosage extraction
- Hallucinated drugs not present in the original prescription
- Missing medications due to image quality or handwriting clarity

**The AI system is explicitly designed to assist, not replace, pharmacist expertise.**

## Mandatory Pharmacist Verification

Before dispensing any medication based on a Medibox extraction:

1. The licensed pharmacist must visually compare the extracted data against the original prescription
2. The pharmacist must verify drug names, dosages, and frequencies independently
3. The pharmacist must apply their professional judgment regarding drug interactions, patient-specific contraindications, and prescribing physician intent

The mandatory disclaimer displayed on every Medibox output is:

> **"Pharmacist verification required. Medibox assists, it does not dispense."**

## Dispensing Errors

In the event of a dispensing error, the **licensed pharmacist** bears professional and legal responsibility under:

- **Code de Déontologie des Pharmaciens** (Tunisian Pharmacist Code of Ethics)
- **Loi n°92-73 du 3 août 1992** (Tunisian pharmaceutical law)
- General professional liability under Tunisian law

Medibox operators accept no liability for clinical harm arising from dispensing errors, whether or not Medibox was used in the workflow.

## Accuracy Targets (Not Guarantees)

The system targets:
- Drug name extraction F1 ≥ 0.80 (eval threshold)
- JSON validity rate ≥ 99.5%

These are development targets measured on held-out evaluation data. Real-world accuracy may vary based on prescription image quality, handwriting style, and drug types not well-represented in training data.

**No accuracy guarantee is made for clinical use.**

## Rare and High-Risk Drugs

The system includes special attention to rare Tunisian drugs (clomipramine, phenobarbital, levothyroxine, metformine). However, for high-risk or narrow therapeutic index drugs, pharmacists should apply heightened scrutiny beyond the AI extraction.

## Data Retention and Audit

Prescription data is retained according to the terms of service. Pharmacist corrections submitted via the system are used to improve model accuracy in future retraining cycles. All corrections are associated with the submitting pharmacist's account for audit purposes.
