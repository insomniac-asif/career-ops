import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "career_ops_cli.py"
FIXTURE = ROOT / "examples" / "leads.synthetic.json"


def _run(*args):
    return subprocess.run(
        [sys.executable, str(CLI), *map(str, args)],
        cwd=ROOT, text=True, capture_output=True, timeout=20,
    )


def test_plan_outputs_the_expected_three_synthetic_candidates():
    result = _run("plan", FIXTURE)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert [row["lead_id"] for row in payload["selected"]] == [
        "synthetic-stretch-confirmed",
        "synthetic-stretch-possible",
        "synthetic-bridge",
    ]
    assert payload["daily_cap"] == 3
    assert payload["authority"]["submit_application"] == "owner_only"


def test_simulation_is_idempotent_and_never_reports_submission(tmp_path):
    state = tmp_path / "state.json"
    first = _run("simulate", FIXTURE, "--state", state)
    second = _run("simulate", FIXTURE, "--state", state)
    assert first.returncode == second.returncode == 0
    first_payload, second_payload = json.loads(first.stdout), json.loads(second.stdout)
    assert first_payload["prepared_today"] == 3
    assert second_payload["prepared_today"] == 3
    assert second_payload["results"] == []
    for row in first_payload["results"]:
        assert row["result"]["submitted"] is False


def test_bad_input_fails_with_nonzero_exit(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"not_leads": true}', encoding="utf-8")
    result = _run("plan", bad)
    assert result.returncode == 2
    assert "leads[]" in result.stderr
