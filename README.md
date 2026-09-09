# Event-Driven Agent Runtime

A local Python runtime for markdown-defined agents, typed events, durable jobs, human review, and replayable prompt artifacts. The complete demo workflow drafts a cited podcast brief, independently checks its evidence, then waits for your decision.

**V1 uses deterministic synthetic providers.** It makes no LLM, email, publishing, GitHub, or GUI calls. Nothing runs on a timer unless you explicitly invoke the CLI. This project does not restart the project-ideas automation.

## Quickstart

Requires Python 3.12+ and uv. Run these commands from the cloned repository:

```sh
git clone https://github.com/jpequegn/event-driven-agent-runtime.git
cd event-driven-agent-runtime
uv sync --frozen --python 3.12
uv run python -m event_runtime demo --out data/first-demo
uv run python -m event_runtime evaluate --out reports/first-evaluation
```

The demo prints a run ID and stops in `review`, with `automated_check_passed: true` and `accepted: false`. Its database and report live under `data/first-demo`. The evaluation writes a Markdown comparison and runs twelve synthetic cases with explicitly simulated reviewers. Use a new output directory for each demo/evaluation.

There is no browser dashboard or long-running service. Use `inspect`, `artifact`, `review`, and `diff` to explore the evidence. The [project guide](PROJECT_GUIDE.md) walks through acceptance, corrections, context gaps, crash recovery, replay, and manual schedule ticks.

## Verify

```sh
uv run python -m pytest
uv run ruff check .
uv run ruff format --check .
uv build
```

`edr` is also installed as a console command. The module form above avoids editable-install `.pth` issues seen in some iCloud-backed macOS directories. No environment flag changes are required to use the module form from the checkout.

See [architecture](docs/ARCHITECTURE.md), [evaluation](docs/EVALUATION.md), and the [source-scope map](docs/SCOPE.md). Source: [project-ideas #243](https://github.com/jpequegn/project-ideas/issues/243).
