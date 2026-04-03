from __future__ import annotations

import argparse
import glob
import json
import math
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "harness"
RESULTS = HARNESS / "results"
RESULTS_MD = HARNESS / "RESULTS.md"
PLANS = HARNESS / "plans"
SCHEMA = HARNESS / "schemas" / "planner.schema.json"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
if not PYTHON.exists():
    PYTHON = Path(sys.executable)

TIER_THRESHOLD = 90.0
OVERALL_THRESHOLD = 92.0
TIER_WEIGHTS = {1: 1 / 3, 2: 1 / 3, 3: 1 / 3}

TIERS = [
    ("Prompt Quality", Path("harness/scripts/run_prompt_harness.py"), RESULTS / "tier-1.json"),
    ("UX Quality", Path("harness/scripts/run_ux_harness.py"), RESULTS / "tier-2.json"),
    ("Deploy Safety", Path("harness/scripts/run_deploy_harness.py"), RESULTS / "tier-3.json"),
]
BREAKTHROUGH_FAILURE_WINDOW = 3
BREAKTHROUGH_MIN_IMPROVEMENT = 1.0
FORBIDDEN_GENERATOR_PATTERNS = (
    r"harness/scripts/run_all_harness\.py",
    r"harness/scripts/run_prompt_harness\.py",
    r"harness/scripts/run_ux_harness\.py",
    r"harness/scripts/run_deploy_harness\.py",
    r"tier1-prompt/runs",
    r"tier2-ux/runs",
    r"tier3-deploy/runs",
)
ARCHITECTURE_PATTERNS = {
    "pipeline": "Sequential evaluator -> planner -> generator -> evaluator loop for tightly-coupled fixes.",
    "fan_out_fan_in": "Split tier investigation in parallel, merge the evidence, then execute one focused repair round.",
    "producer_reviewer": "One step proposes edits and a review step verifies scope, allowed paths, and regression risk.",
    "supervisor": "Central orchestrator chooses the next move from cross-tier evidence, especially during stagnation.",
}
LEGACY_PATH_MAP = {
    "harness/tier1-prompt/runs/": "harness/results/tier-1.json",
    "harness/tier2-ux/runs/": "harness/results/tier-2.json",
    "harness/tier3-deploy/runs/": "harness/results/tier-3.json",
}


def run_python(script: Path) -> int:
    return subprocess.run([str(PYTHON), str(ROOT / script)], cwd=ROOT).returncode


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def weighted_geometric_mean(tiers: list[dict]) -> float:
    total = 0.0
    for tier in tiers:
        weight = TIER_WEIGHTS.get(int(tier["tier"]), 0.0)
        value = max(float(tier["score"]), 0.01) / 100.0
        total += math.log(value) * weight
    return round(math.exp(total) * 100.0, 2)


def summarize_failure(data: dict) -> str:
    details = data.get("details") or {}
    if not isinstance(details, dict):
        return ""

    if details.get("issue"):
        return str(details["issue"])

    checks = details.get("checks")
    if isinstance(checks, list):
        failed = [check for check in checks if not check.get("pass")]
        if failed:
            return "; ".join(
                f"{check.get('name')}: {str(check.get('details') or '').strip()[:220]}" for check in failed
            )

    audits = details.get("audits")
    if isinstance(audits, list):
        failed = [audit for audit in audits if audit.get("issues")]
        if failed:
            sample = []
            for audit in failed[:3]:
                issues = ", ".join(audit.get("issues") or [])
                sample.append(f"{audit.get('file')}: {issues}")
            return "; ".join(sample)

    for key in ("stderr", "stdout"):
        text = str(details.get(key) or "").strip()
        if text:
            return text[:220]
    return ""


def artifact_manifest() -> dict:
    return {
        "results_markdown": "harness/RESULTS.md",
        "planner_artifacts": ["harness/plans/LATEST.json", "harness/plans/LATEST.md"],
        "generator_artifacts": ["harness/generator/round-XX.last.txt"],
        "tier_results": {
            "1": ["harness/results/tier-1.json", "harness/results/promptfoo-eval.json"],
            "2": ["harness/results/tier-2.json", "harness/results/screenshots/*.png"],
            "3": ["harness/results/tier-3.json", "harness/results/tier-3.server.out.log", "harness/results/tier-3.server.err.log"],
        },
        "editable_targets": {
            "1": [
                "prompts/당_부합_점검_시스템.txt",
                "prompts/당_부합_점검_유저.txt",
                "prompts/공약_챗봇_시스템.txt",
                "prompts/정책_생성_시스템.txt",
                "prompts/정책_생성_유저.txt",
                "backend/prompts.py",
                "backend/check_service.py",
                "backend/policy_drafter.py",
            ],
            "2": ["static/*.html", "static/admin/*.html"],
            "3": ["backend/*.py", "tests/*.py", "scripts/*.py", "harness/**/*.py"],
        },
    }


