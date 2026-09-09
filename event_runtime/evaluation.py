"""Fixed synthetic cases; accepted outcomes explicitly require a simulated reviewer."""

from collections import Counter
from pathlib import Path

from event_runtime.contracts import Context, Snapshot, canonical
from event_runtime.output import write_report
from event_runtime.queue import Queue
from event_runtime.replay import drive, metrics
from event_runtime.review import Review, Reviewer
from event_runtime.store import Store


def evaluate(output: Path, snapshot: Snapshot):
    if snapshot.trigger.fault != "none" or not snapshot.trigger.required_terms:
        raise ValueError("evaluation requires a clean baseline with required context")
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    results = []
    with Store(output / "evaluation.db") as store:
        queue = Queue(store)
        for profile in ("economy", "careful"):
            for case in (
                "normal",
                "transient",
                "unsupported",
                "missing-context",
                "stale",
                "cutoff",
            ):
                data = snapshot.model_dump()
                data["profile"] = profile
                if case == "transient":
                    data["trigger"]["fault"] = "transient"
                elif case == "unsupported":
                    data["trigger"]["fault"] = "invalid"
                elif case == "missing-context":
                    data["context"] = Context().model_dump()
                elif case == "stale":
                    data["trigger"]["sources"][0]["published"] = "2000-01-01"
                elif case == "cutoff":
                    data["capabilities"] = ["verify"]
                run = queue.trigger(Snapshot.model_validate(data), f"{profile}:{case}", 0)
                report = drive(queue, run)
                if metrics(report)["automated_check_passed"]:
                    Reviewer(queue).decide(
                        run,
                        Review(
                            revision=report["run"]["revision"],
                            reviewer="simulated-reviewer",
                            decision="accept",
                            note="Evaluation fixture explicitly accepts supported source claims",
                        ),
                        20,
                    )
                    report = store.inspect(run)
                result = {"case": case, "profile": profile, "run_id": run, **metrics(report)}
                expected_accept = case in ("normal", "transient")
                if result["accepted"] != expected_accept:
                    raise AssertionError(f"unexpected fixture outcome: {case}")
                results.append(result)
        store.verify()
    totals = []
    for profile in ("economy", "careful"):
        rows = [r for r in results if r["profile"] == profile]
        cost = sum(r["synthetic_cost_units"] for r in rows)
        accepted = sum(r["accepted"] for r in rows)
        totals.append(
            {
                "profile": profile,
                "accepted": accepted,
                "cases": len(rows),
                "synthetic_cost_units": cost,
                "units_per_accepted_outcome": cost / accepted if accepted else None,
                "review_decisions": sum(r["review_decisions"] for r in rows),
                "retries": sum(r["retries"] for r in rows),
            }
        )
    failures = Counter(reason for r in results for reason in r["reasons"])
    result = {
        "schema_version": 1,
        "synthetic": True,
        "simulated_review": True,
        "totals": totals,
        "failure_clusters": dict(sorted(failures.items())),
        "cases": results,
        "external_effects": 0,
    }
    write_report(output / "evaluation.json", canonical(result) + "\n")
    lines = [
        "# Synthetic workflow evaluation",
        "",
        "Reviews are simulated fixtures, not real human judgments or model quality scores.",
        "Cost units are invented test units, not dollars. Review time is not measured.",
        "",
        "| Profile | Accepted / cases | Units | Units / accepted | Reviews | Retries |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in totals:
        lines.append(
            f"| {row['profile']} | {row['accepted']} / {row['cases']} | "
            f"{row['synthetic_cost_units']} | {row['units_per_accepted_outcome']} | "
            f"{row['review_decisions']} | {row['retries']} |"
        )
    lines += [
        "",
        "Failure clusters: " + canonical(result["failure_clusters"]),
        "",
        "No live provider, email, publish, merge, or GUI action was performed.",
    ]
    write_report(output / "evaluation.md", "\n".join(lines) + "\n")
    return result
