# Deploying nanobot to Google Cloud Platform (GCP)

This guide walks you through deploying nanobot on GCP so it runs 24/7 — receiving Telegram/WhatsApp messages and responding even when your Mac is off.

## Why Run on GCP?

| Reason | Explanation |
|--------|-------------|
| **24/7 availability** | Your Mac can sleep; nanobot stays online for Telegram/WhatsApp |
| **Stable network** | Cloud has a static IP and reliable uptime |
| **Scalability** | Easily upgrade CPU/RAM if needed |
| **Security** | API keys stored in GCP Secret Manager, not on your laptop |
| **CI/CD** | Auto-deploy when you push to GitHub |

## Architecture Overview

```
┌──────────────────────────────────────────────┐
│                  GCP                          │
│                                               │
│  ┌─────────────┐   ┌──────────────────────┐  │
│  │ Secret       │   │ Cloud Run            │  │
│  │ Manager      │──▶│ (nanobot container)  │  │
│  │ (API keys)   │   │  - gateway           │  │
│  └─────────────┘   │  - telegram bot      │  │
│                     │  - whatsapp bridge   │  │
│                     │  - web UI (:8080)    │  │
│                     └──────────────────────┘  │
│                                               │
│  ┌─────────────┐                              │
│  │ Artifact     │  (persistent storage for    │
│  │ Registry     │   Docker images)            │
│  └─────────────┘                              │
└──────────────────────────────────────────────┘
```

---

## Prerequisites

