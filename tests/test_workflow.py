from pathlib import Path

import yaml

TEXT = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "radar-collect.yml").read_text(encoding="utf-8")
FLOW = yaml.safe_load(TEXT)
JOB = FLOW["jobs"]["collect"]
FALLBACK = "${{ inputs.domain || 'embodied' }}"


def step(name_or_uses: str) -> int:
    return next(i for i, s in enumerate(JOB["steps"]) if name_or_uses in (s.get("name"), s.get("uses")))


def test_weekly_schedule():
    assert FLOW[True]["schedule"] == [{"cron": "0 2 * * 1"}]  # PyYAML reads the `on` key as True


def test_domain_falls_back_once_for_scheduled_runs():
    assert JOB["env"]["DOMAIN"] == FALLBACK
    assert JOB["concurrency"]["group"] == "radar-collect-" + FALLBACK
    assert TEXT.count("inputs.domain") == 2


def test_collect_artifact_and_commit_use_domain():
    steps = JOB["steps"]
    assert '--domain "$DOMAIN"' in steps[step("Collect")]["run"]
    upload = steps[step("Upload artifact")]["with"]
    assert upload == {"name": "collect-${{ env.DOMAIN }}", "path": "out/${{ env.DOMAIN }}/latest/"}
    commit = steps[step("Commit state and candidates")]["run"]
    assert 'git add "state/$DOMAIN/" "history/$DOMAIN/"' in commit and "git commit" in commit


def test_weekly_judge_runs_between_collect_and_commit_with_pinned_model():
    weekly = JOB["steps"][step("Judge, wiki and report")]
    assert step("Collect") < step("Judge, wiki and report") < step("Commit state and candidates")
    assert weekly["run"] == 'python -m radar.weekly --domain "$DOMAIN"'
    assert weekly["env"]["DEEPSEEK_API_KEY"] == "${{ secrets.DEEPSEEK_API_KEY }}"
    assert weekly["env"]["DEEPSEEK_MODEL"] == "deepseek-chat"
    assert JOB["timeout-minutes"] == 45
    commit = JOB["steps"][step("Commit state and candidates")]["run"]
    assert '"wiki/$DOMAIN/" "reports/$DOMAIN/"' in commit and "judged.jsonl" in commit


def test_state_is_pulled_before_collecting_and_pushable():
    assert FLOW["permissions"]["contents"] == "write"
    assert step("Pull latest state") < step("Collect")
    assert JOB["steps"][step("Pull latest state")]["run"] == "git pull --ff-only origin main"
