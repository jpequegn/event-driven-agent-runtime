# Project guide

## What it does

The runtime snapshots two markdown agent definitions, source metadata, context, provider profile, and capability policy at trigger time. Later file changes do not change that run. Events enqueue the next agent by subscription, rather than by a user-authored graph.

The drafter emits cited claims. The checker independently requires exact support in the curated source facts, at least one claim from each source, valid source-window dates, and disposition of required context terms. A valid draft is still not accepted until a human records a decision.

SQLite persists jobs, attempts, leases, checkpoints, artifacts, context revisions, and review outcomes. The CLI can stop between any steps and resume. A stale worker cannot commit after lease expiry, cancellation, or pause. Replays create new runs and never copy approval from the original.

## Run a workflow one step at a time

Run from the repository root. These shell examples use `jq` to extract IDs from JSON.

```sh
RUN=$(uv run python -m event_runtime trigger --key brief-001 | jq -r .run_id)
uv run python -m event_runtime work --run-id "$RUN" --max-steps 1
uv run python -m event_runtime inspect "$RUN"
uv run python -m event_runtime work --run-id "$RUN"
uv run python -m event_runtime inspect "$RUN"
```

After the first `work`, drafting is complete and the checker is queued. After the second, the run waits in `review`. The `inspect` output includes jobs, event history, artifacts, and metrics. The `latest_brief` and `latest_check` fields are artifact hashes; their contents appear in the `artifacts` map. Prompt hashes in `handler.attempt` records can be fetched with `artifact HASH`.

The same `--key` with identical saved inputs returns the original run. Reusing it with a different profile, context, or agent definition fails. Use a new key for intentionally new work. Retrying `work` after all jobs complete does nothing.

`--db` is a global option and must come before the command:

```sh
uv run python -m event_runtime --db data/first-demo/runtime.db runs
uv run python -m event_runtime --db data/first-demo/runtime.db verify
```

## Review, correct, or reject

For the run created above, inspect its current revision and record your decision:

```sh
REV=$(uv run python -m event_runtime inspect "$RUN" | jq -r .run.revision)
uv run python -m event_runtime review "$RUN" accept \
  --revision "$REV" --reviewer julien --note 'Checked the source claims and window'
```

Acceptance produces a typed outcome linking the goal, brief, verification, context, and review event. External effects remain empty. A stale revision fails rather than approving a draft that changed while you were reading it.

For a fresh run waiting in review, use `correct` instead of `accept`:

```sh
uv run python -m event_runtime review "$RUN" correct \
  --revision "$REV" --reviewer julien --note 'Include the missing retry detail' \
  --correction examples/correction.json
uv run python -m event_runtime work --run-id "$RUN"
uv run python -m event_runtime feedback "$RUN"
uv run python -m event_runtime feedback-eval "$RUN" --profile careful
```

Correction submits a new Brief JSON, queues the checker again, and records a candidate regression case. It does not accept the edit. An unsupported correction cannot pass acceptance; `feedback-eval` labels such a case invalid rather than treating it as a trusted oracle. After a supported correction is verified, inspect the new revision and accept or reject explicitly.

`reject` records a rejected outcome without requiring a passing check. Completed and rejected runs cannot be edited in place; create a replay for further investigation.

Reviewer names provide attribution and reject an agent's own configured name. They are not authentication. The local operator can impersonate another name, so this is not a two-person security system.

## Resolve a context gap

```sh
GAP=$(uv run python -m event_runtime trigger --key gap-001 \
  --context examples/context-empty.json | jq -r .run_id)
uv run python -m event_runtime work --run-id "$GAP"
REV=$(uv run python -m event_runtime inspect "$GAP" | jq -r .run.revision)
uv run python -m event_runtime resolve-context "$GAP" slo save-definition \
  --revision "$REV" --reviewer julien --value 'Service-level objective'
uv run python -m event_runtime work --run-id "$GAP"
uv run python -m event_runtime feedback-eval "$GAP"
```

Dispositions are `save-definition`, `save-source`, `irrelevant`, and `needs-review`. `save-source` also requires `--source-id` matching the run's source catalog. A saved definition is human-supplied context, not automatically verified knowledge. `irrelevant` explicitly waives that term requirement and records who did so. `needs-review` leaves the run blocked and increments its review revision.

Updates produce immutable before/after context hashes and feedback cases. They affect this run only; they do not rewrite your context files or silently update other runs. This makes review and rollback explicit through replay with the original context.

