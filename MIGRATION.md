# Medibox — Migration Notes

## Redis command-churn fix (Phase 1)

### Problem
Upstash free tier: **500 K commands/month**.

| Source | Rate (24/7) | Commands/month |
|---|---|---|
| arq `poll_delay` default 0.5 s | 2/s | ~5.18 M — 10× budget |
| Pi → `GET /camera/command` poll (2 s) | 0.5/s | ~1.3 M |
| `/readyz` probe (every 10 s) | 0.1/s | ~260 K |

### Fixes applied

**arq poll_delay** (`ARQ_POLL_DELAY` env var, default `2.0`):
- The arq worker's inner loop calls `ZRANGEBYSCORE arq:queue` every `poll_delay` seconds
  to check for pending jobs. The arq default is 0.5 s.
- At `ARQ_POLL_DELAY=2.0`: ~1.3 M/month. Still over free-tier if the service runs
  24/7 but acceptable during clinic-hours-only operation (see scale-to-zero below).
- All retry / backoff / dead-letter / idempotency logic in `tasks.py` is unchanged.

**Camera commands over WebSocket** (Phase 1b):
- Old path: Pi polls `GET /v1/camera/command` every 2 s → Redis `GETDEL camera:cmd:*`.
- New path: Pi holds a persistent WebSocket to `/v1/camera/ws/push/{device_id}`.
  Browser sends `{"type":"start"}` / `{"type":"stop"}` over the feed WS;
  the relay manager (`ws/camera_relay.py`) forwards them to the Pi's WS connection.
  Zero Redis commands for camera signaling.
- Legacy `GET /camera/command` endpoint kept for backward compat but is now a no-op
  path — the Pi patch (`/tmp/patch_ws.py`) replaces HTTP polling with WS.
- Legacy `POST /camera/push` and Redis keys `camera:latest:*`, `camera:stable:*`,
  `camera:cmd:*` are no longer written by the new Pi code. The snapshot fallback
  (`GET /camera/snapshot`) remains for debugging but returns 404 when WS is active.

**`/readyz` caching**:
- Result cached for 10 seconds. A probe at 30 s interval saves 2 Redis GETs per probe.
- Maintenance-mode changes take up to 10 s to propagate — acceptable.

### New env var
```
ARQ_POLL_DELAY=2.0   # seconds; floor 0.5 (arq minimum)
```

---

## Scale-to-zero / clinic-hours operation (Phase 2)

### What keeps the service running 24/7
1. The arq worker runs as a background asyncio task inside the FastAPI process.
   It polls Redis continuously while the process is alive.
2. The Pi's camera WebSocket (`cloud_ws_loop`) maintains a persistent connection,
   preventing Railway/Render from spinning the container down.
3. Any uptime monitors or Grafana Cloud uptime checks will also keep the process alive.

### Tradeoff: warmup vs. idle
- **If the service spins down** (no connections for ≥ 15 min on Render Hobby):
  first request pays a cold-start cost (Render container restart ~5–10 s).
- **Warmup on clinic open**: `POST /v1/warmup` (auth required, rate-limited to 1/user/5 min)
  enqueues a no-op job so RunPod loads model weights before the first real scan.
  The Pi or frontend should call this when the clinician opens the app.
- **RunPod separately spins down** when its serverless worker has no jobs.
  The warmup endpoint triggers a minimal inference to keep it warm.

### Upstash budget alert
Set an Upstash budget alert at 400 K commands/month (80 % of free tier) so you
see churn regressions before hitting the limit.

---

## OTel push monitoring (Phase 3)

### Decision: push via OTLP, not scrape via prometheus_client

| | Old (prometheus_client scrape) | New (OTel OTLP push) |
|---|---|---|
| Transport | Prometheus scrapes `/metrics` | Process pushes to Grafana Cloud |
| Spin-down compatible | No — scraper either wakes the box or misses data | Yes — push happens while process is alive |
| Single pipeline | No — separate trace + metric exporters | Yes — one OTLP endpoint for both |
| Grafana Cloud storage | Prometheus-compatible | Prometheus-compatible (unchanged) |
| PromQL / alerts | Work | Work (unchanged) |

**What was removed**: `prometheus-client` library, `/metrics` scrape endpoint.  
**What was added**: `opentelemetry-exporter-otlp-proto-http`, `opentelemetry-instrumentation-httpx`.

### New env vars (set in Railway dashboard, never in repo)
```
GRAFANA_CLOUD_OTLP_ENDPOINT=https://otlp-gateway-prod-<region>.grafana.net/otlp
GRAFANA_CLOUD_INSTANCE_ID=<numeric-stack-id>
GRAFANA_CLOUD_TOKEN=<token-with-traces:write-metrics:write>
```

### How to get the values
1. Grafana Cloud → Connections → Add connection → OpenTelemetry (OTLP).
2. Follow the wizard — it shows the endpoint URL and lets you generate a token.
3. The Instance ID (numeric) is shown on the OTLP configuration page.

### PII in spans
`PiiScrubSpanProcessor` (in `telemetry.py`) redacts the following span attribute keys
before export: `patient_name`, `doctor_name`, `patient.name`, `doctor.name`,
`patient.last_name`, `patient.address`, `token`, `authorization`, `x-camera-secret`,
`password`, `image_base64`, `frame`, `raw_output`, `db.statement`.

Audit / security events are written to the `audit_log` DB table (durable) — they do
**not** ride on best-effort metrics or traces.

### Metrics pushed (subset)
| Name | Type | Description |
|---|---|---|
| `http_requests_total` | Counter | By method, endpoint, status_code |
| `http_request_duration_seconds` | Histogram | Request latency |
| `ocr_jobs_total` | Counter | By outcome (queued/completed/failed/cancelled) |
| `ocr_processing_seconds` | Histogram | End-to-end pipeline duration |
| `ocr_inference_latency_seconds` | Histogram | RunPod inference per grid |
| `ocr_queue_depth` | UpDownCounter | Pending arq jobs |
| `runpod_cold_starts_total` | Counter | Cold-start events |
| `pipeline_retries_total` | Counter | arq task retries |
| `redis_commands_total` | Counter | Approximate Redis command rate |
| `auth_failures_total` | Counter | Auth failures by reason |
| `rate_limit_hits_total` | Counter | Rate limit breaches by layer |

---

## Removed env vars

| Var | Reason |
|---|---|
| `CELERY_BROKER_URL` | Celery replaced by arq |
| `CELERY_RESULT_BACKEND` | Same |
| `ADMIN_BOOTSTRAP_UID` | One-time bootstrap complete; unset after first deploy |
| `METRICS_SECRET` | `/metrics` scrape endpoint removed |
