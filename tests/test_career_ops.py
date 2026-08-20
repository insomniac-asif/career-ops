import json

import career_ops as ops


DAY = "2026-08-16"


def lead(lead_id, *, low=80_000, high=100_000, lane_priority=2,
         blocked=False, eligible=True, live_ok=True, has_application=False,
         stage="drafting", created_at=100, score=50):
    return {
        "id": lead_id, "company": "Synthetic Co", "role": "Software Engineer",
        "stage": stage, "blocked": blocked, "eligible": eligible,
        "live_ok": live_ok, "has_application": has_application,
        "target_lane": {
            "id": "python_backend_automation", "label": "Python Backend",
            "priority": lane_priority,
        },
        "compensation": {"state": "known", "min_annual": low,
                         "max_annual": high},
        "created_at": created_at, "score": score,
    }


def test_authority_caps_preparation_and_keeps_submission_owner_only():
    state = ops.new_state(enabled=True, day=DAY)
    assert state["daily_cap"] == 3
    assert ops.DAILY_PREP_CAP == 3
    assert state["authority"]["prepare_local_draft"].startswith("automated")
    assert state["authority"]["submit_application"] == "owner_only"
    assert state["authority"]["send_message"] == "owner_only"


def test_rank_is_two_tier_and_never_invents_unknown_pay():
    rows = [
        lead("bridge", low=60_000, high=80_000, created_at=300),
        lead("stretch-possible", low=100_000, high=130_000, created_at=200),
        lead("stretch-confirmed", low=125_000, high=140_000, created_at=100),
        lead("below", low=40_000, high=50_000),
    ]
    unknown = lead("unknown")
    unknown["compensation"] = {"state": "unknown"}
    rows.append(unknown)
    ranked = ops.rank_candidates(rows)
    assert [row["lead_id"] for row in ranked] == [
        "stretch-confirmed", "stretch-possible", "bridge",
    ]
    assert ops.classify_pay(unknown)["id"] == "needs_pay_research"


def test_fail_closed_filters_every_unverified_or_already_handled_path():
    rows = [
        lead("good"),
        lead("blocked", blocked=True),
        lead("eligibility-unknown", eligible=None),
        lead("dead", live_ok=False),
        lead("already-drafted", has_application=True),
        lead("already-applied", stage="applied"),
    ]
    outside = lead("outside")
    outside["target_lane"] = {"id": None, "priority": 99}
    rows.append(outside)
    assert [row["lead_id"] for row in ops.rank_candidates(rows)] == ["good"]


def test_run_prepares_at_most_three_successes_and_is_idempotent(tmp_path):
    path = tmp_path / "state.json"
    ops.set_enabled(str(path), True, day=DAY)
    calls = []

    def generate(lead_id):
        calls.append(lead_id)
        return {"ok": True, "draft_label": "draft — review before sending"}

    rows = [lead(f"lead-{n}", created_at=1000 - n) for n in range(6)]
    first = ops.run_preparation(rows, generate, str(path), day=DAY, now=1)
    second = ops.run_preparation(rows, generate, str(path), day=DAY, now=2)
    assert first["prepared_today"] == 3
    assert first["remaining_today"] == 0
    assert second["prepared_today"] == 3
    assert calls == ["lead-0", "lead-1", "lead-2"]
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert len(saved["prepared"]) == 3
    assert saved["authority"]["submit_application"] == "owner_only"


def test_failure_does_not_consume_success_slot_or_retry_same_day(tmp_path):
    path = tmp_path / "state.json"
    ops.set_enabled(str(path), True, day=DAY)
    calls = []

    def fail_once(lead_id):
        calls.append(lead_id)
        return {"ok": lead_id != "lead-0", "error": "synthetic failure"}

    rows = [lead(f"lead-{n}", created_at=1000 - n) for n in range(5)]
    first = ops.run_preparation(rows, fail_once, str(path), day=DAY, now=1)
    second = ops.run_preparation(rows, fail_once, str(path), day=DAY, now=2)
    assert first["prepared_today"] == 2
    assert second["prepared_today"] == 3
    assert calls.count("lead-0") == 1
    assert calls == ["lead-0", "lead-1", "lead-2", "lead-3"]


def test_new_day_resets_counter_but_preserves_owner_enablement(tmp_path):
    path = tmp_path / "state.json"
    state = ops.new_state(enabled=True, day=DAY)
    state["prepared"] = [{"lead_id": "old", "ok": True, "at": 1}]
    path.write_text(json.dumps(state), encoding="utf-8")
    tomorrow = ops.load_state(str(path), day="2026-08-17")
    assert tomorrow["enabled"] is True
    assert tomorrow["prepared"] == []
    assert tomorrow["attempts"] == []


def test_disabling_during_a_run_stops_before_the_next_draft(tmp_path):
    path = tmp_path / "state.json"
    ops.set_enabled(str(path), True, day=DAY)
    calls = []

    def disable_after_first(lead_id):
        calls.append(lead_id)
        ops.set_enabled(str(path), False, day=DAY)
        return {"ok": True}

    rows = [lead(f"lead-{n}", created_at=1000 - n) for n in range(3)]
    result = ops.run_preparation(rows, disable_after_first, str(path), day=DAY)
    assert calls == ["lead-0"]
    assert result["enabled"] is False
    assert result["prepared_today"] == 1
    assert "owner disabled" in result["last_run"]["message"]


def test_malformed_state_fails_disabled_with_warning(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("not-json", encoding="utf-8")
    state = ops.load_state(str(path), day=DAY)
    assert state["enabled"] is False
    assert state["prepared"] == []
    assert "warning" in state["_meta"]


def test_cap_above_three_is_rejected():
    try:
        ops.new_state(enabled=True, day=DAY, daily_cap=4)
    except ValueError as exc:
        assert "between 1 and 3" in str(exc)
    else:
        raise AssertionError("daily cap above three was accepted")
