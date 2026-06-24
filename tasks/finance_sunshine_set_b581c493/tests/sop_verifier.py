#!/usr/bin/env python3
"""Run bundled SOP Python verifiers inside a Harbor task container."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

WORKDIR = Path("/workdir")
DATA_DIR = Path("/data")
INITIAL_DATA_DIR = Path("/initial_data")
TESTS_DIR = Path("/tests")
VERIFIER_DIR = Path("/logs/verifier")

SERVICE_COMPAT_FILES: dict[str, tuple[str, tuple[str, ...]]] = {
    "slack": ("slack.json", ("slack.json", "slack_data.json")),
    "google_mail": ("inbox.json", ("inbox.json", "mailbox.json")),
    "google_calendar": ("calendar_data.json", ("calendar_data.json", "calendar.json")),
    "jira": ("jira_state.json", ("jira_state.json", "jira_data.json")),
    "shopify": ("shopify_data.json", ("shopify_data.json",)),
}


def _state_path(service: str, seed_name: str) -> Path | None:
    candidates = [
        DATA_DIR / service / "final.json",
        DATA_DIR / service / seed_name,
        INITIAL_DATA_DIR / service / seed_name,
    ]
    return next((p for p in candidates if p.is_file()), None)


def _build_compat_external_services(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for service, (seed_name, compat_names) in SERVICE_COMPAT_FILES.items():
        src = _state_path(service, seed_name)
        if src is not None:
            for compat_name in compat_names:
                shutil.copy2(src, dest / compat_name)


def _coerce_result(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        passed = bool(raw.get("pass", raw.get("passed", False)))
        score = raw.get("score", 1.0 if passed else 0.0)
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = 1.0 if passed else 0.0
        return {
            "pass": passed,
            "score": max(0.0, min(1.0, score)),
            "feedback": str(raw.get("feedback", "")),
        }
    passed = bool(raw)
    return {"pass": passed, "score": 1.0 if passed else 0.0, "feedback": str(raw)}


def _run_one(rubric: dict[str, Any], external_services_path: Path) -> dict[str, Any]:
    rubric_id = str(rubric.get("id") or "rubric")
    code = rubric.get("verifier_code")
    if not isinstance(code, str) or not code.strip():
        return {
            "id": rubric_id,
            "pass": False,
            "score": 0.0,
            "feedback": "rubric has no verifier_code",
        }

    namespace: dict[str, Any] = {"__builtins__": __builtins__}
    try:
        exec(compile(code, f"<{rubric_id}>", "exec"), namespace)
        verify = namespace.get("verify")
        if not callable(verify):
            raise RuntimeError("verifier_code did not define verify()")
        result = _coerce_result(verify(str(WORKDIR), str(external_services_path)))
        return {"id": rubric_id, **result}
    except Exception:
        return {
            "id": rubric_id,
            "pass": False,
            "score": 0.0,
            "feedback": traceback.format_exc(),
        }


def main() -> None:
    rubrics_path = TESTS_DIR / "rubrics.json"
    if not rubrics_path.is_file():
        print("[sop-verifier] ERROR: rubrics.json not found", file=sys.stderr)
        sys.exit(1)

    rubrics = json.loads(rubrics_path.read_text())
    if not isinstance(rubrics, list):
        print("[sop-verifier] ERROR: rubrics.json must be a list", file=sys.stderr)
        sys.exit(1)

    with tempfile.TemporaryDirectory(prefix="sop-external-services-") as tmp:
        compat_dir = Path(tmp)
        _build_compat_external_services(compat_dir)
        results = [_run_one(r, compat_dir) for r in rubrics]

    total = len(results)
    passed = sum(1 for r in results if r.get("pass"))
    average_score = round(
        sum(float(r.get("score", 0.0)) for r in results) / total,
        4,
    ) if total else 0.0

    print(f"[sop-verifier] {passed}/{total} rubrics passed; score={average_score:.2f}")
    for result in results:
        status = "PASS" if result.get("pass") else "FAIL"
        feedback = str(result.get("feedback", "")).replace("\n", " ")[:500]
        print(f"  [{status}] {result.get('id')}: {feedback}")

    output = {
        "passed": passed == total,
        "rubrics_passed": passed,
        "rubrics_total": total,
        "score": average_score,
        "rubric_results": results,
    }
    (TESTS_DIR / "results.json").write_text(json.dumps(output, indent=2) + "\n")

    VERIFIER_DIR.mkdir(parents=True, exist_ok=True)
    (VERIFIER_DIR / "reward.txt").write_text(str(average_score))


if __name__ == "__main__":
    main()
