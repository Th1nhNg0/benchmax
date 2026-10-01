# The sandbox every attempt runs in: the agent CLIs (pi, Codex, Claude Code) and the toolchain their
# commands use. Changing this file changes the image tag, which is recorded in each run's run.json,
# so results stay traceable to the environment and agent versions.
FROM node:24-bookworm-slim AS node

FROM python:3.12-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      bash build-essential ca-certificates git ripgrep jq bc procps file less \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir numpy sympy pandas networkx

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

ARG PI_VERSION=0.99.2
ARG PI_ANTIGRAVITY_VERSION=0.9.0
ARG CODEX_VERSION=0.159.3
ARG CLAUDE_CODE_VERSION=2.1.286
RUN npm install -g --no-fund --no-audit \
      @earendil-works/pi-coding-agent@${PI_VERSION} \
      pi-antigravity@${PI_ANTIGRAVITY_VERSION} \
      @openai/codex@${CODEX_VERSION} \
      @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} \
 && npm cache clean --force

# HOME is replaced per attempt; the agents' logins are mounted into it (see bench.py).
RUN mkdir -m 1777 /home/agent
ENV HOME=/home/agent PYTHONDONTWRITEBYTECODE=1 \
    DISABLE_AUTOUPDATER=1 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
