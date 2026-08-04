# Heartbeat Tasks for Kurt (AI Portfolio Manager)

- [ ] **Target Acquisition:** Read the cached targets from `/root/.openclaw/workspace/memory/top_5_cache.txt`.
- [ ] **Market Alert (Periodic):** Use the `batchGetQuote` MCP tool to instantly pull the live prices for the Top 5 tickers in a single call. Check for unusual volatility.
- [ ] **Spreadsheet Health:** Briefly verify that the 'Simulator V3' sheet is accessible via `gog`.

## Rules
- Only alert on Telegram if there is a significant event (e.g., price swing > 3% or major political trade > $50k or involving key committee members).
- If a >3% price swing is triggered, automatically run a `tavily` web search to find the news catalyst BEFORE sending the Telegram alert, so you can include the "Why" in your message.
- Otherwise, log silently in `memory/heartbeat-state.json`.
- IF there are no alerts triggered, your entire output to the session must be exactly: NO_REPLY