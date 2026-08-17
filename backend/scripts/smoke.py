"""End-to-end smoke test against a running server.

Exercises the documented workflow over real HTTP - connect, calculate, get
rejected, get approved, run the ladder, inspect the journal - and prints a
readable transcript.  Intended for a server started with
``TC_MT5_GATEWAY=mock``; it will refuse to run against a live gateway because it
places real orders.

    python -m uvicorn app.main:app --port 8000        # in one terminal
    python scripts/smoke.py                           # in another
"""

from __future__ import annotations

import argparse
import secrets
import sys
import time
from typing import Any

import httpx

PASS = "PASS"
FAIL = "FAIL"

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = PASS if condition else FAIL
    print(f"  [{status}] {label}" + (f" - {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def section(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument(
        "--login",
        type=int,
        default=0,
        help=(
            "Simulated account number. Defaults to a fresh random one, because the "
            "simulator keeps state per login and Rule 1 would (correctly) block a "
            "re-entry on a symbol left open by a previous run."
        ),
    )
    parser.add_argument("--server", default="MockBroker-Demo")
    args = parser.parse_args()

    if args.login <= 0:
        args.login = secrets.randbelow(89_999) + 910_000

    client = httpx.Client(base_url=args.base, timeout=30.0)

    section("Health")
    health = client.get("/api/health").json()
    print(f"  {health['app']} v{health['version']} env={health['environment']}")
    check("API is reachable", health.get("status") == "ok")
    check("position monitor is running", health.get("monitor_running") is True)

    if health.get("mt5_gateway") != "mock":
        print(
            "\nRefusing to continue: the server is using the live MT5 gateway and this script "
            "places orders. Restart it with TC_MT5_GATEWAY=mock."
        )
        return 2

    # ---------------------------------------------------------------- account
    section("Registration and MT5 connection")
    email = f"smoke-{secrets.token_hex(4)}@example.com"
    token = client.post(
        "/api/auth/register",
        json={"email": email, "password": "smoke-test-password", "display_name": "Smoke"},
    ).json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    print(f"  registered {email}")

    connected = client.post(
        "/api/mt5/accounts",
        json={
            "login": args.login,
            "password": "simulated-password",
            "server": args.server,
            "label": "Smoke demo",
        },
    )
    check("MT5 account connected", connected.status_code == 201, connected.text[:120])
    state = connected.json()
    capital = state["capital"]
    currency = state["snapshot"]["currency"]
    print(f"  balance {state['snapshot']['balance']:,.2f} {currency}, capital {capital:,.2f}")

    bad = client.post(
        "/api/mt5/accounts",
        json={"login": 1, "password": "x", "server": args.server},
    )
    check("bad credentials are refused", bad.status_code >= 400, f"status {bad.status_code}")

    # ------------------------------------------------------------- calculator
    section("Pre-entry calculation")
    preview = client.post(
        "/api/calculator/preview",
        json={"symbol": args.symbol, "side": "buy", "stop_points": 500},
    )
    check("preview succeeded", preview.status_code == 200, preview.text[:160])
    plan = preview.json()["plan"]
    rules = preview.json()["rules"]

    expected_lots = round(capital / 1000 * 0.02, 2)
    print(
        f"  entry {plan['entry_price']} stop {plan['stop_loss']} "
        f"volume {plan['volume']} risk {plan['max_loss']:,.2f} {currency} "
        f"({plan['risk_pct_of_capital']:.2f}%)"
    )
    for stage in plan["stages"]:
        print(
            f"    {stage['key']}: {stage['target_price']} x{stage['volume']} "
            f"-> {stage['money_profit']:,.2f} (stop after {stage['sl_after']}, "
            f"worst case {stage['locked_in_money']:,.2f})"
        )

    check("Rule 2 lot allocation", abs(plan["volume"] - expected_lots) < 1e-9,
          f"{plan['volume']} vs {expected_lots}")
    check("risk within the 2% ceiling", plan["max_loss"] <= plan["max_risk_money"])
    check("three targets planned", len(plan["stages"]) == 3)
    check("targets are 1R/2R/3R", [s["r_multiple"] for s in plan["stages"]] == [1.0, 2.0, 3.0])
    check("all rules pass", rules["approved"] is True, rules["summary"])

    # ------------------------------------------------------------ rejection
    section("Rule 3 rejection")
    rejected = client.post(
        "/api/trades", json={"symbol": args.symbol, "side": "buy", "stop_points": 2500}
    ).json()
    check("wide stop is rejected", rejected["approved"] is False)
    check("nothing was executed", rejected["executed"] is False)
    check("RULE3_MAX_RISK fired", "RULE3_MAX_RISK" in rejected["rules"]["violations"],
          str(rejected["rules"]["violations"]))
    print(f"  {rejected['message'][:200]}")

    # ------------------------------------------------------------- execution
    section("Execution")
    submitted = client.post(
        "/api/trades",
        json={"symbol": args.symbol, "side": "buy", "stop_points": 500, "comment": "smoke"},
    ).json()
    check("trade approved", submitted["approved"] is True, submitted["message"])
    check("trade executed", submitted["executed"] is True, submitted["message"])
    trade = submitted["trade"]
    if trade is None:
        print("  no trade returned; aborting")
        return 1
    trade_id = trade["id"]
    print(
        f"  trade #{trade_id} position #{trade['position_ticket']} "
        f"{trade['initial_volume']} lots at {trade['entry_price']}"
    )

    duplicate = client.post(
        "/api/trades", json={"symbol": args.symbol, "side": "buy", "stop_points": 500}
    ).json()
    check("Rule 1 blocks a second entry",
          "RULE1_ONE_ACTIVE_TRADE" in duplicate["rules"]["violations"],
          str(duplicate["rules"]["violations"]))

    other = client.post(
        "/api/trades", json={"symbol": "XAUUSD", "side": "buy", "stop_points": 800}
    ).json()
    check("a different derivative is still tradable", other["executed"] is True, other["message"])

    positions = client.get("/api/positions").json()
    check("positions are linked to managed trades",
          all(row["managed"] for row in positions["rows"]),
          f"{len(positions['rows'])} rows")
    print(f"  risk on: {positions['risk_on']:,.2f} {currency}")

    # ----------------------------------------------------------- the ladder
    section("Ladder execution (driving the simulated market)")
    stages = {s["stage_key"]: s for s in trade["stages"]}
    tp1 = stages["TP1"]["target_price"]
    tp2 = stages["TP2"]["target_price"]

    move_price(client, args.symbol, tp1)
    detail = wait_for(client, trade_id, lambda t: t["status"] == "scaling")
    print(f"  after TP1: status={detail['status']} remaining={detail['remaining_volume']} "
          f"stop={detail['current_stop']} realised={detail['realised_pl']:,.2f}")
    check("TP1 closed half the position",
          abs(detail["remaining_volume"] - trade["initial_volume"] / 2) < 1e-6,
          str(detail["remaining_volume"]))
    check("stop tightened after TP1", detail["current_stop"] > trade["initial_stop"],
          f"{trade['initial_stop']} -> {detail['current_stop']}")
    tp1_stage = next(s for s in detail["stages"] if s["stage_key"] == "TP1")
    check("TP1 recorded as filled", tp1_stage["status"] == "filled")

    move_price(client, args.symbol, tp2)
    detail = wait_for(client, trade_id, lambda t: t["status"] == "closed")
    print(f"  after TP2: status={detail['status']} reason={detail['close_reason']} "
          f"realised={detail['realised_pl']:,.2f} planned={detail['planned_profit']:,.2f}")
    check("position fully closed", detail["remaining_volume"] == 0)
    check("realised matches the plan",
          abs(detail["realised_pl"] - detail["planned_profit"]) < 1.0,
          f"{detail['realised_pl']} vs {detail['planned_profit']}")

    reentry = client.post(
        "/api/trades", json={"symbol": args.symbol, "side": "buy", "stop_points": 500}
    ).json()
    check("Rule 1 lock released after closing", reentry["approved"] is True, reentry["message"])
    if reentry.get("trade"):
        client.post(f"/api/trades/{reentry['trade']['id']}/close", json={})

    # -------------------------------------------------------------- journal
    section("Journal")
    events = client.get("/api/journal/events", params={"trade_id": trade_id}).json()
    kinds = {e["event_type"] for e in events}
    print(f"  {len(events)} events: {', '.join(sorted(kinds))}")
    for expected in ("validated", "order_filled", "partial_close", "sl_modified", "position_closed"):
        check(f"event '{expected}' logged", expected in kinds)

    decisions = client.get("/api/journal/decisions").json()
    check("rejections are journalled", any(not d["approved"] for d in decisions))
    performance = client.get("/api/journal/performance").json()
    print(
        f"  closed={performance['closed_trades']} net={performance['net_pl']:,.2f} "
        f"approved={performance['decisions_approved']} blocked={performance['decisions_rejected']} "
        f"adherence={performance['rule_adherence_pct']:.0f}%"
    )
    check("performance summary reflects the run", performance["closed_trades"] >= 1)

    # ---------------------------------------------------------------- result
    section("Result")
    if failures:
        print(f"  {len(failures)} check(s) failed:")
        for failure in failures:
            print(f"    - {failure}")
        return 1
    print("  All checks passed.")
    return 0


def move_price(client: httpx.Client, symbol: str, bid: float) -> None:
    """Drive the simulated market through the development-only endpoint."""
    response = client.post("/api/dev/mock/price", json={"symbol": symbol, "bid": bid})
    if response.status_code != 200:
        raise SystemExit(
            f"Could not move the simulated price ({response.status_code}): {response.text[:200]}\n"
            "The /api/dev/mock endpoints require TC_MT5_GATEWAY=mock and TC_ENV != production."
        )


def wait_for(client: httpx.Client, trade_id: int, predicate, timeout: float = 12.0) -> Any:
    """Poll a trade until ``predicate`` holds or the timeout expires."""
    deadline = time.monotonic() + timeout
    detail: Any = None
    while time.monotonic() < deadline:
        detail = client.get(f"/api/trades/{trade_id}").json()
        if predicate(detail):
            return detail
        time.sleep(0.4)
    return detail


if __name__ == "__main__":
    sys.exit(main())
