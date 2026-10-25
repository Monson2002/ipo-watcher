# GitHub Actions Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run both existing pipelines (the SEBI filing collector, the GMP poller) unattended on GitHub Actions' free scheduled workflows — the one free, card-free, always-on option available — without losing data to the ephemeral-runner/concurrent-write problem that design creates.

**Architecture:** One small change to `ipo_watcher/run.py` (a flag to skip PDF downloads, since nothing persists them), plus two new GitHub Actions workflow files that checkout the repo, run the existing scripts with secrets injected as environment variables, and commit `data/ipo_watcher.db` back — serialized through a shared `concurrency` group so the two pipelines' writes to the same database file never race.

**Tech Stack:** GitHub Actions workflow YAML, Python stdlib `argparse`, `pytest` + `unittest.mock` (code), `pyyaml` (declared as an explicit dev dependency in Task 2, not left as an incidental transitive one) for structural tests on the workflow YAML itself.

**Spec:** `docs/superpowers/specs/2026-10-25-github-actions-deployment-design.md`

## Global Constraints

- `main(skip_pdf_download=False)` — default `False` so every existing manual/local invocation (`uv run python -m ipo_watcher.run`) keeps its current behavior completely unchanged. Only the GitHub Actions collector workflow passes `--skip-pdf-download`.
- Both workflow files use the **exact same** `concurrency.group` value: `ipo-watcher-db-write`.
- Both workflow files set `queue: max` on their `concurrency` block — `cancel-in-progress: false` alone does not protect a *queued* run from being silently cancelled and replaced by a newer trigger (confirmed against GitHub's own concurrency documentation during the design pass).
- `queue: max` and `cancel-in-progress: true` cannot be combined — GitHub rejects that combination at validation time. Both workflows must use `cancel-in-progress: false`.
- Both workflows' checkout step must set `ref: main` explicitly — a queued run's default checkout resolves to the commit SHA captured at *trigger* time, not at actual execution time, which would be stale after waiting behind another run.
- Secret names in each workflow's `env:` block must exactly match what `ipo_watcher/config.py` reads from `os.environ`. **Both** workflows need the full set: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_FROM`, `ALERT_TO`, `IPO_GURU_API_KEY` — not split between them. (Corrected post-implementation: `gmp_source.py`'s `main()` calls `run.send_gmp_update_alerts`, which can send a real email, so the poller needs SMTP access too, not just the API key.)
- `data/ipo_watcher.db` must actually be trackable by git — a blanket `data/` entry in `.gitignore` would make every `git add data/ipo_watcher.db` in both workflows fail (`git add` on a gitignored path exits non-zero, and GitHub Actions `run:` steps default to `bash -e`), so the DB is never persisted and every run starts from empty. `.gitignore` uses `data/*` plus `!data/ipo_watcher.db` so `data/pdfs/` stays ignored but the one file that matters does not.
- An unconfigured GitHub secret expands to an empty string, not an absent variable — `ipo_watcher/config.py` must treat `""` the same as unset wherever a real default/fallback exists (e.g. `SMTP_PORT`'s `587` default, `ALERT_FROM`'s fallback to `SMTP_USER`), via a small `_env(name, default="")` helper, not `os.environ.get(name, default)` directly.
- No `.env` file is ever written in either workflow — secrets go directly into the step's `env:`, which `python-dotenv`'s `load_dotenv()` never overrides.
- PDF downloads are never attempted in the collector workflow — not disabled-but-still-recorded, genuinely never called, so `pdf_path` is never falsely marked as set.
- Both workflow files write the trigger key as `"on":` (quoted), not bare `on:` — YAML 1.1 parses an unquoted `on`/`off`/`yes`/`no` as a boolean, so PyYAML (used by `tests/test_workflows.py`) would otherwise parse the key as `True` instead of the string `"on"`. GitHub Actions itself treats both forms identically; quoting only matters for the Python-side structural tests.
- `pyyaml` is declared as an explicit `dev` dependency (`uv add --dev pyyaml`), not relied on as an incidental transitive one — `tests/test_workflows.py` imports it directly, so the project's own dependency manifest must say so.
- Two of the poller's ten UTC cron times land exactly on the hour by coincidence of the IST→UTC conversion (`5:00`, `8:00`); both are nudged to `5:01`/`8:01` to avoid GitHub's documented highest-delay-risk window ("the start of every hour") — this changes nothing a person watching the IST schedule would notice.
- `queue: max` makes queued runs wait their turn rather than replace each other — it does not guarantee every scheduled trigger executes. GitHub's own `schedule` event documentation states the event "can be delayed during periods of high load" and that jobs "may be dropped" under sufficiently high load. At this project's volume (13 triggers/week total, far under the 100-pending-run cap) the practical risk is low, but no plan step claims an absolute guarantee.