def choose_architecture_pattern(evaluation: dict, strategy_mode: str) -> str:
    failing_tiers = [tier for tier in evaluation.get("tiers", []) if not tier.get("pass")]
    if strategy_mode == "breakthrough":
        return "supervisor"
    if len(failing_tiers) > 1:
        return "fan_out_fan_in"
    if any(int(tier.get("tier") or 0) == 3 for tier in failing_tiers):
        return "producer_reviewer"
    return "pipeline"


def normalize_plan_path(path: str) -> str:
    return LEGACY_PATH_MAP.get(path, path).replace("\\", "/")


def path_exists_or_matches(path: str) -> bool:
    if any(char in path for char in "*?[]"):
        return len(glob.glob(str(ROOT / path), recursive=True)) > 0
    return (ROOT / path).exists()


def sanitize_generator_prompt(prompt: str, manifest: dict) -> str:
    sanitized = (prompt or "").strip()
    for pattern in FORBIDDEN_GENERATOR_PATTERNS:
        sanitized = re.sub(pattern, "[managed-by-orchestrator]", sanitized, flags=re.IGNORECASE)

    guidance = [
        "Read only the current harness artifacts listed below.",
        f"Results: {manifest['results_markdown']}",
        "Tier 1 artifacts: " + ", ".join(manifest["tier_results"]["1"]),
        "Tier 2 artifacts: " + ", ".join(manifest["tier_results"]["2"]),
        "Tier 3 artifacts: " + ", ".join(manifest["tier_results"]["3"]),
        "Edit only the files approved in planner priorities.",
        "Do not run any harness/scripts/*.py evaluator from inside the generator.",
    ]
    if sanitized:
        guidance.append("Planner-specific instructions: " + sanitized)
    return "\n".join(guidance)


def sanitize_plan(plan: dict, evaluation: dict, strategy_mode: str) -> dict:
    manifest = artifact_manifest()
    sanitized = {
        "strategy_mode": strategy_mode,
        "architecture_pattern": str(plan.get("architecture_pattern") or choose_architecture_pattern(evaluation, strategy_mode)),
        "round_goal": str(plan.get("round_goal") or "").strip() or "Raise the lowest-scoring tier with the smallest reliable repair set.",
        "priorities": [],
        "generator_prompt": "",
    }
    if sanitized["architecture_pattern"] not in ARCHITECTURE_PATTERNS:
        sanitized["architecture_pattern"] = choose_architecture_pattern(evaluation, strategy_mode)

    raw_priorities = plan.get("priorities")
    if not isinstance(raw_priorities, list):
        raw_priorities = []

    for item in raw_priorities[:3]:
        try:
            tier = int(item.get("tier"))
        except Exception:
            continue
        if tier < 1 or tier > 3:
            continue

        files: list[str] = []
        for raw_path in item.get("files") or []:
            normalized = normalize_plan_path(str(raw_path))
            if path_exists_or_matches(normalized):
                files.append(normalized)
        if not files:
            files = list(manifest["editable_targets"].get(str(tier), []))

        sanitized["priorities"].append(
            {
                "tier": tier,
                "title": str(item.get("title") or f"T{tier} repair"),
                "action": str(item.get("action") or "Inspect current evidence, patch the narrowest failing surface, and stop."),
                "files": files,
            }
        )

    if not sanitized["priorities"]:
        lowest_tier = min(evaluation.get("tiers", []), key=lambda tier: float(tier.get("score") or 0.0))
        tier_id = str(lowest_tier["tier"])
        sanitized["priorities"].append(
            {
                "tier": int(tier_id),
                "title": f"T{tier_id} highest-leverage repair",
                "action": "Inspect the current result artifacts, patch the direct failure source, and keep scope tight.",
                "files": list(manifest["editable_targets"][tier_id]),
            }
        )

    sanitized["generator_prompt"] = sanitize_generator_prompt(str(plan.get("generator_prompt") or ""), manifest)
    return sanitized


