# Deploying nanobot to Microsoft Azure

This guide walks you through deploying nanobot on Azure so it runs 24/7 — receiving Telegram/WhatsApp messages and responding even when your Mac is off.

## Why Run on Azure?

| Reason | Explanation |
|--------|-------------|
| **24/7 availability** | Your Mac can sleep; nanobot stays online for Telegram/WhatsApp |
| **Stable network** | Cloud has a static IP and reliable uptime |
| **Azure Key Vault** | API keys encrypted at rest with hardware security modules (HSMs) |
| **CI/CD** | Auto-deploy from GitHub Actions with OIDC (no stored credentials) |
| **Enterprise integration** | If you already use Azure AD, Teams, or other Microsoft services |

## Architecture Overview

```
┌──────────────────────────────────────────────────┐
│                    Azure                          │
│                                                   │
│  ┌─────────────┐   ┌──────────────────────────┐  │
│  │ Key Vault    │   │ Azure Container Apps      │  │
│  │ (API keys)   │──▶│ (nanobot container)       │  │
│  └─────────────┘   │  - gateway                │  │
│                     │  - telegram bot           │  │
│                     │  - whatsapp bridge        │  │
│                     │  - web UI (:8080)         │  │
│                     └──────────────────────────┘  │
│                                                   │
│  ┌─────────────┐                                  │
│  │ Azure        │  (Docker image storage)         │
│  │ Container    │                                  │
│  │ Registry     │                                  │
│  └─────────────┘                                  │
└──────────────────────────────────────────────────┘
```

---

## Prerequisites

1. An Azure account with an active subscription
2. Azure CLI (`az`) installed on your Mac ([Install guide](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli-macos))
3. Your nanobot repo pushed to GitHub
4. Your local `~/.nanobot/config.json` with working API keys

```bash
# Install Azure CLI (if not installed)
brew install azure-cli
```

---

## Step 1: Login & Create Resource Group

**Why:** Azure organizes all resources into Resource Groups — a logical container for billing, access control, and lifecycle management.

```bash
# Login to Azure
az login

# Set your subscription (if you have multiple)
az account set --subscription "Your Subscription Name"

# Create a resource group
az group create \
  --name nanobot-rg \
  --location eastus
```

| Concept | Why |
|---------|-----|
| Resource Group | Groups all nanobot resources together for management and billing |
| Location | Physical datacenter region; `eastus` is typically cheapest in the US |

---

## Step 2: Create Azure Container Registry (ACR)

**Why:** ACR stores your Docker images privately within Azure. It's faster than pulling from Docker Hub and integrates natively with Azure Container Apps.

```bash
# Create a container registry (name must be globally unique, lowercase, no dashes)
az acr create \
  --resource-group nanobot-rg \
  --name nanobotcr \
  --sku Basic

# Login to the registry
az acr login --name nanobotcr
```

| SKU | Storage | Cost |
|-----|---------|------|
| Basic | 10 GB | ~$5/month |
| Standard | 100 GB | ~$20/month |

Basic is fine for a single project.

---

## Step 3: Build & Push Docker Image

**Why:** You need to build the Docker image and push it to ACR so Azure Container Apps can pull and run it.

