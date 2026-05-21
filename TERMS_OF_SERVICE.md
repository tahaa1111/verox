# Terms of Service — Medibox

**Last updated:** 2026-05-20

---

## 1. Clinical Decision Support — Not Autonomous Dispensing

Medibox is a **clinical decision-support tool**. It provides AI-assisted extraction of data from handwritten prescriptions to assist licensed pharmacists. Medibox does **not**:

- Authorize or approve any drug dispensing
- Replace the professional judgment of a licensed pharmacist
- Guarantee the accuracy, completeness, or correctness of any extraction

**Every Medibox output must be reviewed and verified by a licensed pharmacist before any clinical action is taken.**

All API responses include the mandatory disclaimer:
> "Pharmacist verification required. Medibox assists, it does not dispense."

---

## 2. Authorized Users

Medibox is intended for use by:
- Licensed pharmacists registered with the **Ordre National des Pharmaciens de Tunisie**
- Pharmacy staff operating under the direct supervision of a licensed pharmacist

Use by unlicensed individuals for clinical purposes is prohibited.

---

## 3. Data and Privacy

Medibox processes personal health data (patient names, doctor names, prescription contents) subject to **Tunisian Loi n°2004-63** on the protection of personal data.

- Patient and doctor names are encrypted at rest
- Prescription images are stored in Google Cloud Storage with 90-day retention
- Data is not shared with third parties except as required to operate the service (Google Cloud Platform)
- Users may request deletion of their data by contacting the data controller

---

## 4. Limitation of Liability

To the maximum extent permitted by Tunisian law:

- Medibox and its operators accept **no liability** for clinical outcomes resulting from reliance on AI-generated prescription extractions
- The service is provided "as is" without warranty of accuracy or fitness for any particular clinical purpose
- Pharmacists remain solely responsible for verifying all prescriptions before dispensing

---

## 5. Prohibited Uses

Users must not:
- Use Medibox to dispense medications without pharmacist verification
- Attempt to bypass the pharmacist review requirement
- Submit prescription images of individuals not under their care
- Use Medibox for any purpose other than pharmacy operations in Tunisia
- Attempt to reverse-engineer, scrape, or abuse the API

---

## 6. Service Availability

Medibox is provided on a best-effort basis. Planned maintenance will be communicated in advance via the maintenance mode banner. The operator makes no uptime guarantee.

---

## 7. Governing Law

These terms are governed by the laws of the **Republic of Tunisia**.
