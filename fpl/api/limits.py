"""Rate limiting for the solver routes (#83).

    FPL_SOLVER_RATE=10/60     # ten requests a minute per client (the default)
    FPL_SOLVER_RATE=off       # no limit: for a load test against a local server

A sliding window per client, in memory. Each machine counts its own clients,
so with N machines a client gets up to N times the rate. That is the right
trade for a free service on one or two machines that stop when idle: the limit
is there so one client cannot keep the solver busy for everyone else, not to
meter anybody. On Fly (FPL_HOSTED), the client's address is Fly-Client-IP,
which Fly's proxy sets; anywhere else the header is anyone's to send, so the
socket's peer is used.
"""

import os
import sys
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

DEFAULT = "10/60"
# Clients remembered before the stale ones are swept out.
SWEEP_AT = 10_000


def parse(setting):
    """(requests, seconds) from '10/60', or None for 'off'. ValueError if neither."""
    if setting.strip().lower() in ("off", "0", ""):
        return None
    count, slash, seconds = setting.partition("/")
    if not slash or int(count) < 1 or float(seconds) <= 0:
        raise ValueError(f"{setting!r} is not a rate: want requests/seconds, like 10/60, or off")
    return int(count), float(seconds)


def configured(setting):
    """The limit from FPL_SOLVER_RATE. A setting that cannot be read falls back to the
    default with a warning, rather than stopping every route from starting."""
    try:
        return parse(setting)
    except ValueError as e:
        print(f"FPL_SOLVER_RATE: {e}; using {DEFAULT}.", file=sys.stderr)
        return parse(DEFAULT)


class SlidingWindow:
    def __init__(self, limit, clock=time.monotonic):
        self.limit = limit
        self.clock = clock
        self.seen = defaultdict(deque)
        self.lock = threading.Lock()
        self.sweep_at = SWEEP_AT

    def allow(self, client):
        """None if `client` may go ahead, else the seconds until it may."""
        if self.limit is None:
            return None
        count, seconds = self.limit
        now = self.clock()
        with self.lock:
            times = self.seen[client]
            while times and times[0] <= now - seconds:
                times.popleft()
            if len(times) >= count:
                return times[0] + seconds - now
            times.append(now)
            if len(self.seen) > self.sweep_at:
                self._sweep(now - seconds)
            return None

    def _sweep(self, cutoff):
        """Forget every client with nothing left in the window, so memory stays bounded.
        The next sweep waits until the clients still here have doubled, so a crowd that
        is all active costs one sweep per doubling, not one per request."""
        for key in [k for k, v in self.seen.items() if not v or v[-1] <= cutoff]:
            del self.seen[key]
        self.sweep_at = max(SWEEP_AT, 2 * len(self.seen))


solver_window = SlidingWindow(configured(os.environ.get("FPL_SOLVER_RATE", DEFAULT)))


def client_of(request: Request, environ=os.environ):
    if environ.get("FPL_HOSTED") == "1" and request.headers.get("fly-client-ip"):
        return request.headers["fly-client-ip"]
    return request.client.host if request.client else "unknown"


def solver_rate(request: Request):
    """A dependency for the solver routes: 429 with Retry-After once over the rate."""
    wait = solver_window.allow(client_of(request))
    if wait is not None:
        raise HTTPException(429, "Too many optimiser requests; try again shortly.",
                            headers={"Retry-After": str(max(1, int(wait + 0.999)))})
