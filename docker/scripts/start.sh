#!/usr/bin/env bash

cd /app

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export WORLDBENCH_ROOT="${WORLDBENCH_ROOT:-"$(dirname "$SCRIPT_DIR")"}"

# Translate legacy CLI args into WORLDBENCH_* env vars.
TOOL_SETS=()
MCP_ARGS=()
while [[ $# -gt 0 ]]; do
  case $1 in
    --root)            export WORLDBENCH_ROOT="$2";         shift 2;;
    --current-time)    export WORLDBENCH_CURRENT_TIME="$2"; shift 2;;
    --tool-sets)       shift;
                       while [[ $# -gt 0 && ! "$1" =~ ^-- ]]; do
                         TOOL_SETS+=("$1"); shift;
                       done
                       export WORLDBENCH_TOOL_SETS="${TOOL_SETS[*]}";;
    --method)          MCP_ARGS+=(--method "$2");           shift 2;;
    --port)            MCP_ARGS+=(--port "$2");             shift 2;;
    *)                 shift;;
  esac
done

export OUTPUTDIR="${OUTPUTDIR:-${WORLDBENCH_ROOT}/output_data}"
export INPUTDIR="${INPUTDIR:-${WORLDBENCH_ROOT}/setup_data/entities}"
mkdir -p "$OUTPUTDIR"

# /workdir is normally created by docker/Dockerfile, but boot paths that
# don't run that Dockerfile (e.g. local dev, custom base images) need it too.
# mkdir is a no-op when it already exists.
mkdir -p /workdir
if [ "$(id -u)" = "0" ] && id model >/dev/null 2>&1; then
  chown -R model:model /workdir
fi

# `uv run` needs to resolve the workspace pyproject; the container's WORKDIR is
# the task's runtime workdir (e.g. /workdir or /workspace), not the app root.
cd "$WORLDBENCH_ROOT"
uv run --package mcp-proxy mcp-proxy mcp "${MCP_ARGS[@]}"
