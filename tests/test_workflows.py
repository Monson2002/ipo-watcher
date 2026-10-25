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
    assert set(env.keys()) == {
        "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD",
        "ALERT_FROM", "ALERT_TO", "IPO_GURU_API_KEY",
    }
    for value in env.values():
        assert value.startswith("${{ secrets.")


def test_both_workflows_share_the_same_concurrency_group():
    collector = _load("sebi-collector.yml")
    poller = _load("gmp-poller.yml")
    assert collector["concurrency"]["group"] == poller["concurrency"]["group"]
