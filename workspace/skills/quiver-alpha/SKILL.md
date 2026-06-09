---
name: quiver-alpha
description: Next-generation Quiver Quantitative API engine. Connects directly to the raw /beta/ endpoints for high-speed, lightweight data retrieval without heavy Pandas dependencies.
---

# Quiver-Alpha (Advanced Alternative Data)

This skill provides direct, lightweight access to the raw Quiver Quantitative API. It is designed to be faster than the legacy `quiver` skill and exposes advanced endpoints for Institutional Whales, Dark Pools, and global market pulses.

## Prerequisites
- **API Token:** Uses the `QUIVER_API_KEY` defined in `TOOLS.md` or the environment.

## Commands

All commands output raw JSON which can be piped to `jq` or parsed by the agent.

### 1. Dark Pools (Off-Exchange Activity)
Track hidden, off-exchange block trading by institutions to detect massive accumulation or distribution before it impacts the public price.
```bash
python3 skills/quiver-alpha/scripts/fetch.py darkpool NVDA
```

### 2. Institutional Whales (Top Shareholders)
Identify the massive hedge funds and asset managers holding specific stocks.
```bash
python3 skills/quiver-alpha/scripts/fetch.py whales COST
```

### 3. Global Congress Pulse
High-speed heartbeat check. Returns the absolute newest congressional trades across the *entire* market in one call.
```bash
python3 skills/quiver-alpha/scripts/fetch.py pulse
```

### 4. Smart Money Moves (SEC 13F)
Track recent SEC 13F portfolio changes to see if major funds are buying or selling a specific ticker.
```bash
python3 skills/quiver-alpha/scripts/fetch.py sec13f VRT
```

### 5. Corporate Insiders
Track C-Suite and Board Member stock transactions (Form 4).
```bash
python3 skills/quiver-alpha/scripts/fetch.py insiders AAPL
```