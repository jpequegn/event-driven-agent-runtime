"""Trusted pure handlers. Prompt text never grants tools or changes queue policy."""

from event_runtime.contracts import Brief, Claim, Context, Snapshot, Verification, digest
from event_runtime.queue import Queue


class RetryableError(Exception):
    pass


def draft(snapshot: Snapshot, attempt: int) -> Brief:
    trigger = snapshot.trigger
    if trigger.fault == "transient" and attempt == 1:
        raise RetryableError("synthetic provider unavailable")
    claims = []
    for source in trigger.sources:
        facts = source.facts if snapshot.profile == "careful" else source.facts[:1]
        claims.extend(Claim(text=fact, source_id=source.id) for fact in facts)
    if trigger.fault == "invalid":
        claims[0] = Claim(text="This unsupported conclusion was invented.", source_id="missing")
    return Brief(
        title="Evidence brief" if snapshot.profile == "careful" else "Brief", claims=claims
    )


def verify(brief: Brief, snapshot: Snapshot, context: Context) -> Verification:
    sources = {s.id: s for s in snapshot.trigger.sources}
    reasons = []
    seen = set()
    for claim in brief.claims:
        source = sources.get(claim.source_id)
        if source is None:
            reasons.append("unknown_citation")
            continue
        seen.add(source.id)
        if claim.text not in source.facts:
            reasons.append("unsupported_claim")
        if not snapshot.trigger.window_start <= source.published <= snapshot.trigger.window_end:
            reasons.append("outside_source_window")
    if seen != set(sources):
        reasons.append("missing_source_coverage")
    missing = sorted(
        set(snapshot.trigger.required_terms) - set(context.definitions) - set(context.ignored_terms)
    )
    if missing:
        reasons.append("context_gap")
    return Verification(
        brief_hash=digest(brief),
        context_hash=digest(context),
        passed=not reasons,
        reasons=sorted(set(reasons)),
        missing_terms=missing,
    )


class Worker:
    def __init__(self, queue: Queue):
        self.queue = queue
        self.store = queue.store

    def step(self, now, run_id=None):
        job = self.queue.claim(now, run_id)
        if job is None:
            return None
        with self.store.transaction() as c:
            self.queue.owned(c, job["id"], job["lease"], now)
            run = self.store.run(c, job["run_id"])
            snapshot = Snapshot.model_validate(self.store.get(c, run["snapshot"]))
            context = Context.model_validate(self.store.get(c, run["context"]))
            agent = next(a for a in snapshot.agents if a.name == job["agent"])
            payload = self.store.get(c, job["input"])
            allowed = agent.handler in snapshot.capabilities
            prompt = self.store.put(
                c,
                {
                    "instructions": agent.prompt,
                    "input": payload,
                    "context": context.model_dump(),
                    "profile": snapshot.profile,
                },
            )
            self.store.record(
                c,
                run["id"],
                "handler.attempt",
                {
                    "job_id": job["id"],
                    "agent": agent.name,
                    "prompt": prompt,
                    "context_hash": run["context"],
                    "profile": snapshot.profile,
                    "allowed": allowed,
                    "effects_allowed": False,
                    "synthetic_cost_units": (100 if snapshot.profile == "economy" else 400)
                    if allowed and agent.handler == "draft"
                    else 0,
                    "workflow_level": "direct_inspection"
                    if snapshot.trigger.high_risk
                    else "workflow",
                    "reason": "high-risk work needs operator judgment"
                    if snapshot.trigger.high_risk
                    else "bounded synthetic brief procedure",
                },
                now,
                job["event_seq"],
            )
        if not allowed:
            self.queue.fail(job["id"], job["lease"], now, "capability denied", retry=False)
            return {"job": job["id"], "status": "dead_letter"}
        try:
            if agent.handler == "draft":
                result = draft(snapshot, job["attempt"])
            else:
                result = verify(Brief.model_validate(payload), snapshot, context)
        except RetryableError as exc:
            self.queue.fail(job["id"], job["lease"], now, str(exc))
            return {"job": job["id"], "status": "retry"}
        self.queue.complete(job["id"], job["lease"], now, result.model_dump())
        return {"job": job["id"], "status": "done", "result": digest(result)}

    def work(self, now, run_id=None, max_steps=20):
        if not 1 <= max_steps <= 100:
            raise ValueError("max_steps must be 1..100")
        results = []
        for _ in range(max_steps):
            result = self.step(now, run_id)
            if result is None:
                break
            results.append(result)
        return results
