# Source scope and limits

Source: [project-ideas #243](https://github.com/jpequegn/project-ideas/issues/243).

## Complete V1 slice

| Source concern | Implementation |
| --- | --- |
| Markdown-defined agents | Strict YAML metadata and prompt body; exactly one draft and one check handler |
| Typed event orchestration | Three validated workflow event payloads and subscription enqueueing |
| Durable execution | SQLite jobs, atomic claims, lease fencing, retries, checkpoints, dead letters |
| Scheduling | Durable interval cursor with explicit, bounded manual ticks |
| Prompt/response provenance | Canonical content-addressed artifacts and saved run snapshots |
| Causal history | Append-only hash-linked events and transaction seals |
| Human intervention | Revision-bound accept/correct/reject, pause/resume/cancel |
| Context gaps | Explicit dispositions, before/after context versions, operator attribution |
| Feedback-to-eval | Stored correction candidates and runnable mock-profile regression checks |
| Replay across providers | New isolated run using economy/careful deterministic profiles |
| Verified outcomes | Separate automated check, reviewer decision, final outcome, and external effects |

## Explicit extensions, not shipped claims

The daily source comments propose much larger lanes. V1 provides the durable queue/review/replay contracts they would need, but does not implement a real software factory, disposable coding environments, CI/merge automation, an SOP compiler, composite-tool promotion, GUI observation/rollback, automatic context compaction, a live model adapter, or a Castflow integration. No claim is made about parallel speedup, real business impact, reviewer-time reduction, or semantic model quality.

Compaction with provenance is a future transformation over saved artifacts; current replay compares full saved context. Workflow-level attribution distinguishes high-risk direct inspection from the bounded brief procedure, but does not execute a different abstraction-level agent factory.

Agent Budget Proxy #245 integration is a future trusted adapter. This runtime does not enforce money budgets or control Codex subscription usage. Its cost units are fictional evaluation counters.

## Operating limits

- Python 3.12+, local filesystem, one SQLite database, trusted operator.
- Two pure handler types; no arbitrary code, model endpoints, URLs, shell tools, or outbound actions.
- At most 100 runs, 50 jobs per run, 20 schedules, 10,000 causal events and 10,000 changed transactions.
- At most 5,000 artifacts, 20 MB total canonical artifact text, and 100 KB per artifact/input JSON.
- Agent files are limited to 16 KB; aliases and duplicate metadata keys are rejected.
- Up to five configured attempts, default three; normal worker lease is 30 seconds.
- Source dates must be canonical YYYY-MM-DD. Sources must be inside the declared window for acceptance.
- Default demo does not accept itself. Only `--accept-demo` and the documented evaluation simulate review.
- No retention daemon or garbage collection. Start a separate ledger for a new lab when limits are reached.

The first completed release uses SQLite schema version 2. Intermediate development version-1 databases are deliberately rejected rather than silently trusted without state seals. Keep old evidence separately and start a fresh ledger; no production migration is claimed.

Hash chains are not signatures. A malicious filesystem owner or trusted Python caller can replace/recompute a ledger. Multi-user deployment requires authenticated reviewers, authorization, isolation, and stronger tamper evidence. Repeated pure computation is permitted after a crash; exactly-once external side effects are not promised.