1. A GCP account with billing enabled
2. `gcloud` CLI installed on your Mac ([Install guide](https://cloud.google.com/sdk/docs/install))
3. Your nanobot repo pushed to GitHub
4. Your local `~/.nanobot/config.json` with working API keys

---

## Step 1: Set Up GCP Project

**Why:** Every GCP resource lives inside a project. This is the billing and access boundary.

```bash
# Login to GCP
gcloud auth login

# Create a new project (or use an existing one)
gcloud projects create nanobot-prod --name="Nanobot Production"

# Set it as the active project
gcloud config set project nanobot-prod

# Enable required APIs
gcloud services enable \
  run.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com
```

| API | Why it's needed |
|-----|----------------|
| `run.googleapis.com` | Cloud Run hosts your container |
| `secretmanager.googleapis.com` | Securely stores API keys |
| `artifactregistry.googleapis.com` | Stores your Docker images |
| `cloudbuild.googleapis.com` | Builds Docker images in the cloud |

---

## Step 2: Store Secrets in GCP Secret Manager

**Why:** Never put API keys in Docker images or environment variables in plain text. Secret Manager encrypts them at rest and provides audit logging.

```bash
# Store your OpenAI API key
echo -n "sk-your-openai-key" | \
  gcloud secrets create OPENAI_API_KEY --data-file=-

# Store your Anthropic API key (if used)
echo -n "sk-ant-your-key" | \
  gcloud secrets create ANTHROPIC_API_KEY --data-file=-

# Store your OpenRouter API key (if used)
echo -n "sk-or-your-key" | \
  gcloud secrets create OPENROUTER_API_KEY --data-file=-

# Store Telegram bot token (if used)
echo -n "123456:ABC-your-token" | \
  gcloud secrets create TELEGRAM_BOT_TOKEN --data-file=-

# Store Groq API key (for voice transcription)
echo -n "gsk_your-key" | \
  gcloud secrets create GROQ_API_KEY --data-file=-
```

Only create secrets for the providers you actually use.

---

## Step 3: Create a Cloud-Ready Config

**Why:** Your local config uses your Mac's `~/.nanobot/config.json`. On GCP, we inject API keys via environment variables so secrets stay in Secret Manager.

Create a file `config.cloud.json` in your repo root:

```json
{
  "agents": {
    "defaults": {
      "workspace": "/data/workspace",
      "model": "openai/gpt-oss-120b",
      "maxTokens": 8192,
      "temperature": 0.7,
      "maxToolIterations": 20,
      "memoryWindow": 50
    }
  },
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "${TELEGRAM_BOT_TOKEN}",
      "allowFrom": []
    },
    "whatsapp": {
      "enabled": false,
      "bridgeUrl": "ws://localhost:3001",
      "allowFrom": []
    }
  },
  "providers": {
    "openai": {
      "apiKey": "${OPENAI_API_KEY}"
    },
    "openrouter": {
      "apiKey": "${OPENROUTER_API_KEY}"
    },
    "anthropic": {
      "apiKey": "${ANTHROPIC_API_KEY}"
    },
    "groq": {
      "apiKey": "${GROQ_API_KEY}"
    }
  },
  "gateway": {
    "host": "0.0.0.0",
    "port": 18790
  },
  "tools": {
    "exec": { "timeout": 60 },
    "restrictToWorkspace": true
  }
}
```

> **Note:** The `${VAR}` placeholders will be substituted at container startup using an entrypoint script.

---

## Step 4: Create Cloud Entrypoint Script

**Why:** This script substitutes environment variable placeholders in the config with actual values from Secret Manager, then starts nanobot.

Create `docker-entrypoint.sh` in your repo root:

```bash
#!/bin/bash
set -e

CONFIG_DIR="/root/.nanobot"
mkdir -p "$CONFIG_DIR" /data/workspace/memory

# If cloud config exists, copy and substitute env vars
if [ -f /app/config.cloud.json ]; then
    cp /app/config.cloud.json "$CONFIG_DIR/config.json"

    # Substitute all ${VAR_NAME} patterns with actual env values
    for var in $(env | cut -d= -f1); do
        value=$(printenv "$var" | sed 's/[&/\]/\\&/g')
        sed -i "s|\${${var}}|${value}|g" "$CONFIG_DIR/config.json" 2>/dev/null || true
    done

    echo "✓ Config generated from cloud template"
fi

# Run nanobot with whatever command was passed
exec nanobot "$@"
```

Make it executable:

```bash
chmod +x docker-entrypoint.sh
```

---

## Step 5: Update Dockerfile for Cloud

**Why:** The existing Dockerfile works, but we need to add the cloud config and entrypoint script.

Create `Dockerfile.cloud` (or modify the existing one):

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      curl ca-certificates gnupg git && \
    mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
      | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" \
      > /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get purge -y gnupg && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies (cached layer)
COPY pyproject.toml README.md LICENSE ./
RUN mkdir -p nanobot bridge && touch nanobot/__init__.py && \
    uv pip install --system --no-cache ".[ui]" && \
    rm -rf nanobot bridge

# Copy full source and install
COPY nanobot/ nanobot/
COPY bridge/ bridge/
RUN uv pip install --system --no-cache ".[ui]"

# Build WhatsApp bridge
WORKDIR /app/bridge
RUN npm install && npm run build
WORKDIR /app

# Cloud config and entrypoint
COPY config.cloud.json ./
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

# Create directories
RUN mkdir -p /root/.nanobot /data/workspace/memory

# Ports: gateway (18790), UI (8080)
EXPOSE 18790 8080

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["gateway"]
```

---

## Step 6: Create Artifact Registry & Build Image

**Why:** Artifact Registry is GCP's Docker image storage. Cloud Build compiles your image in the cloud (no need to push large images from your Mac).

```bash
# Set your region
REGION=us-central1

# Create a Docker repository
gcloud artifacts repositories create nanobot-repo \
  --repository-format=docker \
  --location=$REGION \
  --description="Nanobot Docker images"

# Configure Docker auth for GCP
gcloud auth configure-docker ${REGION}-docker.pkg.dev

# Build and push using Cloud Build (builds in the cloud)
gcloud builds submit \
  --tag ${REGION}-docker.pkg.dev/nanobot-prod/nanobot-repo/nanobot:latest \
  --dockerfile Dockerfile.cloud \
  --timeout 1200
```

> This takes ~5-10 minutes the first time (Node.js + Python deps). Subsequent builds are faster due to caching.

---

## Step 7: Deploy to Cloud Run

**Why:** Cloud Run is the simplest way to run a container on GCP. It scales to zero when idle (saves money), auto-restarts on crashes, and provides HTTPS.

```bash
REGION=us-central1

gcloud run deploy nanobot \
  --image ${REGION}-docker.pkg.dev/nanobot-prod/nanobot-repo/nanobot:latest \
  --region $REGION \
  --platform managed \
  --port 18790 \
  --memory 1Gi \
  --cpu 1 \
  --min-instances 1 \
  --max-instances 1 \
  --timeout 3600 \
  --no-cpu-throttling \
  --set-secrets "OPENAI_API_KEY=OPENAI_API_KEY:latest,\
OPENROUTER_API_KEY=OPENROUTER_API_KEY:latest,\
ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,\
GROQ_API_KEY=GROQ_API_KEY:latest,\
TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN:latest" \
  --allow-unauthenticated
```

| Flag | Why |
|------|-----|
| `--min-instances 1` | Keeps container always running (required for Telegram polling) |
| `--no-cpu-throttling` | CPU stays allocated even when idle (needed for background polling) |
| `--timeout 3600` | Long timeout for WebSocket/streaming connections |
| `--set-secrets` | Injects Secret Manager values as environment variables |
| `--memory 1Gi` | Enough for Python + Node.js bridge |

---

## Step 8: Verify Deployment

```bash
# Check the service URL
gcloud run services describe nanobot --region $REGION --format="value(status.url)"

# Check logs
gcloud run services logs read nanobot --region $REGION --limit 50

# Test the gateway
SERVICE_URL=$(gcloud run services describe nanobot --region $REGION --format="value(status.url)")
curl "${SERVICE_URL}/api/status"
```

If Telegram is enabled, send a message to your bot — it should respond!

---

## Step 9: Set Up Continuous Deployment (Optional)

**Why:** Automatically deploy when you push to `main`, so you never manually build again.

Add this to `.github/workflows/deploy-gcp.yml`:

```yaml
name: Deploy to GCP

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write

    steps:
      - uses: actions/checkout@v4

      - name: Authenticate to GCP
        uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ secrets.GCP_WORKLOAD_IDENTITY }}
          service_account: ${{ secrets.GCP_SERVICE_ACCOUNT }}

      - name: Set up Cloud SDK
        uses: google-github-actions/setup-gcloud@v2

      - name: Build and push image
        run: |
          gcloud builds submit \
            --tag us-central1-docker.pkg.dev/nanobot-prod/nanobot-repo/nanobot:${{ github.sha }} \
            --dockerfile Dockerfile.cloud \
            --timeout 1200

      - name: Deploy to Cloud Run
        run: |
          gcloud run deploy nanobot \
            --image us-central1-docker.pkg.dev/nanobot-prod/nanobot-repo/nanobot:${{ github.sha }} \
            --region us-central1
