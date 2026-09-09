"""Local operator dispositions, optimistic concurrency, and immutable feedback cases."""

from typing import Literal

from pydantic import Field, model_validator

from event_runtime.contracts import Brief, Context, Contract, Name, Snapshot, Text, Verification
from event_runtime.queue import Queue
from event_runtime.store import Conflict
from event_runtime.worker import verify


class Review(Contract):
    revision: int = Field(ge=0)
    reviewer: Name
    decision: Literal["accept", "correct", "reject"]
    note: Text
    correction: Brief | None = None

    @model_validator(mode="after")
    def correction_required(self):
        if (self.decision == "correct") != (self.correction is not None):
            raise ValueError("only a correction decision requires a corrected brief")
        return self


class Resolution(Contract):
    revision: int = Field(ge=0)
    reviewer: Name
    term: Name
    disposition: Literal["irrelevant", "save-definition", "save-source", "needs-review"]
    value: Text | None = None
    source_id: Name | None = None

    @model_validator(mode="after")
    def required_value(self):
        if (self.disposition in ("save-definition", "save-source")) != (self.value is not None):
            raise ValueError("saved definition/source requires a value")
        if (self.disposition == "save-source") != (self.source_id is not None):
            raise ValueError("save-source requires source_id")
        return self


class Reviewer:
    def __init__(self, queue: Queue):
        self.queue = queue
        self.store = queue.store

    def editable(self, c, run_id, revision, reviewer):
        run = self.store.run(c, run_id)
        if run["revision"] != revision:
            raise Conflict("stale review revision; inspect the run again")
        if run["status"] not in ("review", "context_gap"):
            raise Conflict("run is not awaiting review")
        snapshot = Snapshot.model_validate(self.store.get(c, run["snapshot"]))
        if reviewer in {a.name for a in snapshot.agents}:
            raise Conflict("agent cannot review its own run")
        return run, snapshot

    def decide(self, run_id, request: Review, now):
        with self.store.transaction() as c:
            run, snapshot = self.editable(c, run_id, request.revision, request.reviewer)
            context = Context.model_validate(self.store.get(c, run["context"]))
            brief = Brief.model_validate(self.store.get(c, run["latest_brief"]))
            if request.decision == "accept":
                saved = Verification.model_validate(self.store.get(c, run["latest_check"]))
                current = verify(brief, snapshot, context)
                if not current.passed or saved != current or run["status"] != "review":
                    raise Conflict("acceptance requires current, passing independent evidence")
            event = self.store.record(
                c,
                run_id,
                "review.disposition",
                {
                    **request.model_dump(),
                    "brief": run["latest_brief"],
                    "verification": run["latest_check"],
                    "context": run["context"],
                },
                now,
            )
            if request.decision == "correct":
                after = self.store.put(c, request.correction)
                feedback = {
                    "kind": "brief_correction",
                    "snapshot": run["snapshot"],
                    "before": run["latest_brief"],
                    "expected": after,
                    "reviewer": request.reviewer,
                    "note": request.note,
                    "context": run["context"],
                }
                self.store.record(c, run_id, "feedback.case", feedback, now, event)
                c.execute(
                    "UPDATE runs SET latest_brief=?,latest_check=NULL,status='queued',"
                    "revision=revision+1 WHERE id=?",
                    (after, run_id),
                )
                self.queue.emit(
                    c, run_id, "brief.drafted", request.correction.model_dump(), now, event
                )
            else:
                accepted = request.decision == "accept"
                outcome = self.store.put(
                    c,
                    {
                        "goal": snapshot.trigger.goal,
                        "intended_change": "prepare a local review packet only",
                        "observed_brief": run["latest_brief"],
                        "verification": run["latest_check"],
                        "context": run["context"],
                        "status": "accepted" if accepted else "rejected",
                        "reviewer": request.reviewer,
                        "review_event": event,
                        "external_effects": [],
                        "reason": request.note,
                    },
                )
                c.execute(
                    "UPDATE runs SET status=?,outcome=?,revision=revision+1 WHERE id=?",
                    ("completed" if accepted else "rejected", outcome, run_id),
                )
                self.store.record(c, run_id, "outcome", {"artifact": outcome}, now, event)

    def resolve(self, run_id, request: Resolution, now):
        with self.store.transaction() as c:
            run, snapshot = self.editable(c, run_id, request.revision, request.reviewer)
            if run["status"] != "context_gap":
                raise Conflict("run has no pending context gap")
            context = Context.model_validate(self.store.get(c, run["context"]))
            brief = Brief.model_validate(self.store.get(c, run["latest_brief"]))
            check = verify(brief, snapshot, context)
            if request.term not in check.missing_terms:
                raise Conflict("term is not a current context gap")
            event = self.store.record(c, run_id, "context.disposition", request.model_dump(), now)
            if request.disposition == "needs-review":
                c.execute("UPDATE runs SET revision=revision+1 WHERE id=?", (run_id,))
                return
            value = context.model_dump()
            if request.disposition == "irrelevant":
                value["ignored_terms"].append(request.term)
            else:
                if request.source_id and request.source_id not in {
                    s.id for s in snapshot.trigger.sources
                }:
                    raise ValueError("unknown context source")
                value["definitions"][request.term] = request.value
            value["provenance"][request.term] = (
                f"operator:{request.reviewer}; disposition:{request.disposition}; "
                f"source:{request.source_id or 'human'}"
            )
            updated = Context.model_validate(value)
            context_hash = self.store.put(c, updated)
            self.store.record(
                c,
                run_id,
                "context.updated",
                {
                    "before": run["context"],
                    "after": context_hash,
                    "term": request.term,
                    "disposition": request.disposition,
                    "reviewer": request.reviewer,
                    "promotion": "human-approved",
                    "prior_check": run["latest_check"],
                },
                now,
                event,
            )
            self.store.record(
                c,
                run_id,
                "feedback.case",
                {
                    "kind": "context_correction",
                    "snapshot": run["snapshot"],
                    "context": context_hash,
                    "expected_resolved_term": request.term,
                    "reviewer": request.reviewer,
                },
                now,
                event,
            )
            c.execute(
                "UPDATE runs SET context=?,latest_check=NULL,status='queued',"
                "revision=revision+1 WHERE id=?",
                (context_hash, run_id),
            )
            self.queue.emit(c, run_id, "brief.drafted", brief.model_dump(), now, event)