## Replay and compare

```sh
REPLAY=$(uv run python -m event_runtime replay "$RUN" --profile careful | jq -r .run_id)
uv run python -m event_runtime work --run-id "$REPLAY"
uv run python -m event_runtime diff "$RUN" "$REPLAY"
```

Replay reruns the saved initial trigger with the selected mock provider. Add `--latest-context` to use context changes you recorded on the original. The default uses the original context. A replay starts fresh, excludes human corrections to the output, and never inherits approval. It does not repeat historical side effects.

`diff` compares snapshots, contexts, briefs, and verification, with metrics shown separately. `same_behavior` means these deterministic artifacts match; it does not mean review state, timing, or run identity match. Failed and cancelled runs can be replayed too.

The economy profile selects one fact per source; careful includes every supplied fact. Both preserve exact source text. They are stubs for testing orchestration, not real language models. Prompt changes are captured and diffable, but these stubs do not interpret arbitrary prompt prose.

## Recovery and manual schedules

```sh
uv run python -m event_runtime control "$RUN" pause
uv run python -m event_runtime control "$RUN" resume
uv run python -m event_runtime control "$RUN" cancel
```

Only valid state transitions are allowed. For example, a completed run cannot be paused. `work` processes due jobs once and exits; it never waits for retries. On a transient failure, inspect the job's `due` time and invoke `work` again later. Default leases last 30 seconds and retry delays are 2, then 4 seconds, with three total attempts. Exhaustion dead-letters the run; replay is the deliberate recovery path.

```sh
uv run python -m event_runtime schedule-add morning --every-seconds 3600
uv run python -m event_runtime schedules
uv run python -m event_runtime tick --limit 3
uv run python -m event_runtime work --max-steps 10
uv run python -m event_runtime schedule-set morning --disabled
```

Schedule registration snapshots the current fixture and starts it due now. `tick` enqueues due occurrences but never executes agents. It commits the new runs and schedule cursor together, so duplicate or competing ticks do not duplicate a scheduled occurrence. Catch-up is bounded by `--limit`. Re-enabling a schedule retains its cursor, so historical occurrences may be caught up on your next explicit tick. There is no cron grammar, daemon, LaunchAgent, installed automation, or email delivery.

## Practical uses

Use the stepwise workflow to learn what happens between a model response and a trustworthy handoff. Test failures at boundaries: before drafting, after drafting but before checking, after a lease expires, and after a human edits the brief.

For Castflow, a future read-only adapter could export selected source facts into the Trigger contract. Drafting and review would remain separate from database ingestion or publishing. Use only curated or explicitly redacted source material; this repo does not connect to your podcast database.

The runtime is also a small comparison baseline before adopting a larger orchestration framework. Its constraints are visible: one draft/check procedure, pure handlers, bounded queues, local operator review, and a single SQLite database.

## Learning exercises and extensions

1. Follow a run from `Queue.trigger` through `claim`, `Worker.step`, and `complete`. Find the transaction that commits both a result and its downstream job.
2. Set `fault` to `transient` in a new trigger file. Run one batch, inspect the retry, wait until due, and run again. Then try `invalid` and explain why generation completes but acceptance fails.
3. Read `tests/test_hardening.py`. Reproduce a stale review and a late worker completion. Decide which failures are safe to retry only because the handlers are pure.
4. Save a human context correction, replay with and without `--latest-context`, and compare the verification result. Evaluate the saved correction with `feedback-eval`.
5. Add an optional local-model adapter with explicit timeouts and schema validation. Keep tool capabilities out of prompt-controlled data. Test provider-side idempotency and accounting before introducing paid calls.
6. Integrate [Agent Budget Proxy #245](https://github.com/jpequegn/agent-budget-proxy) at the trusted execution boundary. A lease retry must not silently create a second paid action; design durable action IDs and receipt reconciliation first.
7. Turn repeated, validated correction cases into a reviewable skill-change proposal. Require regression checks and explicit promotion instead of letting an agent rewrite its own policy.
8. Build an incident-training set from context gaps, dead letters, and rejected drafts. Evaluate whether proposed recovery rules reduce reviewer corrections on held-out cases, not just on the examples that produced them.
9. Add a read-only review UI over the same artifact hashes. Keep acceptance revision-bound so a reviewer cannot accidentally approve a newer draft than the one displayed.

See [scope and limits](docs/SCOPE.md) before adding external actions or multi-user access.
