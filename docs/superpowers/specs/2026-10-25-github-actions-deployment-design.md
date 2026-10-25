# GitHub Actions Deployment — Design

> Design doc for actually running this project unattended, on a free
> host, with no credit card and no always-on machine available. Covers
> both existing pipelines (the SEBI filing collector, the GMP poller) —
> not a new feature, a new way of running what already exists.

## Context

Deployment has been an open item since the collector was first built —
`CLAUDE.md` has always said "deployment is not automated... install by
hand on whatever server ends up hosting this." That server never
materialized: Oracle Cloud's free tier requires a card for identity
verification (none available), and there's no spare always-on machine at
home right now. Deep research into other free/always-on hosts
(PythonAnywhere, Render, Fly.io, Vercel/Netlify, Replit) found none that
are simultaneously free, card-free, and capable of a persistent
cron+SQLite process — each fails on at least one of those three
requirements.

GitHub Actions' free scheduled workflows are the one option that
actually fits: free for public repos, no card at signup, real cron
scheduling. The gap is that Actions runners are ephemeral — nothing on
disk survives between runs except what gets committed back to the repo.
This spec is entirely about closing that gap correctly.

## Decisions made in brainstorming

- **PDF downloads are skipped entirely in this deployment.** PDFs are
  the real SEBI filing documents (DRHP/RHP/Prospectus/Addenda/
  Corrigenda) — several GB possible per the existing `README.md`
  warning, far too large to commit into git history. The email alert
  already includes `document_url`, a direct link to the same
  SEBI-hosted file, so nothing is lost for actually *reading* a filing —
  only the optional local-archive copy is gone. Letting
  `download_pending_pdfs` run anyway would be worse than skipping it:
  the downloaded file would vanish at the end of the ephemeral run while
  the database still recorded `pdf_path` as set, permanently hiding that
  nothing was actually saved.
- **Credentials go into GitHub's repo secrets store** (Settings →
  Secrets → Actions) — encrypted at rest, injected only into this
  repo's own Actions runs as environment variables, never written to a
  file on disk or printed in logs.

## Architecture

Two new workflow files, one per existing pipeline — matching this
project's already-deliberate separation between the collector and the
poller (different cadence, different failure domain, already documented
in `CLAUDE.md`):

- `.github/workflows/sebi-collector.yml` — runs
  `uv run python -m ipo_watcher.run --skip-pdf-download`.
- `.github/workflows/gmp-poller.yml` — runs
  `uv run python -m ipo_watcher.gmp_source` (no change needed here; it
  never downloaded anything).

Each workflow:
1. `actions/checkout@v4` (fetches the repo, including the current
   `data/ipo_watcher.db`).
2. Sets up Python 3.11+ and `uv` (`astral-sh/setup-uv@v3`).
3. `uv sync`.
4. Runs the script, with every required secret passed as that step's
   `env:` — not written to a `.env` file. `python-dotenv`'s
   `load_dotenv()` (already called in `ipo_watcher/config.py`) never
   overrides a variable already present in the environment, so secrets
   injected this way take precedence automatically and nothing sensitive
   ever touches disk.
5. `cat logs/ipo_watcher.log` (or a tail of it) — surfaces the run's log
   output directly in the Actions UI, since `setup_logging()` writes to
   a file, not stdout.
6. Commits `data/ipo_watcher.db` back to `main` if it changed, using the
   automatically-provided `GITHUB_TOKEN` (workflow needs
   `permissions: contents: write`) and a bot git identity
   (`github-actions[bot]`).

## The concurrency problem