def evaluate_once() -> dict:
    tier_records = []
    for index, (name, script, result_path) in enumerate(TIERS, start=1):
        exit_code = run_python(script)
        if not result_path.exists():
            tier_records.append(
                {
                    "tier": index,
                    "name": name,
                    "score": 0.0,
                    "pass": False,
                    "summary": "result file missing",
                    "result_path": str(result_path.relative_to(ROOT)),
                }
            )
            continue

        data = read_json(result_path)
        tier_records.append(
            {
                "tier": index,
                "name": name,
                "score": float(data.get("score") or 0.0),
                "pass": bool(data.get("pass")) and exit_code == 0,
                "summary": summarize_failure(data),
                "result_path": str(result_path.relative_to(ROOT)),
            }
        )

    overall_score = weighted_geometric_mean(tier_records)
    overall_pass = (
        len(tier_records) == len(TIERS)
        and all(record["score"] >= TIER_THRESHOLD for record in tier_records)
        and overall_score >= OVERALL_THRESHOLD
    )
    return {"tiers": tier_records, "overall_score": overall_score, "pass": overall_pass}


def codex_executable() -> str | None:
    return shutil.which("codex.cmd") or shutil.which("codex")


def should_trigger_breakthrough(history: list[dict], evaluation: dict) -> bool:
    if evaluation.get("pass"):
        return False
    recent_failures = [record for record in history if not record.get("pass")]
    recent_failures.append(
        {
            "overall_score": evaluation.get("overall_score", 0.0),
            "tiers": evaluation.get("tiers", []),
        }
    )
    if len(recent_failures) < BREAKTHROUGH_FAILURE_WINDOW:
        return False
    window = recent_failures[-BREAKTHROUGH_FAILURE_WINDOW:]
    scores = [float(record.get("overall_score") or 0.0) for record in window]
    if max(scores) - min(scores) > BREAKTHROUGH_MIN_IMPROVEMENT:
        return False
    tier_signatures = {
        tuple((int(tier["tier"]), round(float(tier["score"]), 2), bool(tier["pass"])) for tier in record.get("tiers", []))
        for record in window
    }
    return len(tier_signatures) <= 2


def planner_prompt(round_number: int, evaluation: dict, strategy_mode: str) -> str:
    payload = json.dumps(evaluation, ensure_ascii=False, indent=2)
    manifest = json.dumps(artifact_manifest(), ensure_ascii=False, indent=2)
    strategy_block = (
        "Planning mode: BREAKTHROUGH.\n"
        f"The harness has stalled after {BREAKTHROUGH_FAILURE_WINDOW} failed rounds with less than "
        f"{BREAKTHROUGH_MIN_IMPROVEMENT:.1f} overall-point movement.\n"
        "Stop incremental patching and propose a breakthrough plan.\n"
        "A breakthrough plan may redesign the failing surface end-to-end, replace brittle prompts, "
        "rework routing or data flow, or restructure a subsystem so the repeated failure mode disappears.\n"
    ) if strategy_mode == "breakthrough" else (
        "Planning mode: NARROW.\n"
        "Use the smallest high-leverage repair set that can move the next evaluation.\n"
    )
    return f"""You are the planner in a harness loop for the repository at {ROOT}.

Loop contract:
1. Evaluator scores the repository.
2. Planner decides the next repair plan.
3. Generator edits the repository only from the planner plan.
4. Evaluator runs again.

Scoring rule:
- each tier must be >= {TIER_THRESHOLD:.0f}
- overall weighted geometric mean must be >= {OVERALL_THRESHOLD:.0f}

Current round: {round_number}
{strategy_block}
Evaluator payload:
{payload}

Repository artifact manifest:
{manifest}

Available orchestration patterns:
- pipeline: {ARCHITECTURE_PATTERNS['pipeline']}
- fan_out_fan_in: {ARCHITECTURE_PATTERNS['fan_out_fan_in']}
- producer_reviewer: {ARCHITECTURE_PATTERNS['producer_reviewer']}
- supervisor: {ARCHITECTURE_PATTERNS['supervisor']}

Produce a JSON object matching the provided schema.
Set `strategy_mode` to `{strategy_mode}`.
Select one `architecture_pattern` that fits the current failure shape.
Keep priorities focused on the highest-leverage fixes for the selected strategy.
Reference only real repository paths or valid globs from the artifact manifest.
Do not ask for human input.
Do not tell the generator to run this harness script or any recursive planner/generator/evaluator loop.
Do not tell the generator to run any `harness/scripts/*.py` evaluator script at all.
"""


