# Resend Email Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `notifier.py`'s Gmail SMTP sending with Resend's HTTP API, so scheduled GitHub Actions runs stop failing with `SMTPServerDisconnected`.

**Architecture:** `_send_email` in `ipo_watcher/notifier.py` currently opens an `smtplib.SMTP` connection to Gmail, which Gmail's relay drops when the connection comes from a GitHub Actions runner IP (reproduced on real scheduled runs on 2026-10-26 and 2026-10-30). `requests.post` to `https://api.resend.com/emails` replaces it with an HTTPS call, never blocked by SMTP-relay IP-reputation filtering. The function's signature and `True`/`False` contract stay identical, so `send_alert` and `send_gmp_update_alert` — both already calling `_send_email` — need no changes.

**Tech Stack:** `requests` (already a `pyproject.toml` dependency, used elsewhere in the project — no new dependency needed), Resend's HTTP API.

**Spec:** No separate spec document exists for this change — it was scoped and designed in-chat as a bounded change (swap one function's internals behind an unchanged interface), per `superpowers:brainstorming`'s bounded path. The design context below is this plan's spec-equivalent.

**Design context (from brainstorming):**
- Root cause (already diagnosed via `superpowers:systematic-debugging`, not re-litigated here): Gmail's SMTP relay drops connections from GitHub Actions runner IPs. Confirmed identically on two real scheduled runs.
- User chose Resend over Brevo (3,000 emails/month free, no card).
- User has no custom domain to verify with Resend. Resend's own knowledge base states `onboarding@resend.dev` can only deliver to the email address the Resend account itself was signed up with (a 403 otherwise). This project's email has exactly one recipient (`ALERT_TO`), so the user will sign up for Resend using that same address — making `onboarding@resend.dev` workable without a verified domain.
- Resend's real API (verified via `WebFetch` against `https://resend.com/docs/api-reference/emails/send-email`): `POST https://api.resend.com/emails`, header `Authorization: Bearer <key>`, JSON body `{"from": ..., "to": [...], "subject": ..., "text": ...}`. A 2xx response returns `{"id": "<uuid>"}`.

## Global Constraints

- `requests==2.32.3` is already pinned in `pyproject.toml` — do not add a new HTTP library.
- Never run `git commit` or `git push` — stage with `git add` and stop (per `CLAUDE.md`).
- Config values are env vars only, read via `config._env(name, default="")` (per `ipo_watcher/config.py`'s existing pattern) — never read `os.environ` directly from `notifier.py`.
- `_send_email`'s signature (`subject, body`) and return contract (`True` on success, `False` on any failure, never raises) must not change — `send_alert`/`send_gmp_update_alert` call it with no awareness of the transport underneath.
- The SEBI `530 BLOCKED` pagination issue, `JOURNAL.md`, and GitHub commit-history cleanup are explicitly out of scope for this plan.

## Review Focus

- **Non-2xx Resend response (e.g. the documented 403 for sending to an address other than the account's own)** — `_send_email` must treat this as a failure (return `False`, log a warning) exactly like a connection error, not raise an uncaught `HTTPError` that crashes the caller.
- **Missing `RESEND_API_KEY`** — an empty/unset key should still produce a clean `False`-returning failure (a 401 from Resend via `raise_for_status()`), not a `KeyError`/`AttributeError` crash, since `config._env` already returns `""` rather than raising for an unset var.
- **Timeout** — `requests.post` must pass an explicit `timeout`, matching the existing `smtplib.SMTP(..., timeout=30)` behavior, so a hung connection doesn't block a scheduled run indefinitely.
- **`ALERT_TO` must be sent as a list, not a bare string** — Resend's `to` field accepts "a string or an array of strings," but this project's existing `ALERT_TO` config is a single address; sending `[config.ALERT_TO]` (not `config.ALERT_TO` alone) keeps the JSON body unambiguous and matches the documented array form used in Resend's own examples.
- **Workflow secret-name tests must track the real workflow YAML** — `tests/test_workflows.py`'s two secret-name assertions currently hardcode the SMTP secret set; a stale assertion here would pass against outdated YAML and mask a secrets mismatch at the one place this project already caught two Critical deployment bugs (the earlier `GitHub Actions deployment` final review). Both assertions are updated in Task 2.

---

### Task 1: Rewrite `_send_email` to use Resend's HTTP API

**Files:**
- Modify: `ipo_watcher/notifier.py:1-43` (imports and `_send_email`)
- Test: `tests/test_notifier.py:45-146` (the `smtplib`-mocking tests)

**Interfaces:**
- Consumes: `config.RESEND_API_KEY`, `config.ALERT_FROM`, `config.ALERT_TO` (added/changed in Task 2 — Task 1's tests set these directly via `monkeypatch.setattr(notifier.config, ...)`, so Task 1 does not need Task 2's `config.py` changes landed first to pass its own tests).
- Produces: `notifier._send_email(subject, body) -> bool` — unchanged signature and contract, now implemented via `requests.post` instead of `smtplib.SMTP`. `send_alert`/`send_gmp_update_alert` are unaffected and untouched.

- [ ] **Step 1: Write the failing test for a successful Resend send**

Replace the `import smtplib` mocking tests in `tests/test_notifier.py` (lines 45-146) with `requests`-mocking equivalents. Start with the success-path test, replacing `test_send_alert_returns_true_and_uses_smtp`:

```python
from unittest.mock import patch, MagicMock
import requests


def test_send_alert_returns_true_and_posts_to_resend(monkeypatch):
    monkeypatch.setattr(notifier.config, "RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(notifier.config, "ALERT_FROM", "onboarding@resend.dev")
    monkeypatch.setattr(notifier.config, "ALERT_TO", "me@example.com")

    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()

    with patch("ipo_watcher.notifier.requests.post", return_value=fake_response) as mock_post:
        ok = notifier.send_alert(ROWS)

    assert ok is True
    mock_post.assert_called_once_with(
        "https://api.resend.com/emails",
        headers={"Authorization": "Bearer re_test_key"},
        json={
            "from": "onboarding@resend.dev",
            "to": ["me@example.com"],
            "subject": notifier.build_email_subject(ROWS),
            "text": notifier.build_email_body(ROWS),
        },
        timeout=30,
    )
    fake_response.raise_for_status.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `UV_CACHE_DIR="$TMPDIR/uv-cache" uv run pytest tests/test_notifier.py::test_send_alert_returns_true_and_posts_to_resend -v`
Expected: FAIL — `AttributeError: module 'ipo_watcher.notifier' has no attribute 'requests'` (or similar, since `notifier.py` doesn't import `requests` yet).

- [ ] **Step 3: Write the minimal implementation**

In `ipo_watcher/notifier.py`, replace the top of the file (imports) and `_send_email`:

```python
import logging

import requests

from . import config

log = logging.getLogger("ipo_watcher.notifier")
```

(Remove `import smtplib` and `from email.message import EmailMessage` — nothing else in the file uses them.)

```python
def _send_email(subject, body):
    try:
        response = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {config.RESEND_API_KEY}"},
            json={
                "from": config.ALERT_FROM,
                "to": [config.ALERT_TO],
                "subject": subject,
                "text": body,
            },
            timeout=30,
        )
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        log.warning("Resend send failed: %s", exc)
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `UV_CACHE_DIR="$TMPDIR/uv-cache" uv run pytest tests/test_notifier.py::test_send_alert_returns_true_and_posts_to_resend -v`
Expected: PASS

- [ ] **Step 5: Write the failing test for a connection error**

Replace `test_send_alert_returns_false_on_smtp_error`:

```python
def test_send_alert_returns_false_on_connection_error(monkeypatch):
    monkeypatch.setattr(notifier.config, "RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(notifier.config, "ALERT_FROM", "onboarding@resend.dev")
    monkeypatch.setattr(notifier.config, "ALERT_TO", "me@example.com")

    with patch(
        "ipo_watcher.notifier.requests.post",
        side_effect=requests.exceptions.ConnectionError("boom"),
    ):
        ok = notifier.send_alert(ROWS)

    assert ok is False
```

- [ ] **Step 6: Run test to verify it passes**

Run: `UV_CACHE_DIR="$TMPDIR/uv-cache" uv run pytest tests/test_notifier.py::test_send_alert_returns_false_on_connection_error -v`
Expected: PASS immediately — Step 3's implementation already has `except requests.RequestException`, and `requests.exceptions.ConnectionError` is a subclass of it, so no further code change is needed. This step exists to pin that behavior with a test, not to drive new code.

- [ ] **Step 7: Write the test for a non-2xx Resend response (Review Focus item)**

```python
def test_send_alert_returns_false_on_non_2xx_response(monkeypatch):
    monkeypatch.setattr(notifier.config, "RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(notifier.config, "ALERT_FROM", "onboarding@resend.dev")
    monkeypatch.setattr(notifier.config, "ALERT_TO", "someone-else@example.com")

    fake_response = MagicMock()
    fake_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
        "403 Client Error: Forbidden"
    )

    with patch("ipo_watcher.notifier.requests.post", return_value=fake_response):
        ok = notifier.send_alert(ROWS)

    assert ok is False
```

- [ ] **Step 8: Run test to verify it passes**

Run: `UV_CACHE_DIR="$TMPDIR/uv-cache" uv run pytest tests/test_notifier.py::test_send_alert_returns_false_on_non_2xx_response -v`
Expected: PASS immediately — `requests.exceptions.HTTPError` is also a `requests.RequestException` subclass, already caught by Step 3's implementation. This confirms the Review Focus item (non-2xx handling) is covered without further code changes; if it fails, the implementation's except clause is wrong and needs fixing before continuing.

- [ ] **Step 9: Update the remaining "sends nothing when no rows" and GMP-update tests**

Replace `test_send_alert_returns_true_and_sends_nothing_when_no_rows`:

```python
def test_send_alert_returns_true_and_sends_nothing_when_no_rows():
    with patch("ipo_watcher.notifier.requests.post") as mock_post:
        ok = notifier.send_alert([])
    assert ok is True
    mock_post.assert_not_called()
```

Replace `test_send_gmp_update_alert_returns_true_and_sends_nothing_when_no_rows`:

```python
def test_send_gmp_update_alert_returns_true_and_sends_nothing_when_no_rows():
    with patch("ipo_watcher.notifier.requests.post") as mock_post:
        ok = notifier.send_gmp_update_alert([])
    assert ok is True
    mock_post.assert_not_called()
```

Replace `test_send_gmp_update_alert_uses_smtp`:

```python
def test_send_gmp_update_alert_posts_to_resend(monkeypatch):
    monkeypatch.setattr(notifier.config, "RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(notifier.config, "ALERT_FROM", "onboarding@resend.dev")
    monkeypatch.setattr(notifier.config, "ALERT_TO", "me@example.com")

    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()

    with patch("ipo_watcher.notifier.requests.post", return_value=fake_response) as mock_post:
        ok = notifier.send_gmp_update_alert(GMP_UPDATE_ROWS)

    assert ok is True
    mock_post.assert_called_once()
```

- [ ] **Step 10: Run the full notifier test file**

Run: `UV_CACHE_DIR="$TMPDIR/uv-cache" uv run pytest tests/test_notifier.py -v`
Expected: All tests PASS (the `build_email_subject`/`build_email_body`/`build_gmp_update_*` tests are untouched and should already pass; all 7 send-path tests now test `requests.post` instead of `smtplib.SMTP`).

- [ ] **Step 11: Commit**

```bash
git add ipo_watcher/notifier.py tests/test_notifier.py
git commit -m "feat: send alert emails via Resend's HTTP API instead of Gmail SMTP"
```

---

### Task 2: Update config, env template, and both GitHub Actions workflows

**Files:**
- Modify: `ipo_watcher/config.py:19-24`
- Modify: `.env.example:1-6`
- Modify: `.github/workflows/sebi-collector.yml:33-39`
- Modify: `.github/workflows/gmp-poller.yml:42-49`
- Test: `tests/test_workflows.py:45-54,87-96`

**Interfaces:**
- Consumes: Task 1's `notifier._send_email` (unchanged interface — this task only changes where its inputs come from, not `notifier.py` itself).
- Produces: `config.RESEND_API_KEY` (string, `""` if unset), `config.ALERT_FROM` defaulting to `"onboarding@resend.dev"` instead of `SMTP_USER`. `config.SMTP_HOST`/`SMTP_PORT`/`SMTP_USER`/`SMTP_PASSWORD` no longer exist — nothing in the codebase after Task 1 references them (confirmed by grep in Step 1 below).

- [ ] **Step 1: Confirm nothing else references the SMTP config values**

Run: `grep -rn "SMTP_HOST\|SMTP_PORT\|SMTP_USER\|SMTP_PASSWORD" ipo_watcher/ tests/`
Expected: After Task 1, the only remaining matches are in `tests/test_notifier.py`'s now-deleted old tests (should be zero, since Task 1 replaced them) and this plan file itself. If `ipo_watcher/run.py` or `ipo_watcher/gmp_source.py` show any match, stop and investigate before proceeding — this plan assumes `notifier.py` is their only consumer.

- [ ] **Step 2: Write a config test confirming the new default**

Add to `tests/test_config.py`:

```python
def test_alert_from_defaults_to_resend_onboarding_address(monkeypatch):
    monkeypatch.delenv("ALERT_FROM", raising=False)
    import importlib
    from ipo_watcher import config
    importlib.reload(config)
    assert config.ALERT_FROM == "onboarding@resend.dev"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `UV_CACHE_DIR="$TMPDIR/uv-cache" uv run pytest tests/test_config.py::test_alert_from_defaults_to_resend_onboarding_address -v`
Expected: FAIL — `config.py` still has the old `ALERT_FROM = _env("ALERT_FROM", SMTP_USER)` line, so the reloaded default is whatever `SMTP_USER` resolves to (likely `""`), not `"onboarding@resend.dev"`.

- [ ] **Step 4: Update `config.py`**

Replace lines 19-24 of `ipo_watcher/config.py`:

```python
RESEND_API_KEY = _env("RESEND_API_KEY")
ALERT_FROM = _env("ALERT_FROM", "onboarding@resend.dev")
ALERT_TO = _env("ALERT_TO")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `UV_CACHE_DIR="$TMPDIR/uv-cache" uv run pytest tests/test_config.py::test_alert_from_defaults_to_resend_onboarding_address -v`
Expected: PASS

- [ ] **Step 6: Update `.env.example`**

Replace lines 1-6 of `.env.example`:

```
RESEND_API_KEY=
ALERT_FROM=
ALERT_TO=
```

Leave the rest of the file (the optional-overrides block, `IPO_GURU_API_KEY=`) as-is. Delete the now-duplicate trailing `RESEND_API_KEY=` line (currently line 14) since it's now declared at the top with the other alert-related vars. Add a one-line comment above the new block:

```
# ALERT_FROM defaults to onboarding@resend.dev if left blank — Resend's
# test-only address, which can only deliver to the email Resend account's
# own signup address. Set ALERT_TO to that same address, or override
# ALERT_FROM once you verify a real domain with Resend.
RESEND_API_KEY=
ALERT_FROM=
ALERT_TO=
```

- [ ] **Step 7: Update `.github/workflows/sebi-collector.yml`**

Replace the `env:` block under "Run the SEBI collector" (lines 33-39):

```yaml
        env:
          RESEND_API_KEY: ${{ secrets.RESEND_API_KEY }}
          ALERT_FROM: ${{ secrets.ALERT_FROM }}
          ALERT_TO: ${{ secrets.ALERT_TO }}
```

- [ ] **Step 8: Update `.github/workflows/gmp-poller.yml`**

Replace the `env:` block under "Run the GMP poller" (lines 42-49):

```yaml
        env:
          RESEND_API_KEY: ${{ secrets.RESEND_API_KEY }}
          ALERT_FROM: ${{ secrets.ALERT_FROM }}
          ALERT_TO: ${{ secrets.ALERT_TO }}
          IPO_GURU_API_KEY: ${{ secrets.IPO_GURU_API_KEY }}
```

- [ ] **Step 9: Update the failing workflow secret-name tests**

Replace `test_collector_workflow_secret_names_match_config` in `tests/test_workflows.py`:

```python
def test_collector_workflow_secret_names_match_config():
    wf = _load("sebi-collector.yml")
    step = _find_step(wf, lambda s: "ipo_watcher.run" in s.get("run", ""))
    env = step.get("env", {})
    assert set(env.keys()) == {"RESEND_API_KEY", "ALERT_FROM", "ALERT_TO"}
    for value in env.values():
        assert value.startswith("${{ secrets.")
```

Replace `test_poller_workflow_secret_names_match_config`:

```python
def test_poller_workflow_secret_names_match_config():
    wf = _load("gmp-poller.yml")
    step = _find_step(wf, lambda s: "ipo_watcher.gmp_source" in s.get("run", ""))
    env = step.get("env", {})
    assert set(env.keys()) == {"RESEND_API_KEY", "ALERT_FROM", "ALERT_TO", "IPO_GURU_API_KEY"}
    for value in env.values():
        assert value.startswith("${{ secrets.")
```

- [ ] **Step 10: Run test to verify both fail before Steps 7-8, then pass after**

Run: `UV_CACHE_DIR="$TMPDIR/uv-cache" uv run pytest tests/test_workflows.py::test_collector_workflow_secret_names_match_config tests/test_workflows.py::test_poller_workflow_secret_names_match_config -v`
Expected: If run against the workflow YAML before Steps 7-8, FAIL with a set-mismatch (`{'SMTP_HOST', ...} != {'RESEND_API_KEY', ...}`). After Steps 7-8, PASS.

- [ ] **Step 11: Run the full test suite**

Run: `UV_CACHE_DIR="$TMPDIR/uv-cache" uv run pytest tests/ -v`
Expected: All tests PASS — this is the full-suite confirmation that Task 1's `notifier.py`/`test_notifier.py` changes, Task 2's `config.py`/`test_config.py` changes, and Task 2's workflow YAML/`test_workflows.py` changes are all consistent with each other and with every other untouched test file (`test_run.py`, `test_db.py`, `test_sebi_source.py`, `test_gmp_source.py`, `test_scoring.py`, `test_downloader.py`, `test_git_tracking.py`).

- [ ] **Step 12: Commit**

```bash
git add ipo_watcher/config.py .env.example .github/workflows/sebi-collector.yml .github/workflows/gmp-poller.yml tests/test_config.py tests/test_workflows.py
git commit -m "chore: switch GitHub Actions secrets and config from SMTP to Resend"
```

---

## After implementation (manual steps for the user, not part of this plan's automated tasks)

These cannot be done by an agent and are not part of either task's completion contract:

1. Sign up for a Resend account using the same email address as `ALERT_TO`.
2. Generate a Resend API key from the dashboard.
3. Set the `RESEND_API_KEY` GitHub repo secret (Settings → Secrets and variables → Actions). **Critical — clear or delete the existing `ALERT_FROM` GitHub secret** (it was set to a `@gmail.com` address by the earlier SMTP-based deployment plan). If it stays set to a non-`resend.dev` address, every Resend call 403s (you haven't verified that domain) and `_send_email` silently returns `False` — the workflow still exits 0, so this fails invisibly. Leaving the secret unset (or deleted entirely) lets `config.py`'s `onboarding@resend.dev` default take effect. Remove the now-unused `SMTP_HOST`/`SMTP_PORT`/`SMTP_USER`/`SMTP_PASSWORD` secrets if desired (optional cleanup, not required for correctness).
4. Update the local `.env` file (gitignored, not `.env.example`) with the real `RESEND_API_KEY`. The same `ALERT_FROM` issue applies locally — already fixed as part of this plan's execution (the local `.env`'s `ALERT_FROM=ipo_watcher@gmail.com` line was blanked).
5. Manually trigger both workflows via `workflow_dispatch` once to confirm real delivery before trusting the next scheduled run.
