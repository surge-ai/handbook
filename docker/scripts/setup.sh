#!/usr/bin/env bash

# Translate legacy CLI args into HANDBOOK_* env vars.
TOOL_SETS=()
while [[ $# -gt 0 ]]; do
  case $1 in
    --task-id)      export HANDBOOK_TASK_ID="$2";       shift 2;;
    --world-root)   export HANDBOOK_ROOT="$2";          shift 2;;
    --tool-sets)    shift;
                    while [[ $# -gt 0 && ! "$1" =~ ^-- ]]; do
                        TOOL_SETS+=("$1"); shift;
                    done
                    export HANDBOOK_TOOL_SETS="${TOOL_SETS[*]}";;
    *)              shift;;
  esac
done

uv run --package mcp-proxy mcp-proxy setup
