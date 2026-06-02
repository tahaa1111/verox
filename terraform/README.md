# Medibox — Terraform Infrastructure

Manages all cloud resources as code. If you ever need to migrate providers
or recreate the stack from scratch, this is the single source of truth.

## Resources managed

| Resource | Provider | Purpose |
|---|---|---|
| Neon project + database + role | neon-database/neon | PostgreSQL |
| Upstash Redis | upstash/upstash | Task queue broker + rate limiting + idempotency |
| Cloudflare R2 bucket (medibox-crops) | cloudflare/cloudflare | Private crop storage |
| Cloudflare R2 bucket (terraform-state) | cloudflare/cloudflare | Terraform state backend |
| Cloudflare API token (r2 read/write) | cloudflare/cloudflare | Scoped R2 credentials |
| GitHub Actions secrets (10 secrets) | integrations/github | CI/CD credentials injection |
| RunPod endpoint (stub) | runpod-io/runpod | GPU inference (uncomment when ready) |

## Prerequisites

```bash
# Install Terraform
brew install terraform   # macOS
# or: https://developer.hashicorp.com/terraform/install

# Set provider credentials as env vars (never in files)
export TF_VAR_neon_api_key="..."
export TF_VAR_upstash_email="guesmitaha96@gmail.com"
export TF_VAR_upstash_api_key="..."
export TF_VAR_cloudflare_api_token="cfat_ftLP0ZEGW7NDSVLy3r0nyJwfuurCs9hL2Uxj9uRoa7522d35"
export TF_VAR_github_token="..."
export TF_VAR_firebase_admin_json="$(cat /path/to/firebase-adminsdk.json)"
export TF_VAR_pii_encryption_key="4Od5bcywunm0AaH_uy__U-Is-Q20Mv52Ql5Sp1Ue1ME="
```

## First-time setup

```bash
cd terraform/

# Download providers
terraform init

# Preview changes (dry run)
terraform plan

# Apply — creates all resources
terraform apply
```

## After first apply

Copy the `railway_api_env_vars` output into Railway:
```bash
terraform output -raw railway_api_env_vars
```

## Adding RunPod (when credentials available)

1. Uncomment the `runpod` provider block in `providers.tf`
2. Uncomment the resource block in `runpod.tf`
3. Set `TF_VAR_runpod_api_key=rpa_xxxx`
4. Run `terraform apply -target=runpod_endpoint.qwen`

## Migrating to a different cloud provider

This is the key benefit of Terraform. To migrate (example: Upstash → Redis Cloud):

1. Add the new provider to `providers.tf`
2. Add a new `redis_cloud.tf` with equivalent resource
3. Update `outputs.tf` to reference the new resource
4. `terraform plan` to preview the change
5. `terraform apply` — creates new, destroys old

The application code never changes — only the Terraform files and the
env var values that get injected.

## State management

Terraform state contains plaintext secrets. Never commit it.
After first apply, move state to R2 (uncomment backend block in providers.tf):

```bash
terraform init -migrate-state
```

## Security notes

- All `sensitive = true` outputs are hidden in `terraform plan` output
- API tokens are scoped to minimum permissions (R2 read/write on specific bucket only)
- `terraform.tfvars` is in `.gitignore` — use env vars instead
- GitHub secrets are created/rotated via Terraform — no manual copy-paste
