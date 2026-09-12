#!/usr/bin/env python3
"""Runtime smoke test for the browser-auth contract.

Usage:
  MASYG_SMOKE_EMAIL='user@example.com' \\
  MASYG_SMOKE_PASSWORD='...' \\
  python scripts/smoke_auth_runtime.py

The script never prints credentials or JWT/cookie values.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

import requests


BASE_URL = (os.getenv("MASYG_SMOKE_BASE_URL") or "http://127.0.0.1:5000").rstrip("/")
EMAIL = (os.getenv("MASYG_SMOKE_EMAIL") or "").strip()
PASSWORD = os.getenv("MASYG_SMOKE_PASSWORD") or ""
TIMEOUT = float(os.getenv("MASYG_SMOKE_TIMEOUT", "10"))
ORIGIN = (os.getenv("MASYG_SMOKE_ORIGIN") or "http://localhost:4000").rstrip("/")


@dataclass
class Check:
    name: str
    expected: int
    actual: int

    @property
    def ok(self) -> bool:
        return self.actual == self.expected


def request(session: requests.Session, method: str, path: str, **kwargs) -> requests.Response:
    return session.request(method, f"{BASE_URL}{path}", timeout=TIMEOUT, **kwargs)


def main() -> int:
    if not EMAIL or not PASSWORD:
        print("Set MASYG_SMOKE_EMAIL and MASYG_SMOKE_PASSWORD before running this smoke test.", file=sys.stderr)
        return 2

    checks: list[Check] = []
    browser = requests.Session()

    health = request(browser, "GET", "/health")
    checks.append(Check("health", 200, health.status_code))

    preflight = request(
        browser,
        "OPTIONS",
        "/api/user/login",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    checks.append(Check("credentialed CORS preflight", 200, preflight.status_code))
    cors_header_ok = preflight.headers.get("access-control-allow-origin") == ORIGIN
    if not cors_header_ok:
        print(
            "FAIL CORS allow-origin header: "
            f"expected={ORIGIN!r} actual={preflight.headers.get('access-control-allow-origin')!r}",
            file=sys.stderr,
        )
        return report(checks) or 1

    login = request(browser, "POST", "/api/user/login", json={"email": EMAIL, "password": PASSWORD})
    checks.append(Check("login", 200, login.status_code))
    if login.status_code != 200:
        return report(checks)

    current = request(browser, "GET", "/api/user/current")
    checks.append(Check("current user", 200, current.status_code))

    old_refresh = browser.cookies.get("refresh_token")
    if not old_refresh:
        print("FAIL refresh cookie missing after successful login", file=sys.stderr)
        return 1

    refresh = request(browser, "POST", "/api/user/refresh-token")
    checks.append(Check("refresh rotation", 200, refresh.status_code))

    # Replay exactly the pre-rotation credential in an independent client.
    replay = requests.Session()
    replay.cookies.set("refresh_token", old_refresh)
    replay_response = request(replay, "POST", "/api/user/refresh-token")
    checks.append(Check("old refresh replay rejected", 401, replay_response.status_code))

    logout = request(browser, "POST", "/api/user/logout")
    checks.append(Check("logout", 200, logout.status_code))

    after_logout = request(browser, "GET", "/api/user/current")
    checks.append(Check("current rejected after logout", 401, after_logout.status_code))

    after_logout_refresh = request(browser, "POST", "/api/user/refresh-token")
    checks.append(Check("refresh rejected after logout", 401, after_logout_refresh.status_code))

    return report(checks)


def report(checks: list[Check]) -> int:
    for check in checks:
        state = "PASS" if check.ok else "FAIL"
        print(f"{state:4} {check.name}: expected={check.expected} actual={check.actual}")
    failed = [check for check in checks if not check.ok]
    print(f"summary: {len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
