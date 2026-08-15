"""Guardrail: structurally enforce the no-auto-submit contract.

Retargeted 2026-08-04. It used to forbid *every* non-GET verb against BRAIN.
That rule conflated two different actions: simulating an alpha (a backtest on
your own account, which the platform provides an endpoint for) and submitting
one (irreversible, and always a human decision). Simulation is now automated;
submission is not. See docs/DECISIONS.md.

So the guardrail got narrower and more specific rather than being deleted — a
check that gets removed the first time it fires was never a check. It fails the
build if:

1. a non-GET HTTP verb appears in any non-allowlisted file that reaches the BRAIN
   host — detected by the literal host OR the centralized ``brain_api_base``
   config symbol every real caller goes through; or
2. any module outside the single allowlisted BRAIN package imports a raw HTTP
   client at all (httpx / requests / urllib / aiohttp); or
3. **the allowlisted package POSTs to any path outside its declared whitelist**
   — this is what replaces "no POST at all", and it is the load-bearing check; or
4. anything anywhere looks like a submission call; or
5. the ``brain_fetch_log`` table loses its ``http_method = 'GET'`` CHECK (the
   catalog fetcher is still genuinely read-only); or
6. a submission/trade WRITE table sneaks into the schema.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.models import Base
from app.models.brain import BrainFetchLog

APP_DIR = Path(__file__).resolve().parents[1] / "app"

# A file "reaches BRAIN" if it names the literal host OR the centralized config
# symbol. Gating on the literal alone is useless: the host lives only in
# config.py, so every real caller (which uses ``settings.brain_api_base``) would
# otherwise evade the scan entirely.
BRAIN_MARKERS = (
    re.compile(r"api\.worldquantbrain\.com"),
    re.compile(r"\bbrain_api_base\b"),
)

# The ONLY package allowed to talk to the BRAIN host. It may POST, but only to
# the endpoints in ALLOWED_POST_PATHS — see test_allowlisted_package_posts_only_to_whitelist.
ALLOWLISTED = {"services/brain"}

# Anything that reads as "submit this alpha to the platform". Forbidden
# everywhere, including inside the allowlisted package.
SUBMISSION_MARKERS = (
    re.compile(r"[\"'][^\"']*/submit[^\"']*[\"']"),  # a URL path containing /submit
    re.compile(r"\bdef\s+submit_alpha\b"),
    re.compile(r"/alphas/\{[^}]*\}/submit"),
)

# Dotted verbs: client.post(...) / .put / .patch / .delete.
DOTTED_VERB = re.compile(r"\.(post|put|patch|delete)\s*\(", re.IGNORECASE)
# Method-as-argument forms the dotted regex misses:
#   httpx.request("POST", url) / client.stream("POST", url) / session.send(...)
#   urllib.request.urlopen(Request(url, method="POST"))
METHOD_ARG = re.compile(
    r"\b(request|stream|send|urlopen)\s*\(.*?[\"'](POST|PUT|PATCH|DELETE)[\"']",
    re.IGNORECASE | re.DOTALL,
)
METHOD_KWARG = re.compile(r"\bmethod\s*=\s*[\"'](POST|PUT|PATCH|DELETE)[\"']", re.IGNORECASE)

# Raw HTTP clients. No module outside the fetcher may import these at all; the
# Anthropic SDK (`import anthropic`) is not a raw client and is fine.
RAW_HTTP_IMPORT = re.compile(
    r"^\s*(?:import\s+(?:httpx|requests|urllib\.request|aiohttp)\b"
    r"|from\s+(?:httpx|requests|urllib(?:\.request)?|aiohttp)\s+import)",
    re.MULTILINE,
)


def _rel(path: Path) -> str:
    return path.relative_to(APP_DIR).as_posix()


def _is_allowlisted(rel: str) -> bool:
    # Path-component match, NOT a raw string prefix: "services/brain_fetcher"
    # must not also exempt a sibling like "services/brain_fetcher_submit.py".
    return any(rel == a or rel.startswith(a + "/") for a in ALLOWLISTED)


def _reaches_brain(text: str) -> bool:
    return any(m.search(text) for m in BRAIN_MARKERS)


def _has_write_verb(text: str) -> bool:
    return bool(DOTTED_VERB.search(text) or METHOD_ARG.search(text) or METHOD_KWARG.search(text))


def test_no_write_verbs_against_brain_host() -> None:
    offenders: list[str] = []
    for py in APP_DIR.rglob("*.py"):
        rel = _rel(py)
        if _is_allowlisted(rel):
            continue
        text = py.read_text(encoding="utf-8")
        if _reaches_brain(text) and _has_write_verb(text):
            offenders.append(rel)
    assert not offenders, f"non-GET verb in a file that reaches BRAIN: {offenders}"


def test_no_raw_http_client_imports_outside_fetcher() -> None:
    # Defense in depth: even fully-indirect BRAIN access (no host literal in the
    # file) is blocked, because the only sanctioned HTTP path is the audited
    # GET-only fetcher package.
    offenders: list[str] = []
    for py in APP_DIR.rglob("*.py"):
        rel = _rel(py)
        if _is_allowlisted(rel):
            continue
        text = py.read_text(encoding="utf-8")
        if RAW_HTTP_IMPORT.search(text):
            offenders.append(rel)
    assert (
        not offenders
    ), f"raw HTTP client imported outside the allowlisted GET-only fetcher: {offenders}"


def test_allowlisted_package_posts_only_to_whitelist() -> None:
    """The load-bearing check.

    "No POST at all" was easy to enforce and is no longer the rule. What
    replaces it must be just as mechanical: every POST target inside the BRAIN
    package has to appear in that package's own declared whitelist. Adding an
    endpoint is then a visible, reviewable edit to ALLOWED_POST_PATHS rather
    than a line of code nobody notices.
    """
    from app.services.brain import ALLOWED_POST_PATHS

    # A submission endpoint must never be whitelisted, whatever else is.
    assert not any(
        "submit" in p.lower() for p in ALLOWED_POST_PATHS
    ), f"submission endpoint whitelisted: {sorted(ALLOWED_POST_PATHS)}"

    post_target = re.compile(r"\.post\(\s*[\"']([^\"']+)[\"']")
    offenders: list[str] = []
    for py in APP_DIR.rglob("*.py"):
        rel = _rel(py)
        if not _is_allowlisted(rel):
            continue
        for path in post_target.findall(py.read_text(encoding="utf-8")):
            if path not in ALLOWED_POST_PATHS:
                offenders.append(f"{rel}: POST {path}")
    assert (
        not offenders
    ), f"POST to a path outside ALLOWED_POST_PATHS {sorted(ALLOWED_POST_PATHS)}: {offenders}"


def test_no_submission_calls_anywhere() -> None:
    """No file may contain something that reads as an alpha submission —
    including the allowlisted package. This is the invariant that survives."""
    offenders: list[str] = []
    for py in APP_DIR.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        if any(m.search(text) for m in SUBMISSION_MARKERS):
            offenders.append(_rel(py))
    assert not offenders, f"submission code path found: {offenders}"


def test_brain_fetch_log_has_get_only_check() -> None:
    checks = [
        c for c in BrainFetchLog.__table__.constraints if c.__class__.__name__ == "CheckConstraint"
    ]
    sqltexts = [str(c.sqltext) for c in checks]
    assert any(
        "http_method" in s and "GET" in s for s in sqltexts
    ), f"brain_fetch_log must keep a GET-only CHECK; found {sqltexts}"


# Local provenance/result tables that legitimately contain a flagged substring
# but are NOT write-to-BRAIN action tables (data arrives via paste or read-only GET).
READ_ONLY_ALLOW = {"simulation_imports", "submission_attempts"}
_SIMULATION_WRITE = re.compile(r"simulat.*(job|queue|run|submit)", re.IGNORECASE)


def _is_write_table(name: str) -> bool:
    if name in READ_ONLY_ALLOW:
        return False
    parts = set(name.split("_"))
    if parts & {"order", "orders", "trade", "trades"}:
        return True
    if "submit" in name or "submission" in name:  # alpha_submissions, submission_queue, ...
        return True
    return bool(_SIMULATION_WRITE.search(name))


def test_no_submission_write_tables() -> None:
    # Substring/component match (not exact equality), so near-variants like
    # "alpha_submissions", "submission_queue", or "simulation_jobs" are caught.
    bad = [t for t in Base.metadata.tables if _is_write_table(t)]
    assert not bad, f"submission/trade write tables are forbidden, found: {bad}"
