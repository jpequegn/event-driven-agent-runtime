# Implementation plan

Source: https://github.com/jpequegn/project-ideas/issues/243

1. **Define strict agent and workflow contracts**: Python 3.12 package, pinned dependencies, CI, strict markdown/YAML agent definitions, typed source/brief/verification contracts and initial fixtures. Reject unknown fields and unsafe definitions. Document the eight-task V1 plan.

2. **Build content-addressed storage and causal history**: SQLite transactions, immutable SHA256-addressed JSON artifacts, versioned run snapshots, hash-linked causal events and verified reads. Test persistence, rollback, duplicate triggers, conflicts and tamper detection.

3. **Implement durable jobs and fenced recovery**: Transactional event subscriptions and durable jobs; atomic claims, expiring leases with fencing, bounded retries/backoff, dead-letter, pause/resume/cancel and checkpoint history. Test competing workers, restart, duplicate delivery and stale completion.

4. **Execute the two-agent brief workflow**: Trusted deterministic draft/check handlers driven by markdown subscriptions; snapshot prompts/context/provider; independently validate citations and source windows; bound fanout and record outcomes. Include missing context, unsupported claims and transient failure fixtures. No external effects.

5. **Add human review and correction feedback**: Accept/correct/reject and context-gap dispositions with revision-bound review. Corrections must pass validation before acceptance. Persist approved context revisions and regression cases; keep model claims separate from accepted outcomes.

6. **Implement replay, diff and evaluation reports**: Fork saved trigger/agent/context snapshots into isolated replay runs, compare two mock profiles and approved context revisions, preserve originals and disable effects. Report artifact diffs, verification, retries, review burden and failure clusters; test reproducibility and no inherited approval.

7. **Expose CLI and manually ticked schedules**: Commands for trigger, bounded work, inspect, pause/resume/cancel, review, context resolution, replay/diff, demo, evaluation, verification and schedule management. Durable interval schedule with explicit tick only; no background daemon installation, email or publishing.

8. **Harden runtime and document capabilities**: Adversarial/concurrency/restart tests, CLI integration and fresh checkout verification. Commit architecture, usage, learning exercises, limitations, innovative extensions and source-scope mapping. All task PRs must pass CI before source closeout.

## V1 scope

Python 3.12, SQLite transactions, strict Pydantic contracts, YAML front matter, Typer CLI. Two trusted synthetic handlers, economy/careful mock profiles, no live LLM credentials or network effects. Durable interval schedules advance only with an explicit CLI tick; no daemon or system automation is installed. Agent definitions cannot execute commands. Human review is a local operator boundary, not multi-user authentication.

The podcast brief is the complete vertical slice. Real GUI automation, autonomous code factories, SOP/composite-tool promotion, semantic LLM grading, and direct integration with #245 remain extensions. The runtime will preserve their prerequisite checkpoint, context-gap, correction, and outcome records without pretending to execute those larger lanes.