def run_planner(round_number: int, evaluation: dict, strategy_mode: str) -> dict:
    codex = codex_executable()
    if not codex:
        raise RuntimeError("codex executable not found")

    PLANS.mkdir(parents=True, exist_ok=True)
    output_path = PLANS / f"round-{round_number:02d}.planner.json"
    command = [
        codex,
        "exec",
        "--ephemeral",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "-m",
        "gpt-5.4-mini",
        "-c",
        "reasoning_effort=\"low\"",
        "-C",
        str(ROOT),
        "--output-schema",
        str(SCHEMA),
        "-o",
        str(output_path),
        planner_prompt(round_number, evaluation, strategy_mode),
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "planner failed").strip())
    return sanitize_plan(read_json(output_path), evaluation, strategy_mode)


def generator_prompt(plan: dict) -> str:
    plan_json = json.dumps(plan, ensure_ascii=False, indent=2)
    strategy_mode = str(plan.get("strategy_mode") or "narrow")
    strategy_guidance = (
        "This is a BREAKTHROUGH round. If the approved plan calls for decisive redesign, do that instead of another tiny patch.\n"
    ) if strategy_mode == "breakthrough" else ""
    return f"""You are the generator in a harness loop for the repository at {ROOT}.

You must implement only the planner-approved work below.
Make code changes directly in the repository.
Do not broaden scope.
{strategy_guidance}Do not run `harness/scripts/run_all_harness.py` or any nested harness loop from inside the generator.
Do not run any `harness/scripts/*.py` harness script from inside the generator.
You may run narrow checks for the files you edit, but the outer orchestrator owns the post-edit evaluation.
After edits, stop. Do not run long explanations.

Planner output:
{plan_json}
"""


def run_generator(round_number: int, plan: dict) -> str:
    codex = codex_executable()
    if not codex:
        raise RuntimeError("codex executable not found")

    generator_dir = HARNESS / "generator"
    generator_dir.mkdir(parents=True, exist_ok=True)
    output_path = generator_dir / f"round-{round_number:02d}.last.txt"
    command = [
        codex,
        "exec",
        "--ephemeral",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "-m",
        "gpt-5.4-mini",
        "-c",
        "reasoning_effort=\"low\"",
        "-C",
        str(ROOT),
        "-o",
        str(output_path),
        generator_prompt(plan),
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=420,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "generator failed").strip())
    return output_path.read_text(encoding="utf-8", errors="replace").strip()