## Review Focus

- Both workflow files' `concurrency.group` must be character-for-character identical — a typo here means the whole serialization protection silently does nothing, with no error anywhere to reveal it.
- `queue: max` must be present on **both** files, not just one — a reasonable person who read the design doc's explanation would expect the fix to actually be applied everywhere it's needed, not partially.
- The checkout step in both workflows must use `ref: main`, not the default — otherwise a run that waited in the queue risks checking out stale state and failing to push (non-fast-forward) against what the other workflow already pushed.
- `--skip-pdf-download` must appear only on the collector's run command, never the poller's — `ipo_watcher.gmp_source` has no such flag and passing it would error immediately.
- Each secret name in a workflow's `env:` block must exactly match the environment variable name `ipo_watcher/config.py` actually reads — a mismatch (e.g. `SMTP_HOSTNAME` instead of `SMTP_HOST`) would silently leave that config value empty rather than raising any visible error, since `config.py` uses `os.environ.get(..., "")` with a blank default everywhere.

---

### Task 1: `--skip-pdf-download` flag on `ipo_watcher/run.py`

**Files:**
- Modify: `ipo_watcher/run.py:177-194` (the `main()` function and its `__main__` block)
- Test: `tests/test_run.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `main(skip_pdf_download=False)` — when `True`, skips the `download_pending_pdfs(conn)` call entirely. Later tasks (the collector workflow) consume this via the CLI flag, not by calling `main()` directly.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_run.py`:

```python
def test_main_skips_pdf_download_when_flag_set(tmp_path, monkeypatch):
    monkeypatch.setattr(run.config, "DB_PATH", str(tmp_path / "main_test1.db"))
    with patch("ipo_watcher.run.discover_new_filings") as mock_discover, \
         patch("ipo_watcher.run.download_pending_pdfs") as mock_download, \
         patch("ipo_watcher.run.send_pending_alert") as mock_alert:
        run.main(skip_pdf_download=True)
    mock_discover.assert_called_once()
    mock_download.assert_not_called()
    mock_alert.assert_called_once()


def test_main_downloads_pdfs_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(run.config, "DB_PATH", str(tmp_path / "main_test2.db"))
    with patch("ipo_watcher.run.discover_new_filings") as mock_discover, \
         patch("ipo_watcher.run.download_pending_pdfs") as mock_download, \
         patch("ipo_watcher.run.send_pending_alert") as mock_alert:
        run.main()
    mock_discover.assert_called_once()
    mock_download.assert_called_once()
    mock_alert.assert_called_once()
```

- [ ] **Step 2: Run the tests to verify they fail (or pass for the wrong reason)**

Run: `uv run pytest tests/test_run.py -k "test_main_" -v`
Expected: `test_main_skips_pdf_download_when_flag_set` FAILS with
`TypeError: main() got an unexpected keyword argument 'skip_pdf_download'`.
`test_main_downloads_pdfs_by_default` PASSES already — `main()` with no
arguments already calls all three functions today. This step exists to
confirm that test is pinning real existing behavior, not driving new
code; if it fails too, something else is already broken.

- [ ] **Step 3: Add the parameter and CLI flag**

In `ipo_watcher/run.py`, replace the `main()` function and its
`__main__` block with:

```python
def main(skip_pdf_download=False):
    setup_logging()
    os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
    conn = db.connect(config.DB_PATH)
    db.init_db(conn)
    try:
        discover_new_filings(conn)
        if not skip_pdf_download:
            download_pending_pdfs(conn)
        send_pending_alert(conn)
    except Exception:
        log.exception("Unhandled error in ipo_watcher run")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-pdf-download", action="store_true")
    args = parser.parse_args()
    main(skip_pdf_download=args.skip_pdf_download)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_run.py -k "test_main_" -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: all tests pass (the pre-existing 92 plus the 2 new ones).

- [ ] **Step 6: Commit**

```bash
git add ipo_watcher/run.py tests/test_run.py
git commit -m "feat: add --skip-pdf-download flag to ipo_watcher.run"
```

---

### Task 2: SEBI collector workflow

**Files:**
- Create: `.github/workflows/sebi-collector.yml`
- Modify: `pyproject.toml` (new explicit dev dependency)
- Test: `tests/test_workflows.py`

**Interfaces:**
- Consumes: `ipo_watcher.run`'s `--skip-pdf-download` CLI flag (Task 1).
- Produces: the concurrency group name `ipo-watcher-db-write` — Task 3's
  poller workflow must use this exact same string.

- [ ] **Step 1: Declare `pyyaml` as an explicit dev dependency**

Run: `uv add --dev pyyaml`
Expected: `pyproject.toml`'s `[dependency-groups] dev` list now includes
`pyyaml`, and `uv.lock` is updated. `pyyaml` was already importable as a
transitive dependency of `jupyterlab`/`ipykernel` before this step — this
step only makes that dependency explicit and reproducible, not newly
functional.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_workflows.py`:

