# =============================================================================
# RunPod Serverless — Qwen2.5-VL-7B-Instruct-AWQ inference endpoint
#
# Required env vars (export before terraform apply):
#   export TF_VAR_runpod_api_key=rpa_xxxxxxxxxxxx
#   export TF_VAR_runpod_template_id=<template-id-from-runpod-dashboard>
#
# Optional — persistent model weights (faster cold starts, no re-download):
#   export TF_VAR_runpod_network_volume_id=vol_xxxxxxxxxxxx
# =============================================================================

resource "runpod_endpoint" "qwen" {
  name = "medibox-qwen-vl"

  # Template ID from RunPod console: Serverless → Manage → Templates
  # Use the official "vLLM" serverless template or your own saved template.
  template_id = var.runpod_template_id

  # RTX 3090 (24GB VRAM) — fits Qwen2.5-VL-7B AWQ with 8192 context + KV cache.
  # Fallback to RTX 4090 if 3090 unavailable.
  gpu_ids = ["NVIDIA GeForce RTX 3090", "NVIDIA GeForce RTX 4090"]

  # Scale to zero when idle — $0 cost between scans.
  # Frontend fires /v1/warmup when camera page opens to front-load cold start.
  workers_min = 0
  workers_max = 3

  # Scale down after 5 minutes idle (covers typical scan session gaps)
  idle_timeout = 300

  # Max 10 minutes per job — covers worst-case cold start + long prescription
  execution_timeout_ms = 600000

  env = [
    # Model — AWQ INT4 quantized, fits in 24GB with 8192 context
    { key = "MODEL_NAME",              value = "Qwen/Qwen2.5-VL-7B-Instruct-AWQ" },
    { key = "MAX_MODEL_LEN",           value = "8192" },
    { key = "DTYPE",                   value = "auto" },
    { key = "QUANTIZATION",            value = "awq" },

    # Guided decoding backend — enforces JSON schema at token level
    { key = "GUIDED_DECODING_BACKEND", value = "xgrammar" },

    # Disable chunked prefill for multimodal stability
    { key = "DISABLE_CHUNKED_PREFILL", value = "1" },

    # Trust remote code for Qwen2.5-VL processor
    { key = "TRUST_REMOTE_CODE",       value = "true" },

    # Optional: HF token for authenticated downloads (avoids rate limits)
    # { key = "HUGGING_FACE_HUB_TOKEN", value = var.hf_token },
  ]

  # Optional: mount model weights from a RunPod network volume so cold starts
  # skip the HuggingFace download (~12GB for AWQ). Create in RunPod dashboard
  # under Storage → Network Volumes, then set TF_VAR_runpod_network_volume_id.
  # network_volume_id = var.runpod_network_volume_id != "" ? var.runpod_network_volume_id : null
}

# ── GitHub secrets — Railway reads these to call RunPod ──────────────────────

resource "github_actions_secret" "vllm_url" {
  repository      = var.github_repo
  secret_name     = "VLLM_URL"
  plaintext_value = "https://api.runpod.ai/v2/${runpod_endpoint.qwen.id}/openai"
}

resource "github_actions_secret" "vllm_api_key" {
  repository      = var.github_repo
  secret_name     = "VLLM_API_KEY"
  plaintext_value = var.runpod_api_key
}

resource "github_actions_secret" "vllm_model" {
  repository      = var.github_repo
  secret_name     = "VLLM_MODEL"
  plaintext_value = "qwen/qwen2.5-vl-7b-instruct-awq"
}
