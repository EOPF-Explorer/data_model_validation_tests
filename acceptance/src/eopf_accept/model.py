"""Check results and the known-issue rule."""

import datetime as dt
from dataclasses import asdict, dataclass, field

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

    def to_dict(self) -> dict:
        return asdict(self)


def apply_known_issues(results: list[Result], known: list[dict], today: dt.date) -> None:
    """Turn a FAIL into KNOWN when a configured, unexpired known issue matches it.

    A known issue matches on check id (and group, e.g. `titiler:raster`, when it names one)
    plus a substring of the summary or evidence. After `until`, the same failure is a FAIL
    again, so an accepted issue cannot hide forever.
    """
    for r in results:
        if r.status != FAIL:
            continue
        text = " ".join([r.summary, *r.evidence])
        for k in known:
            if (k["check"] == r.id and k.get("group", r.group) == r.group and k["match"] in text
                    and today <= dt.date.fromisoformat(str(k["until"]))):
                r.status, r.known_issue = KNOWN, f"{k['ref']} (until {k['until']})"
                break
