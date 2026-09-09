# Evaluation and evidence

## Reproduce

```sh
uv run python -m pytest
uv run ruff check .
uv run ruff format --check .
uv run python -m event_runtime demo --out data/evidence-demo
uv run python -m event_runtime --db data/evidence-demo/runtime.db verify
uv run python -m event_runtime evaluate --out reports/evidence-eval
```

The default demo produces two completed handler jobs, a passing automated check, 100 synthetic cost units, zero review decisions, zero external effects, and `accepted=false`. This is a successful draft/check pipeline waiting for a human, not a completed accepted outcome.

## Twelve-case fixture

Each profile runs six cases: normal, one transient provider failure, unsupported claims, missing context, stale source metadata, and a removed drafting capability. Only normal and transient cases pass the evidence checker and receive an explicitly simulated acceptance in the evaluator.

| Mock profile | Accepted / cases | Synthetic units | Units / accepted | Reviews | Retries |
| --- | ---: | ---: | ---: | ---: | ---: |
| economy | 2 / 6 | 600 | 300 | 2 | 1 |
| careful | 2 / 6 | 2400 | 1200 | 2 | 1 |

These invented units exercise attribution, not provider pricing. The careful profile includes more facts but does not improve acceptance on these simple fixtures. No claim about real model quality, token savings, latency, or reviewer minutes follows from this table. Reviewer time and parallel-throughput gains are not measured.

## Test coverage

The suite covers strict schema and metadata parsing, duplicate JSON/YAML keys, oversized input, canonical dates, duplicate trigger conflicts, transaction rollback, immutable artifacts, hash verification, competing SQLite workers, lease expiry, stale completion, pause/cancel fencing, bounded retries, process restart, citation support, source windows, context gaps, capability denial, review revision races, unsupported human corrections, approved context replay, and invalid regression-case rejection.

Concurrent schedule ticks produce one run per occurrence. Concurrent reviews accept one revision once. Materialized run/job/schedule corruption fails before work can proceed. A subprocess test starts separate CLI processes for triggering, drafting, checking, inspection, and verification.

Replay tests prove the source run remains unchanged, the same profile reproduces deterministic artifacts, another profile changes the brief, and approval is never inherited. Feedback evaluation requires the saved expected correction itself to pass evidence checks before using it as an oracle.

CI runs tests, lint, formatting, package build, demo, ledger verification, and evaluation on Python 3.12. Final closeout includes a separate GitHub checkout check.

## Evidence gaps

No live model, real podcast ingestion, external write, semantic judge, multi-user authentication, high-volume load, or production scheduling has been tested. The worker handlers are fast and pure; lease behavior around slow paid providers needs a separate adapter design with current-time checks, idempotent receipts, and explicit uncertain-outcome handling.
