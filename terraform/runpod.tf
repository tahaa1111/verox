# =============================================================================
# RunPod Serverless — Qwen2.5-VL-7B-Instruct GPU inference
#
# STATUS: Placeholder — fill in when RunPod credentials are available.
# Uncomment the provider in providers.tf and set runpod_api_key variable.
#
# To apply:
#   export TF_VAR_runpod_api_key=rpa_xxxxxxxxxxxx
#   terraform apply -target=runpod_endpoint.qwen
# =============================================================================

# resource "runpod_endpoint" "qwen" {
#   name = "medibox-qwen-vl"
#
#   # vLLM template with Qwen2.5-VL-7B-Instruct
#   # Uses RunPod's official vLLM handler image
#   template_id = "runpod-vllm"
#
#   gpu_ids     = ["NVIDIA GeForce RTX 4090"]
#   workers_min = 0   # scale to zero when idle — $0 cost when no scans
#   workers_max = 3   # burst capacity for clinic hours
#
#   # Idle timeout: scale down after 5 min idle
#   idle_timeout = 300
#
#   # Environment variables for the vLLM worker
#   env = [
#     { key = "MODEL_NAME",     value = "Qwen/Qwen2.5-VL-7B-Instruct" },
#     { key = "MAX_MODEL_LEN",  value = "4096" },
#     { key = "DTYPE",          value = "bfloat16" },
#     # Network volume for model weights (no re-download on cold start)
#     # { key = "MODEL_BASE_PATH", value = "/runpod-volume/models" },
#   ]
#
#   # Network volume: mount model weights from persistent storage
#   # Create once in RunPod dashboard, then reference ID here
#   # network_volume_id = "vol_xxxxxxxxxxxxxxxx"
# }
#
# # Expose the endpoint ID as a GitHub Actions secret for CI/CD
# resource "github_actions_secret" "runpod_endpoint_id" {
#   repository      = var.github_repo
#   secret_name     = "RUNPOD_ENDPOINT_ID"
#   plaintext_value = runpod_endpoint.qwen.id
# }
#
# resource "github_actions_secret" "runpod_api_key" {
#   repository      = var.github_repo
#   secret_name     = "RUNPOD_API_KEY"
#   plaintext_value = var.runpod_api_key
# }

# Temporary: manual secret until RunPod credentials are available
# Set these in Railway manually for now
output "runpod_setup_instructions" {
  value = <<-EOT
    RunPod setup (manual — not yet Terraformed):
    1. Go to https://www.runpod.io/console/serverless
    2. New Endpoint → vLLM template
    3. Model: Qwen/Qwen2.5-VL-7B-Instruct
    4. GPU: RTX 4090, Max Workers: 3, Min Workers: 0
    5. Set VLLM_URL=https://api.runpod.ai/v2/<endpoint-id>/openai
    6. Set VLLM_API_KEY=<your-runpod-api-key>
    7. Uncomment runpod.tf and add runpod_api_key variable, then re-apply
  EOT
}