def write_planner_artifacts(round_number: int, plan: dict, evaluation: dict) -> None:
    PLANS.mkdir(parents=True, exist_ok=True)
    latest_json = PLANS / "LATEST.json"
    latest_md = PLANS / "LATEST.md"
    combined = {
        "round": round_number,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "loop": ["evaluator", "planner", "generator", "evaluator"],
        "evaluation": evaluation,
        "planner": plan,
        "artifact_manifest": artifact_manifest(),
    }
    serialized = json.dumps(combined, ensure_ascii=False, indent=2) + "\n"
    latest_json.write_text(serialized, encoding="utf-8")

    lines = [
        "# Planner Brief",
        "",
        f"- Round: {round_number}",
        f"- Overall score: {evaluation['overall_score']:.2f}",
        f"- Pass: {'yes' if evaluation['pass'] else 'no'}",
        f"- Loop: evaluator -> planner -> generator -> evaluator",
        f"- Strategy mode: {plan.get('strategy_mode', 'narrow')}",
        f"- Architecture pattern: {plan.get('architecture_pattern', 'pipeline')}",
        "",
        "## Priorities",
        "",
    ]
    for item in plan.get("priorities", []):
        lines.append(f"- T{item['tier']} {item['title']}")
        lines.append(f"  action: {item['action']}")
        lines.append(f"  files: {', '.join(item.get('files', [])) or 'none'}")
    lines.append("")
    lines.append("## Artifact Manifest")
    lines.append("")
    lines.append(f"- Results: {artifact_manifest()['results_markdown']}")
    for tier, items in artifact_manifest()["tier_results"].items():
        lines.append(f"- Tier {tier}: {', '.join(items)}")
    lines.append("")
    lines.append("## Generator Prompt")
    lines.append("")
    lines.append(plan.get("generator_prompt", ""))
    latest_md.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_results(history: list[dict]) -> None:
    lines = ["# Harness Results", ""]
    latest = history[-1]
    lines.append(f"- Latest round: {latest['round']}")
    lines.append(f"- Generated: {latest['timestamp']}")
    lines.append(f"- Consecutive passes: {latest['consecutive_passes']}")
    lines.append(f"- Total rounds completed: {len(history)}")
    lines.append("- Actual loop: evaluator -> planner -> generator -> evaluator")
    lines.append(f"- Thresholds: each tier >= {TIER_THRESHOLD:.0f}, overall weighted geometric mean >= {OVERALL_THRESHOLD:.0f}")
    lines.append(
        f"- Stagnation rule: if {BREAKTHROUGH_FAILURE_WINDOW} failed rounds stall within {BREAKTHROUGH_MIN_IMPROVEMENT:.1f} overall point, switch to breakthrough redesign"
    )
    lines.append(f"- Overall weighted geometric mean: {latest['overall_score']:.2f}")
    lines.append("")

    for record in reversed(history[-10:]):
        lines.append(f"## Round {record['round']}")
        lines.append("")
        lines.append(f"- Timestamp: {record['timestamp']}")
        lines.append(f"- Overall score: {record['overall_score']:.2f}")
        lines.append(f"- Passed gate: {'yes' if record['pass'] else 'no'}")
        for tier in record["tiers"]:
            lines.append(f"- T{tier['tier']}: {tier['name']} | score {tier['score']:.2f} | {'PASS' if tier['pass'] else 'FAIL'}")
            if tier["summary"]:
                lines.append(f"  summary: {tier['summary']}")
        if record.get("planner_status"):
            lines.append(f"- Planner: {record['planner_status']}")
        if record.get("strategy_mode"):
            lines.append(f"- Strategy mode: {record['strategy_mode']}")
        if record.get("architecture_pattern"):
            lines.append(f"- Architecture pattern: {record['architecture_pattern']}")
        if record.get("generator_status"):
            lines.append(f"- Generator: {record['generator_status']}")
        if record.get("post_generator_score") is not None:
            lines.append(f"- Post-generator overall score: {record['post_generator_score']:.2f}")
        lines.append("")

    RESULTS_MD.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=15)
    args = parser.parse_args()

    RESULTS.mkdir(parents=True, exist_ok=True)
    PLANS.mkdir(parents=True, exist_ok=True)
    history: list[dict] = []
    consecutive_passes = 0

    for round_number in range(1, args.rounds + 1):
        evaluation = evaluate_once()
        record = {
            "round": round_number,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "overall_score": evaluation["overall_score"],
            "pass": evaluation["pass"],
            "tiers": evaluation["tiers"],
            "planner_status": "",
            "generator_status": "",
            "post_generator_score": None,
            "strategy_mode": "narrow",
            "architecture_pattern": "pipeline",
        }
        consecutive_passes = consecutive_passes + 1 if evaluation["pass"] else 0
        record["consecutive_passes"] = consecutive_passes

        if not evaluation["pass"]:
            try:
                strategy_mode = "breakthrough" if should_trigger_breakthrough(history, evaluation) else "narrow"
                record["strategy_mode"] = strategy_mode
                plan = run_planner(round_number, evaluation, strategy_mode)
                record["architecture_pattern"] = str(plan.get("architecture_pattern") or choose_architecture_pattern(evaluation, strategy_mode))
                write_planner_artifacts(round_number, plan, evaluation)
                record["planner_status"] = "completed"
                generator_result = run_generator(round_number, plan)
                record["generator_status"] = generator_result[:240]
                post_evaluation = evaluate_once()
                record["post_generator_score"] = post_evaluation["overall_score"]
                record["overall_score"] = post_evaluation["overall_score"]
                record["pass"] = post_evaluation["pass"]
                record["tiers"] = post_evaluation["tiers"]
                consecutive_passes = consecutive_passes + 1 if post_evaluation["pass"] else 0
                record["consecutive_passes"] = consecutive_passes
            except Exception as exc:
                record["planner_status"] = f"failed: {exc}"
                history.append(record)
                write_results(history)
                return 1

        history.append(record)
        write_results(history)

        if consecutive_passes >= 3:
            break

    return 0 if history and history[-1]["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
