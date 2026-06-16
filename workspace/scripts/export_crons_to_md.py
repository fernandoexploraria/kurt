import sys
import os

# Add the scripts directory to the path to import the original CRONS list
sys.path.append('/root/.openclaw/workspace/scripts')
from setup_fleet_crons import CRONS

output_path = "/root/.openclaw/workspace/memory/cron_architecture.md"

def export_to_markdown():
    with open(output_path, "w") as f:
        f.write("# OpenClaw Fleet: Cron Architecture\n\n")
        f.write("This document outlines the automated schedules, logic, and prompts that drive the AI Portfolio Manager's architecture.\n\n")
        
        for cron in CRONS:
            f.write(f"## {cron['name']}\n")
            f.write(f"* **Schedule:** `{cron['expr']}` ({cron['tz']})\n")
            f.write(f"* **Thinking Budget:** `{cron['thinking']}`\n")
            f.write(f"* **Agent Payload / Instructions:**\n")
            
            # Format payload as a blockquote for clean markdown rendering
            payload_lines = cron['payload'].split('\n')
            for line in payload_lines:
                f.write(f"> {line}\n")
            
            f.write("\n---\n\n")

if __name__ == "__main__":
    export_to_markdown()
    print(f"✅ Successfully exported {len(CRONS)} crons to {output_path}")