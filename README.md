# HANDBOOK.md Benchmark Tasks

This repo contains HANDBOOK.md tasks and the environment they run in. Tasks are in Harbor format. More information on the benchmark can be found [here](https://surgehq.ai/blog/handbook-md).

## Overview

- `agent_harness/` — Agent harness used to evaluate HANDBOOK.md. Built with [OpenHands/software-agent-sdk](https://github.com/OpenHands/software-agent-sdk). 
- `docker/` — build context for the `handbook` base image. Contains mock services and the
  bundled agent harness.
- `tasks/` — one subdirectory per benchmark task.
- `.env.example` — template for required environment variables.

## Quick start

```bash
# 1. Build the base image (build context is self-contained)
docker build -t handbook_base docker/

# 2. Install Harbor + the bundled agent harness into an isolated venv
uv venv .venv --python 3.13
uv pip install --python .venv/bin/python harbor -e ./agent_harness

# 3. Run a single task locally
.venv/bin/harbor run -p tasks/<task_name> \
    --agent-import-path agent_harness.openhands_agent:OpenHandsAgent \
    -m anthropic/claude-opus-4-8 -n 1 \
    --env-file .env
```

## License

Copyright 2026 Surge AI.

Licensed under the Apache License, Version 2.0. See [`LICENSE`](LICENSE) for the
full text.
