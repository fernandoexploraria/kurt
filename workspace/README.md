# Kurt: The Autonomous AI Portfolio Manager

Welcome to the fleet. This repository contains the "Brain" of an autonomous, quantitative AI trading architecture built on OpenClaw, Docker, and Google Cloud Platform (GCP).

The system is designed to execute institutional-grade algorithmic trading. It synthesizes real-time market data, technical indicators, Wall Street consensus, dark pool indices, and congressional trading sentiment to execute perfectly timed Limit and Trailing Stop orders directly via the Charles Schwab API.

---

## 🚀 Fleet Provisioning Guide

Follow these exact steps to breathe life into a blank clone of this repository.

### Step 1: The Vault (`.env`)
**NEVER COMMIT YOUR SECRETS TO GIT.**
Before booting the engine, you must create a local `.env` file on your host machine to store your specific API keys. The system requires the following variables:

```env
# Schwab API (Trading Execution)
SCHWAB_APP_KEY=your_app_key_here
SCHWAB_APP_SECRET=your_app_secret_here

# Market Data & Quantitative APIs
RAPIDAPI_KEY=your_rapidapi_key_here
QUIVER_API_KEY=your_quiver_key_here

# Search & LLM Engines
TAVILY_API_KEY=your_tavily_key_here
GOOGLE_API_KEY=your_google_key_here

# Optional: Spotify (DJ-Kurt)
SPOTIFY_CLIENT_ID=your_spotify_client_id
SPOTIFY_CLIENT_SECRET=your_spotify_client_secret
SPOTIFY_REFRESH_TOKEN=your_spotify_refresh_token
```
*Ensure your `docker-compose.yml` explicitly passes these environment variables into the OpenClaw container.*

### Step 2: The Boot Sequence
Once your `.env` vault is locked and loaded, start the containerized engine from your VM terminal:
```bash
docker compose up -d
```

### Step 3: The Google Sheets Dashboard (GOG CLI)
The AI uses a Google Spreadsheet as its visual dashboard and ledger. You must authenticate the `gog` CLI to give the container access to Google Drive/Sheets:
```bash
gog auth credentials /path/to/your/client_secret.json
gog auth add your.email@gmail.com --services sheets,drive
```
*Note: Make sure to update the `TEST_SHEET_ID` variable in the Python scripts to point to your newly cloned version of the Google Sheet!*

### Step 4: The Schwab Handshake
You must physically authorize the AI to trade on your behalf via OAuth 2.0. Run the authentication script and follow the terminal prompts to log into Schwab securely:
```bash
python3 workspace/scripts/schwab_auth.py
```
This will generate the required permanent `schwab_tokens.json` file inside your protected `memory/` folder.

### Step 5: The Heartbeat (Cron Injection)
A blank clone has the brain but no heartbeat. You must inject the automated schedules (the 1-Minute Snipers, the nightly calibrations, and the Morning Briefing) into the local OpenClaw database.
Run the provisioning script:
```bash
python3 workspace/scripts/setup_fleet_crons.py
```
*(Type `y` to confirm the injection).*

---

## 🛡️ Security Posture & Architecture
*   **Stateless Code:** All Python scripts are designed to fetch keys exclusively from `os.environ.get()`. There are zero hardcoded passwords in this repository.
*   **Git Ignore:** The `.gitignore` is specifically configured to block the `memory/` folder. This ensures your live trading ledgers, pending orders, and trailing stops are completely insulated from code updates.
*   **Zero Trust Networking:** Ensure your VM firewall blocks inbound traffic to the OpenClaw gateway. Route access exclusively via secure outbound Cloudflare Tunnels or Tailscale.
*   **Fleet Updates:** Pull from the `main` branch to instantly update the "Brain" (scripts, skills, and prompts) without overwriting your local "State" (ledgers and keys).