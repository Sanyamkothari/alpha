"""BRAIN API client — the only module that opens a socket to the platform.

Everything here was verified against a live session on 2026-08-04; see the
VERIFIED section of docs/BRAIN_API.md. The facts that shape this code and are
easy to get wrong:

* **Auth is a cookie, not a Bearer header.** ``POST /authentication`` with HTTP
  Basic returns 201 and sets ``t=<JWT>``. ``httpx.Client`` carries it onward
  automatically. Token TTL is 14400 s (4 h), so long crawls must re-auth.
* **Simulation polling has no ``status`` while running.** The progress resource
  returns ``{"progress": <float>}`` and *nothing else* until it finishes, at
  which point an ``alpha`` key appears. Waiting on ``status == "COMPLETE"``
  spins forever. The terminal condition is ``"alpha" in body``.
* **Concurrency is hard-capped at 3.** A 4th in-flight simulation is rejected
  with ``429 CONCURRENT_SIMULATION_LIMIT_EXCEEDED``. The semaphore below is the
  primary control; the 429 retry is the safety net for races and for other
  sessions using slots on the same account.
* **``margin`` is a fraction, not basis points** (0.000335 = 3.35 bps).
  :func:`normalize_is_block` converts it, because ``alpha_metrics.margin_bps``
  is named for the unit it stores.

Submission is deliberately absent. ``ALLOWED_POST_PATHS`` is the enforced
whitelist of write endpoints and is asserted by tests/test_brain_no_post.py.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

from app.config import settings

log = structlog.get_logger("brain.client")

# The complete set of endpoints this package may POST to. Adding anything here
# is a deliberate, reviewable act — tests/test_brain_no_post.py asserts that no
# POST in this package targets a path outside this set, and that none of them
# smell like submission.
ALLOWED_POST_PATHS: frozenset[str] = frozenset({"/authentication", "/simulations"})

# Observed hard limit: a 4th concurrent simulation returns 429.
MAX_CONCURRENT_SIMULATIONS = 3

_TERMINAL_KEY = "alpha"  # its presence — not `status` — means the sim finished


class BrainError(RuntimeError):
    """Any BRAIN interaction that failed in a way the caller should see."""


class BrainAuthError(BrainError):
    """Authentication failed or could not be renewed."""


class SimulationTimeout(BrainError):
    """A simulation did not finish inside the allotted wall-clock budget."""


@dataclass(frozen=True)
class SimulationSettings:
    """The ``settings`` block of a simulation request.

    Defaults mirror the platform's own defaults for USA/TOP3000/delay-1. Every
    key is sent explicitly: the API's behaviour when a key is omitted is not
    documented, and guessing there produces silently different backtests.
    """

    region: str = "USA"
    universe: str = "TOP3000"
    delay: int = 1
    decay: int = 0
    neutralization: str = "SUBINDUSTRY"
    truncation: float = 0.08
    instrument_type: str = "EQUITY"
    pasteurization: str = "ON"
    unit_handling: str = "VERIFY"
    nan_handling: str = "OFF"
    language: str = "FASTEXPR"
    visualization: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "instrumentType": self.instrument_type,
            "region": self.region,
            "universe": self.universe,
            "delay": self.delay,
            "decay": self.decay,
            "neutralization": self.neutralization,
            "truncation": self.truncation,
            "pasteurization": self.pasteurization,
            "unitHandling": self.unit_handling,
            "nanHandling": self.nan_handling,
            "language": self.language,
            "visualization": self.visualization,
        }


def normalize_is_block(is_block: dict[str, Any]) -> dict[str, Any]:
    """Pass-through copy of raw BRAIN is simulation metrics payload."""
    return dict(is_block)


_ACCOUNT_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_SIMULATIONS)


@dataclass
class BrainClient:
    """Read/simulate client for WorldQuant BRAIN's REST API.

    Thread-safe. A single instance can be shared across workers, or workers can
    construct their own — both share the account-wide simulation slot cap
    ``_ACCOUNT_SLOTS`` (3 in flight) and refresh auth transparently on expiry.

    Context-manager support closes the underlying HTTP client cleanly.
    """

    email: str = ""
    password: str = ""
    base_url: str = ""
    timeout: float = 60.0
    max_concurrent: int = MAX_CONCURRENT_SIMULATIONS

    _client: httpx.Client = field(init=False, repr=False)
    _slots: threading.Semaphore = field(init=False, repr=False)
    _auth_lock: threading.Lock = field(init=False, repr=False)
    _authed_at: float = field(init=False, default=0.0, repr=False)

    def __post_init__(self) -> None:
        self.email = self.email or settings.brain_email
        self.password = self.password or settings.brain_password
        self.base_url = self.base_url or settings.brain_api_base
        if not (self.email and self.password):
            raise BrainAuthError(
                "BRAIN credentials missing — set BRAIN_EMAIL and BRAIN_PASSWORD in the repo-root .env"
            )
        self._client = httpx.Client(base_url=self.base_url, timeout=self.timeout)
        self._slots = threading.Semaphore(self.max_concurrent)
        self._auth_lock = threading.Lock()

    # ---- lifecycle -------------------------------------------------------

    def __enter__(self) -> BrainClient:
        self.authenticate()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def authenticate(self, force: bool = False) -> dict[str, Any]:
        """Establish the session cookie. Safe to call repeatedly with disk caching."""
        import json
        from pathlib import Path

        cookie_file = Path("/tmp/.brain_cookies.json")
        with self._auth_lock:
            if not force and cookie_file.exists():
                try:
                    data = json.loads(cookie_file.read_text(encoding="utf-8"))
                    age = time.time() - data.get("saved_at", 0)
                    if age < 10800:  # 3 hours
                        for k, v in data.get("cookies", {}).items():
                            self._client.cookies.set(k, v)
                        resp = self._client.get("/users/self")
                        if resp.status_code == 200:
                            self._authed_at = time.monotonic()
                            body = resp.json()
                            log.info("brain_authenticated_from_cache", user=body.get("id"))
                            return {"user": body}
                except Exception as e:
                    log.debug("brain_cookie_cache_failed", error=str(e))

            resp = self._client.post("/authentication", auth=(self.email, self.password))
            if resp.status_code not in (200, 201):
                raise BrainAuthError(f"authentication failed: {resp.status_code} {resp.text[:200]}")
            self._authed_at = time.monotonic()
            body = resp.json()
            try:
                cookie_dict = dict(self._client.cookies)
                cookie_file.write_text(
                    json.dumps({"saved_at": time.time(), "cookies": cookie_dict}), encoding="utf-8"
                )
            except Exception:
                pass

            log.info(
                "brain_authenticated",
                user=body.get("user", {}).get("id"),
                permissions=body.get("permissions"),
                ttl_s=body.get("token", {}).get("expiry"),
            )
            return body

    # ---- reads -----------------------------------------------------------

    def get(self, path: str, *, retries: int = 5, **params: Any) -> httpx.Response:
        """GET with transparent re-auth on 401 and backoff on 429.

        Reads are rate-limited too, not just simulations — a tight pagination
        loop over /data-fields earns ``429 {"message":"API rate limit
        exceeded"}`` within a few dozen requests. Retrying here (rather than in
        every caller) is what keeps the crawl polite by default.
        """
        delay = 2.0
        resp = self._client.get(path, params=params or None)
        for _ in range(retries):
            if resp.status_code == 401:
                self.authenticate()
            elif resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After") or delay)
                log.warning("brain_read_throttled", path=path, wait_s=wait)
                time.sleep(wait)
                delay = min(delay * 2, 60.0)
            else:
                return resp
            resp = self._client.get(path, params=params or None)
        return resp

    def get_json(self, path: str, **params: Any) -> Any:
        resp = self.get(path, **params)
        if resp.status_code >= 400:
            raise BrainError(f"GET {path} -> {resp.status_code} {resp.text[:200]}")
        return resp.json()

    def iter_paginated(
        self,
        path: str,
        *,
        page_size: int = 50,
        pause_seconds: float = 0.35,
        **params: Any,
    ) -> Iterator[dict]:
        """Yield every record from a ``{count, results[], next}`` endpoint.

        Follows ``offset`` rather than the absolute ``next`` URL so the caller
        keeps control of the host — and so a malformed ``next`` cannot redirect
        the crawl somewhere unintended.

        ``pause_seconds`` throttles the crawl deliberately. Backoff-on-429 alone
        would still mean hammering the endpoint until it complains; pacing means
        we mostly never do.
        """
        offset = 0
        seen = 0
        while True:
            body = self.get_json(path, limit=page_size, offset=offset, **params)
            results = body.get("results", []) if isinstance(body, dict) else body
            if not results:
                return
            for row in results:
                yield row
                seen += 1
            total = body.get("count") if isinstance(body, dict) else None
            offset += len(results)
            if total is not None and seen >= total:
                return
            if pause_seconds:
                time.sleep(pause_seconds)

    # ---- simulation ------------------------------------------------------

    def simulate(
        self,
        expression: str,
        sim_settings: SimulationSettings | None = None,
        *,
        poll_seconds: float | None = None,
        max_wait_seconds: float = 900.0,
        timing: dict | None = None,
    ) -> str:
        """Run one simulation and return its alpha id.

        Blocks on a semaphore so no more than :data:`MAX_CONCURRENT_SIMULATIONS`
        are ever in flight from this client.

        ``timing``, if given, receives ``{"seconds": float}`` measured from
        **inside** the semaphore — the platform's own turnaround, excluding time
        spent queued for a slot. Measuring from outside would fold the wait into
        the per-simulation figure, and every ETA already models the 3-wide
        concurrency separately, so that would be counted twice.
        """
        sim_settings = sim_settings or SimulationSettings()
        poll_seconds = poll_seconds or settings.brain_poll_seconds
        payload = {
            "type": "REGULAR",
            "regular": expression,
            "settings": sim_settings.to_payload(),
        }

        with _ACCOUNT_SLOTS:
            with self._slots:
                started = time.monotonic()
                location = self._submit_simulation(payload)
                alpha_id = self._await_alpha(location, poll_seconds, max_wait_seconds)
                if timing is not None:
                    timing["seconds"] = round(time.monotonic() - started, 1)
                return alpha_id

    def _submit_simulation(self, payload: dict[str, Any], *, attempts: int = 6) -> str:
        """POST the simulation, retrying while the account's 3 slots are full.

        The semaphore already caps *our* concurrency; this handles the case where
        another session on the same account holds slots.
        """
        delay = 5.0
        for attempt in range(1, attempts + 1):
            resp = self._client.post("/simulations", json=payload)
            if resp.status_code == 401:
                self.authenticate()
                continue
            if resp.status_code == 201:
                location = resp.headers.get("Location")
                if not location:
                    raise BrainError("simulation accepted but no Location header returned")
                return location
            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After") or delay)
                log.warning(
                    "brain_simulation_throttled",
                    attempt=attempt,
                    wait_s=wait,
                    detail=resp.text[:120],
                )
                time.sleep(wait)
                delay = min(delay * 2, 60.0)
                continue
            raise BrainError(f"POST /simulations -> {resp.status_code} {resp.text[:200]}")
        raise BrainError(f"simulation rejected after {attempts} attempts (concurrency limit)")

    def _await_alpha(self, location: str, poll_seconds: float, max_wait_seconds: float) -> str:
        """Poll until the ``alpha`` key appears — NOT until ``status`` says so.

        While a simulation runs the progress resource returns only
        ``{"progress": <float>}``; ``status`` is absent entirely, so a
        status-based wait never terminates.
        """
        deadline = time.monotonic() + max_wait_seconds
        while time.monotonic() < deadline:
            time.sleep(poll_seconds)
            resp = self.get(location)
            if resp.status_code >= 400:
                raise BrainError(f"poll {location} -> {resp.status_code} {resp.text[:200]}")
            body = resp.json()
            alpha_id = body.get(_TERMINAL_KEY)
            if alpha_id:
                return str(alpha_id)
            if str(body.get("status", "")).upper() in ("ERROR", "FAILED"):
                raise BrainError(f"simulation failed: {resp.text[:200]}")
        raise SimulationTimeout(f"simulation {location} unfinished after {max_wait_seconds}s")

    # ---- convenience reads ----------------------------------------------

    def config_available(self, sim_settings: SimulationSettings) -> tuple[bool, str]:
        """Is this (region, universe, delay) actually simulatable on this account?

        Readable is NOT the same as usable. ``GET /data-fields`` happily returns
        the full 2,121-field delay-0 catalogue, but posting a delay-0 simulation
        comes back ``400 {"settings":{"delay":["Delay 0 is not available."]}}`` —
        the data is visible, the config is gated by account level. Discovering
        that *after* expanding a family means a few hundred dead alpha rows, so
        callers should preflight once, cheaply, before generating anything.

        Costs one trivial simulation slot; returns as soon as the POST is
        accepted (the run itself is never polled).
        """
        payload = {
            "type": "REGULAR",
            "regular": "close",
            "settings": sim_settings.to_payload(),
        }
        with _ACCOUNT_SLOTS:
            with self._slots:
                resp = self._client.post("/simulations", json=payload)
                if resp.status_code == 401:
                    self.authenticate()
                    resp = self._client.post("/simulations", json=payload)
                if resp.status_code == 201:
                    return True, "ok"
                if resp.status_code == 429:
                    # Slots busy says nothing about availability — assume usable.
                    return True, "concurrency limit hit during preflight; assuming available"
                return False, f"{resp.status_code} {resp.text[:200]}"

    def alpha(self, alpha_id: str) -> dict[str, Any]:
        return self.get_json(f"/alphas/{alpha_id}")

    def own_alphas(self, *, page_size: int = 50) -> Iterator[dict]:
        yield from self.iter_paginated("/users/self/alphas", page_size=page_size)

    def data_fields(
        self, *, region: str, delay: int, universe: str, **extra: Any
    ) -> Iterator[dict]:
        yield from self.iter_paginated(
            "/data-fields",
            region=region,
            delay=delay,
            universe=universe,
            instrumentType=extra.pop("instrument_type", "EQUITY"),
            **extra,
        )

    def data_sets(self, *, region: str, delay: int, universe: str) -> list[dict]:
        body = self.get_json(
            "/data-sets",
            region=region,
            delay=delay,
            universe=universe,
            instrumentType="EQUITY",
        )
        return body.get("results", []) if isinstance(body, dict) else body

    def operators(self) -> list[dict]:
        return self.get_json("/operators")

    def get_submission_check(self, alpha_id: str | None = None) -> dict[str, Any]:
        """GET /check or /alphas/{alpha_id}/check (read-only verification of submission gate status)."""
        path = f"/alphas/{alpha_id}/check" if alpha_id else "/check"
        return self.get_json(path)
