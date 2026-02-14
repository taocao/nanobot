# From Laptop to Cloud: Deploying an AI Agent to Production

**A practical engineering guide for deploying nanobot on GCP and Azure**

*This guide teaches you not just the "how" but the "why" behind every step — building the cloud engineering skills you need to take any application from local development to production.*

---

## Table of Contents

1. [Why Move to the Cloud?](#1-why-move-to-the-cloud)
2. [How Chat Bots Actually Work (The Architecture You Must Understand)](#2-how-chat-bots-actually-work)
3. [Preparing Your Application for Cloud Deployment](#3-preparing-your-application-for-cloud-deployment)
4. [Deploying on Google Cloud Platform (GCP)](#4-deploying-on-google-cloud-platform-gcp)
5. [Deploying on Microsoft Azure](#5-deploying-on-microsoft-azure)
6. [Running the Web UI Remotely](#6-running-the-web-ui-remotely)
7. [Troubleshooting Real-World Failures](#7-troubleshooting-real-world-failures)
8. [Engineering Skills Reference](#8-engineering-skills-reference)

---

## 1. Why Move to the Cloud?

You've built an AI agent that works perfectly on your laptop. You run `nanobot gateway`, send a Telegram message, and your bot responds. So why bother with the cloud?

| Problem | Local Development | Cloud Production |
|---------|-------------------|------------------|
| **Availability** | Bot dies when your laptop sleeps | Runs 24/7/365[^1] |
| **Network stability** | Wi-Fi drops, IP changes | Static IP, 99.9% SLA |
| **Security** | API keys in a plain JSON file | Encrypted in a vault service |
| **Scalability** | Bounded by your laptop's RAM | Upgrade with one command |
| **Disaster recovery** | Hard drive dies = everything lost | Managed backups, snapshots |

[^1]: Cloud Run and Azure Container Apps both offer 99.95% availability SLAs.

### Engineering Skill: The Local-to-Cloud Mindset

The biggest shift isn't technical — it's mental. On your laptop, **you are the operator**. You start processes, read logs in the terminal, and `Ctrl+C` when things go wrong. In the cloud, you need to design for **unattended operation**:

- Processes must **auto-restart** on failure
- Logs must go somewhere **persistent** (not just stdout)
- **Secrets** can't live in files you manually edit
- Configuration must be **environment-aware** (dev vs. prod)

---

## 2. How Chat Bots Actually Work

Before deploying anywhere, you **must** understand how messages flow. This knowledge prevents a class of bugs that are impossible to debug without understanding the architecture.

### 2.1 Telegram: The Polling Model

```
  User's Phone                Telegram Cloud                Your Server
  ┌──────────┐             ┌──────────────────┐           ┌─────────────┐
  │ Send      │────────────▶│  Message Queue    │           │  nanobot     │
  │ "Hello"   │             │  (per bot token)  │◀──poll───│  (gateway)   │
  └──────────┘             │                  │           │             │
                           │  1. Store msg     │──deliver─▶│  2. Process  │
                           │                  │           │  3. Call LLM │
                           │                  │◀──reply───│  4. Respond  │
  ┌──────────┐             │                  │           └─────────────┘
  │ See       │◀────────────│  5. Deliver reply │
  │ response  │             └──────────────────┘
  └──────────┘
```

**How it works step by step:**

1. You create a bot with [@BotFather](https://t.me/BotFather) — he gives you a **bot token** (e.g., `123456:ABC-DEF...`)
2. This token is your bot's identity — a combination of username and password
3. Nanobot uses **long polling**: it repeatedly calls Telegram's `getUpdates` API asking *"Any new messages?"*
4. Telegram holds the connection open for ~30 seconds. If a message arrives, it sends it immediately
5. Nanobot processes the message through the AI pipeline and calls `sendMessage` to reply

**The critical rule:** Telegram delivers each message to **exactly one poller** per bot token. If two servers poll with the same token:

- Messages get **randomly split** between them, or
- One gets a `409 Conflict` error and stops receiving

> **🔑 Key Takeaway:** One bot token = one active instance. You **cannot** run the same bot on your laptop AND in the cloud simultaneously.

### 2.2 WhatsApp: The Bridge Model

```
  Your Phone              WhatsApp Cloud              Your Server
  ┌──────────┐          ┌──────────────────┐        ┌─────────────────┐
  │ Primary   │          │                  │        │ nanobot          │
  │ WhatsApp  │──linked─▶│  WhatsApp         │        │ ┌─────────────┐ │
  │ device    │          │  Multi-device     │◀─ws───│ │ WA Bridge   │ │
  └──────────┘          │  Protocol         │        │ │ (Node.js)   │ │
                         │                  │──msg──▶│ └──────┬──────┘ │
  Someone sends          │                  │        │        │        │
  you a message ────────▶│                  │        │  ┌─────▼─────┐  │
                         └──────────────────┘        │  │ Gateway   │  │
                                                     │  │ (Python)  │  │
                                                     │  └───────────┘  │
                                                     └─────────────────┘
```

**How it works step by step:**

1. The WhatsApp bridge (`bridge/` directory) acts like **WhatsApp Web** — it's a "linked device" on your phone
2. When you ran `nanobot channels login` and scanned the QR code, your phone linked the bridge as a new device
3. WhatsApp allows up to ~4 linked devices per phone number
4. The bridge maintains a persistent **WebSocket** connection to WhatsApp's servers
5. Messages arrive in real-time (no polling needed) via the WebSocket
6. The bridge forwards them to nanobot's gateway over a local WebSocket (`ws://localhost:3001`)

**The critical rule:** Each bridge instance creates a separate linked device. Running two bridges means **both** receive messages and **both** reply → the user gets duplicate responses.

### 2.3 The End-to-End Message Flow

Here's the complete journey of a single Telegram message:

```
1. User types "What's the weather?"
       │
       ▼
2. Telegram servers store it in the queue for bot token XXXX
       │
       ▼
3. nanobot polls: GET /getUpdates?token=XXXX
   Telegram responds: {"message": {"text": "What's the weather?", "chat_id": 12345}}
       │
       ▼
4. Channel handler creates:
   InboundMessage(channel="telegram", content="What's the weather?", chat_id="12345")
       │
       ▼
5. MessageBus dispatches to AgentLoop
       │
       ▼
6. AgentLoop:
   a. Loads session history for chat_id 12345
   b. Builds prompt: system_prompt + history + "What's the weather?"
   c. Calls LLM API (e.g., OpenAI gpt-oss-120b)
       │
       ▼
7. LLM responds: "I don't have real-time weather access, but you can check..."
       │
       ▼
8. nanobot formats response as HTML (Markdown → Telegram HTML)
   If response > 4096 chars → splits into chunks at safe boundaries
       │
       ▼
9. Calls POST /sendMessage for each chunk
       │
       ▼
10. User sees the response in Telegram
```

### 2.4 Can I Run on My Mac AND in the Cloud?

| Scenario | Telegram | WhatsApp | Verdict |
|----------|----------|----------|---------|
| Mac ON + Cloud ON (same token) | ❌ Messages randomly split | ❌ Duplicate replies | **Broken** |
| Mac ON + Cloud OFF | ✅ Works | ✅ Works | **OK** |
| Mac OFF + Cloud ON | ✅ Works | ✅ Works | **OK** |
| Mac + Cloud with **different** bot tokens | ✅ Both work | N/A | **OK for dev/prod** |

**Recommended setup for engineers:**

- Create **two Telegram bots**: `@mybot_dev` (for your laptop) and `@mybot` (for production)
- Use your laptop for development and testing with the dev bot
- Cloud runs the production bot 24/7
- For WhatsApp: only run the bridge in one place (cloud is the better choice)

---

## 3. Preparing Your Application for Cloud Deployment

Before deploying to any cloud, you need three things: a containerized application, externalized configuration, and a secret management strategy.

### 3.1 Containerization with Docker

**What is it?** Docker packages your application + all its dependencies into a single image that runs identically everywhere.

**Why it matters:** On your Mac, nanobot works because you have Python 3.12, Node.js, and the right libraries installed. A cloud VM starts empty — Docker ensures nothing is missing.

**Engineering Skill: Multi-Stage Dependency Caching**

A well-structured Dockerfile separates dependency installation from code copying. This means rebuilds are fast because Docker caches layers:

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Layer 1: System packages (cached unless Dockerfile changes)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      curl ca-certificates gnupg git && \
    mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
      | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] \
      https://deb.nodesource.com/node_20.x nodistro main" \
      > /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get purge -y gnupg && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Layer 2: Python deps (cached unless pyproject.toml changes)
COPY pyproject.toml README.md LICENSE ./
RUN mkdir -p nanobot bridge && touch nanobot/__init__.py && \
    uv pip install --system --no-cache ".[ui]" && \
    rm -rf nanobot bridge

# Layer 3: Application code (rebuilds when code changes)
COPY nanobot/ nanobot/
COPY bridge/ bridge/
RUN uv pip install --system --no-cache ".[ui]"

# Layer 4: WhatsApp bridge
WORKDIR /app/bridge
RUN npm install && npm run build
WORKDIR /app

# Layer 5: Cloud configuration
COPY config.cloud.json ./
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh
RUN mkdir -p /root/.nanobot /data/workspace/memory

EXPOSE 18790 8080

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["gateway"]
```

> **💡 Why this ordering matters:** When you change your Python code, Docker only rebuilds Layers 3-5. System packages (Layer 1) and Python dependencies (Layer 2) are cached, saving ~5 minutes per build.

### 3.2 Externalizing Configuration

**The problem:** Your local config at `~/.nanobot/config.json` contains hardcoded API keys. You can't bake these into a Docker image (anyone who pulls the image gets your keys).

**The solution:** Use environment variable placeholders:

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
    }
  },
  "providers": {
    "openai": { "apiKey": "${OPENAI_API_KEY}" },
    "openrouter": { "apiKey": "${OPENROUTER_API_KEY}" },
    "anthropic": { "apiKey": "${ANTHROPIC_API_KEY}" },
    "groq": { "apiKey": "${GROQ_API_KEY}" }
  },
  "gateway": { "host": "0.0.0.0", "port": 18790 },
  "tools": {
    "exec": { "timeout": 60 },
    "restrictToWorkspace": true
  }
}
```

Then an entrypoint script substitutes the placeholders at container startup:

```bash
#!/bin/bash
set -e

CONFIG_DIR="/root/.nanobot"
mkdir -p "$CONFIG_DIR" /data/workspace/memory

if [ -f /app/config.cloud.json ]; then
    cp /app/config.cloud.json "$CONFIG_DIR/config.json"

    # Substitute all ${VAR_NAME} patterns with actual env values
    for var in $(env | cut -d= -f1); do
        value=$(printenv "$var" | sed 's/[&/\]/\\&/g')
        sed -i "s|\${${var}}|${value}|g" "$CONFIG_DIR/config.json" 2>/dev/null || true
    done

    echo "✓ Config generated from cloud template"
fi

exec nanobot "$@"
```

**Engineering Skill: The 12-Factor App**

This pattern follows the [12-Factor App](https://12factor.net/) methodology, specifically Factor III (Config). The principle states:

> *Store config in the environment, not in code.*

This means the same Docker image runs in development, staging, and production — only the environment variables change. Never branch code based on the environment.

### 3.3 Secret Management

**The hierarchy of secret storage (worst to best):**

| Level | Method | Security | Example |
|-------|--------|----------|---------|
| 1 | Hardcoded in source | ❌ Terrible | `apiKey: "sk-abc123"` in code |
| 2 | Config file on disk | ⚠️ Risky | `~/.nanobot/config.json` on your Mac |
| 3 | Environment variables | ⚠️ Better | `docker run -e API_KEY=sk-abc123` |
| 4 | Cloud secret manager | ✅ Best | GCP Secret Manager / Azure Key Vault |

Level 2 is fine for local development. Level 4 is what you use in production. The cloud secret managers provide:

- **Encryption at rest** (your key is encrypted when stored)
- **Access control** (only your container can read it)
- **Audit logging** (you can see who accessed each secret and when)
- **Rotation** (update a key without redeploying)

---

## 4. Deploying on Google Cloud Platform (GCP)

GCP's approach: **Cloud Run** (serverless containers) + **Secret Manager** + **Artifact Registry**.

### 4.1 Initial Setup

```bash
# Install gcloud CLI: https://cloud.google.com/sdk/docs/install

# Login
gcloud auth login

# Create project
gcloud projects create nanobot-prod --name="Nanobot Production"
gcloud config set project nanobot-prod

# Enable required APIs
gcloud services enable \
  run.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com
```

**What each API does:**

| API | Purpose | Why needed |
|-----|---------|------------|
| `run` | Runs your container | Hosts nanobot |
| `secretmanager` | Stores API keys | Secure credential storage |
| `artifactregistry` | Stores Docker images | Container image storage |
| `cloudbuild` | Builds images in the cloud | No need to push large images from your laptop |

### 4.2 Store Secrets

```bash
# Each secret is created with a single command
echo -n "sk-your-openai-key" | \
  gcloud secrets create OPENAI_API_KEY --data-file=-

echo -n "sk-ant-your-key" | \
  gcloud secrets create ANTHROPIC_API_KEY --data-file=-

echo -n "sk-or-your-key" | \
  gcloud secrets create OPENROUTER_API_KEY --data-file=-

echo -n "123456:ABC-your-token" | \
  gcloud secrets create TELEGRAM_BOT_TOKEN --data-file=-

echo -n "gsk_your-key" | \
  gcloud secrets create GROQ_API_KEY --data-file=-
```

> **⚠️ Important:** The `-n` flag in `echo -n` prevents adding a trailing newline to your secret. Without it, your API key would have an invisible `\n` at the end, causing authentication failures that are very hard to debug.

### 4.3 Build the Docker Image

```bash
REGION=us-central1

# Create a Docker repository in Artifact Registry
gcloud artifacts repositories create nanobot-repo \
  --repository-format=docker \
  --location=$REGION \
  --description="Nanobot Docker images"

# Build in the cloud (uploads your source, builds remotely)
gcloud builds submit \
  --tag ${REGION}-docker.pkg.dev/nanobot-prod/nanobot-repo/nanobot:latest \
  --timeout 1200
```

**Engineering Skill: Cloud Builds vs. Local Builds**

| Approach | Pros | Cons |
|----------|------|------|
| Build locally, push image | Fast iteration, works offline | Must push ~1GB image over your internet |
| Cloud Build | No local Docker needed, fast push (only source code) | Costs ~$0.003/build-minute |

Cloud Build is almost always better. You upload ~50MB of source code instead of pushing a ~1GB image.

### 4.4 Deploy to Cloud Run

```bash
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

**Understanding each flag:**

| Flag | Value | Why |
|------|-------|-----|
| `--min-instances 1` | Always keep 1 container running | Telegram needs a persistent polling process |
| `--max-instances 1` | Never scale beyond 1 | Only one poller per bot token (see Section 2) |
| `--no-cpu-throttling` | Keep CPU allocated when idle | Without this, polling freezes between requests |
| `--timeout 3600` | 1-hour request timeout | Long-running WebSocket/streaming connections |
| `--set-secrets` | Map secrets to env vars | Injects Secret Manager values at runtime |
| `--memory 1Gi` | 1 GB RAM | Python + Node.js bridge need this |

> **💡 Key insight:** Cloud Run is designed for HTTP request-response workloads. A chat bot that polls continuously is an **atypical** use case. That's why we need `--min-instances 1` and `--no-cpu-throttling` — without them, Cloud Run would scale to zero and your bot would go offline.

### 4.5 Verify

```bash
# Get the service URL
gcloud run services describe nanobot --region $REGION \
  --format="value(status.url)"

# Check logs
gcloud run services logs read nanobot --region $REGION --limit 50

# Test the endpoint
SERVICE_URL=$(gcloud run services describe nanobot --region $REGION \
  --format="value(status.url)")
curl "${SERVICE_URL}/api/status"
```

### 4.6 CI/CD with GitHub Actions

Create `.github/workflows/deploy-gcp.yml`:

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
      id-token: write  # Required for Workload Identity Federation

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
            --timeout 1200

      - name: Deploy to Cloud Run
        run: |
          gcloud run deploy nanobot \
            --image us-central1-docker.pkg.dev/nanobot-prod/nanobot-repo/nanobot:${{ github.sha }} \
            --region us-central1
```

**Engineering Skill: Workload Identity Federation**

Traditional CI/CD uses long-lived service account keys (JSON files) stored as GitHub secrets. This is a security risk — if the key leaks, anyone can deploy to your project.

**Workload Identity Federation** eliminates stored credentials entirely. Instead:

1. GitHub Actions generates a short-lived OIDC token for each workflow run
2. GCP verifies this token came from your specific GitHub repo
3. GCP grants temporary credentials (valid for ~1 hour)
4. No permanent keys exist anywhere

Setup:

```bash
# Create a service account
gcloud iam service-accounts create github-deploy \
  --display-name="GitHub Actions Deploy"

# Grant deployment permissions
for role in roles/run.admin roles/cloudbuild.builds.editor \
            roles/artifactregistry.writer; do
  gcloud projects add-iam-policy-binding nanobot-prod \
    --member="serviceAccount:github-deploy@nanobot-prod.iam.gserviceaccount.com" \
    --role="$role"
done

# Create Workload Identity Pool + Provider
gcloud iam workload-identity-pools create github-pool \
  --location="global" \
  --display-name="GitHub Pool"

gcloud iam workload-identity-pools providers create-oidc github-provider \
  --location="global" \
  --workload-identity-pool="github-pool" \
  --display-name="GitHub Provider" \
  --attribute-mapping="google.subject=assertion.sub,\
attribute.repository=assertion.repository" \
  --issuer-uri="https://token.actions.githubusercontent.com"

# Allow your specific repo to impersonate the service account
gcloud iam service-accounts add-iam-policy-binding \
  github-deploy@nanobot-prod.iam.gserviceaccount.com \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/\
projects/PROJECT_NUMBER/locations/global/\
workloadIdentityPools/github-pool/\
attribute.repository/taocao/nanobot"
```

### 4.7 GCP Cost Summary

| Resource | Spec | Monthly Cost |
|----------|------|-------------|
| Cloud Run (1 vCPU, 1GB, always-on) | min-instances=1 | ~$30-40 |
| Secret Manager | 6 secrets | ~$0.06 |
| Artifact Registry | ~500MB images | ~$0.50 |
| Cloud Build | ~10 builds/month | Free tier |
| **Total** | | **~$30-40/month** |

---

## 5. Deploying on Microsoft Azure

Azure's approach: **Container Apps** (or a simple VM) + **Key Vault** + **Azure Container Registry**.

### 5.1 Initial Setup

```bash
# Install Azure CLI: brew install azure-cli

# Login
az login

# Create a resource group (Azure's way of organizing resources)
az group create --name nanobot-rg --location eastus
```

**Engineering Skill: Resource Providers**

Azure requires you to register **resource providers** before using certain services. If you get a `MissingSubscriptionRegistration` error:

```bash
# Register all providers you'll need upfront
az provider register --namespace Microsoft.ContainerRegistry
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.KeyVault
az provider register --namespace Microsoft.OperationalInsights

# Check registration status
az provider show --namespace Microsoft.ContainerRegistry \
  --query "registrationState" -o tsv
```

This is a one-time operation per subscription. The registration takes 1-2 minutes.

### 5.2 The VM Approach (Recommended for Personal Projects)

For a personal AI bot, a VM is the simplest and cheapest option. No container registry, no Docker — just `git pull` and `pip install`, exactly like on your Mac.

#### Create the VM

```bash
az vm create \
  --resource-group nanobot-rg \
  --name nanobot-vm \
  --image Ubuntu2204 \
  --size Standard_B1ms \
  --admin-username taocao \
  --generate-ssh-keys \
  --public-ip-sku Standard

# Open the gateway port
az vm open-port --resource-group nanobot-rg \
  --name nanobot-vm --port 18790
```

> **Standard_B1ms**: 1 vCPU, 2 GB RAM, ~$13/month — plenty for nanobot.

#### Set Up the Environment

```bash
# SSH into the VM
az ssh vm --resource-group nanobot-rg --name nanobot-vm

# On the VM: install dependencies
sudo apt update && sudo apt upgrade -y
sudo apt install -y software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt install -y python3.12 python3.12-venv python3.12-dev \
  python3-pip git build-essential

# Node.js 20 (for WhatsApp bridge)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

# Clone your repo
git clone https://github.com/taocao/nanobot.git
cd nanobot

# Create venv and install
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[ui]"

# Build WhatsApp bridge (optional)
cd bridge && npm install && npm run build && cd ..
```

#### Configure nanobot

```bash
mkdir -p ~/.nanobot

cat > ~/.nanobot/config.json << 'ENDOFCONFIG'
{
  "agents": {
    "defaults": {
      "workspace": "~/.nanobot/workspace",
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
      "token": "YOUR_TELEGRAM_BOT_TOKEN",
      "allowFrom": []
    }
  },
  "providers": {
    "openai": { "apiKey": "YOUR_OPENAI_KEY" },
    "openrouter": { "apiKey": "YOUR_OPENROUTER_KEY" },
    "anthropic": { "apiKey": "YOUR_ANTHROPIC_KEY" }
  },
  "gateway": { "host": "0.0.0.0", "port": 18790 },
  "tools": { "exec": { "timeout": 60 }, "restrictToWorkspace": true }
}
ENDOFCONFIG

# Edit with your actual keys
nano ~/.nanobot/config.json
```

#### Run with systemd (Auto-Restart, Survives Reboot)

**Engineering Skill: systemd Service Management**

On Linux servers, [systemd](https://systemd.io/) is the standard process manager. It:
- **Starts services on boot** (your bot comes back after VM restarts)
- **Auto-restarts on crash** (if nanobot crashes, it's back in 10 seconds)
- **Manages logging** (all output goes to `journalctl`)
- **Handles dependencies** (waits for networking before starting)

```bash
sudo tee /etc/systemd/system/nanobot.service << 'EOF'
[Unit]
Description=nanobot AI Assistant
After=network.target

[Service]
Type=simple
User=taocao
WorkingDirectory=/home/taocao/nanobot
ExecStart=/home/taocao/nanobot/.venv/bin/nanobot gateway
Restart=always
RestartSec=10
Environment=PATH=/home/taocao/nanobot/.venv/bin:/usr/local/bin:/usr/bin

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable nanobot   # Start on boot
sudo systemctl start nanobot    # Start now

# Verify
sudo systemctl status nanobot
sudo journalctl -u nanobot -f   # Live logs
```

**Understanding each section:**

| Section | Key | Purpose |
|---------|-----|---------|
| `[Unit]` | `After=network.target` | Don't start until networking is ready |
| `[Service]` | `User=taocao` | Run as your user, not root |
| `[Service]` | `WorkingDirectory=...` | Set the working directory (must match the code location) |
| `[Service]` | `Restart=always` | Restart unconditionally on exit |
| `[Service]` | `RestartSec=10` | Wait 10 seconds between restarts |
| `[Service]` | `Environment=PATH=...` | Ensure the venv's `bin/` is in PATH |
| `[Install]` | `WantedBy=multi-user.target` | Enable on boot when multi-user mode is reached |

> **⚠️ Common pitfall:** The `User=` must match the actual user who owns the files. If files are under `/home/taocao/` but the service runs as `azureuser`, you get `Permission denied` — a `CHDIR` failure that loops forever.

#### Update Deployment

When you push new code:

```bash
# SSH into VM
az ssh vm --resource-group nanobot-rg --name nanobot-vm

# Pull and restart
cd ~/nanobot
git pull
source .venv/bin/activate
pip install -e ".[ui]"
sudo systemctl restart nanobot
```

### 5.3 Container Apps Approach (For Teams/Production)

If you want the managed container experience (similar to GCP Cloud Run):

```bash
# Create container registry
az acr create --resource-group nanobot-rg --name nanobotcr --sku Basic

# Build in the cloud
az acr build --registry nanobotcr --image nanobot:latest \
  --file Dockerfile . --timeout 1200

# Create Container Apps environment
az containerapp env create \
  --resource-group nanobot-rg --name nanobot-env --location eastus

# Deploy
ACR_PASSWORD=$(az acr credential show --name nanobotcr \
  --query "passwords[0].value" -o tsv)

az containerapp create \
  --resource-group nanobot-rg \
  --name nanobot \
  --environment nanobot-env \
  --image nanobotcr.azurecr.io/nanobot:latest \
  --registry-server nanobotcr.azurecr.io \
  --registry-username nanobotcr \
  --registry-password "$ACR_PASSWORD" \
  --target-port 18790 \
  --ingress external \
  --cpu 1.0 --memory 2.0Gi \
  --min-replicas 1 --max-replicas 1 \
  --env-vars \
    "OPENAI_API_KEY=secretref:openai-api-key" \
    "TELEGRAM_BOT_TOKEN=secretref:telegram-bot-token"
```

### 5.4 Azure Cost Summary

| Approach | Spec | Monthly Cost |
|----------|------|-------------|
| **VM (Standard_B1ms)** | 1 vCPU, 2 GB RAM | ~$13 |
| **Container Apps** | 1 vCPU, 2 GB, always-on | ~$40-50 |
| Key Vault | 5-6 secrets | ~$0.03 |
| Container Registry (Basic) | 10 GB storage | ~$5 |

---

## 6. Running the Web UI Remotely

nanobot includes a web UI (port 8080) for interacting with your bot from a browser. On your Mac, you just visit `http://localhost:8080`. On a cloud VM, you need a way to access it.

### 6.1 SSH Tunneling (Best Option — Free, Secure)

**Engineering Skill: SSH Port Forwarding**

SSH tunneling creates an encrypted connection between a port on your local machine and a port on the remote server. The SSH protocol handles all the encryption and authentication — you don't need a web server, firewall rules, or SSL certificates.

```
Your Mac                    SSH Tunnel                 Azure VM
┌──────────────┐     encrypted connection       ┌──────────────┐
│ Browser      │◄──────────────────────────────▶│ nanobot UI   │
│ localhost:   │       (port forwarding)        │ :8080        │
│   8080       │                                │              │
└──────────────┘                                └──────────────┘
```

**Setup:**

First, update systemd to run both gateway and UI:

```bash
sudo tee /etc/systemd/system/nanobot.service << 'EOF'
[Unit]
Description=nanobot AI Assistant
After=network.target

[Service]
Type=simple
User=taocao
WorkingDirectory=/home/taocao/nanobot
ExecStart=/bin/bash -c '\
  /home/taocao/nanobot/.venv/bin/nanobot gateway & \
  /home/taocao/nanobot/.venv/bin/nanobot ui --port 8080 & \
  wait'
Restart=always
RestartSec=10
Environment=PATH=/home/taocao/nanobot/.venv/bin:/usr/local/bin:/usr/bin

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl restart nanobot
```

**Then from your Mac, one command:**

```bash
# Azure CLI method
az ssh vm --resource-group nanobot-rg --name nanobot-vm \
  -- -L 8080:localhost:8080

# Or standard SSH
ssh -L 8080:localhost:8080 taocao@<VM-IP-ADDRESS>
```

Open `http://localhost:8080` in your Mac browser. Done!

The `-L 8080:localhost:8080` means: *"Take connections to my Mac's port 8080 and forward them through the SSH tunnel to the VM's localhost:8080."*

### 6.2 Public Access with nginx + Basic Auth

If you need to access the UI from anywhere (phone, other computers):

```bash
# On the VM:
sudo apt install -y nginx apache2-utils

# Create a password
sudo htpasswd -c /etc/nginx/.htpasswd taocao

# Configure reverse proxy
sudo tee /etc/nginx/sites-available/nanobot << 'EOF'
server {
    listen 80;
    server_name _;

    auth_basic "nanobot";
    auth_basic_user_file /etc/nginx/.htpasswd;

    location / {
        proxy_pass http://localhost:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /api/ {
        proxy_pass http://localhost:18790;
        proxy_set_header Host $host;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/nanobot \
  /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl restart nginx

# Open port 80
az vm open-port --resource-group nanobot-rg \
  --name nanobot-vm --port 80 --priority 1020
```

### 6.3 Comparison

| Method | Cost | Security | Accessible From | Setup Time |
|--------|------|----------|----------------|------------|
| **SSH Tunnel** | Free | ✅ Encrypted | Your Mac only | 1 command |
| **Open Port** | Free | ❌ No auth | Anyone | 1 command |
| **nginx + auth** | Free | ✅ Password | Anywhere | 5 minutes |

---

## 7. Troubleshooting Real-World Failures

These are real errors encountered during deployment and how to fix them.

### 7.1 systemd: "Failed to locate executable"

```
nanobot.service: Failed to locate executable
  /home/azureuser/nanobot/.venv/bin/nanobot: No such file or directory
```

**Root cause:** The `nanobot` command-line entry point wasn't installed in the virtual environment.

**Fix:**

```bash
source /home/taocao/nanobot/.venv/bin/activate
pip install -e ".[ui]"
which nanobot  # Should show the venv path
```

If `pip install` doesn't create the `nanobot` command, use the Python module path:

```bash
# In the systemd service, change ExecStart to:
ExecStart=/home/taocao/nanobot/.venv/bin/python -m nanobot.cli.commands gateway
```

### 7.2 systemd: "Permission denied" (CHDIR failure)

```
nanobot.service: Changing to the requested working directory failed:
  Permission denied
```

**Root cause:** The `User=` in the service file doesn't match the owner of the files.

**Example:** Files are in `/home/taocao/` but service runs as `User=azureuser`. User `azureuser` has no permission to read `/home/taocao/`.

**Fix:** Update the service file:

```diff
 [Service]
-User=azureuser
-WorkingDirectory=/home/azureuser/nanobot
-ExecStart=/home/azureuser/nanobot/.venv/bin/nanobot gateway
+User=taocao
+WorkingDirectory=/home/taocao/nanobot
+ExecStart=/home/taocao/nanobot/.venv/bin/nanobot gateway
```

Then `sudo systemctl daemon-reload && sudo systemctl restart nanobot`.

### 7.3 Azure: "MissingSubscriptionRegistration"

```
The subscription is not registered to use namespace
'Microsoft.ContainerRegistry'
```

**Root cause:** Azure requires explicit registration of resource providers before use. This is a one-time setup.

**Fix:**

```bash
az provider register --namespace Microsoft.ContainerRegistry
# Wait 1-2 minutes, then retry your command
```

### 7.4 nanobot CLI: "No such command 'ui'"

```
Error: No such command 'ui'.
```

**Root cause:** The `ui` command exists on a feature branch (`agentic-ui-enhanced`) but not on `main`. You need to either switch branches or merge the UI branch.

**Fix:**

```bash
git checkout agentic-ui-enhanced
pip install -e ".[ui]"
nanobot ui --port 8080
```

### 7.5 General Debugging Workflow

```bash
# 1. Check service status
sudo systemctl status nanobot

# 2. Read recent logs
sudo journalctl -u nanobot --since "5 minutes ago"

# 3. Try running manually (to see errors in real-time)
sudo systemctl stop nanobot
/home/taocao/nanobot/.venv/bin/nanobot gateway

# 4. Once it works manually, restart the service
sudo systemctl start nanobot
```

---

## 8. Engineering Skills Reference

Here's a summary of the engineering skills covered in this guide:

### Cloud Architecture

| Skill | What You Learned | Why It Matters |
|-------|-----------------|----------------|
| **Container orchestration** | Cloud Run, Container Apps | Run the same image everywhere |
| **Secret management** | Secret Manager, Key Vault | Never expose API keys |
| **CI/CD pipelines** | GitHub Actions + OIDC | Automated, keyless deployment |
| **Infrastructure as Code** | CLI-based provisioning | Reproducible environments |

### Linux Server Administration

| Skill | What You Learned | Why It Matters |
|-------|-----------------|----------------|
| **systemd** | Service files, `journalctl` | Process management on Linux servers |
| **SSH tunneling** | `-L` port forwarding | Secure remote access without exposing ports |
| **File permissions** | User matching in services | #1 cause of "Permission denied" |
| **nginx reverse proxy** | Basic auth, proxy_pass | Password-protect web interfaces |

### Application Architecture

| Skill | What You Learned | Why It Matters |
|-------|-----------------|----------------|
| **12-Factor App** | Config via environment | Same image for dev/staging/prod |
| **Docker layer caching** | Dependency ordering in Dockerfile | Fast rebuilds (seconds vs. minutes) |
| **Message queue patterns** | Polling vs. WebSocket | Why only one bot instance can run |
| **Entrypoint scripts** | Env var substitution at startup | Dynamic config without rebuilding |

### Cost Optimization

| Deployment | Monthly Cost | Best For |
|------------|-------------|----------|
| Azure VM (B1ms) | **~$13** | Personal projects, maximum control |
| GCP Cloud Run | **~$30-40** | Serverless, auto-scaling teams |
| Azure Container Apps | **~$40-50** | Azure-native organizations |

---

## Quick Reference Commands

### GCP

```bash
# Deploy
gcloud builds submit --tag REGION-docker.pkg.dev/PROJECT/REPO/nanobot:latest
gcloud run deploy nanobot --image ... --region us-central1

# Logs
gcloud run services logs read nanobot --region us-central1 --limit 100

# Update secret
echo -n "new-value" | gcloud secrets versions add SECRET_NAME --data-file=-
```

### Azure VM

```bash
# SSH in
az ssh vm --resource-group nanobot-rg --name nanobot-vm

# SSH tunnel for UI
az ssh vm --resource-group nanobot-rg --name nanobot-vm -- -L 8080:localhost:8080

# Service management (on the VM)
sudo systemctl status nanobot
sudo systemctl restart nanobot
sudo journalctl -u nanobot -f

# Update code (on the VM)
cd ~/nanobot && git pull && source .venv/bin/activate
pip install -e ".[ui]" && sudo systemctl restart nanobot
```

---

*Written as part of the nanobot project — an open-source AI agent framework. Contributions welcome at [github.com/taocao/nanobot](https://github.com/taocao/nanobot).*
