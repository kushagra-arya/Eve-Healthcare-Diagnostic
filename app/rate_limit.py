import logging
from math import ceil
from threading import Lock
from time import monotonic

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import Settings
from app.exceptions import error_response

logger = logging.getLogger("eve.rate_limit")


class RateLimiter:
    """Fixed-window counters, local to one application process."""

    def __init__(self, settings: Settings):
        self.limits = {
            "/auth/signup": settings.auth_rate_limit,
            "/auth/login": settings.auth_rate_limit,
            "/payments": settings.payment_rate_limit,
            "/payments/webhook": settings.webhook_rate_limit,
        }
        self.enabled = settings.rate_limit_enabled
        self.window = settings.rate_limit_window_seconds
        self.clock = monotonic
        self.counters: dict[tuple[str, str], tuple[int, float]] = {}
        self.lock = Lock()
        self.max_keys = 10_000

    def check(self, path: str, address: str) -> int | None:
        limit = self.limits.get(path)
        if not self.enabled or limit is None:
            return None
        with self.lock:
            now = self.clock()
            key = (path, address)
            count, expires = self.counters.get(key, (0, now))
            if expires <= now:
                if key not in self.counters and len(self.counters) >= self.max_keys:
                    self.counters = {
                        key: counter for key, counter in self.counters.items() if counter[1] > now
                    }
                    # Bound memory even when clients keep using new source addresses.
                    if len(self.counters) >= self.max_keys:
                        return max(1, ceil(min(value[1] for value in self.counters.values()) - now))
                count, expires = 0, now + self.window
            if count >= limit:
                return max(1, ceil(expires - now))
            self.counters[key] = (count + 1, expires)
        return None


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp, limiter: RateLimiter):
        self.app = app
        self.limiter = limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http" and scope["method"] == "POST":
            request = Request(scope)
            # Forwarded headers are not trusted; only the connected peer is used.
            address = request.client.host if request.client else "unknown"
            retry_after = self.limiter.check(scope["path"].rstrip("/"), address)
            if retry_after is not None:
                logger.warning(
                    "rate_limit_exceeded",
                    extra={
                        "fields": {
                            "endpoint": scope["path"].rstrip("/"),
                            "retry_after": retry_after,
                        }
                    },
                )
                response = error_response(
                    request,
                    429,
                    "rate_limit_exceeded",
                    "Too many requests",
                    headers={"Retry-After": str(retry_after)},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
