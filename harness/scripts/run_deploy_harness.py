from __future__ import annotations

import json
import os
import py_compile
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.main import app
from harness.scripts._server_utils import ensure_harness_credentials, start_server, stop_server

RESULTS = ROOT / "harness" / "results"
TIER_RESULT = RESULTS / "tier-3.json"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
if not PYTHON.exists():
    PYTHON = Path(sys.executable)


def compile_python() -> tuple[bool, str]:
    failures: list[str] = []
    for folder in ("backend", "tests", "scripts", "harness"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            try:
                py_compile.compile(str(path), doraise=True)
            except py_compile.PyCompileError as exc:
                failures.append(f"{path.relative_to(ROOT)}: {exc.msg}")
    return (len(failures) == 0, "\n".join(failures))


def run_pytest() -> tuple[bool, str]:
    completed = subprocess.run(
        [str(PYTHON), "-m", "pytest", "-q"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    output = "\n".join(part for part in (completed.stdout.strip(), completed.stderr.strip()) if part)
    return (completed.returncode == 0, output)


def smoke_server() -> tuple[bool, str]:
    process = None
    try:
        process = start_server(8002, RESULTS / "tier-3.server.out.log", RESULTS / "tier-3.server.err.log")
        urls = [
            "http://127.0.0.1:8002/",
            "http://127.0.0.1:8002/api",
            "http://127.0.0.1:8002/api/regions",
            "http://127.0.0.1:8002/policy-lab",
        ]
        statuses = []
        for url in urls:
            with urlopen(url, timeout=20) as response:
                statuses.append(f"{url} -> {response.status}")
                if response.status >= 400:
                    return False, "\n".join(statuses)
        return True, "\n".join(statuses)
    except Exception as exc:
        return False, str(exc)
    finally:
        stop_server(process)


def authenticated_api_smoke() -> tuple[bool, str]:
    login_email, login_password = ensure_harness_credentials()

    try:
        with TestClient(app) as client:
            login_response = client.post("/api/auth/login", json={"email": login_email, "password": login_password, "next": "/"})
            if login_response.status_code != 200:
                return False, f"login failed: {login_response.status_code}"

            me_response = client.get("/api/auth/me")
            if me_response.status_code != 200:
                return False, f"/api/auth/me unexpected status: {me_response.status_code}"
            me_payload = me_response.json()
            if not me_payload.get("id"):
                return False, "authenticated smoke missing user id"
            if (me_payload.get("email") or "").lower() != login_email.lower():
                return False, "authenticated smoke returned unexpected user identity"

            verify_response = client.post(
                "/api/pledge/verify",
                json=
                {
                    "text": "강남 교통을 바꾸겠습니다.",
                    "phase": "quick",
                    "top_k_platform": 4,
                    "top_k_pledge": 4,
                    "top_k_regional": 5,
                },
            )
            if verify_response.status_code != 200:
                return False, f"/api/pledge/verify unexpected status: {verify_response.status_code}"
            payload = verify_response.json()
            if "total_score" not in payload or "signal_light" not in payload:
                return False, "verify smoke missing normalized fields"

        return True, "login -> /api/auth/me -> /api/pledge/verify passed"
    except Exception as exc:
        return False, str(exc)


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)

    compile_ok, compile_output = compile_python()
    pytest_ok, pytest_output = run_pytest()
    smoke_ok, smoke_output = smoke_server()
    auth_smoke_ok, auth_smoke_output = authenticated_api_smoke()

    checks = [
        {"name": "py_compile", "pass": compile_ok, "details": compile_output},
        {"name": "pytest -q", "pass": pytest_ok, "details": pytest_output},
        {"name": "startup smoke", "pass": smoke_ok, "details": smoke_output},
        {"name": "authenticated api smoke", "pass": auth_smoke_ok, "details": auth_smoke_output},
    ]
    passed = sum(1 for check in checks if check["pass"])
    score = round((passed / len(checks)) * 100, 2)

    payload = {
        "tier": 3,
        "score": score,
        "pass": passed == len(checks),
        "details": {
            "checks": checks,
            "pass_count": passed,
            "fail_count": len(checks) - passed,
        },
    }
    TIER_RESULT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Tier 3 score: {score:.2f}")
    return 0 if payload["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
