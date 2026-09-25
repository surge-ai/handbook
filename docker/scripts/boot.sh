#!/usr/bin/env bash
set -euo pipefail

pip3 install uv==0.11.2
curl -fsSL https://bun.sh/install | BUN_INSTALL=/usr/local BUN_VERSION=v1.3.11 bash
bun install --frozen-lockfile
