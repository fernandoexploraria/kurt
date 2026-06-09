# Heartbeat Tasks for Kurt (AI Portfolio Manager)

- [ ] **Target Acquisition:** Use `gog sheets get 1kjzfc6uEzBFtmNjlU1x3TVbHuWPgY7jnNce8mNTe66I "Positions!A:F" --json` to fetch the live portfolio. Identify the **Top 5** tickers by Total Value (Column F).
- [ ] **Market Alert (Periodic):** Use the `batchGetQuote` MCP tool to instantly pull the live prices for the Top 5 tickers in a single call. Check for unusual volatility.
- [ ] **Insider Pulse:** Use `python3 ~/.openclaw/workspace/skills/quiver-alpha/scripts/fetch.py pulse` to execute a single, global market sweep for the absolute newest congressional trades. Cross-reference the output against our active portfolio locally.
- [ ] **Spreadsheet Health:** Briefly verify that the 'Simulator V3' sheet is accessible via `gog`.

## Rules
- Only alert on Telegram if there is a significant event (e.g., price swing > 3% or major political trade > $50k or involving key committee members).
- If a >3% price swing is triggered, automatically run a `tavily` web search to find the news catalyst BEFORE sending the Telegram alert, so you can include the "Why" in your message.
- Otherwise, log silently in `memory/heartbeat-state.json`.
- **DIAGNOSTIC MODE:** Until instructed otherwise, send a brief "Pulse Check" message to Telegram on EVERY single heartbeat execution (e.g., "🤖 Kurt's Heartbeat check complete. No major market anomalies.") so Fer can verify the schedule is running.