Both workflows write to the **same** `data/ipo_watcher.db`, and their
schedules genuinely overlap (the collector's Mon/Wed/Fri 9:00 AM IST
slot coincides with one of the poller's ten daily times). If both ran at
once, each starts from its own checkout; whichever pushes second either
fails outright (non-fast-forward) or, worse, could silently clobber the
other's changes — git cannot text-merge a binary SQLite file, so a
"rebase and retry" strategy does not actually resolve a real collision
here.

**Fix:** both workflow files declare the same `concurrency:` group name
(e.g. `ipo-watcher-db-write`). GitHub Actions concurrency groups work
across different workflow files when they share a group name, so this
serializes every run of either workflow — a collector run and a poller
run never execute simultaneously; one queues until the other finishes
and pushes.

```yaml
concurrency:
  group: ipo-watcher-db-write
  queue: max
  cancel-in-progress: false
```

**`cancel-in-progress: false` alone is not enough.** It only protects a
currently-*running* job — GitHub's documented default behavior is that
when a new run is queued for a concurrency group, any existing *pending*
(not-yet-started) run in that same group is cancelled and replaced,
independent of `cancel-in-progress`. With only two workflows and ten
poller triggers a day, this would be a real, likely occurrence: a poller
run queued behind the collector could get silently cancelled and
replaced by the *next* poller trigger before it ever got a turn —
quietly dropping a whole scheduled poll with no error anywhere.
`queue: max` changes this: it allows up to 100 pending runs to actually
wait their turn instead of replacing each other (confirmed against
GitHub's own concurrency documentation). `queue: max` cannot be combined
with `cancel-in-progress: true` — GitHub rejects that combination at
validation time, which is why `cancel-in-progress: false` is required,
not just a reasonable default.

**This is not an absolute guarantee that every scheduled trigger
executes** — only that queued runs wait their turn instead of replacing
each other. Two real limits still apply, both documented by GitHub
directly: the queue itself holds at most 100 pending runs (irrelevant at
this project's volume — 13 triggers/week total — but worth naming
rather than implying unlimited), and GitHub's own `schedule` event docs
state it "can be delayed during periods of high load" and that "if the
load is sufficiently high enough, some queued jobs may be dropped." At
this project's volume the practical risk is low, but "queued rather than
replaced" is the accurate claim, not "guaranteed to run."

**A related, concrete finding from that same GitHub documentation**: the
highest-delay-risk window is explicitly "the start of every hour," and
GitHub's own advice is to schedule away from `:00`. Two of the ten
GMP-poller UTC cron times below land exactly on the hour (`5:00`, `8:00`
— an artifact of converting IST times ending in `:30` through the
UTC+5:30 offset), so each is nudged by one minute (`5:01`, `8:01`) in
the final cron list below, purely to avoid that documented high-load
window. This does not change the real-world IST trigger time in any way
a person would notice.

**A second, related gap: checkout after a wait must not use a stale
commit.** A workflow run queued behind another one keeps the commit SHA
that was current when it was *triggered*, not when it actually starts
executing after waiting. `actions/checkout@v4`'s default `ref` resolves
to that originally-captured SHA — so a run that waited through another
run's push would check out the *old* database state and then fail to
push its own changes (non-fast-forward against what the other run
already pushed). Fix: check out the branch name explicitly, not the
default ref, so the checkout step (which runs after the concurrency slot
is actually acquired) fetches whatever is truly current at that moment:

```yaml
- name: Checkout repository
  uses: actions/checkout@v4
  with:
    ref: main
```

## Cadence (GitHub Actions cron is always UTC, never IST)

**SEBI collector** (Mon/Wed/Fri 9:00 AM IST = 3:30 AM UTC):

```yaml
"on":
  schedule:
    - cron: '30 3 * * 1,3,5'
  workflow_dispatch: {}
```

**GMP poller** (ten IST times, Mon-Fri, same times `README.md` already
documents, converted to UTC):

| IST | UTC cron | Note |
|---|---|---|
| 9:00 | `30 3 * * 1-5` | |
| 9:45 | `15 4 * * 1-5` | |
| 10:30 | `1 5 * * 1-5` | nudged off `:00`, see above |
| 11:15 | `45 5 * * 1-5` | |
| 12:00 | `30 6 * * 1-5` | |
| 12:45 | `15 7 * * 1-5` | |
| 13:30 | `1 8 * * 1-5` | nudged off `:00`, see above |
| 14:15 | `45 8 * * 1-5` | |
| 15:00 | `30 9 * * 1-5` | |
| 15:45 | `15 10 * * 1-5` | |

```yaml
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
```

`workflow_dispatch: {}` on both — a manual "Run workflow" button in
GitHub's UI, essential for testing real runs by hand before trusting the
unattended schedule.

## The `run.py` code change

`main()` gains a `skip_pdf_download=False` parameter. When `True`, it
skips the `download_pending_pdfs(conn)` call entirely — never runs it,
never sets a `pdf_path` for a file that will not persist. A new
`--skip-pdf-download` CLI flag (via `argparse`) wires this to the
command line, defaulting to `False`, so any existing manual/local
invocation (`uv run python -m ipo_watcher.run`) keeps its current
behavior completely unchanged. Only the GitHub Actions workflow passes
the flag.

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

## Required GitHub repo secrets

**Correction, post-implementation review**: the original draft of this
section split secrets by workflow — SMTP-only for the collector,
`IPO_GURU_API_KEY`-only for the poller. That was wrong. `gmp_source.py`'s
`main()` calls `poll_gmp_snapshots` *and then* `run.send_gmp_update_alerts`
in the same invocation — the latter can send a real email via
`notifier.send_gmp_update_alert` whenever a tracked company's GMP moves
≥5pp. Both workflows need the full secret set:

`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_FROM`,
`ALERT_TO`, `IPO_GURU_API_KEY` — all seven, in **both** workflows'
`env:` blocks, not split between them. Set once, by hand, in the
repository's Settings → Secrets and variables → Actions — not something
this spec or its implementation plan can do on your behalf.

## Error handling

No new retry logic is needed beyond what already exists:
`send_pending_alert`'s established contract (a failed send leaves
filings unnotified, so the whole batch is retried automatically next
scheduled run) already covers a transient SMTP failure correctly, and
`poll_gmp_snapshots`/`send_gmp_update_alerts` already abort cleanly on a
`429`/HTTP error with zero side effects. A workflow run that raises an
unhandled exception shows as a failed run in GitHub's Actions tab —
GitHub's own default notification settings email the repo owner on a
failed scheduled workflow run, which is sufficient visibility without
building anything new.

## Explicitly out of scope (deferred, not needed now)

- **PDF archival by any other mechanism** (GitHub Release assets, Git
  LFS, external storage) — decided against; the email's direct
  `document_url` link already serves the actual need.
- **Persisting `logs/ipo_watcher.log` across runs** — each run's log is
  visible in that run's own Actions output via the `cat` step; nothing
  is lost that matters, and committing a growing log file back to git
  adds churn for no real benefit.
- **Handling a real git push race between a manual local commit and a
  scheduled Actions run** — low-probability (the user's own manual
  commits target code/docs, not the DB file directly), not engineered
  around now per YAGNI.
- **Migrating off GitHub Actions** if its free-tier limits change later
  — not a problem that exists today.

## What this spec asks to be implemented next

1. Add `skip_pdf_download` parameter and `--skip-pdf-download` CLI flag
   to `ipo_watcher/run.py`'s `main()`.
2. Create `.github/workflows/sebi-collector.yml`.
3. Create `.github/workflows/gmp-poller.yml`.
4. Tests for the `main(skip_pdf_download=True)` behavior (that
   `download_pending_pdfs` is not called, that the existing default
   behavior is unchanged).
5. **Manual, non-automatable step**: add the seven required secrets to
   the GitHub repo's Settings → Secrets → Actions, by hand.

## Open questions for the next pass

1. GitHub's documented 60-day auto-disable for a scheduled workflow with
   no repository activity should self-resolve once this runs regularly
   (each successful commit-back is itself repository activity), but this
   is unverified against real long-term behavior — worth a note-to-self
   to check back in a couple of months that both schedules are still
   actually firing.
2. The GMP poller's exact 10/day free-tier budget assumes nothing else
   ever calls the IPO Guru API in the same day (e.g. manual `curl`
   testing, as happened repeatedly today) — this spec does not add any
   safeguard against a human accidentally exhausting the day's quota
   before the scheduled runs complete; flagged, not solved.