```

To set up Workload Identity Federation (keyless auth from GitHub):

```bash
# Create service account
gcloud iam service-accounts create github-deploy \
  --display-name="GitHub Actions Deploy"

# Grant permissions
gcloud projects add-iam-policy-binding nanobot-prod \
  --member="serviceAccount:github-deploy@nanobot-prod.iam.gserviceaccount.com" \
  --role="roles/run.admin"

gcloud projects add-iam-policy-binding nanobot-prod \
  --member="serviceAccount:github-deploy@nanobot-prod.iam.gserviceaccount.com" \
  --role="roles/cloudbuild.builds.editor"

gcloud projects add-iam-policy-binding nanobot-prod \
  --member="serviceAccount:github-deploy@nanobot-prod.iam.gserviceaccount.com" \
  --role="roles/artifactregistry.writer"

# Create Workload Identity Pool
gcloud iam workload-identity-pools create github-pool \
  --location="global" \
  --display-name="GitHub Pool"

gcloud iam workload-identity-pools providers create-oidc github-provider \
  --location="global" \
  --workload-identity-pool="github-pool" \
  --display-name="GitHub Provider" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --issuer-uri="https://token.actions.githubusercontent.com"

# Allow GitHub repo to impersonate
gcloud iam service-accounts add-iam-policy-binding \
  github-deploy@nanobot-prod.iam.gserviceaccount.com \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github-pool/attribute.repository/taocao/nanobot"
```

Then add these GitHub secrets:
- `GCP_SERVICE_ACCOUNT`: `github-deploy@nanobot-prod.iam.gserviceaccount.com`
- `GCP_WORKLOAD_IDENTITY`: `projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github-pool/providers/github-provider`

---

## Cost Estimate

| Resource | Spec | Monthly Cost |
|----------|------|-------------|
| Cloud Run (1 vCPU, 1GB, always-on) | min-instances=1 | ~$30-40 |
| Secret Manager | 6 secrets | ~$0.06 |
| Artifact Registry | ~500MB images | ~$0.50 |
| Cloud Build | ~10 builds/month | Free tier |
| **Total** | | **~$30-40/month** |

> **Cost saving tip:** If you only need Telegram (no WhatsApp), you can use a smaller instance (512MB) for ~$15-20/month.

---

## Alternative: Compute Engine (VM)

If Cloud Run doesn't fit (e.g., you need persistent disk for WhatsApp session data), use a VM:

```bash
# Create a small VM
gcloud compute instances create nanobot-vm \
  --zone=us-central1-a \
  --machine-type=e2-small \
  --image-family=debian-12 \
  --image-project=debian-cloud \
  --boot-disk-size=20GB

# SSH in
gcloud compute ssh nanobot-vm --zone=us-central1-a

# On the VM:
sudo apt update && sudo apt install -y docker.io docker-compose-plugin
sudo usermod -aG docker $USER
# Then pull and run your container
```

Cost: e2-small is ~$13/month (committed use) or ~$17/month on-demand.

---

## Quick Reference

```bash
# Build & deploy (one command)
gcloud builds submit \
  --tag us-central1-docker.pkg.dev/nanobot-prod/nanobot-repo/nanobot:latest \
  --dockerfile Dockerfile.cloud && \
gcloud run deploy nanobot \
  --image us-central1-docker.pkg.dev/nanobot-prod/nanobot-repo/nanobot:latest \
  --region us-central1

# View logs
gcloud run services logs read nanobot --region us-central1 --limit 100

# Update a secret
echo -n "new-key-value" | \
  gcloud secrets versions add OPENAI_API_KEY --data-file=-

# Restart (picks up new secrets)
gcloud run services update nanobot --region us-central1
```
