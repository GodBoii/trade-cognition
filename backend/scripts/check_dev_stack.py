"""Verify the development stack is wired correctly.

Confirms that the Next.js server renders, that its routes resolve, and that
`/api` is proxied through to the Python backend - i.e. that a developer running
`npm run dev:all` gets a working system.

    python backend/scripts/check_dev_stack.py
    python backend/scripts/check_dev_stack.py --web http://localhost:3000
"""

from __future__ import annotations

import argparse
import sys

import httpx

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web", default="http://localhost:3000", help="Next.js server")
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="FastAPI server")
    args = parser.parse_args()

    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        print("Next.js server")
        try:
            login = client.get(f"{args.web}/login")
            check(
                "renders the sign-in page",
                login.status_code == 200 and "Trade Cognition" in login.text,
                f"status {login.status_code}",
            )

            for route in ("/", "/trade", "/trades", "/rules", "/journal", "/accounts"):
                response = client.get(f"{args.web}{route}")
                check(f"route {route} resolves", response.status_code == 200,
                      f"status {response.status_code}")

            dynamic = client.get(f"{args.web}/trades/1")
            check("dynamic route /trades/[id] resolves", dynamic.status_code == 200,
                  f"status {dynamic.status_code}")

            missing = client.get(f"{args.web}/no-such-page")
            check("unknown paths return 404", missing.status_code == 404,
                  f"status {missing.status_code}")

            robots = client.get(f"{args.web}/robots.txt")
            check(
                "robots.txt keeps the console out of search engines",
                robots.status_code == 200 and "Disallow: /" in robots.text,
            )
        except httpx.HTTPError as exc:
            check("web server reachable", False, str(exc))

        print("API through the Next proxy")
        try:
            proxied = client.get(f"{args.web}/api/health")
            ok = proxied.status_code == 200
            check("/api is proxied to the backend", ok, f"status {proxied.status_code}")
            if ok:
                body = proxied.json()
                print(
                    f"    {body.get('app')} v{body.get('version')} "
                    f"env={body.get('environment')} gateway={body.get('mt5_gateway')} "
                    f"monitor={body.get('monitor_running')}"
                )
                if body.get("mt5_gateway") == "mock":
                    print("    note: simulated broker - no real orders are placed")
        except httpx.HTTPError as exc:
            check("/api is proxied to the backend", False, str(exc))

        print("API directly")
        try:
            direct = client.get(f"{args.api}/api/health")
            check("backend responds on its own port", direct.status_code == 200)
            docs = client.get(f"{args.api}/docs")
            check("OpenAPI docs are served", docs.status_code == 200)
        except httpx.HTTPError as exc:
            check("backend responds on its own port", False, str(exc))

    if failures:
        print(f"\n{len(failures)} check(s) failed:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
