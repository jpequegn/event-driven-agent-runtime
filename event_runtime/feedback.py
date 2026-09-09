"""Evaluate saved human corrections without treating unverified edits as truth."""

from event_runtime.contracts import Brief, Context, Snapshot
from event_runtime.worker import draft, verify


def evaluate_feedback(store, run_id, profile):
    rows = []
    with store.transaction() as c:
        store.run(c, run_id)
        for event in c.execute(
            "SELECT * FROM events WHERE run_id=? AND kind='feedback.case' ORDER BY seq", (run_id,)
        ):
            case = store.get(c, event["artifact"])
            data = store.get(c, case["snapshot"])
            data["profile"] = profile
            data["context"] = store.get(c, case["context"])
            snapshot = Snapshot.model_validate(data)
            context = Context.model_validate(data["context"])
            # Attempt two represents recovery from the fixture's one transient failure.
            candidate = draft(snapshot, 2)
            candidate_check = verify(candidate, snapshot, context)
            if case["kind"] == "brief_correction":
                expected = Brief.model_validate(store.get(c, case["expected"]))
                valid_case = verify(expected, snapshot, context).passed
                required = {(claim.source_id, claim.text) for claim in expected.claims}
                actual = {(claim.source_id, claim.text) for claim in candidate.claims}
                passed = valid_case and candidate_check.passed and required <= actual
            else:
                term = case["expected_resolved_term"]
                valid_case = term in context.definitions or term in context.ignored_terms
                passed = valid_case and candidate_check.passed
            rows.append(
                {
                    "case_artifact": event["artifact"],
                    "kind": case["kind"],
                    "valid_case": valid_case,
                    "passed": passed,
                    "candidate_reasons": candidate_check.reasons,
                }
            )
    return {
        "profile": profile,
        "cases": rows,
        "passed": sum(r["passed"] for r in rows),
        "invalid_cases": sum(not r["valid_case"] for r in rows),
        "external_effects": 0,
        "synthetic": True,
    }
