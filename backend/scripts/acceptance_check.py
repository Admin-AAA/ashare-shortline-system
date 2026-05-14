from __future__ import annotations

import json
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BASE_URL = "http://127.0.0.1:8000"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def request(method: str, path: str, payload: Any | None = None, timeout: int = 180) -> tuple[int, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = raw
        return exc.code, body


def add(checks: list[Check], name: str, condition: bool, detail: str = "") -> None:
    checks.append(Check(name, bool(condition), detail))


def weighted_score(component_scores: dict[str, Any], weights: dict[str, Any]) -> float:
    return sum(float(component_scores.get(k, 0)) * float(weights.get(k, 0)) for k in weights)


def perf(method: str, path: str, payload: Any | None = None, rounds: int = 12) -> dict[str, Any]:
    timings: list[float] = []
    statuses: list[int] = []
    for _ in range(rounds):
        started = time.perf_counter()
        status, _ = request(method, path, payload, timeout=240)
        elapsed = (time.perf_counter() - started) * 1000
        statuses.append(status)
        timings.append(elapsed)
    samples = timings[2:] if len(timings) > 2 else timings
    ordered = sorted(samples)
    p95_index = min(len(ordered) - 1, int(len(ordered) * 0.95))
    return {
        "path": path,
        "statuses": statuses,
        "min_ms": round(min(samples), 1),
        "p50_ms": round(statistics.median(samples), 1),
        "p95_ms": round(ordered[p95_index], 1),
        "max_ms": round(max(samples), 1),
    }


def main() -> int:
    checks: list[Check] = []
    artifacts: dict[str, Any] = {"created": {}, "performance": []}

    status, health = request("GET", "/api/health")
    add(checks, "GET /api/health", status == 200 and health == {"ok": True}, f"status={status}")

    status, presets = request("GET", "/api/presets")
    add(checks, "GET /api/presets", status == 200 and isinstance(presets, list) and len(presets) > 0)
    first_preset = presets[0]
    required_preset_fields = {"preset_id", "name", "run_mode", "output_count", "bucket_limits", "skills"}
    add(checks, "preset fields complete", required_preset_fields.issubset(first_preset), str(first_preset.keys()))

    status, skills = request("GET", "/api/skills")
    add(checks, "GET /api/skills", status == 200 and isinstance(skills, list) and len(skills) > 0)
    first_skill = skills[0]
    add(
        checks,
        "skill list fields complete",
        {"skill_id", "name", "mode", "enabled", "assigned_presets"}.issubset(first_skill),
    )
    skill_id = next((s["skill_id"] for s in skills if s["skill_id"] == "daily-stock-analysis-skill"), first_skill["skill_id"])

    preset_payload = {
        "name": f"验收自动预设-{int(time.time())}",
        "description": "自动验收创建",
        "run_mode": "experiment",
        "market_scope": "industry",
        "market_scope_value": "电气设备",
        "risk_template": "balanced",
        "output_count": 2,
        "data_source": "local_pool",
        "hide_recent_repeats": False,
        "bucket_limits": {"容量核心": 1, "高弹观察": 1},
        "skills": [{"skill_id": skill_id, "weight": 1.0, "sort_order": 0}],
    }
    status, created_preset = request("POST", "/api/presets", preset_payload)
    preset_id = created_preset.get("preset_id") if isinstance(created_preset, dict) else ""
    artifacts["created"]["preset_id"] = preset_id
    add(checks, "POST /api/presets", status == 200 and bool(preset_id), f"status={status}")

    status, refreshed_presets = request("GET", "/api/presets")
    add(checks, "created preset visible", any(p.get("preset_id") == preset_id for p in refreshed_presets), preset_id)

    patch_payload = {
        "name": f"{preset_payload['name']}-patched",
        "output_count": 3,
        "skills": [{"skill_id": skill_id, "weight": 1.37, "sort_order": 0}],
        "bucket_limits": {"容量核心": 2, "高弹观察": 1},
    }
    status, patched = request("PATCH", f"/api/presets/{preset_id}", patch_payload)
    status2, after_patch = request("GET", "/api/presets")
    saved = next((p for p in after_patch if p.get("preset_id") == preset_id), {})
    add(checks, "PATCH /api/presets persists scalar fields", status == 200 and saved.get("name") == patch_payload["name"] and saved.get("output_count") == 3)
    add(
        checks,
        "PATCH /api/presets persists skill order/weight",
        saved.get("skills", [{}])[0].get("skill_id") == skill_id
        and abs(float(saved.get("skills", [{}])[0].get("weight", 0)) - 1.37) < 0.001
        and int(saved.get("skills", [{}])[0].get("sort_order", -1)) == 0,
    )
    status, _ = request("PATCH", "/api/presets/not-exists", {"name": "x"})
    add(checks, "PATCH nonexistent preset returns 404", status == 404, f"status={status}")

    status, skill_detail = request("GET", f"/api/skills/{skill_id}")
    add(
        checks,
        "GET /api/skills/{id}",
        status == 200 and {"skill_md", "performance", "errors", "assigned_presets"}.issubset(skill_detail),
    )
    original_mode = next(s["mode"] for s in skills if s["skill_id"] == skill_id)
    for mode in ["experiment", "disabled", original_mode]:
        status, mode_result = request("POST", f"/api/skills/{skill_id}/mode", {"mode": mode})
        status_detail, detail_after_mode = request("GET", f"/api/skills/{skill_id}")
        add(checks, f"POST /api/skills/{{id}}/mode {mode}", status == 200 and status_detail == 200 and detail_after_mode.get("mode") == mode)

    status, bad_mode = request("POST", f"/api/skills/{skill_id}/mode", {})
    add(checks, "empty mode body returns validation error", status in {400, 422}, f"status={status}")

    run_payload = {"preset_id": preset_id, "skip_aux_refresh": True, "agent_review_limit": 3, "wait_for_completion": True}
    started = time.perf_counter()
    status, run_result = request("POST", "/api/runs", run_payload, timeout=240)
    run_elapsed = time.perf_counter() - started
    run = run_result.get("run", {}) if isinstance(run_result, dict) else {}
    run_id = run.get("run_id")
    artifacts["created"]["run_id"] = run_id
    add(
        checks,
        "POST /api/runs completed",
        status == 200 and run.get("status") == "completed" and run.get("recommended_count", 0) > 0,
        f"status={status}, elapsed={run_elapsed:.1f}s, count={run.get('recommended_count')}",
    )

    status, runs = request("GET", "/api/runs")
    add(checks, "GET /api/runs desc", status == 200 and runs[0].get("started_at") >= runs[min(1, len(runs) - 1)].get("started_at", ""))

    status, run_detail = request("GET", f"/api/runs/{run_id}")
    audit = run_detail.get("pipeline_audit", {})
    add(checks, "GET /api/runs/{id} includes params/snapshot/audit", status == 200 and run_detail.get("params") and run_detail.get("market_snapshot") and audit)
    add(
        checks,
        "pipeline audit counts self-consistent",
        int(audit.get("actual_scan_count", 0)) >= int(audit.get("skill_input_count", 0)) >= 0
        and int(audit.get("skill_agent_call_count", 0)) >= int(audit.get("skill_review_success_count", 0)) >= 0,
        json.dumps(audit, ensure_ascii=False)[:500],
    )

    status, recs = request("GET", f"/api/runs/{run_id}/recommendations")
    add(checks, "GET /api/runs/{id}/recommendations sorted", status == 200 and recs == sorted(recs, key=lambda r: r.get("final_score", 0), reverse=True))
    first_rec = recs[0]
    rec_id = first_rec["recommendation_id"]
    artifacts["created"]["recommendation_id"] = rec_id

    status, rec_detail = request("GET", f"/api/recommendations/{rec_id}")
    add(checks, "GET /api/recommendations/{id}", status == 200 and {"feature_snapshot", "skill_breakdown", "agent_logs", "timeline", "outcome", "history"}.issubset(rec_detail))
    fs = rec_detail.get("feature_snapshot", {})
    component_scores = fs.get("component_scores", {})
    weights = fs.get("aggregation_weights") or rec_detail.get("market_context", {}).get("aggregation_weights") or fs.get("aggregation_weights") or {}
    add(checks, "component scores five dimensions", {"skill_agent", "strategy_rule", "market", "risk", "evidence"}.issubset(component_scores))
    if not weights:
        weights = fs.get("aggregation_weights") or first_rec.get("feature_snapshot", {}).get("aggregation_weights") or {}
    approx = weighted_score(component_scores, weights) if weights else rec_detail.get("final_score", 0)
    add(checks, "final_score approx weighted component sum", abs(float(rec_detail["final_score"]) - approx) / max(float(rec_detail["final_score"]), 1) < 0.015, f"final={rec_detail['final_score']} approx={approx:.2f}")
    add(checks, "recommend price positive", float(rec_detail.get("recommend_price") or 0) > 0)
    placeholder_terms = ["测试证据", "证据1", "证据2", "placeholder"]
    logs_text = json.dumps(rec_detail.get("agent_logs", []), ensure_ascii=False)
    add(checks, "agent logs no placeholder text", not any(term in logs_text for term in placeholder_terms))
    skipped = [
        s for s in rec_detail.get("skill_breakdown", [])
        if s.get("agent_review_skipped")
    ]
    add(checks, "skipped skill reason present", all((s["agent_review_skipped"] or {}).get("reason") for s in skipped))

    status, watch = request("POST", f"/api/recommendations/{rec_id}/watch", {"watch_status": "watching", "note": "验收观察"})
    status2, rec_after_watch = request("GET", f"/api/recommendations/{rec_id}")
    add(checks, "POST /api/recommendations/{id}/watch persists", status == 200 and rec_after_watch.get("watch_status") == "watching")

    status, review = request("POST", f"/api/recommendations/{rec_id}/review", {"user_label": "acceptance", "failure_reason": "", "review_text": "验收复盘"})
    status2, rec_after_review = request("GET", f"/api/recommendations/{rec_id}")
    add(checks, "POST /api/recommendations/{id}/review persists", status == 200 and rec_after_review.get("review_note", {}).get("review_text") == "验收复盘")

    status, overview = request("GET", "/api/stats/overview?mode=experiment")
    add(checks, "GET /api/stats/overview arithmetic", status == 200 and overview.get("success_count", 0) + overview.get("failed_count", 0) + overview.get("pending_count", 0) == overview.get("recommendation_count", -1))
    status, by_skill = request("GET", "/api/stats/by-skill?mode=experiment")
    add(checks, "GET /api/stats/by-skill", status == 200 and isinstance(by_skill, list))
    status, by_preset = request("GET", "/api/stats/by-preset?mode=experiment")
    add(checks, "GET /api/stats/by-preset", status == 200 and any(row.get("preset_id") == preset_id for row in by_preset))

    status, sources = request("GET", "/api/data-sources")
    add(checks, "GET /api/data-sources", status == 200 and isinstance(sources, list))
    status, pool = request("GET", "/api/data-sources/pool?u_limit=3&b_limit=3")
    add(checks, "GET /api/data-sources/pool pagination", status == 200 and len(pool.get("unified_quotes", [])) <= 3 and len(pool.get("daily_bars", [])) <= 3)
    if sources:
        source = sources[0]
        status, toggled = request("POST", f"/api/data-sources/{source['source_type']}/{source['source']}/enabled", {"enabled": not source["enabled"]})
        status2, restored = request("POST", f"/api/data-sources/{source['source_type']}/{source['source']}/enabled", {"enabled": source["enabled"]})
        add(checks, "POST /api/data-sources/{type}/{source}/enabled", status == 200 and status2 == 200 and restored.get("enabled") == source["enabled"])

    status, one_label = request("POST", f"/api/outcomes/{rec_id}/label", {"outcome_label": "success", "outcome_reason": "验收手动标注", "horizon_days": 5})
    add(checks, "POST /api/outcomes/{id}/label", status == 200 and one_label.get("outcome_label") == "success")
    status, many_label = request("POST", "/api/outcomes/label?horizon_days=5&run_mode=experiment")
    add(checks, "POST /api/outcomes/label", status == 200 and many_label.get("labeled", 0) >= 0)

    status, bootstrap = request("GET", "/api/home/bootstrap")
    summary = bootstrap.get("skill_summary", {}) if isinstance(bootstrap, dict) else {}
    add(checks, "GET /api/home/bootstrap", status == 200 and bootstrap.get("presets") and sum(summary.values()) >= len(skills))

    status, _ = request("GET", "/api/runs/not-exists")
    add(checks, "nonexistent run returns 404", status == 404)
    status, _ = request("GET", "/api/recommendations/not-exists")
    add(checks, "nonexistent recommendation returns 404", status == 404)
    status, _ = request("POST", "/api/runs", {})
    add(checks, "empty POST /api/runs does not 500", status in {400, 422}, f"status={status}")

    perf_cases = [
        ("GET", "/api/home/bootstrap", None),
        ("GET", "/api/presets", None),
        ("GET", f"/api/skills/{skill_id}", None),
        ("GET", "/api/data-sources", None),
        ("GET", "/api/data-sources/pool?u_limit=10&b_limit=10", None),
        ("GET", "/api/runs?limit=20", None),
        ("GET", f"/api/runs/{run_id}", None),
        ("GET", f"/api/runs/{run_id}/recommendations", None),
        ("GET", f"/api/recommendations/{rec_id}", None),
        ("GET", "/api/stats/overview?mode=experiment", None),
        ("GET", "/api/stats/by-skill?mode=experiment", None),
        ("GET", "/api/stats/by-preset?mode=experiment", None),
    ]
    for method, path, payload in perf_cases:
        artifacts["performance"].append(perf(method, path, payload, rounds=12))

    report = {
        "checks": [check.__dict__ for check in checks],
        "summary": {
            "total": len(checks),
            "passed": sum(1 for check in checks if check.ok),
            "failed": sum(1 for check in checks if not check.ok),
        },
        "artifacts": artifacts,
    }
    out = Path(__file__).resolve().parents[1] / "acceptance_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    for check in checks:
        print(("OK " if check.ok else "FAIL ") + check.name + (f" :: {check.detail}" if check.detail else ""))
    print(f"report={out}")
    return 1 if report["summary"]["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
