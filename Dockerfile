FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl bash git nodejs npm build-essential tini \
    && rm -rf /var/lib/apt/lists/*

# Install Hermes Agent, pinned to the exact commit this deployment is tested against.
# An unpinned install here pulls whatever is newest on `main` at build time - that's what
# broke the Telegram adapter on 2026-08-14 (a rebuild silently picked up a new commit with a
# startup race bug). Pinning makes every rebuild reproducible. Update this SHA deliberately,
# not by accident, and only after testing the new commit.
RUN curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --skip-setup --commit 56a41715dc3b8bf6f50a740ff9416c4036ef4259
ENV PATH="/root/.local/bin:${PATH}"

# Hermes v0.20.1 pins python-telegram-bot 22.8, a confirmed upstream regression
# breaking Telegram connect entirely ("Any cannot be instantiated" -
# NousResearch/hermes-agent#85272). The obvious fix - pinning 22.6 here at
# build time - does not stick: something in Hermes's own startup silently
# re-resolves it back to 22.8 before the gateway ever runs. The real fix lives
# in entrypoint.sh instead, applied last, right before the gateway starts.

WORKDIR /app
COPY . .

# shop_backend + shop_mcp_server dependencies
RUN pip install --no-cache-dir -r requirements.txt

RUN hermes --version

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/entrypoint.sh"]
