# Multi-Tenant nanobot: Running Multiple Instances on a Single VM

**Serve multiple clients from one Azure VM with 24/7 availability and zero-downtime updates.**

---

## Table of Contents

1. [Why Multi-Tenant?](#1-why-multi-tenant)
2. [Architecture](#2-architecture)
3. [VM Sizing](#3-vm-sizing)
4. [Setting Up Multiple Instances](#4-setting-up-multiple-instances)
5. [nginx: Unified Entry Point](#5-nginx-unified-entry-point)
6. [Monitoring & Resource Limits](#6-monitoring--resource-limits)
7. [Zero-Downtime Update Strategy](#7-zero-downtime-update-strategy)
8. [Backup & Recovery](#8-backup--recovery)
9. [Client Onboarding Checklist](#9-client-onboarding-checklist)

---

## 1. Why Multi-Tenant?

Your current VM uses **4.5 minutes of CPU over 5 days** — that's **0.06% utilization**. You're paying for a machine that's idle 99.94% of the time.

```
Current: 1 client, 1 VM
┌─────────────────────────────┐
│ Standard_B1ms ($13/month)   │
│                             │
│  ██░░░░░░░░░░░░ 0.06% CPU  │
│  ████████░░░░░░ 200MB / 2GB │
│                             │
│  Wasted: 99.94% CPU, 1.8GB │
└─────────────────────────────┘

Multi-tenant: 5 clients, 1 VM
┌─────────────────────────────┐
│ Standard_B2ms ($26/month)   │
│                             │
│  ██░░░░░░░░░░░░ ~1% CPU    │
│  ████████████░░ 1GB / 4GB   │
│                             │
│  Cost per client: ~$5/month │
└─────────────────────────────┘
```

| Metric | Single Tenant | 5 Clients on 1 VM |
|--------|---------------|-------------------|
| VM cost | $13/month | $26/month |
| **Per-client cost** | **$13** | **$5.20** |
| CPU util | 0.06% | ~1% |
| Memory util | 200MB / 2GB | ~1GB / 4GB |

**Why it works:** nanobot is mostly **I/O-bound** (waiting for Telegram polls and LLM API responses), not CPU-bound. A single VM can easily handle 5-10 clients because they're almost never processing simultaneously.

---

## 2. Architecture

```
                            Azure VM (Standard_B2ms)
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  nginx (:80/:443)                                            │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ /client-a/api → localhost:18791                        │  │
│  │ /client-b/api → localhost:18792                        │  │
│  │ /client-c/api → localhost:18793                        │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐               │
│  │ nanobot    │ │ nanobot    │ │ nanobot    │               │
│  │ instance-a │ │ instance-b │ │ instance-c │  ...          │
│  │            │ │            │ │            │               │
│  │ Port 18791 │ │ Port 18792 │ │ Port 18793 │               │
│  │ Bot: @a_bot│ │ Bot: @b_bot│ │ Bot: @c_bot│               │
│  │ Config: a/ │ │ Config: b/ │ │ Config: c/ │               │
│  └────────────┘ └────────────┘ └────────────┘               │
│                                                              │
│  ┌─────────────────────────────────────────┐                 │
│  │ Shared code: /opt/nanobot (git repo)    │                 │
│  │ Shared venv: /opt/nanobot/.venv         │                 │
│  └─────────────────────────────────────────┘                 │
│                                                              │
│  /srv/nanobot/                                               │
│  ├── client-a/   (config, workspace, memory)                 │
│  ├── client-b/                                               │
│  └── client-c/                                               │
└──────────────────────────────────────────────────────────────┘
```

**Key design decisions:**

| Decision | Why |
|----------|-----|
| **Shared code, separate data** | One git repo to update; each client has isolated config and workspace |
| **Separate systemd services** | Start/stop/restart per client independently |
| **One Telegram bot per client** | Each client has their own @BotFather bot with a unique token |
| **Sequential port numbers** | Easy to manage: 18791, 18792, 18793... |

---

## 3. VM Sizing

| Clients | VM Size | vCPU | RAM | Monthly Cost | Per-Client |
|---------|---------|------|-----|-------------|------------|
| 1-3 | Standard_B1ms | 1 | 2 GB | ~$13 | $4-13 |
| 4-8 | Standard_B2ms | 2 | 4 GB | ~$26 | $3-7 |
| 9-15 | Standard_B2s_v2 | 2 | 8 GB | ~$40 | $3-4 |
| 16-25 | Standard_B4ms | 4 | 16 GB | ~$70 | $3-4 |

**Rule of thumb:** Each nanobot instance uses ~150-200MB RAM at idle. Budget ~200MB per client plus 500MB for the OS and nginx.

To upgrade your existing VM:

```bash
# Stop the VM first
az vm deallocate --resource-group nanobot-rg --name nanobot-vm

# Resize
az vm resize --resource-group nanobot-rg --name nanobot-vm \
  --size Standard_B2ms

# Start it back
az vm start --resource-group nanobot-rg --name nanobot-vm
```

---

## 4. Setting Up Multiple Instances

### 4.1 Create the Shared Code Directory

```bash
# On the VM:

# Move code to a shared location
sudo mkdir -p /opt/nanobot
sudo git clone https://github.com/taocao/nanobot.git /opt/nanobot
cd /opt/nanobot

# Create shared virtual environment
sudo python3.12 -m venv .venv
sudo /opt/nanobot/.venv/bin/pip install -e ".[ui]"

# Build WhatsApp bridge (if needed)
cd bridge && sudo npm install && sudo npm run build && cd ..

# Create the data directory structure
sudo mkdir -p /srv/nanobot
```

### 4.2 Create a Client Setup Script

This script automates adding a new client:

```bash
sudo tee /opt/nanobot/scripts/add-client.sh << 'SCRIPT'
#!/bin/bash
set -e

CLIENT_NAME="$1"
PORT="$2"
TELEGRAM_TOKEN="$3"
OPENAI_KEY="$4"

if [ -z "$CLIENT_NAME" ] || [ -z "$PORT" ] || [ -z "$TELEGRAM_TOKEN" ] || [ -z "$OPENAI_KEY" ]; then
    echo "Usage: add-client.sh <name> <port> <telegram_token> <openai_key>"
    echo "Example: add-client.sh client-a 18791 '123:ABC' 'sk-xxx'"
    exit 1
fi

CLIENT_DIR="/srv/nanobot/${CLIENT_NAME}"
echo "=== Setting up client: ${CLIENT_NAME} on port ${PORT} ==="

# 1. Create client directory structure
mkdir -p "${CLIENT_DIR}"/{workspace,memory,logs}

# 2. Generate config
cat > "${CLIENT_DIR}/config.json" << ENDCONFIG
{
  "agents": {
    "defaults": {
      "workspace": "${CLIENT_DIR}/workspace",
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
      "token": "${TELEGRAM_TOKEN}",
      "allowFrom": []
    }
  },
  "providers": {
    "openai": { "apiKey": "${OPENAI_KEY}" }
  },
  "gateway": { "host": "127.0.0.1", "port": ${PORT} },
  "tools": {
    "exec": { "timeout": 60 },
    "restrictToWorkspace": true
  }
}
ENDCONFIG

# 3. Create systemd service
cat > "/etc/systemd/system/nanobot-${CLIENT_NAME}.service" << ENDSERVICE
[Unit]
Description=nanobot - ${CLIENT_NAME}
After=network.target
PartOf=nanobot.target

[Service]
Type=simple
ExecStart=/opt/nanobot/.venv/bin/nanobot gateway
Restart=always
RestartSec=10
Environment=HOME=${CLIENT_DIR}
Environment=NANOBOT_CONFIG=${CLIENT_DIR}/config.json
Environment=PATH=/opt/nanobot/.venv/bin:/usr/local/bin:/usr/bin
WorkingDirectory=/opt/nanobot
StandardOutput=journal
StandardError=journal
SyslogIdentifier=nanobot-${CLIENT_NAME}

# Resource limits (prevent one client from hogging the VM)
MemoryMax=512M
CPUQuota=50%

[Install]
WantedBy=nanobot.target
ENDSERVICE

# 4. Enable and start
systemctl daemon-reload
systemctl enable "nanobot-${CLIENT_NAME}"
systemctl start "nanobot-${CLIENT_NAME}"

echo ""
echo "✓ Client ${CLIENT_NAME} created!"
echo "  Config:  ${CLIENT_DIR}/config.json"
echo "  Port:    ${PORT}"
echo "  Service: nanobot-${CLIENT_NAME}"
echo "  Status:  sudo systemctl status nanobot-${CLIENT_NAME}"
echo "  Logs:    sudo journalctl -u nanobot-${CLIENT_NAME} -f"
SCRIPT

sudo chmod +x /opt/nanobot/scripts/add-client.sh
```

### 4.3 Create a systemd Target (Group All Instances)

A **target** lets you start/stop all client services together:

```bash
sudo tee /etc/systemd/system/nanobot.target << 'EOF'
[Unit]
Description=All nanobot instances
After=network.target

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable nanobot.target
```

Now you can:

```bash
# Start all nanobot instances
sudo systemctl start nanobot.target

# Stop all nanobot instances
sudo systemctl stop nanobot.target

# Status of all
sudo systemctl list-units 'nanobot-*'
```

### 4.4 Add Your Clients

```bash
# Client A
sudo /opt/nanobot/scripts/add-client.sh \
  client-a 18791 "BOT_TOKEN_A" "sk-openai-key-a"

# Client B
sudo /opt/nanobot/scripts/add-client.sh \
  client-b 18792 "BOT_TOKEN_B" "sk-openai-key-b"

# Client C
sudo /opt/nanobot/scripts/add-client.sh \
  client-c 18793 "BOT_TOKEN_C" "sk-openai-key-c"

# Verify all running
sudo systemctl list-units 'nanobot-*' --no-pager
```

### 4.5 Handle the Config Path

nanobot reads from `~/.nanobot/config.json` by default. We override `HOME` in the systemd service so each instance reads from its own directory. But you also need to check if nanobot supports a `NANOBOT_CONFIG` environment variable. If not, symlink the config:

```bash
# Alternative: use HOME override (already in the service file)
# The service sets Environment=HOME=/srv/nanobot/client-a
# So ~/.nanobot/config.json resolves to /srv/nanobot/client-a/.nanobot/config.json

# Create the expected directory structure for each client
mkdir -p /srv/nanobot/client-a/.nanobot
cp /srv/nanobot/client-a/config.json /srv/nanobot/client-a/.nanobot/config.json
```

---

## 5. nginx: Unified Entry Point

nginx provides a single entry point with SSL termination, basic auth, and routing to each client's gateway.

### 5.1 Install and Configure

```bash
sudo apt install -y nginx apache2-utils certbot python3-certbot-nginx

# Create a password file for admin access
sudo htpasswd -c /etc/nginx/.htpasswd admin

# Main config
sudo tee /etc/nginx/sites-available/nanobot << 'NGINX'
# Redirect HTTP → HTTPS (uncomment after SSL setup)
# server {
#     listen 80;
#     server_name bot.yourdomain.com;
#     return 301 https://$host$request_uri;
# }

server {
    listen 80;
    # listen 443 ssl;  # Uncomment after SSL setup
    server_name _;

    # SSL (uncomment after certbot setup)
    # ssl_certificate /etc/letsencrypt/live/bot.yourdomain.com/fullchain.pem;
    # ssl_certificate_key /etc/letsencrypt/live/bot.yourdomain.com/privkey.pem;

    # Admin dashboard (password-protected)
    location /admin/ {
        auth_basic "nanobot admin";
        auth_basic_user_file /etc/nginx/.htpasswd;

        # Simple status page
        default_type text/html;
        return 200 '
        <html><head><title>nanobot Admin</title></head>
        <body><h1>nanobot Instances</h1>
        <ul>
          <li><a href="/client-a/api/status">Client A</a></li>
          <li><a href="/client-b/api/status">Client B</a></li>
          <li><a href="/client-c/api/status">Client C</a></li>
        </ul></body></html>';
    }

    # Client A
    location /client-a/api/ {
        proxy_pass http://127.0.0.1:18791/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 300s;
    }

    # Client B
    location /client-b/api/ {
        proxy_pass http://127.0.0.1:18792/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 300s;
    }

    # Client C
    location /client-c/api/ {
        proxy_pass http://127.0.0.1:18793/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 300s;
    }

    # Health check endpoint
    location /health {
        default_type application/json;
        return 200 '{"status": "ok"}';
    }
}
NGINX

sudo ln -sf /etc/nginx/sites-available/nanobot /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

### 5.2 SSL with Let's Encrypt (Free)

```bash
# Point your domain to the VM's IP first, then:
sudo certbot --nginx -d bot.yourdomain.com

# Auto-renew is set up automatically by certbot
```

---

## 6. Monitoring & Resource Limits

### 6.1 Resource Limits per Client (Already in Service File)

```ini
# In each systemd service:
MemoryMax=512M     # Kill if exceeds 512MB (prevents runaway memory)
CPUQuota=50%       # Max 50% of one CPU core
```

### 6.2 Monitoring Script

```bash
sudo tee /opt/nanobot/scripts/status-all.sh << 'SCRIPT'
#!/bin/bash
echo "============================================"
echo " nanobot Multi-Tenant Status"
echo " $(date)"
echo "============================================"
echo ""

printf "%-15s %-10s %-10s %-10s %s\n" \
  "CLIENT" "STATUS" "MEMORY" "CPU" "UPTIME"
printf "%-15s %-10s %-10s %-10s %s\n" \
  "-------" "------" "------" "---" "------"

for service in $(systemctl list-units 'nanobot-*' --no-legend --no-pager | awk '{print $1}'); do
    client=$(echo "$service" | sed 's/nanobot-//;s/\.service//')
    status=$(systemctl is-active "$service" 2>/dev/null || echo "unknown")
    
    if [ "$status" = "active" ]; then
        pid=$(systemctl show -p MainPID --value "$service")
        mem=$(ps -p "$pid" -o rss= 2>/dev/null | awk '{printf "%.0fMB", $1/1024}')
        cpu=$(ps -p "$pid" -o %cpu= 2>/dev/null | awk '{printf "%.1f%%", $1}')
        uptime=$(systemctl show -p ActiveEnterTimestamp --value "$service" | \
          xargs -I{} date -d "{}" +%s 2>/dev/null | \
          awk -v now=$(date +%s) '{
            diff=now-$1;
            d=int(diff/86400); h=int((diff%86400)/3600); m=int((diff%3600)/60);
            if(d>0) printf "%dd %dh", d, h;
            else if(h>0) printf "%dh %dm", h, m;
            else printf "%dm", m}')
        
        printf "%-15s \033[32m%-10s\033[0m %-10s %-10s %s\n" \
          "$client" "$status" "$mem" "$cpu" "$uptime"
    else
        printf "%-15s \033[31m%-10s\033[0m %-10s %-10s %s\n" \
          "$client" "$status" "-" "-" "-"
    fi
done

echo ""
echo "VM Resources:"
echo "  CPU:    $(nproc) cores, $(uptime | awk -F'load average:' '{print $2}' | xargs) load"
echo "  Memory: $(free -h | awk '/Mem:/{printf "%s / %s (%.0f%%)", $3, $2, $3/$2*100}')"
echo "  Disk:   $(df -h / | awk 'NR==2{printf "%s / %s (%s)", $3, $2, $5}')"
SCRIPT

sudo chmod +x /opt/nanobot/scripts/status-all.sh
```

Run with:

```bash
sudo /opt/nanobot/scripts/status-all.sh
```

Output:

```
============================================
 nanobot Multi-Tenant Status
 2026-02-20 10:30:00
============================================

CLIENT          STATUS     MEMORY     CPU        UPTIME
-------         ------     ------     ---        ------
client-a        active     195MB      0.1%       5d 12h
client-b        active     180MB      0.0%       5d 12h
client-c        active     210MB      0.2%       3d 4h

VM Resources:
  CPU:    2 cores,  0.02, 0.01, 0.00 load
  Memory: 950MB / 4.0GB (24%)
  Disk:   3.2GB / 20GB (16%)
```

### 6.3 Automated Monitoring with Cron

```bash
# Log status every hour for trend analysis
(sudo crontab -l 2>/dev/null; echo "0 * * * * /opt/nanobot/scripts/status-all.sh >> /var/log/nanobot-status.log 2>&1") | sudo crontab -

# Alert if any instance goes down (checks every 5 min)
sudo tee /opt/nanobot/scripts/health-check.sh << 'SCRIPT'
#!/bin/bash
for service in $(systemctl list-units 'nanobot-*' --no-legend --no-pager | awk '{print $1}'); do
    if ! systemctl is-active --quiet "$service"; then
        echo "$(date): ALERT - $service is DOWN, attempting restart" >> /var/log/nanobot-alerts.log
        systemctl restart "$service"
    fi
done
SCRIPT

sudo chmod +x /opt/nanobot/scripts/health-check.sh
(sudo crontab -l 2>/dev/null; echo "*/5 * * * * /opt/nanobot/scripts/health-check.sh") | sudo crontab -
```

---

## 7. Zero-Downtime Update Strategy

The most critical part: **updating code without losing any messages.**

### 7.1 How Telegram Handles Downtime

Good news: Telegram **queues messages** for up to 24 hours. If your bot is offline for 30 seconds during an update, no messages are lost — they'll be delivered when the bot reconnects.

```
Client sends message
       │
       ▼
Telegram Queue (stores up to 24h)
       │
       ▼ (bot restarts in ~10 seconds)
nanobot picks up all queued messages
```

### 7.2 Rolling Update Script

This script updates all instances one at a time, verifying each is healthy before moving to the next:

```bash
sudo tee /opt/nanobot/scripts/rolling-update.sh << 'SCRIPT'
#!/bin/bash
set -e

echo "=== nanobot Rolling Update ==="
echo "Started: $(date)"
echo ""

cd /opt/nanobot

# 1. Pull latest code
echo "[1/4] Pulling latest code..."
git fetch origin
git stash  # Save any local changes
git checkout main
git pull origin main
git stash pop 2>/dev/null || true

# 2. Update dependencies
echo "[2/4] Updating dependencies..."
.venv/bin/pip install -e ".[ui]" --quiet

# 3. Build bridge (if needed)
echo "[3/4] Rebuilding WhatsApp bridge..."
cd bridge && npm install --silent && npm run build --silent 2>/dev/null && cd ..

# 4. Rolling restart: one client at a time
echo "[4/4] Rolling restart..."
SERVICES=$(systemctl list-units 'nanobot-*' --no-legend --no-pager | awk '{print $1}')
TOTAL=$(echo "$SERVICES" | wc -w)
COUNT=0

for service in $SERVICES; do
    COUNT=$((COUNT + 1))
    client=$(echo "$service" | sed 's/nanobot-//;s/\.service//')
    
    echo ""
    echo "  [$COUNT/$TOTAL] Restarting ${client}..."
    
    # Restart
    systemctl restart "$service"
    
    # Wait for it to be active
    for i in $(seq 1 30); do
        if systemctl is-active --quiet "$service"; then
            echo "  ✓ ${client} is running"
            break
        fi
        sleep 1
    done
    
    if ! systemctl is-active --quiet "$service"; then
        echo "  ✗ ${client} FAILED TO START — aborting update"
        echo "  Check: journalctl -u $service --since '1 minute ago'"
        exit 1
    fi
    
    # Brief pause between restarts (Telegram re-establishes polling)
    sleep 5
done

echo ""
echo "=== Update complete! ==="
echo "Finished: $(date)"
echo ""
/opt/nanobot/scripts/status-all.sh
SCRIPT

sudo chmod +x /opt/nanobot/scripts/rolling-update.sh
```

Run the update:

```bash
sudo /opt/nanobot/scripts/rolling-update.sh
```

**What happens during the update:**

```
Timeline:
  00:00  Pull code, update pip deps            (all clients still running OLD code)
  00:30  Restart client-a                       (offline ~5s, Telegram queues messages)
  00:35  client-a is healthy ✓                  (catches up on queued messages)
  00:40  Restart client-b                       (client-a already on new code)
  00:45  client-b is healthy ✓
  00:50  Restart client-c
  00:55  client-c is healthy ✓                  All clients on new code!

Total message gap per client: ~5 seconds
Messages lost: 0 (Telegram queues them)
```

### 7.3 Automated Nightly Updates (Optional)

If you want auto-updates:

```bash
# Run rolling update every night at 3 AM UTC
(sudo crontab -l 2>/dev/null; echo "0 3 * * * /opt/nanobot/scripts/rolling-update.sh >> /var/log/nanobot-updates.log 2>&1") | sudo crontab -
```

### 7.4 Rollback

If an update breaks things:

```bash
cd /opt/nanobot

# See recent commits
git log --oneline -10

# Rollback to previous version
git checkout <previous-commit-hash>
sudo .venv/bin/pip install -e ".[ui]" --quiet

# Restart all
sudo systemctl restart nanobot.target
```

---

## 8. Backup & Recovery

### 8.1 What to Back Up

| Data | Location | Frequency |
|------|----------|-----------|
| Client configs | `/srv/nanobot/*/config.json` | On change |
| Conversation memory | `/srv/nanobot/*/memory/` | Daily |
| Workspace files | `/srv/nanobot/*/workspace/` | Daily |
| systemd services | `/etc/systemd/system/nanobot-*` | On change |
| nginx config | `/etc/nginx/sites-available/nanobot` | On change |

### 8.2 Backup Script

```bash
sudo tee /opt/nanobot/scripts/backup.sh << 'SCRIPT'
#!/bin/bash
BACKUP_DIR="/var/backups/nanobot"
DATE=$(date +%Y%m%d)
mkdir -p "$BACKUP_DIR"

# Backup all client data (exclude workspace temp files)
tar czf "${BACKUP_DIR}/clients-${DATE}.tar.gz" \
  --exclude='*.pyc' --exclude='__pycache__' \
  /srv/nanobot/

# Backup configs
tar czf "${BACKUP_DIR}/configs-${DATE}.tar.gz" \
  /etc/systemd/system/nanobot* \
  /etc/nginx/sites-available/nanobot

# Keep only last 7 days
find "$BACKUP_DIR" -name "*.tar.gz" -mtime +7 -delete

echo "$(date): Backup complete → ${BACKUP_DIR}/*-${DATE}.tar.gz"
SCRIPT

sudo chmod +x /opt/nanobot/scripts/backup.sh

# Run daily at 2 AM
(sudo crontab -l 2>/dev/null; echo "0 2 * * * /opt/nanobot/scripts/backup.sh >> /var/log/nanobot-backup.log 2>&1") | sudo crontab -
```

---

## 9. Client Onboarding Checklist

When adding a new client:

### Step 1: Client Creates a Telegram Bot

Have the client:
1. Open Telegram, search for `@BotFather`
2. Send `/newbot`, follow prompts, name it (e.g., `ClientName AI Assistant`)
3. Copy the bot token and send it to you securely

### Step 2: You Allocate Resources

```bash
# Pick the next available port
NEXT_PORT=$(( $(ls /srv/nanobot/ | wc -l) + 18791 ))

# Add the client
sudo /opt/nanobot/scripts/add-client.sh \
  acme-corp $NEXT_PORT "BOT_TOKEN" "OPENAI_KEY"
```

### Step 3: Update nginx

Add a location block for the new client in `/etc/nginx/sites-available/nanobot`:

```nginx
location /acme-corp/api/ {
    proxy_pass http://127.0.0.1:18794/api/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_read_timeout 300s;
}
```

```bash
sudo nginx -t && sudo systemctl reload nginx
```

### Step 4: Verify

```bash
# Check the service
sudo systemctl status nanobot-acme-corp

# Check logs
sudo journalctl -u nanobot-acme-corp -f

# Check all instances
sudo /opt/nanobot/scripts/status-all.sh
```

### Step 5: Client Tests Their Bot

Have them send a message to their Telegram bot. You can monitor the logs in real-time.

---

## Quick Reference

```bash
# === Service Management ===
sudo systemctl status nanobot-client-a       # One client
sudo systemctl list-units 'nanobot-*'        # All clients
sudo systemctl restart nanobot.target        # Restart all
sudo systemctl stop nanobot-client-a         # Stop one client

# === Logs ===
sudo journalctl -u nanobot-client-a -f       # Live logs for one
sudo journalctl -u 'nanobot-*' --since "1h ago"  # All, last hour

# === Operations ===
sudo /opt/nanobot/scripts/status-all.sh      # Dashboard
sudo /opt/nanobot/scripts/rolling-update.sh  # Update all
sudo /opt/nanobot/scripts/add-client.sh ...  # New client
sudo /opt/nanobot/scripts/backup.sh          # Manual backup

# === Remove a client ===
sudo systemctl stop nanobot-client-x
sudo systemctl disable nanobot-client-x
sudo rm /etc/systemd/system/nanobot-client-x.service
sudo systemctl daemon-reload
# Optionally: sudo rm -rf /srv/nanobot/client-x
```

---

## Cost Calculator

| Clients | Recommended VM | Monthly Cost | Per-Client | Savings vs. Separate VMs |
|---------|---------------|-------------|------------|--------------------------|
| 1 | B1ms (1C/2G) | $13 | $13.00 | — |
| 3 | B1ms (1C/2G) | $13 | $4.33 | 67% |
| 5 | B2ms (2C/4G) | $26 | $5.20 | 60% |
| 10 | B2s_v2 (2C/8G) | $40 | $4.00 | 69% |
| 20 | B4ms (4C/16G) | $70 | $3.50 | 73% |