```python
from pathlib import Path

import yaml

WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _load(name):
    return yaml.safe_load((WORKFLOWS_DIR / name).read_text())


def _find_step(workflow, predicate):
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            if predicate(step):
                return step
    raise AssertionError(f"no matching step found in {workflow.get('name')}")


def test_collector_workflow_is_valid_yaml_with_expected_schedule():
    wf = _load("sebi-collector.yml")
    assert wf["on"]["schedule"] == [{"cron": "30 3 * * 1,3,5"}]
    assert "workflow_dispatch" in wf["on"]


def test_collector_workflow_has_queue_max_concurrency():
    wf = _load("sebi-collector.yml")
    assert wf["concurrency"]["group"] == "ipo-watcher-db-write"
    assert wf["concurrency"]["queue"] == "max"
    assert wf["concurrency"]["cancel-in-progress"] is False


def test_collector_workflow_checkout_uses_ref_main():
    wf = _load("sebi-collector.yml")
    step = _find_step(wf, lambda s: s.get("uses", "").startswith("actions/checkout"))
    assert step["with"]["ref"] == "main"


def test_collector_workflow_runs_with_skip_pdf_download_flag():
    wf = _load("sebi-collector.yml")
    step = _find_step(wf, lambda s: "ipo_watcher.run" in s.get("run", ""))
    assert "--skip-pdf-download" in step["run"]


def test_collector_workflow_secret_names_match_config():
    wf = _load("sebi-collector.yml")
    step = _find_step(wf, lambda s: "ipo_watcher.run" in s.get("run", ""))
    env = step.get("env", {})
    assert set(env.keys()) == {
        "SMTP_HOST", "SMTP_PORT", "SMTP_USER",
        "SMTP_PASSWORD", "ALERT_FROM", "ALERT_TO",
    }
    for value in env.values():
        assert value.startswith("${{ secrets.")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_workflows.py -k collector -v`
