# Inert risk-report sign-off adapter

Producer for personal-agent-eval-control-plane issue #88; runtime issue #17.
This opt-in fixture does not change the pure queue's effect policy. It adds no
job engine or generic publisher. The queue renders a cited, read-only integer
risk calculation using its existing document contracts and independent checker.
The existing reviewer accepts a precise draft revision under the visibly
simulated identity `simulated-operator`.

Only `signoff.intent` and `signoff.observed` are added to the existing sealed
Store event stream. A stable action ID binds task version, accepted run, draft,
revision and numeric payload. The original accepted review packet still has
`external_effects=[]`: acceptance alone is not publication. A separate disposable
SQLite sink commits delivery attempts and key-unique results. Independent reads
reopen that sink after the writing connections close. Repeated activities with
one key may increase attempts without increasing effects; no global exactly-once
execution guarantee is claimed.

The closed baseline intentionally retries with a new key or trusts premature
confirmation. The recovery control reuses the prepared action ID and reconciles
confirmed state. Cases include benign success, fenced worker recovery, process
exit before/after effect, lost acknowledgement, duplicate delivery, stale review,
confirmed target failure, unavailable outcome evidence, unrelated confirmation,
and restart awaiting review. Crash children really exit 23 after committing
state; the parent reopens the stores. Logical recovery ticks and measured
publication-phase seconds are separate. Unknown usage remains null.

`inert-signoff-receipt/v1` and `inert-signoff-batch/v1` are producer schemas.
The consumer independently validates them and checks actual target snapshots;
it does not import producer verdicts. In particular, an ack or a completed queue
job cannot establish an external effect. The `missing-outcome` fixture withholds
independent target evidence, yielding `unknown` in the recovery control.

Each matrix attempt uses a fresh directory. Reusing a directory fails rather
than overwriting prior evidence. The CLI does not start a scheduler or contact
services. All 48 attempts are fixed (12 cases, two controls, two repeats).
Their public failure-family assignments and source-case mappings live in the
control-plane integration; this demo alone is not a production safety proof,
a live held-out experiment or approval to use real services. Hash chains are
integrity checks, not authentication against a malicious filesystem owner.
