"""Deterministic, local-only career preparation queue.

The engine ranks already-researched leads and invokes a caller-supplied draft
generator for at most three successful packages per local calendar day.  It
does not know how to browse, message, spend, trade, or submit an application.
Those consequential actions remain outside this module by construction.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import threading
import time
from datetime import date, datetime
from typing import Callable, Iterable


SCHEMA_VERSION = 1
DAILY_PREP_CAP = 3
BRIDGE_ANNUAL_FLOOR = 55_000.0
STRETCH_ANNUAL_TARGET = 120_000.0
AUTHORITY = {
    "discover_extract": "automated_elsewhere",
    "rank_queue": "automated",
    "prepare_local_draft": "automated_up_to_3_per_day_when_enabled",
    "submit_application": "owner_only",
    "send_message": "owner_only",
    "spend_money": "owner_only",
    "trade": "owner_only",
}

_STATE_LOCK = threading.RLock()
_RUN_LOCK = threading.Lock()


def local_day(value: str | date | None = None) -> str:
    """Return a validated ISO day, defaulting to the machine's local day."""
    if value is None:
        return datetime.now().astimezone().date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError("day must be YYYY-MM-DD") from exc


def _cap(value: int = DAILY_PREP_CAP) -> int:
    if isinstance(value, bool):
        raise ValueError("daily cap must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("daily cap must be an integer") from exc
    if parsed < 1 or parsed > DAILY_PREP_CAP or float(value) != parsed:
        raise ValueError("daily cap must be between 1 and 3")
    return parsed


def new_state(*, enabled: bool = False, day: str | date | None = None,
              daily_cap: int = DAILY_PREP_CAP) -> dict:
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a boolean")
    return {
        "schema_version": SCHEMA_VERSION,
        "enabled": enabled,
        "day": local_day(day),
        "daily_cap": _cap(daily_cap),
        "prepared": [],
        "attempts": [],
        "last_run": {"status": "never"},
        "authority": dict(AUTHORITY),
    }


def _compact_rows(value, *, limit: int) -> list:
    rows = []
    for row in value if isinstance(value, list) else []:
        if not isinstance(row, dict) or not str(row.get("lead_id") or "").strip():
            continue
        rows.append({
            "lead_id": str(row.get("lead_id"))[:160],
            "company": str(row.get("company") or "")[:200],
            "role": str(row.get("role") or "")[:300],
            "tier": str(row.get("tier") or "")[:40],
            "ok": row.get("ok") is True,
            "at": float(row.get("at") or 0),
            "error": str(row.get("error") or "")[:300],
        })
    return rows[-limit:]


def normalize_state(raw, *, day: str | date | None = None) -> dict:
    """Fail closed on malformed state and reset only daily counters at midnight."""
    today = local_day(day)
    if not isinstance(raw, dict):
        return new_state(day=today)
    enabled = raw.get("enabled") is True
    try:
        cap = _cap(raw.get("daily_cap", DAILY_PREP_CAP))
    except ValueError:
        cap = DAILY_PREP_CAP
    state = new_state(enabled=enabled, day=today, daily_cap=cap)
    if raw.get("day") == today:
        state["prepared"] = [
            row for row in _compact_rows(raw.get("prepared"), limit=DAILY_PREP_CAP)
            if row.get("ok") is True
        ]
        state["attempts"] = _compact_rows(raw.get("attempts"), limit=30)
    last_run = raw.get("last_run")
    if isinstance(last_run, dict):
        state["last_run"] = {
            key: last_run.get(key) for key in (
                "status", "started_at", "finished_at", "generated", "failed",
                "message", "error",
            ) if last_run.get(key) is not None
        } or {"status": "never"}
    return state


def _state_for_disk(state: dict) -> dict:
    return {key: state[key] for key in (
        "schema_version", "enabled", "day", "daily_cap", "prepared",
        "attempts", "last_run", "authority",
    )}


def _atomic_write_json(path: str, state: dict) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".career-ops-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(_state_for_disk(state), handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_state(path: str, *, day: str | date | None = None) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
        state = normalize_state(raw, day=day)
        state["_meta"] = {"source": "state_file", "persisted": True}
        return state
    except FileNotFoundError:
        state = new_state(day=day)
        state["_meta"] = {"source": "defaults", "persisted": False}
        return state
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        state = new_state(day=day)
        state["_meta"] = {
            "source": "defaults", "persisted": False,
            "warning": "state unreadable: " + str(exc)[:180],
        }
        return state


def set_enabled(path: str, enabled: bool, *, day: str | date | None = None) -> dict:
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a boolean")
    with _STATE_LOCK:
        state = load_state(path, day=day)
        state["enabled"] = enabled
        if not enabled and state.get("last_run", {}).get("status") == "running":
            state["last_run"] = {
                "status": "disabled", "finished_at": time.time(),
                "message": "disabled by owner; current callback cannot be force-killed",
            }
        _atomic_write_json(path, state)
    return normalize_state(state, day=day)


def _finite(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def classify_pay(lead: dict, *, bridge_floor: float = BRIDGE_ANNUAL_FLOOR,
                 stretch_target: float = STRETCH_ANNUAL_TARGET) -> dict:
    """Classify only explicit annualized evidence; unknown never becomes a salary."""
    comp = lead.get("compensation") if isinstance(lead, dict) else {}
    comp = comp if isinstance(comp, dict) else {}
    low, high = _finite(comp.get("min_annual")), _finite(comp.get("max_annual"))
    if low is None or high is None or low > high:
        return {"id": "needs_pay_research", "priority": 3, "eligible": False,
                "reason": "explicit annual compensation is missing or invalid"}
    if low >= stretch_target:
        tier = ("stretch_confirmed", 0,
                "the documented minimum meets the $10k/month gross target")
    elif high >= stretch_target:
        tier = ("stretch_possible", 1,
                "the documented range can meet the $10k/month gross target")
    elif high >= bridge_floor:
        tier = ("bridge", 2,
                "the documented range meets the bridge-income floor")
    else:
        tier = ("below_bridge", 4,
                "the documented maximum is below the bridge-income floor")
    return {
        "id": tier[0], "priority": tier[1],
        "eligible": tier[0] != "below_bridge", "reason": tier[2],
        "min_annual": low, "max_annual": high,
        "bridge_floor": float(bridge_floor),
        "stretch_target": float(stretch_target),
    }


def assess_candidate(lead: dict) -> dict:
    lead = lead if isinstance(lead, dict) else {}
    reasons = []
    lead_id = str(lead.get("id") or "").strip()
    if not lead_id:
        reasons.append("missing lead id")
    if lead.get("stage") != "drafting":
        reasons.append("not in drafting stage")
    if lead.get("blocked") is not False:
        reasons.append("research gate is blocked or unknown")
    if lead.get("eligible") is not True:
        reasons.append("work eligibility is not verified true")
    if lead.get("live_ok") is not True:
        reasons.append("posting liveness is not verified true")
    if lead.get("has_application") is not False:
        reasons.append("draft already exists or state is unknown")
    lane = lead.get("target_lane") if isinstance(lead.get("target_lane"), dict) else {}
    if not lane.get("id"):
        reasons.append("outside configured role lanes")
    pay = classify_pay(lead)
    if not pay["eligible"]:
        reasons.append(pay["reason"])
    try:
        lane_priority = int(lane.get("priority", 99))
    except (TypeError, ValueError):
        lane_priority = 99
    return {
        "eligible": not reasons,
        "reasons": reasons,
        "candidate": {
            "lead_id": lead_id[:160],
            "company": str(lead.get("company") or "")[:200],
            "role": str(lead.get("role") or "")[:300],
            "tier": pay["id"],
            "tier_reason": pay["reason"],
            "pay": pay,
            "target_lane": {
                "id": lane.get("id"), "label": lane.get("label"),
                "priority": lane_priority,
            },
            "score": _finite(lead.get("score")) or 0.0,
            "created_at": _finite(lead.get("created_at")) or 0.0,
        },
    }


def rank_candidates(leads: Iterable[dict]) -> list[dict]:
    candidates = []
    for lead in leads or []:
        assessed = assess_candidate(lead)
        if assessed["eligible"]:
            candidates.append(assessed["candidate"])
    candidates.sort(key=lambda row: (
        row["pay"]["priority"], row["target_lane"]["priority"],
        -row["created_at"], -row["score"], row["lead_id"],
    ))
    return candidates


def _successful_ids(state: dict) -> set[str]:
    return {str(row.get("lead_id")) for row in state.get("prepared", [])
            if isinstance(row, dict) and row.get("ok") is True}


def _attempted_ids(state: dict) -> set[str]:
    return {str(row.get("lead_id")) for row in state.get("attempts", [])
            if isinstance(row, dict)}


def select_candidates(leads: Iterable[dict], state: dict, *,
                      day: str | date | None = None) -> list[dict]:
    state = normalize_state(state, day=day)
    remaining = max(0, state["daily_cap"] - len(_successful_ids(state)))
    attempted = _attempted_ids(state)
    available = [row for row in rank_candidates(leads)
                 if row["lead_id"] not in attempted]
    return available[:remaining]


def build_status(leads: Iterable[dict], state: dict, *,
                 day: str | date | None = None) -> dict:
    state = normalize_state(state, day=day)
    ranked = rank_candidates(leads)
    prepared = len(_successful_ids(state))
    remaining = max(0, state["daily_cap"] - prepared)
    attempted = _attempted_ids(state)
    selected = [row for row in ranked if row["lead_id"] not in attempted][:remaining]
    return {
        "ok": True,
        "enabled": state["enabled"],
        "day": state["day"],
        "daily_cap": state["daily_cap"],
        "prepared_today": prepared,
        "remaining_today": remaining,
        "attempted_today": len(_attempted_ids(state)),
        "eligible_now": len(ranked),
        "queue_preview": selected,
        "last_run": state.get("last_run") or {"status": "never"},
        "authority": dict(AUTHORITY),
        "boundary": (
            "Automates local draft preparation only; the owner reviews facts and "
            "performs every final application submission."
        ),
    }


def run_preparation(leads: Iterable[dict], generator: Callable[[str], dict],
                    state_path: str, *, day: str | date | None = None,
                    now: float | None = None) -> dict:
    """Prepare a bounded queue sequentially and checkpoint after every attempt."""
    timestamp = float(now if now is not None else time.time())
    if not _RUN_LOCK.acquire(blocking=False):
        state = load_state(state_path, day=day)
        status = build_status(leads, state, day=day)
        status.update({"already_running": True, "run_started": False})
        return status
    try:
        with _STATE_LOCK:
            state = load_state(state_path, day=day)
            if not state["enabled"]:
                status = build_status(leads, state, day=day)
                status.update({"run_started": False, "message": "auto-prep is disabled"})
                return status
            queue = select_candidates(leads, state, day=day)
            state["last_run"] = {
                "status": "running", "started_at": timestamp,
                "generated": 0, "failed": 0,
            }
            _atomic_write_json(state_path, state)

        results = []
        stopped_disabled = False
        for candidate in queue:
            try:
                result = generator(candidate["lead_id"])
                if not isinstance(result, dict):
                    raise TypeError("generator must return a dict")
            except Exception as exc:
                result = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:220]}"}
            ok = result.get("ok") is True
            row = {
                "lead_id": candidate["lead_id"], "company": candidate["company"],
                "role": candidate["role"], "tier": candidate["tier"],
                "ok": ok, "at": time.time(),
                "error": "" if ok else str(result.get("error") or "generation failed")[:300],
            }
            results.append(dict(row, result=result))
            with _STATE_LOCK:
                state = load_state(state_path, day=day)
                state["attempts"].append(row)
                state["attempts"] = state["attempts"][-30:]
                if ok and candidate["lead_id"] not in _successful_ids(state):
                    state["prepared"].append(row)
                    state["prepared"] = state["prepared"][-state["daily_cap"]:]
                state["last_run"]["generated"] = sum(1 for item in results if item["ok"])
                state["last_run"]["failed"] = sum(1 for item in results if not item["ok"])
                _atomic_write_json(state_path, state)
                continue_enabled = state["enabled"] is True
            if not continue_enabled:
                stopped_disabled = True
                break

        with _STATE_LOCK:
            state = load_state(state_path, day=day)
            state["last_run"].update({
                "status": "complete", "finished_at": time.time(),
                "generated": sum(1 for item in results if item["ok"]),
                "failed": sum(1 for item in results if not item["ok"]),
                "message": ("stopped after the in-flight draft because the owner "
                            "disabled auto-prep" if stopped_disabled else
                            "no eligible unattempted leads" if not queue else
                            "local draft preparation finished"),
            })
            _atomic_write_json(state_path, state)
        status = build_status(leads, state, day=day)
        status.update({"run_started": True, "results": results})
        return status
    finally:
        _RUN_LOCK.release()
