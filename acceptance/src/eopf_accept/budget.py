"""The request budget: the run's bound on requests, enforced inside the tool.

Two layers:
1. Before anything is sent, `Budget.reserve` adds up each planned check's declared
   maximum. If the total exceeds the cap, the run refuses to start.
2. Every request, to an HTTP endpoint or to the store, calls `Budget.take()` *before*
   it is sent. Once the cap is reached, `take()` raises and nothing more goes out.

There is no watcher, timeout or signal anywhere: the run stops because the tool won't
issue request N+1 (see "Bounded and destructive operations" in ~/.claude/CLAUDE.md).
"""

import json
import secrets
import time
from pathlib import Path

import httpx


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    def __init__(self, max_requests: int):
        if max_requests < 1:
            raise ValueError("max_requests must be >= 1")
        self.max = max_requests
        self.used = 0
        self.planned: dict[str, int] = {}

    def reserve(self, check_id: str, upper_bound: int) -> None:
        self.planned[check_id] = self.planned.get(check_id, 0) + upper_bound

    def assert_plan_fits(self) -> None:
        total = sum(self.planned.values())
        if total > self.max:
            raise BudgetExceeded(
                f"planned upper bound {total} requests exceeds --max-requests {self.max}: "
                + ", ".join(f"{k}={v}" for k, v in sorted(self.planned.items()))
            )

    def take(self, n: int = 1) -> None:
        if self.used + n > self.max:
            raise BudgetExceeded(f"request {self.used + n} would exceed --max-requests {self.max}")
        self.used += n


class Http:
    """A GET-only HTTP client that spends the budget, cache-busts and logs every request.

    GET only: this tool never writes. Every URL gets `cb=<run nonce>-<seq>` unless
    `bust=False` (for STAC and other non-titiler reads), so no titiler response is ever
    served from the tile cache by accident. The nonce is fresh per run (harness trap 2).
    """

    def __init__(self, budget: Budget, log_path: Path | None = None, timeout: float = 120.0):
        self.budget = budget
        self.nonce = f"ea{int(time.time())}{secrets.token_hex(3)}"
        self.seq = 0
        self.log_path = log_path
        # No redirect following: each hop would be a physical request the budget didn't count.
        self.client = httpx.Client(
            http2=True,
            follow_redirects=False,
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={"User-Agent": "eopf-accept/0.1 (read-only acceptance checks)"},
        )

    def get(self, url: str, params: list[tuple[str, str]] | None = None, *, bust: bool = True) -> httpx.Response:
        # httpx *replaces* a URL's query when given params, so merge them: an item link's
        # render parameters must survive the cache-buster.
        u = httpx.URL(url)
        params = list(u.params.multi_items()) + list(params or [])
        url = str(u.copy_with(query=None))
        if bust:
            self.seq += 1
            params.append(("cb", f"{self.nonce}-{self.seq}"))
        self.budget.take()
        t0 = time.perf_counter()
        try:
            resp = self.client.get(url, params=params)
        except httpx.HTTPError as exc:
            self._log(url, params, None, time.perf_counter() - t0, error=f"{type(exc).__name__}: {exc}")
            raise
        self._log(url, params, resp, time.perf_counter() - t0)
        return resp

    def _log(self, url, params, resp, seconds, error=None) -> None:
        if not self.log_path:
            return
        row = {
            "url": str(resp.request.url) if resp is not None else url,
            "status": resp.status_code if resp is not None else None,
            "x_cache": resp.headers.get("x-cache") if resp is not None else None,
            "bytes": len(resp.content) if resp is not None else None,
            "seconds": round(seconds, 4),
            "error": error,
        }
        with self.log_path.open("a") as fh:
            fh.write(json.dumps(row) + "\n")

    def close(self) -> None:
        self.client.close()
