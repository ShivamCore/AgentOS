"""
Redis-backed sliding-window rate limiter.

Usage inside a FastAPI route:
    from backend.api.rate_limiter import RateLimiter
    limiter = RateLimiter()

    @router.post("/task")
    def create_task(request: Request, ...):
        limiter.check(request)   # raises HTTP 429 if over limit
        ...
"""
import time
import redis as redis_pkg
from fastapi import HTTPException, Request
from backend.config import settings


class RateLimiter:
    """Sliding-window counter stored in Redis per client IP."""

    def __init__(self) -> None:
        self._redis = redis_pkg.from_url(settings.REDIS_URL, decode_responses=True)
        self._limit = settings.RATE_LIMIT_RPM
        self._window = 60  # seconds

    def _client_key(self, request: Request) -> str:
        # Use X-Forwarded-For if behind a reverse proxy, else remote addr
        forwarded = request.headers.get("X-Forwarded-For")
        ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
        return f"rl:{ip}"

    def check(self, request: Request) -> None:
        """
        Increments the per-IP request counter and raises HTTP 429 if the
        client has exceeded RATE_LIMIT_RPM requests in the last 60 seconds.
        Uses INCR + EXPIRE for atomic sliding window without Lua scripting.
        """
        try:
            key = self._client_key(request)
            pipe = self._redis.pipeline(transaction=True)
            pipe.incr(key)
            pipe.expire(key, self._window)
            count, _ = pipe.execute()
            if count > self._limit:
                retry_after = self._window
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded: max {self._limit} requests/minute.",
                    headers={"Retry-After": str(retry_after)},
                )
        except HTTPException:
            raise
        except Exception:
            # If Redis is down, fail open (don't block the user)
            pass
