from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.scripts._server_utils import start_server, stop_server

HARNESS = ROOT / "harness"
RESULTS = HARNESS / "results"
RAW_RESULT = RESULTS / "promptfoo-eval.json"
TIER_RESULT = RESULTS / "tier-1.json"


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def compute_verify_quality(results: list[dict]) -> float:
    qualities: list[float] = []
    for result in results:
        test_case = result.get("testCase") or {}
        metadata = test_case.get("metadata") or {}
        if metadata.get("mode") != "verify":
            continue
        if result.get("error") or not result.get("success"):
            qualities.append(0.0)
            continue
        try:
            output = ((result.get("response") or {}).get("output")) or ""
            data = json.loads(output)
            score = float(data.get("total_score") or ((data.get("summary") or {}).get("fit_score")) or 0.0)
        except Exception:
            qualities.append(0.0)
            continue

        expectation = metadata.get("expectation")
        if expectation == "verify_concrete":
            quality = clamp((score - 35.0) / 35.0 * 100.0)
        elif expectation == "verify_authority_mismatch":
            quality = clamp((40.0 - score) / 40.0 * 100.0)
        elif expectation == "verify_slogan":
            quality = clamp((45.0 - score) / 45.0 * 100.0)
        else:
            quality = 0.0
        qualities.append(quality)

    return round(sum(qualities) / len(qualities), 2) if qualities else 0.0


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    npx = shutil.which("npx.cmd") or shutil.which("npx")
    if not npx:
        payload = {
            "tier": 1,
            "score": 0.0,
            "pass": False,
            "details": {
                "stderr": "npx executable not found",
                "command": ["npx", "promptfoo", "eval"],
            },
        }
        TIER_RESULT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 1

    process = None
    command = [
        npx,
        "promptfoo",
        "eval",
        "--config",
        str(HARNESS / "promptfoo.yaml"),
        "--output",
        str(RAW_RESULT),
    ]

    env = dict(**__import__("os").environ)
    env.setdefault("T1_BASE_URL", "http://127.0.0.1:8011")
    env.setdefault("PROMPTFOO_DISABLE_TELEMETRY", "1")
    try:
        process = start_server(8011, RESULTS / "tier-1.server.out.log", RESULTS / "tier-1.server.err.log")
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
    finally:
        stop_server(process)
    if completed.returncode != 0:
        payload = {
            "tier": 1,
            "score": 0.0,
            "pass": False,
            "details": {
                "stdout": completed.stdout.strip(),
                "stderr": completed.stderr.strip(),
                "command": command,
            },
        }
        TIER_RESULT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        sys.stderr.write(completed.stderr)
        return completed.returncode

    data = json.loads(RAW_RESULT.read_text(encoding="utf-8"))
    results_block = (data.get("results") or {})
    metrics = ((results_block.get("prompts") or [{}])[0].get("metrics") or {})
    eval_results = results_block.get("results") or []
    pass_count = int(metrics.get("testPassCount") or 0)
    fail_count = int(metrics.get("testFailCount") or 0)
    error_count = int(metrics.get("testErrorCount") or 0)
    total = pass_count + fail_count + error_count
    pass_rate = 0.0 if total == 0 else (pass_count / total) * 100.0
    verify_quality = compute_verify_quality(eval_results)
    coverage_ok = (
        len([r for r in eval_results if ((r.get("testCase") or {}).get("metadata") or {}).get("mode") == "coach"]) >= 2
        and len([r for r in eval_results if ((r.get("testCase") or {}).get("metadata") or {}).get("mode") == "verify"]) >= 3
    )
    raw_score = (pass_rate * 0.4) + (verify_quality * 0.6)
    score = round(min(raw_score, 89.0) if not coverage_ok else raw_score, 2)

    payload = {
        "tier": 1,
        "score": score,
        "pass": fail_count == 0 and error_count == 0 and total > 0,
        "details": {
            "pass_count": pass_count,
            "fail_count": fail_count,
            "error_count": error_count,
            "total_tests": total,
            "pass_rate": round(pass_rate, 2),
            "verify_quality": verify_quality,
            "coverage_ok": coverage_ok,
            "eval_id": data.get("evalId"),
            "raw_output": str(RAW_RESULT.relative_to(ROOT)),
        },
    }
    TIER_RESULT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Tier 1 score: {score:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
