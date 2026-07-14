"""Persistent, cross-session rate limiter for the scarce Codex/SakanaAI call.

Only the Codex implementation call consumes the SakanaAI subscription quota
(Claude design/review use a separate pool), so throttling *that* call is what
keeps a 24/7 loop under the plan limit. The last-call timestamp is persisted to
disk so the minimum interval is respected even across separate orchestrator
invocations and sessions — a fresh process cannot accidentally burst.

Time and sleep are injectable so the wait logic is unit-testable without a clock.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from . import config

DEFAULT_STATE_FILE = config.RUN_DIR / "codex_rate_limit.json"


class CodexRateLimiter:
    def __init__(
        self,
        min_interval_s: float,
        state_file: Path | None = None,
    ) -> None:
        self.min_interval_s = max(0.0, float(min_interval_s))
        self.state_file = state_file or DEFAULT_STATE_FILE

    def _load(self) -> dict:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self, data: dict) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
        )

    def last_call_ts(self) -> float:
        return float(self._load().get("last_codex_call_ts", 0.0))

    def total_calls(self) -> int:
        return int(self._load().get("total_calls", 0))

    def seconds_until_allowed(self, now: float) -> float:
        """Seconds to wait before the next Codex call is permitted (0 = now)."""
        last = self.last_call_ts()
        if last <= 0.0:
            return 0.0  # no prior call recorded — first call never waits
        remaining = (last + self.min_interval_s) - now
        return max(0.0, remaining)

    def reserve(self, now: float, *, usage_limited: bool = False) -> None:
        """Record that a Codex call happened at `now`."""
        data = self._load()
        data["last_codex_call_ts"] = float(now)
        data["total_calls"] = int(data.get("total_calls", 0)) + 1
        # Rolling log of recent call timestamps + whether the provider reported
        # a usage limit — this is the raw signal for empirically measuring the
        # real quota (see docs: run conservatively, watch when limits first hit).
        recent = data.get("recent_calls", [])
        recent.append({"ts": float(now), "usage_limited": bool(usage_limited)})
        data["recent_calls"] = recent[-200:]
        self._save(data)

    def note_last_result(self, *, usage_limited: bool) -> None:
        """Annotate the most recent reserved call with the provider outcome.

        reserve() runs BEFORE the Codex call (so a crash still counts against
        the interval and can't burst), when usage_limited isn't known yet; this
        back-fills it once the call returns, for empirical quota measurement.
        """
        data = self._load()
        recent = data.get("recent_calls", [])
        if recent:
            recent[-1]["usage_limited"] = bool(usage_limited)
            data["recent_calls"] = recent
            self._save(data)

    def wait_and_reserve(
        self,
        *,
        sleep_fn: Callable[[float], None] = time.sleep,
        now_fn: Callable[[], float] = time.time,
        max_single_sleep_s: float = 30.0,
        log_fn: Callable[[str], None] | None = None,
    ) -> float:
        """Block until a Codex call is allowed, reserve the slot, return waited s.

        Sleeps in <=max_single_sleep_s chunks so a long wait stays responsive to
        interruption. reserve() is called only once, after the wait completes.
        """
        start = now_fn()
        if log_fn:
            rem0 = self.seconds_until_allowed(start)
            if rem0 > 0:
                log_fn(
                    f"[rate-limit] next Codex call in {rem0/60:.1f} min "
                    f"(min interval {self.min_interval_s/60:.1f} min)"
                )
        while True:
            rem = self.seconds_until_allowed(now_fn())
            if rem <= 0:
                break
            sleep_fn(min(rem, max_single_sleep_s))
        self.reserve(now_fn())
        return now_fn() - start
