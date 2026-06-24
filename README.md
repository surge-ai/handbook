# HANDBOOK.md Benchmark Tasks
HANDBOOK.md is a benchmark for long-context agentic instruction following, modeled on how enterprise employees follow company handbooks in their day-to-day work. Each task is a unique RL environment with internal tools and external MCP servers, spanning five enterprise domains: Finance, Medical Billing, Insurance, Logistics, and HR.

### What it tests

The prompts reflect the actual jobs enterprise workers perform every day. Each task drops an AI agent into a live company environment, requiring them to cross-reference an extensive, multi-section handbook against a cluttered inbox, a multi-channel Slack workspace, Jira queues, and a stack of files (spreadsheets, PDFs), and working out both what to do and what the handbook forbids.

### Why it's hard
These policies are written by experts adapting real industry guidelines and are explicitly designed to resist memorization. We created 10 unique base handbooks; every task then modifies its base into a distinct document by changing specific rules and thresholds. Because no two tasks share the same policy, models cannot pattern-match their way through the benchmark. Instead, they must actually read the complex instructions, hold them across a long multi-tool job, and apply them. No frontier model succeeds on more than 25% of tasks.

### Links
- [Blog post](https://surgehq.ai/blog/handbook-md)
- [Leaderboard](https://surgehq.ai/leaderboards/handbook)


Built by the [Surge AI](https://www.surgehq.ai) evals team. To evaluate your models on HANDBOOK.md or build expert-grade benchmarks in your own domains, contact benchmarks@surgehq.ai.


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
