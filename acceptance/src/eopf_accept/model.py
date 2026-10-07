"""Check results and the known-issue rule."""

import datetime as dt
from dataclasses import dataclass, field

PASS, FAIL, WARN, KNOWN, XFAIL, XPASS, SKIP, VOID = (
    "PASS", "FAIL", "WARN", "KNOWN", "XFAIL", "XPASS", "SKIP", "VOID",
)
# A run fails on these; KNOWN/XFAIL/XPASS/WARN/SKIP are reported but don't fail it.
FAILING = {FAIL}


@dataclass
class Result:
    id: str  # check id, e.g. "ST01"
    group: str  # store | host | titiler:<endpoint> | ...
    status: str
    summary: str
    evidence: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    known_issue: str | None = None
    problems: list[str] = field(default_factory=list)  # every failure; the summary shows the first


def apply_known_issues(results: list[Result], known: list[dict], today: dt.date) -> None:
    """Turn a FAIL into KNOWN when a configured, unexpired known issue matches it.

    A known issue matches on check id (and group, e.g. `titiler:raster`, when it names one)
    when its `match` is in every one of the result's `problems`, so it cannot hide a new
    failure reported next to it. A FAIL that lists no problems is never downgraded: its
    summary may be a count ("12 visibility problem(s)") that would also cover a later,
    different failure. Evidence isn't searched: its detail rows over-match (ST08's
    compression rows also say "float64"). After `until`, the same failure is a FAIL again,
    so an accepted issue cannot hide forever.
    """
    for r in results:
        if r.status != FAIL:
            continue
        for k in known:
            if k["check"] != r.id or k.get("group", r.group) != r.group or today > dt.date.fromisoformat(str(k["until"])):
                continue
            if not r.problems:
                r.evidence.append(f"known issue not applied ({k['ref']}): {r.id} lists no individual problems to match")
                break
            if all(k["match"] in p for p in r.problems):
                r.status, r.known_issue = KNOWN, f"{k['ref']} (until {k['until']})"
                break
