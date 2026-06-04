# MediBox Pipeline Benchmark

Tracks Character Error Rate (CER) and end-to-end latency across pipeline changes.
CER = edit_distance(predicted_text, ground_truth_text) / len(ground_truth_text).

---

## Eval set

30 prescriptions covering the hardest cases:
- 8 × bad handwriting (cramped, overlapping strokes)
- 8 × Arabic-heavy (right-to-left dominant)
- 7 × French-heavy (printed + handwritten mix)
- 7 × faded ink / poor lighting / tilted paper

**YOU NEED TO LABEL THESE.** Run `python eval/label.py` to open each image and type
the ground-truth text for `extracted_raw_text`. Labels saved to `eval/labels/`.

Eval set lives at: `eval/images/` (not committed — add real prescription images here).
Label format: `eval/labels/{id}.txt` — verbatim text as a pharmacist would read it.

---

## How to run

```bash
cd medibox-cloud
pip install -r eval/requirements.txt
python eval/run_eval.py --images eval/images/ --labels eval/labels/ --out eval/results/
```

---

## Results

### Baseline (before any fixes)
| Metric | Value |
|--------|-------|
| CER (mean) | TBD — needs labeled eval set |
| CER (p90)  | TBD |
| E2E latency warm (p50) | ~25s |
| E2E latency cold (p50) | ~90s |
| Grid resolution per cell | 341×341 px |
| Capture resolution | 640×480 |

---

### Fix 1 — High-res capture (1920×1080, commit `TODO`)
*Run eval after labeling to fill in numbers.*

| Metric | Before | After | Delta |
|--------|--------|-------|-------|
| CER mean | TBD | TBD | TBD |
| CER p90  | TBD | TBD | TBD |
| Crop resolution (per strip) | ~640×213 px | ~1920×360 px | +3× linear |
| JPEG bytes per submission | ~24 KB | ~150 KB | +6× |

Expected: biggest single accuracy win. Each crop now has ~9× more pixels.

---

### Fix 2 — Grid vs individual strips (A/B test)
*Blocked on labeled eval set.*

Hypothesis: sending 3 separate images at native aspect ratio beats the 1024×1024
grid (which squashes strips into square cells).

| Mode | CER mean | Latency | Cost |
|------|----------|---------|------|
| Grid 1024×1024 (current) | TBD | TBD | 1 inference call |
| 3 individual images       | TBD | TBD | 1 inference call (multi-image) |

---

### Fix 4 — AWQ INT4 vs INT8
*Blocked on RunPod access + eval set.*

The 24GB GPU has headroom. INT8 uses ~14GB vs ~8GB for INT4.

| Quantization | CER mean | VRAM | Latency |
|--------------|----------|------|---------|
| INT4 AWQ (current) | TBD | ~8 GB | TBD |
| INT8 AWQ           | TBD | ~14 GB | TBD |

---

### Fix 6 — Preprocessing A/B test
*Blocked on labeled eval set.*

| Pipeline | CER mean | Notes |
|----------|----------|-------|
| CLAHE + sharpen + denoise greyscale (current) | TBD | |
| Raw color (no preprocessing)                  | TBD | |
| Binarization (Otsu) — tested and reverted     | worse | Off-distribution for Qwen VLM |

---

### Fix 7 — Specialty coherence as soft signal
| Metric | Before | After |
|--------|--------|-------|
| False `requires_review` rate (GPs) | TBD | Expected lower |
| Review flag precision | TBD | TBD |
| Review flag recall | TBD | TBD |

---

### Fix 8 — Confidence recalibration under guided_json
| Weight | Before | After |
|--------|--------|-------|
| logprob | 0.50 | 0.25 |
| formulary | 0.35 | 0.45 |
| completeness | 0.15 | 0.20 |
| specialty | 0.00 | 0.10 |

Calibration quality (requires labeled eval set):
- Review flag precision: TBD
- Review flag recall: TBD

---

## Known limitations

- **Single-process Railway SPOF**: The API and arq worker run in the same process.
  If the worker crashes, the API goes down too. Migration path: split `run_pipeline`
  into a dedicated Railway service using the same arq queue. Not in scope for this pass.

- **RunPod cold starts**: Serverless workers scale to 0 when idle. Cold start is
  60–120s. Warmup endpoint (`POST /v1/warmup`) is called on camera page mount to
  front-load this. A minimum 1-worker config costs ~$X/month (document after testing).

- **No offline fallback**: If Railway or RunPod is unreachable, the Pi cannot submit
  jobs. A local queue with retry is a future improvement.
