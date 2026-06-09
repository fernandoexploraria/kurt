FROM ghcr.io/openclaw/openclaw:2026.5.20

#  Switch to root to install system packages
USER root

# Install Python and Pip (handles both Debian and Alpine based images)
RUN apt-get update && apt-get install -y python3 python3-pip || apk add --no-cache python3 py3-pip

# Now that pip is installed, install the specific Quant tools
RUN pip3 install yfinance pandas --break-system-packages || pip3 install yfinance pandas

# Install the summarize CLI tool globally via npm so OpenClaw skills can use it
RUN npm install -g @steipete/summarize