**Option A: Build locally and push** (uses your Mac's Docker):

```bash
cd /Users/tao.x.cao/tcaiml/nanobot

# Build the image
docker build -t nanobotcr.azurecr.io/nanobot:latest .

# Push to ACR
docker push nanobotcr.azurecr.io/nanobot:latest
```

**Option B: Build in the cloud** (no local Docker needed):

```bash
cd /Users/tao.x.cao/tcaiml/nanobot

# ACR Tasks builds in Azure's cloud
az acr build \
  --registry nanobotcr \
  --image nanobot:latest \
  --file Dockerfile \
  . \
  --timeout 1200
```

> Cloud build takes ~5-10 minutes the first time. Prefer Option B if you don't have Docker Desktop installed.

---

## Step 4: Store Secrets in Azure Key Vault

**Why:** Never put API keys in Docker images or plain-text environment variables. Key Vault encrypts them with HSM-backed keys and provides audit logging.

```bash
# Create a Key Vault (name must be globally unique)
az keyvault create \
  --resource-group nanobot-rg \
  --name nanobot-kv \
  --location eastus

# Store your API keys as secrets
az keyvault secret set --vault-name nanobot-kv \
  --name OPENAI-API-KEY --value "sk-your-openai-key"

az keyvault secret set --vault-name nanobot-kv \
  --name OPENROUTER-API-KEY --value "sk-or-your-key"

az keyvault secret set --vault-name nanobot-kv \
  --name ANTHROPIC-API-KEY --value "sk-ant-your-key"

az keyvault secret set --vault-name nanobot-kv \
  --name TELEGRAM-BOT-TOKEN --value "123456:ABC-your-token"

az keyvault secret set --vault-name nanobot-kv \
  --name GROQ-API-KEY --value "gsk_your-key"
```

> **Note:** Key Vault secret names use dashes (not underscores). We'll map them to env vars with underscores when deploying.

Only create secrets for the providers you actually use.

---

## Step 5: Deploy to Azure Container Apps

**Why:** Azure Container Apps is the simplest way to run a container on Azure. Like Cloud Run, it's serverless, auto-scales, provides HTTPS, and manages infrastructure for you. It's ideal for always-on bots.

### 5a. Create the Container Apps Environment

```bash
# Create the Container Apps environment
az containerapp env create \
  --resource-group nanobot-rg \
  --name nanobot-env \
  --location eastus
```

### 5b. Enable ACR access

```bash
# Enable admin access on the registry (simplest approach)
az acr update --name nanobotcr --admin-enabled true

# Get the credentials
ACR_PASSWORD=$(az acr credential show --name nanobotcr --query "passwords[0].value" -o tsv)
```

### 5c. Deploy the container

```bash
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
  --cpu 1.0 \
  --memory 2.0Gi \
  --min-replicas 1 \
  --max-replicas 1 \
  --env-vars \
    "OPENAI_API_KEY=secretref:openai-api-key" \
    "OPENROUTER_API_KEY=secretref:openrouter-api-key" \
    "ANTHROPIC_API_KEY=secretref:anthropic-api-key" \
    "TELEGRAM_BOT_TOKEN=secretref:telegram-bot-token" \
    "GROQ_API_KEY=secretref:groq-api-key" \
  --secrets \
    "openai-api-key=keyvaultref:https://nanobot-kv.vault.azure.net/secrets/OPENAI-API-KEY,identityref:system" \
    "openrouter-api-key=keyvaultref:https://nanobot-kv.vault.azure.net/secrets/OPENROUTER-API-KEY,identityref:system" \
    "anthropic-api-key=keyvaultref:https://nanobot-kv.vault.azure.net/secrets/ANTHROPIC-API-KEY,identityref:system" \
    "telegram-bot-token=keyvaultref:https://nanobot-kv.vault.azure.net/secrets/TELEGRAM-BOT-TOKEN,identityref:system" \
    "groq-api-key=keyvaultref:https://nanobot-kv.vault.azure.net/secrets/GROQ-API-KEY,identityref:system"
```

> **If Key Vault references are complex**, you can use plain secrets instead:

```bash
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
  --cpu 1.0 \
  --memory 2.0Gi \
  --min-replicas 1 \
  --max-replicas 1 \
  --secrets \
    "openai-api-key=$(az keyvault secret show --vault-name nanobot-kv --name OPENAI-API-KEY --query value -o tsv)" \
    "openrouter-api-key=$(az keyvault secret show --vault-name nanobot-kv --name OPENROUTER-API-KEY --query value -o tsv)" \
    "anthropic-api-key=$(az keyvault secret show --vault-name nanobot-kv --name ANTHROPIC-API-KEY --query value -o tsv)" \
    "telegram-bot-token=$(az keyvault secret show --vault-name nanobot-kv --name TELEGRAM-BOT-TOKEN --query value -o tsv)" \
    "groq-api-key=$(az keyvault secret show --vault-name nanobot-kv --name GROQ-API-KEY --query value -o tsv)" \
  --env-vars \
    "OPENAI_API_KEY=secretref:openai-api-key" \
    "OPENROUTER_API_KEY=secretref:openrouter-api-key" \
    "ANTHROPIC_API_KEY=secretref:anthropic-api-key" \
    "TELEGRAM_BOT_TOKEN=secretref:telegram-bot-token" \
    "GROQ_API_KEY=secretref:groq-api-key"
```

| Flag | Why |
|------|-----|
| `--min-replicas 1` | Keeps container always running (required for Telegram polling) |
| `--max-replicas 1` | Only 1 instance (bot can only have 1 active poller) |
| `--cpu 1.0 --memory 2.0Gi` | Enough for Python + Node.js bridge |
| `--ingress external` | Makes the gateway accessible via HTTPS URL |
| `--secrets` + `--env-vars` | Injects secrets securely as environment variables |

---

## Step 6: Verify Deployment

```bash
# Get the app URL
az containerapp show \
  --resource-group nanobot-rg \
  --name nanobot \
  --query "properties.configuration.ingress.fqdn" -o tsv

# Check logs
az containerapp logs show \
  --resource-group nanobot-rg \
  --name nanobot \
  --tail 50

# Test the gateway
APP_URL=$(az containerapp show --resource-group nanobot-rg --name nanobot --query "properties.configuration.ingress.fqdn" -o tsv)
curl "https://${APP_URL}/api/status"
```

If Telegram is enabled, send a message to your bot — it should respond!

---

## Step 7: Set Up Continuous Deployment (Optional)

**Why:** Automatically deploy when you push to `main`, so you never manually build again.

Add `.github/workflows/deploy-azure.yml`:

```yaml
name: Deploy to Azure

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Login to Azure
        uses: azure/login@v2
        with:
          creds: ${{ secrets.AZURE_CREDENTIALS }}

      - name: Login to ACR
        run: az acr login --name nanobotcr

      - name: Build and push image
        run: |
          az acr build \
            --registry nanobotcr \
            --image nanobot:${{ github.sha }} \
            --image nanobot:latest \
            --file Dockerfile \
            . \
            --timeout 1200

      - name: Deploy to Container Apps
        run: |
          az containerapp update \
            --resource-group nanobot-rg \
            --name nanobot \
            --image nanobotcr.azurecr.io/nanobot:${{ github.sha }}
```

To create the `AZURE_CREDENTIALS` secret:

```bash
# Create a service principal for GitHub Actions
az ad sp create-for-rbac \
  --name "github-nanobot-deploy" \
  --role contributor \
  --scopes /subscriptions/$(az account show --query id -o tsv)/resourceGroups/nanobot-rg \
  --json-auth
```

Copy the JSON output and add it as a GitHub secret named `AZURE_CREDENTIALS`.

---

## Step 8: Custom Domain & SSL (Optional)

**Why:** Replace the auto-generated `*.azurecontainerapps.io` URL with your own domain.

```bash
# Add a custom domain
az containerapp hostname add \
  --resource-group nanobot-rg \
  --name nanobot \
  --hostname bot.yourdomain.com

# Bind a managed SSL certificate (free)
az containerapp hostname bind \
  --resource-group nanobot-rg \
  --name nanobot \
  --hostname bot.yourdomain.com \
  --certificate managed
```

Then add a CNAME record in your DNS:
```
bot.yourdomain.com → <your-app>.azurecontainerapps.io
```

---

## Cost Estimate

| Resource | Spec | Monthly Cost |
|----------|------|-------------|
| Container Apps (1 vCPU, 2GB, always-on) | min-replicas=1 | ~$35-45 |
| Azure Container Registry (Basic) | 10 GB | ~$5 |
| Key Vault | 5-6 secrets | ~$0.03 |
| ACR Tasks (cloud builds) | ~10 builds/month | ~$0.50 |
| **Total** | | **~$40-50/month** |

> **Cost saving tip:** Use `--cpu 0.5 --memory 1.0Gi` if you only need Telegram (no WhatsApp), dropping to ~$20-25/month.

---

## Alternative: Azure Container Instances (ACI)

If Container Apps feels like overkill, ACI is even simpler (but fewer features):

```bash
az container create \
  --resource-group nanobot-rg \
  --name nanobot \
  --image nanobotcr.azurecr.io/nanobot:latest \
  --registry-login-server nanobotcr.azurecr.io \
  --registry-username nanobotcr \
  --registry-password "$ACR_PASSWORD" \
  --cpu 1 \
  --memory 2 \
  --ports 18790 \
  --ip-address Public \
  --restart-policy Always \
  --environment-variables \
    OPENAI_API_KEY="sk-..." \
    TELEGRAM_BOT_TOKEN="123456:ABC..."
```

Cost: ~$30/month for 1 vCPU, 2 GB. Simpler but no auto-scaling, no managed SSL, and secrets are in plain environment variables.

---

## Alternative: Azure VM

For maximum control (persistent disk, SSH access):

```bash
# Create a VM
az vm create \
  --resource-group nanobot-rg \
  --name nanobot-vm \
  --image Ubuntu2204 \
  --size Standard_B1ms \
  --admin-username azureuser \
  --generate-ssh-keys

# SSH in
az ssh vm --resource-group nanobot-rg --name nanobot-vm

# On the VM: install Docker, pull and run
sudo apt update && sudo apt install -y docker.io
sudo docker run -d --restart always \
  -e OPENAI_API_KEY="sk-..." \
  -e TELEGRAM_BOT_TOKEN="123456:ABC..." \
  nanobotcr.azurecr.io/nanobot:latest gateway
```

Cost: Standard_B1ms is ~$13/month (1 vCPU, 2 GB RAM).

---

## GCP vs Azure Comparison

| Feature | GCP (Cloud Run) | Azure (Container Apps) |
|---------|-----------------|----------------------|
| **Setup complexity** | Simpler | Slightly more steps |
| **Secrets** | Secret Manager | Key Vault |
| **Container registry** | Artifact Registry | ACR |
| **Always-on cost** | ~$30-40/month | ~$40-50/month |
| **VM alternative** | e2-small ~$13/mo | B1ms ~$13/mo |
| **Free tier** | 2M req/month free | 180K vCPU-s/month free |
| **Best for** | Simple deployments | Azure AD / Teams integration |

---

## Quick Reference

```bash
# Build & deploy (one command)
az acr build --registry nanobotcr --image nanobot:latest . && \
az containerapp update --resource-group nanobot-rg --name nanobot \
  --image nanobotcr.azurecr.io/nanobot:latest

# View logs
az containerapp logs show --resource-group nanobot-rg --name nanobot --tail 100

# Update a secret
az keyvault secret set --vault-name nanobot-kv \
  --name OPENAI-API-KEY --value "new-key-value"

# Restart (picks up new secrets)
az containerapp revision restart --resource-group nanobot-rg --name nanobot
```
