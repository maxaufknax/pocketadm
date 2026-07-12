"""Background jobs with live-followable logs.

Long operations (image pulls, container recreates, report runs) run
server-side and survive the phone locking its screen. Clients follow
progress via a chunked text stream and can re-attach at any time.
"""
import asyncio
import secrets
import time
from typing import AsyncIterator, Awaitable, Callable

_jobs: dict[str, "Job"] = {}
MAX_JOBS = 40


class Job:
    def __init__(self, title: str, kind: str):
        self.id = secrets.token_hex(6)
        self.title = title
        self.kind = kind
        self.status = "running"          # running | done | error
        self.created = time.time()
        self.finished: float | None = None
        self.lines: list[str] = []
        self._event = asyncio.Event()

    def log(self, line: str) -> None:
        # \r progress lines overwrite the previous one (docker-pull style)
        if line.startswith("\r") and self.lines:
            self.lines[-1] = line.lstrip("\r")
        else:
            self.lines.append(line)
        self._event.set()
        self._event = asyncio.Event()

    def step(self, label: str) -> "_Step":
        """Context manager: logs the label, then keeps emitting a live
        'still working (Xs)' heartbeat every few seconds until the step ends,
        so slow operations (big pulls, nextcloud recreates …) never look stuck."""
        return _Step(self, label)

    def finish(self, ok: bool, line: str = "") -> None:
        if line:
            self.log(line)
        self.status = "done" if ok else "error"
        self.finished = time.time()
        self._event.set()

    def as_dict(self, tail: int = 40) -> dict:
        return {"id": self.id, "title": self.title, "kind": self.kind,
                "status": self.status, "created": self.created,
                "finished": self.finished, "log_tail": self.lines[-tail:]}

    async def follow(self) -> AsyncIterator[str]:
        """Yield log lines from the start, then live until the job ends."""
        idx = 0
        while True:
            while idx < len(self.lines):
                yield self.lines[idx] + "\n"
                idx += 1
            if self.status != "running":
                yield f"[job {self.status}]\n"
                return
            event = self._event
            try:
                await asyncio.wait_for(event.wait(), timeout=15)
            except asyncio.TimeoutError:
                yield ""  # keep-alive chunk


class _Step:
    def __init__(self, job: Job, label: str):
        self.job = job
        self.label = label
        self._hb: asyncio.Task | None = None

    async def __aenter__(self) -> "_Step":
        self.job.log(self.label)
        self._start = time.monotonic()
        self._hb = asyncio.ensure_future(self._heartbeat())
        return self

    async def _heartbeat(self) -> None:
        # client folds consecutive ⏳ lines into one live-updating line
        await asyncio.sleep(6)
        while True:
            elapsed = int(time.monotonic() - self._start)
            self.job.log(f"⏳ still working … ({elapsed}s)")
            await asyncio.sleep(5)

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._hb:
            self._hb.cancel()
        if exc_type is None and time.monotonic() - self._start > 6:
            self.job.log(f"  ✓ step finished after {int(time.monotonic() - self._start)}s")


def start(title: str, kind: str, work: Callable[[Job], Awaitable[None]]) -> Job:
    job = Job(title, kind)
    _jobs[job.id] = job
    if len(_jobs) > MAX_JOBS:  # drop oldest finished
        for jid in sorted(_jobs, key=lambda j: _jobs[j].created):
            if _jobs[jid].status != "running":
                del _jobs[jid]
            if len(_jobs) <= MAX_JOBS:
                break

    async def runner() -> None:
        try:
            await work(job)
            if job.status == "running":
                job.finish(True, "✓ done")
        except Exception as e:
            job.finish(False, f"✗ {type(e).__name__}: {e}")

    asyncio.ensure_future(runner())
    return job


def get(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def recent(kind: str | None = None) -> list[dict]:
    items = [j for j in _jobs.values() if kind is None or j.kind == kind]
    return [j.as_dict(tail=3) for j in sorted(items, key=lambda j: -j.created)]
