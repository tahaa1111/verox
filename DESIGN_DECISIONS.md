# Medibox — Design Decisions (GCP/Vertex AI Edition)

This document is written **before any code** and records every non-obvious architectural
decision, conflict between spec requirements, and places where a spec requirement is
technically impossible. Future changes must be captured here.

---

## DD-001: Vertex AI Endpoint Cannot Scale to Zero

**Conflict:** §2 states a cost ceiling of ≤ $310/month at zero traffic. §4.2 states
"Vertex Endpoints do NOT scale to zero."

**Reality:** A single NVIDIA T4 GPU replica on Vertex AI Endpoint costs approximately
$0.35/hour (n1-standard-4 + T4). At min-replica-count=1, this is:
- **$252/month** baseline even at zero inference traffic.
- Adding Cloud SQL, Memorystore, Cloud Run workers (min-instances=1): ~$55-60/month.
- **Total idle cost: ~$307-312/month** — barely at or just over the $310 target.

**Resolution:**
1. Set `min-replica-count=1` as spec requires.
2. Provide `scripts/pause_endpoint.sh` (undeploy the model, saving ~$252/month) for
   true off-hours savings. Resume takes ~5 min.
3. `COSTS.md` documents this with honest numbers.
4. Alert when monthly projection exceeds $400 (buffer above $310 ceiling).

**Scale-to-zero is not technically possible on Vertex AI Endpoints.** Canary traffic
splitting is the trade-off that eliminates the weekend maintenance window from the
on-prem variant.

---

## DD-002: Worker on Cloud Run Service, Not Cloud Run Jobs

**Decision:** Use Cloud Run **service** with `min-instances=1` for the Celery worker.

**Why not Cloud Run Jobs:** Jobs are for finite, bounded workloads. Celery workers are
long-running processes that poll Redis. A Cloud Run Job would terminate after task
completion and would not re-poll the queue.

**Trade-off:** `min-instances=1` means one worker is always warm (~$10-20/month). This
keeps the Redis connection warm and eliminates queue-consumer cold-start latency.

---

## DD-003: Memorystore Basic Tier (No HA)

**Decision:** Use Memorystore Basic tier, not Standard (HA) tier.

**Why:** Standard tier costs ~2× more ($60/month vs $30/month for 1GB). For MVP
scale (10-20 concurrent devices), a single-zone Redis failure is acceptable — Celery
retries in-flight jobs. Documented in `RISKS.md`.

**Upgrade path:** `gcloud redis instances upgrade` to Standard tier, zero code changes.

---

## DD-004: Cloud SQL Auth Proxy for Database Connectivity

**Decision:** Cloud Run services connect via the built-in Auth Proxy sidecar
(`--add-cloudsql-instances`), not by exposing Cloud SQL on a public IP.

**Why:** Simpler for Cloud Run than private IP + VPC connector. The VPC connector exists
for Memorystore; the Auth Proxy handles Postgres over IAM-authenticated TLS on top of it.

---

## DD-005: Grid Composition Remains Worker-Side

**Decision:** Grid stitching (3×3, 1536×1536) is done in the Cloud Run worker, not inside
the Vertex predict container.

**Why:** Worker already decompresses and validates crops. Stitching produces ~300 KB JPEG
vs 18 MB of raw crops — keeps the Vertex predict payload small. Clean error isolation.

---

## DD-006: KFP v2 (SDK 2.x) with Vertex AI Pipelines

**Decision:** Use `kfp` SDK version ≥ 2.0 for all pipeline components.

**Why:** KFP v1 is end-of-life. Vertex AI Pipelines supports both but v2 is current.
Components use `@component` decorator; compiler produces a YAML artifact uploaded to GCS.

---

## DD-007: Firebase JWT Verification (not Google OAuth/OIDC)

**Decision:** API verifies Firebase JWTs via Firebase Admin SDK.

**Why:** The edge device sends Firebase JWTs (§1.3: out of scope to change). Admin second
factor: `admin_role_grants` Postgres table provides an independent control beyond the
`admin: true` JWT claim (prevents a compromised Firebase console from granting API access).

---

## DD-008: PII Encryption — pgcrypto + Cloud KMS (Envelope Encryption)

**Decision:** Patient name and doctor name encrypted at column level via pgcrypto. Key
wrapping via Cloud KMS (KEK wraps a per-job DEK stored encrypted in the DB).

**Honest trade-off:** Adds ~50ms KMS round-trip per decrypt. Negligible at MVP volume.
Cloud KMS audits every decrypt operation via Cloud Audit Logs — stronger than storing
a Fernet key in Secret Manager and loading it into memory.

---

## DD-009: Drug Formulary Reference Data — Real JSON Files

**Conflict:** Spec (§5.4) references `data/tunisian_drug_formulary.csv` — this file does
not exist. User explicitly instructed: **"use the referances [sic] instead."**

**Decision:** `drug_normalizer.py` reads from:
- `referances/drug_dict.json` — trade name → INN mapping
- `referances/drug_registry.json` — registry metadata (drug class, form, strength)

These JSON files are baked into the worker container image at build time (preferred over
GCS FUSE for simplicity and performance — files are ~few MB, static between retrains).

---

## DD-010: Edge Authentication — WIF Primary, Key Fallback

**Decision:** WIF is the primary auth mechanism. JSON key files generated only if WIF
setup fails or `--use-keyfile` flag is passed.

**Correction to spec §10:** The edge device calls Cloud Run (`roles/run.invoker`), not
Vertex AI directly. The spec incorrectly listed `roles/aiplatform.user` for `medibox-edge`.

---

## DD-011: Vertex Predict Contract + OpenAI API Both Exposed

**Decision:** The vLLM container exposes BOTH the Vertex predict route (`POST /predict`)
and the OpenAI-compatible route (`POST /v1/chat/completions`).

**Why:** Worker uses the google-cloud-aiplatform client for production. Local development
can point at a local vLLM instance using the OpenAI client path. Both routes are tested.

---

## DD-012: Cloud Run WebSocket Idle Timeout

**Decision:** Cloud Run enforces a 60-minute WebSocket idle timeout. 15s heartbeats
(spec §3.3) keep connections alive. The Pi MUST function correctly with polling alone —
WebSocket is an optimization.

---

## DD-013: `admin_role_grants` as Second Auth Factor

**Decision:** Admin endpoints require (a) `admin: true` Firebase JWT claim AND (b) a
non-revoked row in `admin_role_grants` Postgres table. Both must pass.

**Why:** A compromised Firebase console could inject a claim. The DB table requires
independent access to the Postgres instance to grant admin. Revoking requires both
a Firebase claim update AND a DB row update.

---

## DD-014: Cloud Error Reporting Replaces Sentry

**Decision:** Cloud Error Reporting auto-captures tracebacks from Cloud Run structured
logs. Zero additional cost. Sentry is excluded to keep costs bounded.

---

## DD-015: BigQuery Streaming Inserts for Analytics

**Decision:** `requests` and `feedback` tables use streaming inserts (near-real-time),
not batch loads (15-min delay). At MVP volumes (<100k rows/month), streaming insert
cost is negligible (<$0.01/month).
