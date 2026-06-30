"""
Reusable loop runner — the spine both pipeline loops sit on.

A *loop* iterates a list of items (countries, airports, procedures), runs one
``step`` per item, and is:

  * **AIRAC-tagged**   — every run is scoped to one 28-day cycle; output and
    checkpoint files are keyed by ``(loop_name, airac)`` so cycles never collide.
  * **idempotent**     — an item already completed for this cycle is skipped on
    re-run (``--force`` overrides). Re-running a finished loop is a no-op.
  * **resumable**      — progress is checkpointed to ``data/state/`` incrementally,
    so a container reclaim mid-run loses at most one checkpoint window.
  * **observable**     — structured per-item logging plus a final summary
    (counts by status, a flag histogram) so a run is auditable at a glance.

The runner is deliberately generic: it knows nothing about airports or AIP
sources. ``miner/loop/census.py`` supplies the US step; Loop 2 will supply its
own step against the same runner.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = ROOT / "data" / "state"

# Item outcomes. A loop step returns one of these; the runner tallies them.
OK = "ok"            # processed cleanly, no problems
FLAGGED = "flagged"  # processed, but a self-check raised a non-fatal flag
PENDING = "pending"  # could not process yet (no parser / source offline) — not an error
ERROR = "error"      # step raised; recorded with the message, loop continues
SKIPPED = "skipped"  # already done this cycle (idempotent re-run)

_TERMINAL = {OK, FLAGGED, PENDING}  # statuses that count as "done" for resume


@dataclass
class StepResult:
    """What a loop step returns for a single item."""
    key: str
    status: str                              # OK / FLAGGED / PENDING / ERROR
    data: dict = field(default_factory=dict)  # the item's record (deterministic; no timestamps)
    flags: list[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class RunSummary:
    loop: str
    airac: str
    total: int = 0
    by_status: dict = field(default_factory=dict)
    flag_histogram: dict = field(default_factory=dict)
    results: list[StepResult] = field(default_factory=list)

    def line(self) -> str:
        bits = " ".join(f"{k}={v}" for k, v in sorted(self.by_status.items()))
        return f"[{self.loop} {self.airac}] {self.total} items · {bits}"


class LoopRunner:
    """Drive ``step_fn`` over ``items`` with checkpointing and a summary.

    Parameters
    ----------
    name   : loop identifier, used in the checkpoint filename.
    airac  : 4-digit AIRAC cycle tag (e.g. "2607"); scopes the whole run.
    log    : sink for structured progress lines (defaults to print).
    """

    def __init__(self, name: str, airac: str, *, state_dir: Optional[Path] = None,
                 log: Callable[[str], None] = print, checkpoint_every: int = 200):
        self.name = name
        self.airac = airac
        # Resolve at call time (not as a default arg) so STATE_DIR stays monkeypatchable.
        self.state_dir = state_dir if state_dir is not None else STATE_DIR
        self.log = log
        self.checkpoint_every = checkpoint_every
        self.state_path = self.state_dir / f"{name}_{airac}.json"

    # ── checkpoint I/O ───────────────────────────────────────────────────────
    def _load_state(self) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text())
        return {"loop": self.name, "airac": self.airac, "items": {}}

    def _save_state(self, state: dict) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=0))
        tmp.replace(self.state_path)  # atomic on POSIX — never a half-written checkpoint

    def read_records(self) -> dict:
        """Return ``{key: checkpoint_item}`` for the whole cycle.

        The checkpoint stores each item's full result (status, flags, data), so
        this is the resume-complete source of truth — callers assemble their
        output artifact from here, not from a single invocation's results.
        """
        return self._load_state().get("items", {})

    # ── main loop ────────────────────────────────────────────────────────────
    def run(self, items: Iterable, step_fn: Callable[[object], StepResult], *,
            key_fn: Callable[[object], str] = lambda it: it["icao"],
            force: bool = False) -> RunSummary:
        items = list(items)
        state = {} if force else self._load_state()
        done = {} if force else state.get("items", {})
        summary = RunSummary(loop=self.name, airac=self.airac, total=len(items))

        processed = 0
        for i, item in enumerate(items, 1):
            key = key_fn(item)

            if not force and done.get(key, {}).get("status") in _TERMINAL:
                summary.by_status[SKIPPED] = summary.by_status.get(SKIPPED, 0) + 1
                continue

            try:
                res = step_fn(item)
            except Exception as e:  # a bad item must never sink the whole cycle
                res = StepResult(key=key, status=ERROR, error=f"{type(e).__name__}: {e}")

            summary.results.append(res)
            summary.by_status[res.status] = summary.by_status.get(res.status, 0) + 1
            for fl in res.flags:
                summary.flag_histogram[fl] = summary.flag_histogram.get(fl, 0) + 1

            done[key] = {"status": res.status, "flags": res.flags,
                         "error": res.error, "data": res.data, "ts": int(time.time())}
            processed += 1

            if res.status in (FLAGGED, ERROR):
                tail = f" — {res.error}" if res.error else f" {res.flags}"
                self.log(f"  · {key}: {res.status}{tail}")

            if processed % self.checkpoint_every == 0:
                state["items"] = done
                self._save_state(state)
                self.log(f"  … {i}/{len(items)} (checkpoint)")

        state["items"] = done
        self._save_state(state)
        self.log(summary.line())
        if summary.flag_histogram:
            top = sorted(summary.flag_histogram.items(), key=lambda kv: -kv[1])
            self.log("  flags: " + ", ".join(f"{k}×{v}" for k, v in top))
        return summary
