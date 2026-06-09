#!/usr/bin/env python3
import subprocess
import json
import sys

print("==================================================")
print("🚀 OPENCLAW FLEET PROVISIONING: CRON INJECTION")
print("==================================================")

# A simplified dictionary of the core Engine crons to prevent configuration drift
# This acts as the "Single Source of Truth" for the Git repository.

CRONS = [
    {
        "name": "25-Minute Schwab Token Refresh",
        "expr": "*/25 * * * *",
        "tz": "America/Mexico_City",
        "payload": "Run `python3 ~/.openclaw/workspace/scripts/schwab_refresh.py`. CRITICAL INSTRUCTION: You must call the exec tool with `elevated: false`. Do not ask for approval. If it successfully finishes, output NO_REPLY. If there is an error, forward the exact error to the user.",
        "thinking": "low"
    },
    {
        "name": "Target Price Calibration - Global Portfolio",
        "expr": "0 1 * * 1-5",
        "tz": "America/Mexico_City",
        "payload": "### TARGET PRICE CALIBRATION PROTOCOL START (V2 ARCHITECTURE)\n1. SCRIPT EXECUTION: Run `python3 ~/.openclaw/workspace/scripts/calibrate_targets_v3.py`. Wait for the script to finish. It will automatically calculate dynamic Ceilings (using Wall Street targets or ATR fallbacks) and hard Stop-Loss Floors (using the Ratchet logic), adjust for Quiver data, update the spreadsheet, and output a raw list of the 'TOP_3_ACTIONABLE' tickers and 'THE_REST'.\n2. NARRATIVE (LAYER 5): For ONLY the 'TOP_3_ACTIONABLE' tickers output by the script, use the `tavily_search` MCP tool with a 24-hour time filter to quickly find recent news/catalysts to understand their momentum.\n3. PERSISTENCE: Before you finish, use the `write` tool to save your raw target list and final narrative into the file `memory/intel_daily.md`. This must completely overwrite yesterday's data.\n4. DELIVERY: Output your final Synthesis report as your normal text response. Do NOT use any messaging tools. The system will automatically forward your final output to the user. \n**CRITICAL: Your final output MUST be under 3,500 characters to fit in a single Telegram message. Provide detailed 'Angela-Ready' briefs ONLY for the Top 3 most actionable tickers identified by the script. For all remaining tickers in 'THE_REST', simply provide a clean bulleted list of [Ticker] - Ceiling: $[Target] | Floor: $[Floor].**",
        "thinking": "high"
    },
    {
        "name": "Trailing Radar Builder (2 AM)",
        "expr": "0 2 * * 1-5",
        "tz": "America/Mexico_City",
        "payload": "Run `~/.openclaw/workspace/quant_env/bin/python3 ~/.openclaw/workspace/scripts/build_radar.py`. If successful, output exactly NO_REPLY. If it fails, output the error.",
        "thinking": "low"
    },
    {
        "name": "Entry Price Calibration - Global Portfolio",
        "expr": "0 3 * * 1-5",
        "tz": "America/Mexico_City",
        "payload": "### ENTRY PRICE CALIBRATION PROTOCOL START (V7 UNIFIED ARCHITECTURE)\n1. SCRIPT EXECUTION: Run `python3 ~/.openclaw/workspace/scripts/calibrate_watchlist_v7.py`. Wait for the script to finish. It will do all the math (including calculating live ATRs from scratch), adjust for Quiver data, update the spreadsheet, and output a raw list of the 'TOP_3_ACTIONABLE' tickers and 'THE_REST'.\n2. NARRATIVE (LAYER 5): For ONLY the 'TOP_3_ACTIONABLE' tickers output by the script, use the `web_search` tool to quickly find recent news/catalysts to understand why they are at those levels.\n3. PERSISTENCE: Before you finish, use the `read` tool to read the current contents of `memory/intel_daily.md`. Then, use the `write` tool to save the file again, appending your Entry Calibration report below the Target Calibration section.\n4. DELIVERY: Output your final Synthesis report as your normal text response. Do NOT use any messaging tools. The system will automatically forward your final output to the user. \n**CRITICAL: Your final output MUST be under 3,500 characters to fit in a single Telegram message. Provide detailed 'Angela-Ready' briefs ONLY for the Top 3 most actionable tickers identified by the script. For all remaining tickers in 'THE_REST', simply provide a clean bulleted list of [Ticker] - [Target Price].**",
        "thinking": "high"
    },
    {
        "name": "4 AM Quiver Conviction Shield",
        "expr": "0 4 * * *",
        "tz": "America/Mexico_City",
        "payload": "Run `python3 ~/.openclaw/workspace/scripts/quiver_shield.py`. CRITICAL INSTRUCTION: You must call the exec tool with `elevated: false`. Do not ask for approval. If it successfully finishes, output NO_REPLY. If there is an error, forward the exact error to the user.",
        "thinking": "low"
    },
    {
        "name": "Morning Briefing - Full Arsenal Audit",
        "expr": "0 5 * * 1-5",
        "tz": "America/Mexico_City",
        "payload": "### MORNING BRIEFING PROTOCOL START (SCRIPTED ARCHITECTURE)\n1. SCRIPT EXECUTION: Run `python3 ~/.openclaw/workspace/scripts/prep_morning_briefing.py`. Wait for the script to finish. It will analyze live prices against your targets, check TradingView technical momentum, check Quiver for insider dumping, and output a raw map divided into HARVEST ZONE, DANGER ZONE, and STABLE BEDROCK.\n2. NARRATIVE (LAYER 5): Read the raw map generated by the script. For ONLY the tickers listed in the HARVEST ZONE and DANGER ZONE, use the `tavily_search` MCP tool with a 24-hour time filter to find recent news/catalysts explaining their movement. Do not search the Stable Bedrock tickers. Synthesize this into an \"Angela-Ready\" narrative.\n3. PERSISTENCE: Before you finish, use the `read` tool to read the current contents of `memory/intel_daily.md`. Then, use the `write` tool to save the file again, appending your final 5AM synthesis to the bottom of the document.\n4. DELIVERY: Output your final Synthesis report as your normal text response. Do NOT use any messaging tools. The system will automatically forward your final output to the user.\n**CRITICAL: Your final output MUST be under 3,500 characters to fit in a single Telegram message. Provide detailed 'Angela-Ready' briefs ONLY for the Harvest and Danger zone tickers. For the Stable Bedrock, just provide a clean, simple bulleted list.**",
        "thinking": "high"
    },
    {
        "name": "1-Minute Limit Sniper",
        "expr": "* 7-13 * * 1-5",
        "tz": "America/Mexico_City",
        "payload": "Run `python3 ~/.openclaw/workspace/scripts/buy_limit_sniper.py`. CRITICAL INSTRUCTION: You must call the exec tool with `elevated: false`. Do not ask for approval. If the output is exactly NO_REPLY, you must output exactly NO_REPLY and absolutely nothing else. If it prints an alert, forward the exact alert message to the user.",
        "thinking": "low"
    },
    {
        "name": "1-Minute Intraday Trailing Sniper",
        "expr": "* 7-13 * * 1-5",
        "tz": "America/Mexico_City",
        "payload": "Run `python3 ~/.openclaw/workspace/scripts/intraday_sniper.py`. CRITICAL INSTRUCTION: You must call the exec tool with `elevated: false`. Do not ask for approval. If the output is exactly NO_REPLY, you must output exactly NO_REPLY and absolutely nothing else. If it prints an alert, forward the exact alert message to the user.",
        "thinking": "low"
    },
    {
        "name": "Weekly Full Arsenal Audit",
        "expr": "0 4 * * 6",
        "tz": "America/Mexico_City",
        "payload": "Run `python3 /root/.openclaw/workspace/batch_arsenal.py > /tmp/arsenal.log`. Then read the last few lines of the log to extract the Google Doc link. Reply to Telegram WITH ONLY THE GOOGLE DOC LINK and a short greeting. DO NOT print the raw contents of the report in the chat, because it will exceed Telegram's character limits and cause the delivery to fail.",
        "thinking": "low"
    }
]

