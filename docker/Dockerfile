# syntax=docker/dockerfile:1.7
FROM python:3.13-slim

ARG GIT_SHA=unknown
ENV WORKDIR=/workdir

RUN groupadd -g 1000 model && \
    useradd -m -u 1000 -g 1000 model && \
    gpasswd -d model root || true

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        awscli sudo curl zip unzip poppler-utils imagemagick && \
    apt-get clean

RUN pip install --no-cache-dir \
        uv==0.11.8 \
        pdf2image==1.16.3 \
        pandas==2.2.3 \
        numpy==2.4.3

WORKDIR /app

# Deps layer: copy only manifests/lockfiles so `uv sync` stays cached when
# source changes. The list below is explicit by design —
# `just check-dockerfile-manifests` (also run in CI lint) fails if a new
# package gets added without being listed here. All packages are Python
# (jira/slack were ported from TS).
COPY pyproject.toml uv.lock /app/
COPY packages/core/pyproject.toml            /app/packages/core/
COPY packages/google_calendar/pyproject.toml /app/packages/google_calendar/
COPY packages/google_mail/pyproject.toml     /app/packages/google_mail/
COPY packages/jira/pyproject.toml            /app/packages/jira/
COPY packages/mcp_proxy/pyproject.toml       /app/packages/mcp_proxy/
COPY packages/read_file_safe/pyproject.toml  /app/packages/read_file_safe/
COPY packages/shopify/pyproject.toml         /app/packages/shopify/
COPY packages/slack/pyproject.toml           /app/packages/slack/
COPY packages/syntara/pyproject.toml         /app/packages/syntara/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-workspace --all-extras

# Source layer.
COPY . /app

# Finalize workspace package install. External deps were cached above, so this
# step only links editable workspace packages and is fast.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --all-packages --all-extras

# OpenHands runner: a single file that must be staged into the build context at
# openhands-runner/openhands_runner.py before `docker build` runs (it lands at
# /app/openhands-runner/openhands_runner.py via the source-layer COPY above).
# The Harbor OpenHandsAgent execs this runner in-container, so the image carries
# it with openhands-sdk in an isolated venv (kept separate from /app/.venv so its
# deps never collide with the MCP env).
RUN test -f /app/openhands-runner/openhands_runner.py || { \
        echo "openhands_runner.py missing from build context —" \
             "stage it into openhands-runner/ before building" >&2; exit 1; }
# This isolated, eval-only runner venv is not bound by the repo-wide
# `[tool.uv] exclude-newer` supply-chain cutoff (docker/pyproject.toml, ~14 days
# back): openhands-sdk and its deps (litellm, etc.) release continuously and are
# newer than the cutoff. Override it wholesale for just this install with a fixed
# date so resolution is reproducible. Bump the date whenever you bump the pin.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /app/openhands-runner/.venv --python 3.13 && \
    uv pip install --python /app/openhands-runner/.venv/bin/python \
        --exclude-newer 2026-06-17T00:00:00Z \
        openhands-sdk==1.28.1

# Verifier deps: tests/test.sh pip-installs these at runtime; baking them in
# makes the verifier fast and network-independent.
RUN pip install --no-cache-dir openpyxl pdfplumber python-docx
RUN mkdir -p ${WORKDIR} && chown -R model:model ${WORKDIR}

# Lock down sibling MCP-server source from the model user (uid 1000). syntara
# is the only server that runs as model; the rest run as root and never need
# to be readable by the agent. syntara eagerly imports its full dependency
# tree (incl. read_file_safe) before drop_privileges, so nothing else under
# /app/packages needs to stay readable post-setuid. /app/packages itself stays
# traversable so agents get a clean Permission denied rather than "directory
# doesn't exist".
RUN find /app/packages -mindepth 1 -maxdepth 1 -type d ! -name syntara \
        -exec chown -R root:root {} + -exec chmod -R go-rwx {} +

RUN echo "$GIT_SHA" > /app/.git-sha

WORKDIR ${WORKDIR}
