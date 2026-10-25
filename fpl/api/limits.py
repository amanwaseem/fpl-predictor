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
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request


def parse(setting):
    """(requests, seconds) from '10/60', or None for 'off'."""
    if setting.strip().lower() in ("off", "0", ""):
        return None
    count, _, seconds = setting.partition("/")
    return int(count), float(seconds)


class SlidingWindow:
    def __init__(self, limit, clock=time.monotonic):
        self.limit = limit
        self.clock = clock
        self.seen = defaultdict(deque)
        self.lock = threading.Lock()

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
            # Forget clients with nothing in the window, so memory stays bounded.
            if len(self.seen) > 10_000:
                for key in [k for k, v in self.seen.items() if not v]:
                    del self.seen[key]
            return None


solver_window = SlidingWindow(parse(os.environ.get("FPL_SOLVER_RATE", "10/60")))


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
