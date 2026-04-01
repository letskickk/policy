from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.scripts._server_utils import ensure_harness_credentials, start_server, stop_server

RESULTS = ROOT / "harness" / "results"
TIER_RESULT = RESULTS / "tier-2.json"
BASE_URL = "http://127.0.0.1:8001"

VIEWPORTS = {
    "desktop": {"width": 1440, "height": 960, "is_mobile": False},
    "mobile": {"width": 390, "height": 844, "is_mobile": True},
}

PUBLIC_ROUTES = [
    {"path": "/", "label": "home", "needs_auth": False, "expect_login": False},
    {"path": "/login", "label": "login", "needs_auth": False, "expect_login": True},
    {"path": "/signup", "label": "signup", "needs_auth": False, "expect_login": False},
    {"path": "/hub", "label": "hub", "needs_auth": False, "expect_login": False},
]

PROTECTED_ROUTES = [
    {"path": "/pledge", "label": "pledge", "needs_auth": True, "expect_login": False},
    {"path": "/policy-lab", "label": "policy-lab", "needs_auth": True, "expect_login": False},
    {"path": "/tools", "label": "tools", "needs_auth": True, "expect_login": False},
    {"path": "/dashboard", "label": "dashboard", "needs_auth": True, "expect_login": False},
    {"path": "/admin", "label": "admin-home", "needs_auth": True, "expect_login": False},
    {"path": "/admin/users", "label": "admin-users", "needs_auth": True, "expect_login": False},
]

BROKEN_TEXT_MARKERS = ("undefined", "nan", "[object object]")


