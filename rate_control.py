"""A shared, nonblocking request budget so the UI and quotes do not freeze."""
import time
import threading
from collections import deque
from data_utils import BudgetPause


class RequestBudget:
    def __init__(self, per_minute=45, clock=time.monotonic):
        self.limit = per_minute
        self.clock = clock
        self.events = deque()
        self.blocked_until = 0.0
        self.lock = threading.Lock()

    def acquire(self):
        with self.lock:
            now = self.clock()
            while self.events and now - self.events[0] >= 60:
                self.events.popleft()
            wait = max(0, self.blocked_until - now)
            if self.events:
                wait = max(wait, 1.05 - (now - self.events[-1]))
            if len(self.events) >= self.limit:
                wait = max(wait, 60 - (now - self.events[0]) + .05)
            if wait > 0:
                raise BudgetPause(wait)
            self.events.append(now)

    def cooldown(self, seconds=60):
        with self.lock:
            self.blocked_until = max(self.blocked_until, self.clock() + max(1, seconds))

    def status(self):
        now = self.clock()
        return {'requests_last_minute': sum(now - x < 60 for x in self.events), 'limit': self.limit,
                'cooldown_seconds': round(max(0, self.blocked_until - now), 1)}
