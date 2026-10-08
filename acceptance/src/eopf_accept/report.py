"""run.json (machine-readable) and report.md (for a human)."""

import json
from dataclasses import asdict
from pathlib import Path

from .model import FAILING, Result

ORDER = ["FAIL", "VOID", "WARN", "KNOWN", "XPASS", "XFAIL", "SKIP", "PASS"]


def verdict(results: list[Result]) -> str:
    if any(r.status in FAILING for r in results):
        return "FAIL"
    if any(r.status == "VOID" for r in results):
        return "VOID"
    return "PASS"


def write(out_dir: Path, meta: dict, results: list[Result]) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    run = {**meta, "verdict": verdict(results), "results": [asdict(r) for r in results]}
    (out_dir / "run.json").write_text(json.dumps(run, indent=2, default=str))

    lines = [
        f"# Acceptance run: {meta['collection']} — {verdict(results)}",
        "",
        f"- store: `{meta['store']}`",
        f"- item: `{meta.get('item') or '-'}`  stage: `{meta['stage']}`",
        f"- endpoints: {', '.join(f'{k} ({v})' for k, v in meta.get('endpoints', {}).items()) or '-'}",
        f"- run: {meta['started']} → {meta['finished']}, nonce `{meta['nonce']}`, requests {meta['requests_used']}/{meta['max_requests']}",
        f"- store read path: {meta.get('read_path', 'origin')}",
        f"- store reads: {meta.get('store_reads', '-')}",
        *([f"- answered 403, counted as missing ({len(f)}): " + ", ".join(f"`{k}`" for k in f[:20]) + (" …" if len(f) > 20 else "")]
          if (f := meta.get("forbidden_as_missing")) else []),
        "",
        "| check | group | status | summary |",
        "|---|---|---|---|",
    ]
    for r in sorted(results, key=lambda r: (ORDER.index(r.status), r.group, r.id)):
        note = f" — known issue: {r.known_issue}" if r.known_issue else ""
        lines.append(f"| {r.id} | {r.group} | **{r.status}** | {r.summary.replace('|', '/')}{note} |")
    by_id: dict[str, dict[str, str]] = {}
    for r in results:
        if r.group.startswith("titiler:"):
            by_id.setdefault(r.id, {})[r.group.split(":", 1)[1]] = r.status
    endpoints = sorted({e for d in by_id.values() for e in d})
    if len(endpoints) > 1:
        lines += ["", "## Endpoint comparison", "", "| check | " + " | ".join(endpoints) + " |", "|---|" + "---|" * len(endpoints)]
        for cid in sorted(by_id):
            lines.append(f"| {cid} | " + " | ".join(by_id[cid].get(e, "-") for e in endpoints) + " |")
    lines += ["", "## Evidence", ""]
    for r in results:
        if r.evidence:
            lines.append(f"### {r.id} ({r.group}) — {r.status}")
            lines += [f"- {e}" for e in r.evidence]
            lines.append("")
    (out_dir / "report.md").write_text("\n".join(lines) + "\n")
    return out_dir / "run.json", out_dir / "report.md"