def extract_cookie_header() -> str:
    login_email, login_password = ensure_harness_credentials()
    request = Request(
        f"{BASE_URL}/api/auth/login",
        data=json.dumps({"email": login_email, "password": login_password, "next": "/"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        raw = response.headers.get("Set-Cookie") or ""
    for part in raw.split(","):
        part = part.strip()
        if part.startswith("policy_auth="):
            return part.split(";", 1)[0]
    raise RuntimeError("login succeeded but policy_auth cookie was not returned")


def build_cookie(cookie_header: str) -> dict[str, object]:
    name, value = cookie_header.split("=", 1)
    return {
        "name": name,
        "value": value,
        "domain": "127.0.0.1",
        "path": "/",
        "httpOnly": True,
        "sameSite": "Lax",
    }


def audit_page(page, route: dict, viewport_name: str, screenshots_dir: Path) -> dict:
    console_errors: list[str] = []
    request_failures: list[str] = []

    page.on(
        "console",
        lambda msg: console_errors.append(msg.text)
        if msg.type == "error" and "401 (Unauthorized)" not in msg.text
        else None,
    )
    page.on("requestfailed", lambda req: request_failures.append(f"{req.method} {req.url}"))

    url = f"{BASE_URL}{route['path']}"
    response = page.goto(url, wait_until="networkidle", timeout=45000)

    issues: list[str] = []
    if not response or not response.ok:
        issues.append(f"bad status for {route['path']}: {response.status if response else 'no response'}")

    title = page.title().strip()
    lang = page.evaluate("() => document.documentElement?.getAttribute('lang') || ''") or ""
    body_text = page.locator("body").inner_text().strip()
    normalized_body = " ".join(body_text.split()).lower()
    overflow = page.evaluate("() => Math.max(0, document.documentElement.scrollWidth - window.innerWidth)")
    images_missing_alt = page.locator("img:not([alt])").count()
    headings = page.locator("h1, h2, main").count()
    links = page.locator("a, button").count()
    password_fields = page.locator("input[type='password']").count()
    text_inputs = page.locator("textarea, input[type='text'], input[type='email'], input:not([type])").count()

    if not lang:
        issues.append("missing html lang")
    if not title:
        issues.append("missing title")
    if len(body_text) < 120:
        issues.append("body content too thin")
    if headings == 0:
        issues.append("missing visible heading/main landmark")
    if overflow > 4:
        issues.append(f"horizontal overflow {overflow}px")
    if images_missing_alt > 0:
        issues.append(f"images missing alt: {images_missing_alt}")
    if links == 0:
        issues.append("missing interactive elements")
    for marker in BROKEN_TEXT_MARKERS:
        if marker in normalized_body:
            issues.append(f"broken text marker visible: {marker}")
    if "null" in normalized_body and "null hypothesis" not in normalized_body:
        issues.append("raw null string visible")

    is_login_like = ("로그인" in title) or password_fields > 0
    if route["expect_login"]:
        if not is_login_like:
            issues.append("expected login experience but login UI not detected")
    elif route["needs_auth"] and is_login_like:
        issues.append("protected page rendered login UI even after authenticated session")

    if route["label"] in {"login", "signup"} and text_inputs < 2:
        issues.append("auth page missing expected form controls")
    if route["label"] in {"pledge", "policy-lab", "tools", "dashboard"} and text_inputs + links < 3:
        issues.append("workspace page looks underpowered")
    if route["label"].startswith("admin") and "admin" not in title.lower() and "관리" not in body_text:
        issues.append("admin page does not look like admin surface")

    screenshot_path = screenshots_dir / f"{viewport_name}-{route['label']}.png"
    page.screenshot(path=str(screenshot_path), full_page=True)
    return {
        "page": route["path"],
        "label": route["label"],
        "viewport": viewport_name,
        "issues": issues,
        "title": title,
        "lang": lang,
        "overflow_px": overflow,
        "console_errors": console_errors[-10:],
        "request_failures": request_failures[-10:],
        "body_length": len(body_text),
        "screenshot": str(screenshot_path.relative_to(ROOT)),
    }


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    screenshots_dir = RESULTS / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    process = None
    audits: list[dict] = []
    checks: list[bool] = []

    try:
        process = start_server(8001, RESULTS / "tier-2.server.out.log", RESULTS / "tier-2.server.err.log")
        auth_cookie = build_cookie(extract_cookie_header())

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for viewport_name, viewport in VIEWPORTS.items():
                public_context = browser.new_context(
                    viewport={"width": viewport["width"], "height": viewport["height"]},
                    is_mobile=viewport["is_mobile"],
                )
                for route in PUBLIC_ROUTES:
                    page = public_context.new_page()
                    audit = audit_page(page, route, viewport_name, screenshots_dir)
                    audits.append(audit)
                    checks.append(
                        len(audit["issues"]) == 0
                        and len(audit["console_errors"]) == 0
                        and len(audit["request_failures"]) == 0
                    )
                    page.close()
                public_context.close()

                auth_context = browser.new_context(
                    viewport={"width": viewport["width"], "height": viewport["height"]},
                    is_mobile=viewport["is_mobile"],
                )
                auth_context.add_cookies([auth_cookie])
                for route in PROTECTED_ROUTES:
                    page = auth_context.new_page()
                    audit = audit_page(page, route, viewport_name, screenshots_dir)
                    audits.append(audit)
                    checks.append(
                        len(audit["issues"]) == 0
                        and len(audit["console_errors"]) == 0
                        and len(audit["request_failures"]) == 0
                    )
                    page.close()
                auth_context.close()
            browser.close()
    except Exception as exc:
        payload = {
            "tier": 2,
            "score": 0.0,
            "pass": False,
            "details": {"issue": str(exc), "audits": audits},
        }
        TIER_RESULT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 1
    finally:
        stop_server(process)

    total_checks = len(checks)
    passed = sum(1 for check in checks if check)
    score = round((passed / total_checks) * 100, 2) if total_checks else 0.0
    payload = {
        "tier": 2,
        "score": score,
        "pass": passed == total_checks and total_checks > 0,
        "details": {
            "total_checks": total_checks,
            "passed_checks": passed,
            "failed_checks": total_checks - passed,
            "audits": audits,
        },
    }
    TIER_RESULT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Tier 2 score: {score:.2f}")
    return 0 if payload["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
