# Architecture

```text
Markdown agents + trigger + context + provider profile
                         |
                  immutable snapshot
                         |
           brief.requested event + draft job
                         |
                atomic claim + lease
                         |
               checkpoint + prompt artifact
                         |
                 pure draft handler
                         |
        draft result + brief.drafted event + check job
                         |
              independent evidence checker
                         |
           context_gap or review, never auto-accepted
                         |
        human correction / context disposition / acceptance
                         |
            recheck or attributed final outcome
```

## Storage and authority

SQLite immediate transactions serialize state changes across connections. `artifacts` stores canonical JSON under SHA256 hashes. `events` is a hash-linked causal history; SQL triggers prevent ordinary updates/deletes. `runs`, `jobs`, and `schedules` hold the materialized state.

Each changed transaction appends a hash-linked seal binding all materialized rows, artifact IDs, and the event head. Reads and writes verify those seals, event hashes, artifact contents, snapshots, and foreign keys before proceeding. A changed `status='completed'` row therefore fails verification instead of becoming a false accepted-outcome report.

This detects corruption and partial tampering, not an owner who replaces the entire database and recomputes every hash. The local operator, Python process, and filesystem are trusted. Agent markdown and provider text are not executable authority. Only the fixed draft/verify handlers are available, and source-defined capability lists can deny them but cannot grant shell/network access.

Database files and new exported reports use mode 0600; newly created output directories use 0700. Keep the database, WAL files, and reports in a private location. Existing parent directory permissions are not rewritten.

## Queue semantics

Emitting a typed event and enqueuing all matching subscribers happen in the same transaction. A trigger key binds the full snapshot and optional replay parent. Jobs have unique event/agent identities. Claiming a job assigns a new random lease token and increments its attempt count.

Completion requires the current unexpired lease and a running parent run. Result storage, job completion, run revision, output event, and downstream jobs commit together. A restart can reclaim an expired lease, but the previous worker cannot commit. Pausing or cancelling clears leases and fences late results. Retrying pure handlers may repeat computation; it does not promise exactly-once physical execution.

Provider attempts, including the synthetic failed attempt, are recorded separately from job completion. The metrics therefore do not confuse a retry or a generated artifact with an accepted outcome. Expired leases eventually dead-letter when the bounded attempt count is exhausted. There is no external side-effect adapter or ambiguous paid-call reconciliation in V1.

## Review and replay

Review requests include a run revision. Acceptance recomputes the checker against the current brief/context and requires equality with the saved passing check. The outcome points to the reviewed artifact hashes and review event. A correction produces a new candidate artifact and regression case, then re-enqueues verification; it never promotes itself.

Context dispositions record old/new versions and operator provenance. They remain local to the run. Replay forks the original snapshot, optionally substituting the current context or another mock profile. It does not copy corrected output or approval. The original run's records are unchanged.

The checker validates curated exact facts and source metadata. It is independent of the draft handler but is not a semantic truth detector. Human attribution is a local audit boundary, not authenticated separation of duties.

## Time and failure behavior

The CLI uses wall-clock seconds. Unit tests and the bounded demo/evaluation driver use explicit logical time. A regressing per-run event clock rolls back its transaction. `work` never waits for a future retry or installs a loop. Schedules use explicit ticks and atomically advance their due cursor with run creation.

| Condition | Result |
| --- | --- |
| Missing/corrupt ledger evidence | Fail closed before dispatch or report |
| Duplicate trigger / schedule occurrence | Return original / do not enqueue duplicate |
| Transient mock provider failure | Retry after bounded backoff |
| Worker crash before completion | Lease can be reclaimed; late result fenced |
| Retry/lease exhaustion | Dead-letter and cancel outstanding jobs |
| Missing context | Pause for explicit operator disposition |
| Unsupported claim or stale source | Failed verification, acceptance refused |
| Human correction | Store candidate and recheck |
| Stale review revision | Reject without mutation |
| Capability removed | Dead-letter without executing that handler |
| Replay | New isolated run, no inherited approval or external effects |
