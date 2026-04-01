from __future__ import annotations

import json
import os
import py_compile
import subprocess
import sys
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.scripts._server_utils import start_server, stop_server

RESULTS = ROOT / "harness" / "results"
TIER_RESULT = RESULTS / "tier-3.json"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
if not PYTHON.exists():
    PYTHON = Path(sys.executable)
LOGIN_EMAIL = os.environ.get("T1_LOGIN_EMAIL", "")
LOGIN_PASSWORD = os.environ.get("T1_LOGIN_PASSWORD", "")


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
    if not LOGIN_EMAIL or not LOGIN_PASSWORD:
        return False, "missing T1 login credentials for authenticated smoke"

    process = None
    try:
        process = start_server(8003, RESULTS / "tier-3.auth.server.out.log", RESULTS / "tier-3.auth.server.err.log")
        login_request = Request(
            "http://127.0.0.1:8003/api/auth/login",
            data=json.dumps({"email": LOGIN_EMAIL, "password": LOGIN_PASSWORD, "next": "/"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(login_request, timeout=30) as response:
            if response.status >= 400:
                return False, f"login failed: {response.status}"
            raw_cookie = response.headers.get("Set-Cookie") or ""
        cookie_header = ""
        for part in raw_cookie.split(","):
            part = part.strip()
            if part.startswith("policy_auth="):
                cookie_header = part.split(";", 1)[0]
                break
        if not cookie_header:
            return False, "login response did not include policy_auth cookie"

        me_request = Request("http://127.0.0.1:8003/api/auth/me", headers={"Cookie": cookie_header}, method="GET")
        with urlopen(me_request, timeout=30) as response:
            if response.status != 200:
                return False, f"/api/auth/me unexpected status: {response.status}"
            me_payload = json.loads(response.read().decode("utf-8"))
            if not me_payload.get("id"):
                return False, "authenticated smoke missing user id"
            if (me_payload.get("email") or "").lower() != LOGIN_EMAIL.lower():
                return False, "authenticated smoke returned unexpected user identity"

        verify_request = Request(
            "http://127.0.0.1:8003/api/pledge/verify",
            data=json.dumps(
                {
                    "text": "강남 교통을 바꾸겠습니다.",
                    "phase": "quick",
                    "top_k_platform": 4,
                    "top_k_pledge": 4,
                    "top_k_regional": 5,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json", "Cookie": cookie_header},
            method="POST",
        )
        with urlopen(verify_request, timeout=240) as response:
            if response.status != 200:
                return False, f"/api/pledge/verify unexpected status: {response.status}"
            payload = json.loads(response.read().decode("utf-8"))
            if "total_score" not in payload or "signal_light" not in payload:
                return False, "verify smoke missing normalized fields"

        return True, "login -> /api/auth/me -> /api/pledge/verify passed"
    except Exception as exc:
        return False, str(exc)
    finally:
        stop_server(process)


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