Expected: FAIL — `FileNotFoundError` (the file doesn't exist yet).

- [ ] **Step 4: Create the workflow file**

Create `.github/workflows/sebi-collector.yml`:

```yaml
name: SEBI filing collector

"on":
  schedule:
    - cron: '30 3 * * 1,3,5'
  workflow_dispatch: {}

concurrency:
  group: ipo-watcher-db-write
  queue: max
  cancel-in-progress: false

permissions:
  contents: write

jobs:
  collect:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          ref: main

      - name: Install uv
        uses: astral-sh/setup-uv@v3

      - name: Install dependencies
        run: uv sync

      - name: Run the SEBI collector
        run: uv run python -m ipo_watcher.run --skip-pdf-download
        env:
          SMTP_HOST: ${{ secrets.SMTP_HOST }}
          SMTP_PORT: ${{ secrets.SMTP_PORT }}
          SMTP_USER: ${{ secrets.SMTP_USER }}
          SMTP_PASSWORD: ${{ secrets.SMTP_PASSWORD }}
          ALERT_FROM: ${{ secrets.ALERT_FROM }}
          ALERT_TO: ${{ secrets.ALERT_TO }}

      - name: Show run log
        if: always()
        run: cat logs/ipo_watcher.log || true

      - name: Commit and push database changes
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add data/ipo_watcher.db
          git diff --cached --quiet || git commit -m "chore: update database from scheduled collector run"
          git push
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_workflows.py -k collector -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/sebi-collector.yml tests/test_workflows.py pyproject.toml uv.lock
git commit -m "feat: add SEBI collector GitHub Actions workflow"
```

---

### Task 3: GMP poller workflow, and the cross-file concurrency check

**Files:**
- Create: `.github/workflows/gmp-poller.yml`
- Test: `tests/test_workflows.py`

**Interfaces:**
- Consumes: the concurrency group name `ipo-watcher-db-write` produced by
  Task 2 — this workflow must use the identical string.
- Produces: nothing further consumed by other tasks (this is the last task).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_workflows.py`:

```python
def test_poller_workflow_is_valid_yaml_with_expected_schedule():
    wf = _load("gmp-poller.yml")
    expected_crons = [
        "30 3 * * 1-5", "15 4 * * 1-5", "1 5 * * 1-5", "45 5 * * 1-5",
        "30 6 * * 1-5", "15 7 * * 1-5", "1 8 * * 1-5", "45 8 * * 1-5",
        "30 9 * * 1-5", "15 10 * * 1-5",
    ]
    assert [c["cron"] for c in wf["on"]["schedule"]] == expected_crons
    assert "workflow_dispatch" in wf["on"]


def test_poller_workflow_has_queue_max_concurrency():
    wf = _load("gmp-poller.yml")
    assert wf["concurrency"]["group"] == "ipo-watcher-db-write"
    assert wf["concurrency"]["queue"] == "max"
    assert wf["concurrency"]["cancel-in-progress"] is False


def test_poller_workflow_checkout_uses_ref_main():
    wf = _load("gmp-poller.yml")
    step = _find_step(wf, lambda s: s.get("uses", "").startswith("actions/checkout"))
    assert step["with"]["ref"] == "main"


def test_poller_workflow_does_not_pass_skip_pdf_download_flag():
    wf = _load("gmp-poller.yml")
    step = _find_step(wf, lambda s: "ipo_watcher.gmp_source" in s.get("run", ""))
    assert "--skip-pdf-download" not in step["run"]


def test_poller_workflow_secret_names_match_config():
    wf = _load("gmp-poller.yml")
    step = _find_step(wf, lambda s: "ipo_watcher.gmp_source" in s.get("run", ""))
    env = step.get("env", {})
    assert set(env.keys()) == {"IPO_GURU_API_KEY"}
    assert env["IPO_GURU_API_KEY"].startswith("${{ secrets.")


def test_both_workflows_share_the_same_concurrency_group():
    collector = _load("sebi-collector.yml")
    poller = _load("gmp-poller.yml")
    assert collector["concurrency"]["group"] == poller["concurrency"]["group"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_workflows.py -k poller -v`
Expected: FAIL — `FileNotFoundError` for every `test_poller_*` test.
`test_both_workflows_share_the_same_concurrency_group` also FAILS the
same way (it loads `gmp-poller.yml` too).

- [ ] **Step 3: Create the workflow file**

Create `.github/workflows/gmp-poller.yml`:

```yaml
name: GMP poller

"on":
  schedule:
    - cron: '30 3 * * 1-5'
    - cron: '15 4 * * 1-5'
    - cron: '1 5 * * 1-5'
    - cron: '45 5 * * 1-5'
    - cron: '30 6 * * 1-5'
    - cron: '15 7 * * 1-5'
    - cron: '1 8 * * 1-5'
    - cron: '45 8 * * 1-5'
    - cron: '30 9 * * 1-5'
    - cron: '15 10 * * 1-5'
  workflow_dispatch: {}

concurrency:
  group: ipo-watcher-db-write
  queue: max
  cancel-in-progress: false

permissions:
  contents: write

jobs:
  poll:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          ref: main

      - name: Install uv
        uses: astral-sh/setup-uv@v3

      - name: Install dependencies
        run: uv sync

      - name: Run the GMP poller
        run: uv run python -m ipo_watcher.gmp_source
        env:
          IPO_GURU_API_KEY: ${{ secrets.IPO_GURU_API_KEY }}

      - name: Show run log
        if: always()
        run: cat logs/ipo_watcher.log || true

      - name: Commit and push database changes
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add data/ipo_watcher.db
          git diff --cached --quiet || git commit -m "chore: update database from scheduled GMP poll"
          git push
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_workflows.py -v`
Expected: PASS (all 11 tests in this file — 5 from Task 2, 6 from this
task).

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: all tests pass (the pre-existing 92, plus 2 from Task 1, plus
11 from this file).

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/gmp-poller.yml tests/test_workflows.py
git commit -m "feat: add GMP poller GitHub Actions workflow"
```

- [ ] **Step 7: Note the required manual step (cannot be automated)**

This plan's code is complete at this point, but the workflows cannot
actually run successfully until the seven secrets are added by hand to
the repository's Settings → Secrets and variables → Actions:
`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_FROM`,
`ALERT_TO`, `IPO_GURU_API_KEY`. Once added, use each workflow's manual
`workflow_dispatch` trigger (the "Run workflow" button in GitHub's
Actions tab) to test a real run before trusting the unattended schedule.
