# IPO Guru API v2 Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the live GMP poller, which is currently broken — point it at IPO Guru's real `/api/v2/ipos` endpoint instead of the retired `/api/v1/ipos`, and remove the undocumented 3-month default window.

**Architecture:** A one-constant change in `ipo_watcher/gmp_source.py`, pinned by a regression test that asserts the exact URL `fetch_gmp_listing` calls. No other file changes — the spec confirmed every other assumption in the existing parsing/error-handling code already matches the real v2 contract.

**Tech Stack:** Python stdlib `requests`, `pytest` + `unittest.mock` (already this project's conventions).

**Spec:** `docs/superpowers/specs/2026-10-25-ipo-guru-v2-migration-design.md`

## Global Constraints

- The new URL is exactly `https://www.ipoguru.in/api/v2/ipos?months=0` — not just `/api/v2/ipos` without the query string, since the default (`months=3`) silently excludes IPOs filing further out.
- No other code changes — `parse_gmp_listing`'s field reads, `_parse_close_date`/`_parse_is_listed`'s defensive parsing, and the HTTP status-code-based error handling in `fetch_gmp_listing`/`poll_gmp_snapshots` are all already correct against the verified v2 contract per the spec.

## Review Focus

- The URL must be the full `?months=0` string, not merely the bare v2 path — a reviewer who only checks for "v2" in the URL could miss the month-window regression resurfacing silently.
- No other reference to the retired `/api/v1` path should remain anywhere in `ipo_watcher/` after this change.
- The existing test suite's mocks (which patch `requests.get`/`fetch_gmp_listing` directly rather than asserting on the literal URL) must keep passing unmodified — confirmed in the spec's self-review that no test currently hardcodes the v1 URL string.

---

### Task 1: Point `fetch_gmp_listing` at the real v2 endpoint

**Files:**
- Modify: `ipo_watcher/gmp_source.py:18` (the `IPOS_URL` constant)
- Test: `tests/test_gmp_source.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: no change to `fetch_gmp_listing`'s signature or return type — only the URL it requests changes.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gmp_source.py`:

```python
def test_fetch_gmp_listing_uses_v2_endpoint_with_no_month_limit():
    fake_response = MagicMock()
    fake_response.json.return_value = {"success": True, "data": []}
    fake_response.raise_for_status = MagicMock()
    with patch("ipo_watcher.gmp_source.requests.get", return_value=fake_response) as mock_get:
        fetch_gmp_listing()
    args, _ = mock_get.call_args
    assert args[0] == "https://www.ipoguru.in/api/v2/ipos?months=0"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_gmp_source.py::test_fetch_gmp_listing_uses_v2_endpoint_with_no_month_limit -v`
Expected: FAIL — `AssertionError: assert 'https://www.ipoguru.in/api/v1/ipos' == 'https://www.ipoguru.in/api/v2/ipos?months=0'`

- [ ] **Step 3: Update the constant**

In `ipo_watcher/gmp_source.py`, change:

```python
IPOS_URL = "https://www.ipoguru.in/api/v1/ipos"
```

to:

```python
IPOS_URL = "https://www.ipoguru.in/api/v2/ipos?months=0"
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_gmp_source.py::test_fetch_gmp_listing_uses_v2_endpoint_with_no_month_limit -v`
Expected: PASS

- [ ] **Step 5: Confirm no other reference to the retired v1 path remains**

Run: `grep -rn "api/v1" ipo_watcher/`
Expected: no output (zero matches).

- [ ] **Step 6: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: all tests pass (the pre-existing 91 plus the 1 new one from this task).

- [ ] **Step 7: Commit**

```bash
git add ipo_watcher/gmp_source.py tests/test_gmp_source.py
git commit -m "fix: point the GMP poller at the real v2 API, remove the 3-month default window"
```