def inject_crons():
    print("Beginning automated deployment of OpenClaw Fleet Crons...")
    
    for cron in CRONS:
        print(f"\n⏳ Injecting: {cron['name']}")
        
        # We write the payload to a temporary file to avoid complex bash escaping issues
        with open("/tmp/payload.txt", "w") as f:
            f.write(cron["payload"])
            
        # Construct the OpenClaw CLI command
        # Note: In a true deployment, you would want to add logic here to check if the cron already exists 
        # and use 'cron update' instead of 'cron add' to avoid duplicates.
        cmd = [
            "openclaw", "cron", "add",
            "--name", cron["name"],
            "--expr", cron["expr"],
            "--tz", cron["tz"],
            "--thinking", cron["thinking"],
            "--message", f"$(cat /tmp/payload.txt)"
        ]
        
        # Join the command to evaluate the cat subshell properly
        full_cmd = " ".join(cmd)
        
        try:
            result = subprocess.run(full_cmd, shell=True, capture_output=True, text=True)
            if result.returncode == 0:
                print(f"✅ Success! Scheduled for {cron['expr']}")
            else:
                print(f"❌ Failed to inject {cron['name']}: {result.stderr}")
        except Exception as e:
            print(f"❌ Error: {str(e)}")

    print("\n==================================================")
    print("✅ DEPLOYMENT COMPLETE. The AI Fleet is now armed.")
    print("==================================================")

if __name__ == "__main__":
    # Safety Check
    verify = input("⚠️ WARNING: This will inject new crons into the OpenClaw database. Proceed? (y/n): ")
    if verify.lower() == 'y':
        inject_crons()
    else:
        print("Aborted.")
