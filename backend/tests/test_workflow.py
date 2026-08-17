"""End-to-end workflow against the simulated broker.

Covers the whole path from the specification:

    connect -> select derivative -> parameters -> calculation -> rule validation
    -> approved/rejected -> MT5 order -> monitor -> TP1 partial + stop move
    -> TP2 -> TP3 -> completed and logged
"""

from __future__ import annotations

import asyncio

import pytest

from app.workers.monitor import get_monitor

EURUSD_BID = 1.09500
EURUSD_ASK = 1.09512


def run_monitor() -> int:
    """One position-management pass (the loop is disabled during tests)."""
    return asyncio.run(get_monitor().run_once())


def preview(client, **overrides):
    payload = {"symbol": "EURUSD", "side": "buy", "stop_points": 500}
    payload.update(overrides)
    response = client.post("/api/calculator/preview", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def submit(client, **overrides):
    payload = {"symbol": "EURUSD", "side": "buy", "stop_points": 500}
    payload.update(overrides)
    response = client.post("/api/trades", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# connection and market data
# ---------------------------------------------------------------------------
def test_health_reports_the_mock_gateway(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["mt5_gateway"] == "mock"


def test_connecting_an_account_returns_capital_and_the_sizing_hint(auth_client):
    body = auth_client.get("/api/mt5/accounts/state").json()
    assert body["snapshot"]["balance"] == 10_000.0
    assert body["capital"] == 10_000.0
    assert body["capital_basis"] == "balance"
    assert "0.2000 lots" in body["prescribed_lots_hint"]


def test_bad_credentials_are_never_stored(client):
    client.post(
        "/api/auth/register",
        json={"email": "x@example.com", "password": "a-long-enough-pw"},
    )
    token = client.post(
        "/api/auth/login", json={"email": "x@example.com", "password": "a-long-enough-pw"}
    ).json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})

    response = client.post(
        "/api/mt5/accounts",
        json={"login": 777, "password": "x", "server": "MockBroker-Demo"},
    )
    assert response.status_code == 502
    assert response.json()["code"] == "invalid_credentials"
    assert auth_accounts(client) == []


def auth_accounts(client):
    return client.get("/api/mt5/accounts").json()


def test_symbols_and_spec_are_exposed(auth_client):
    symbols = auth_client.get("/api/market/symbols").json()
    names = {s["name"] for s in symbols}
    assert {"EURUSD", "XAUUSD", "US500", "BTCUSD"} <= names

    spec = auth_client.get("/api/market/symbols/EURUSD/spec").json()
    assert spec["volume_step"] == 0.01
    assert spec["money_per_price_unit_per_lot"] == pytest.approx(100_000.0)


# ---------------------------------------------------------------------------
# calculator
# ---------------------------------------------------------------------------
def test_preview_reports_every_required_figure(auth_client):
    body = preview(auth_client)
    plan, rules = body["plan"], body["rules"]

    assert plan["entry_price"] == pytest.approx(EURUSD_ASK)
    assert plan["stop_loss"] == pytest.approx(1.09012)
    assert plan["volume"] == pytest.approx(0.2)
    assert plan["prescribed_volume"] == pytest.approx(0.2)
    assert plan["max_loss"] == pytest.approx(100.0)
    assert plan["risk_pct_of_capital"] == pytest.approx(1.0)
    assert plan["max_risk_money"] == pytest.approx(200.0)
    assert plan["required_margin"] == pytest.approx(219.02, abs=0.05)
    assert plan["expected_profit"] == pytest.approx(150.0)
    assert plan["reward_risk_blended"] == pytest.approx(1.5)
    assert plan["pricing_source"] == "terminal"

    targets = {s["key"]: s for s in plan["stages"]}
    assert targets["TP1"]["target_price"] == pytest.approx(1.10012)
    assert targets["TP2"]["target_price"] == pytest.approx(1.10512)
    assert targets["TP3"]["target_price"] == pytest.approx(1.11012)
    assert targets["TP1"]["volume"] == pytest.approx(0.1)
    assert targets["TP1"]["sl_after"] == pytest.approx(1.09262)
    assert targets["TP1"]["locked_in_money"] == pytest.approx(25.0)

    assert rules["approved"] is True
    assert rules["violations"] == []


def test_preview_has_no_side_effects(auth_client):
    preview(auth_client)
    assert auth_client.get("/api/trades").json() == []
    assert auth_client.get("/api/journal/decisions").json() == []


def test_preview_without_a_stop_is_rejected(auth_client):
    response = auth_client.post(
        "/api/calculator/preview", json={"symbol": "EURUSD", "side": "buy"}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "stop_required"


def test_stop_scan_marks_where_rule_three_bites(auth_client):
    rows = auth_client.post(
        "/api/calculator/stop-scan",
        json={"symbol": "EURUSD", "side": "buy", "stop_points": [250, 500, 1000, 1200]},
    ).json()

    by_points = {r["stop_points"]: r for r in rows}
    assert by_points[500]["loss"] == pytest.approx(100.0)
    assert by_points[500]["within_limit"] is True
    assert by_points[1000]["loss"] == pytest.approx(200.0)
    assert by_points[1000]["within_limit"] is True
    assert by_points[1200]["within_limit"] is False


def test_ladders_are_documented_through_the_api(auth_client):
    ladders = auth_client.get("/api/calculator/ladders").json()
    presets = {ladder["preset"] for ladder in ladders}
    assert presets == {"standard_1_2_3", "runner_1_2_3"}
    standard = next(x for x in ladders if x["preset"] == "standard_1_2_3")
    assert [s["r_multiple"] for s in standard["stages"]] == [1.0, 2.0, 3.0]


# ---------------------------------------------------------------------------
# rule enforcement on submission
# ---------------------------------------------------------------------------
def test_rule_three_rejection_is_journalled_and_nothing_is_sent(auth_client, broker):
    body = submit(auth_client, stop_points=1500)

    assert body["approved"] is False
    assert body["executed"] is False
    assert "RULE3_MAX_RISK" in body["rules"]["violations"]
    assert broker.positions == {}
    assert auth_client.get("/api/trades").json() == []

    decisions = auth_client.get("/api/journal/decisions").json()
    assert len(decisions) == 1
    assert decisions[0]["approved"] is False
    assert decisions[0]["violation_codes"] == "RULE3_MAX_RISK"
    assert decisions[0]["max_loss"] == pytest.approx(300.0)


def test_rule_two_rejection_on_a_hand_typed_volume(auth_client):
    body = submit(auth_client, volume=1.0)
    assert body["approved"] is False
    assert "RULE2_LOT_ALLOCATION" in body["rules"]["violations"]


def test_rule_one_blocks_the_second_entry_and_leaves_others_open(auth_client, broker):
    first = submit(auth_client)
    assert first["executed"] is True

    second = submit(auth_client)
    assert second["approved"] is False
    assert "RULE1_ONE_ACTIVE_TRADE" in second["rules"]["violations"]
    assert len(broker.positions) == 1

    # A different derivative is unaffected.
    other = submit(auth_client, symbol="XAUUSD", stop_points=800)
    assert other["approved"] is True, other["message"]
    assert other["executed"] is True
    assert len(broker.positions) == 2


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------
def test_an_approved_trade_reaches_the_broker_with_stop_and_failsafe_target(
    auth_client, broker
):
    body = submit(auth_client)
    assert body["approved"] and body["executed"]

    trade = body["trade"]
    assert trade["status"] == "open"
    assert trade["initial_volume"] == pytest.approx(0.2)
    assert trade["planned_risk"] == pytest.approx(100.0)
    assert trade["planned_risk_pct"] == pytest.approx(1.0)

    (position,) = broker.positions.values()
    assert position.volume == pytest.approx(0.2)
    assert position.price_open == pytest.approx(EURUSD_ASK)
    assert position.sl == pytest.approx(1.09012)
    # Failsafe take-profit sits at the last rung that carries volume (TP2).
    assert position.tp == pytest.approx(1.10512)

    stages = {s["stage_key"]: s for s in trade["stages"]}
    assert stages["TP1"]["planned_volume"] == pytest.approx(0.1)
    assert stages["TP2"]["planned_volume"] == pytest.approx(0.1)
    assert stages["TP3"]["planned_volume"] == pytest.approx(0.0)
    assert all(s["status"] == "pending" for s in stages.values())


def test_positions_endpoint_links_positions_to_managed_trades(auth_client):
    submit(auth_client)
    body = auth_client.get("/api/positions").json()

    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["managed"] is True
    assert row["trade"]["symbol"] == "EURUSD"
    assert body["risk_on"] == pytest.approx(100.0)
    assert body["orphaned_trades"] == []


# ---------------------------------------------------------------------------
# the ladder
# ---------------------------------------------------------------------------
def test_tp1_takes_half_off_and_halves_the_stop(auth_client, broker):
    trade_id = submit(auth_client)["trade"]["id"]

    broker.set_price("EURUSD", 1.10012)  # bid at TP1
    assert run_monitor() >= 1

    (position,) = broker.positions.values()
    assert position.volume == pytest.approx(0.1)
    assert position.sl == pytest.approx(1.09262)  # half of the original distance

    trade = auth_client.get(f"/api/trades/{trade_id}").json()
    assert trade["status"] == "scaling"
    assert trade["remaining_volume"] == pytest.approx(0.1)
    assert trade["current_stop"] == pytest.approx(1.09262)
    assert trade["realised_pl"] == pytest.approx(50.0)

    stages = {s["stage_key"]: s for s in trade["stages"]}
    assert stages["TP1"]["status"] == "filled"
    assert stages["TP1"]["executed_volume"] == pytest.approx(0.1)
    assert stages["TP1"]["realised_pl"] == pytest.approx(50.0)
    assert stages["TP2"]["status"] == "pending"

    kinds = [e["event_type"] for e in trade["events"]]
    assert "partial_close" in kinds
    assert "sl_modified" in kinds


def test_a_target_touched_between_passes_is_not_missed(auth_client, broker):
    """Regression: the monitor polls, so a target can be touched and retrace.

    Comparing only the *current* price silently skipped the rung. It must be
    detected from the price range since the previous pass instead.
    """
    trade_id = submit(auth_client)["trade"]["id"]

    broker.set_price("EURUSD", 1.10012)  # trades exactly at TP1
    broker.set_price("EURUSD", 1.09800)  # and retraces before the monitor looks

    assert run_monitor() >= 1

    trade = auth_client.get(f"/api/trades/{trade_id}").json()
    tp1 = next(s for s in trade["stages"] if s["stage_key"] == "TP1")
    assert tp1["status"] == "filled"
    assert trade["remaining_volume"] == pytest.approx(0.1)

    # The fill is worse than the target, and the journal says so rather than
    # pretending the exit happened at 1.10012.
    partial = next(e for e in trade["events"] if e["event_type"] == "partial_close")
    assert partial["payload"]["retraced"] is True
    assert "retraced" in partial["message"]
    assert partial["payload"]["price"] < partial["payload"]["target_price"]


def test_database_timestamps_come_back_timezone_aware(auth_client, db):
    """Regression: naive datetimes from SQLite broke every comparison downstream."""
    from app.db.models import ManagedTrade

    submit(auth_client)
    trade = db.query(ManagedTrade).order_by(ManagedTrade.id.desc()).first()
    assert trade is not None
    for field in (trade.created_at, trade.updated_at, trade.opened_at):
        assert field is not None
        assert field.tzinfo is not None, "timestamps must be timezone aware"


def test_the_stop_is_never_widened(auth_client, broker):
    submit(auth_client)
    broker.set_price("EURUSD", 1.10012)
    run_monitor()
    (position,) = broker.positions.values()
    tightened = position.sl

    # Price falls back below TP1; a second pass must not undo the stop move.
    broker.set_price("EURUSD", 1.09600)
    run_monitor()
    (position,) = broker.positions.values()
    assert position.sl == pytest.approx(tightened)


def test_full_progression_to_tp2_closes_the_position_and_books_the_plan(
    auth_client, broker
):
    trade_id = submit(auth_client)["trade"]["id"]

    broker.set_price("EURUSD", 1.10012)
    run_monitor()
    broker.set_price("EURUSD", 1.10512)
    run_monitor()

    assert broker.positions == {}

    trade = auth_client.get(f"/api/trades/{trade_id}").json()
    assert trade["status"] == "closed"
    assert trade["remaining_volume"] == pytest.approx(0.0)
    # 0.1 lots at 1R (50) + 0.1 lots at 2R (100) = the planned 150.
    assert trade["realised_pl"] == pytest.approx(150.0)
    assert trade["planned_profit"] == pytest.approx(150.0)
    assert trade["close_reason"] in {"take_profit", "ladder_complete"}

    stages = {s["stage_key"]: s for s in trade["stages"]}
    assert stages["TP1"]["status"] == "filled"
    assert stages["TP2"]["status"] == "filled"
    assert stages["TP3"]["status"] == "skipped"

    # Rule 1 is released, so the symbol can be traded again.
    again = submit(auth_client)
    assert again["approved"] is True


def test_runner_ladder_executes_all_three_rungs(auth_client, broker):
    body = submit(auth_client, ladder_preset="runner_1_2_3", stop_points=500)
    trade_id = body["trade"]["id"]
    assert body["trade"]["ladder_preset"] == "runner_1_2_3"

    (position,) = broker.positions.values()
    assert position.tp == pytest.approx(1.11012)  # failsafe now at TP3

    broker.set_price("EURUSD", 1.10012)
    run_monitor()
    assert next(iter(broker.positions.values())).volume == pytest.approx(0.1)

    broker.set_price("EURUSD", 1.10512)
    run_monitor()
    remaining = next(iter(broker.positions.values()))
    assert remaining.volume == pytest.approx(0.05)
    assert remaining.sl == pytest.approx(1.10012)  # stop parked at TP1

    broker.set_price("EURUSD", 1.11012)
    run_monitor()
    assert broker.positions == {}

    trade = auth_client.get(f"/api/trades/{trade_id}").json()
    assert trade["status"] == "closed"
    # 0.10 @1R + 0.05 @2R + 0.05 @3R = 50 + 50 + 75
    assert trade["realised_pl"] == pytest.approx(175.0)
    assert [s["status"] for s in trade["stages"]] == ["filled", "filled", "filled"]


def test_stop_loss_hit_is_reconciled_and_logged(auth_client, broker):
    trade_id = submit(auth_client)["trade"]["id"]

    broker.set_price("EURUSD", 1.09012)  # straight to the stop
    assert broker.positions == {}
    run_monitor()

    trade = auth_client.get(f"/api/trades/{trade_id}").json()
    assert trade["status"] == "closed"
    assert trade["close_reason"] == "stop_loss"
    assert trade["realised_pl"] == pytest.approx(-100.0, abs=0.5)
    assert all(s["status"] == "skipped" for s in trade["stages"])
    assert any(e["event_type"] == "stop_hit" for e in trade["events"])


# ---------------------------------------------------------------------------
# manual intervention
# ---------------------------------------------------------------------------
def test_manual_close_releases_the_rule_one_lock(auth_client, broker):
    trade_id = submit(auth_client)["trade"]["id"]

    body = auth_client.post(f"/api/trades/{trade_id}/close", json={}).json()
    assert body["closed"] is True
    assert broker.positions == {}

    trade = auth_client.get(f"/api/trades/{trade_id}").json()
    assert trade["status"] == "closed"
    assert trade["close_reason"] == "manual"

    assert submit(auth_client)["approved"] is True


def test_sync_adopts_an_external_partial_close(auth_client, broker):
    trade_id = submit(auth_client)["trade"]["id"]
    (ticket,) = broker.positions
    broker.close(ticket, 0.05, comment="closed in the terminal")

    body = auth_client.post(f"/api/trades/{trade_id}/sync", json={}).json()
    assert body["changed"] is True
    assert body["trade"]["remaining_volume"] == pytest.approx(0.15)


def test_an_account_with_a_live_trade_cannot_be_disconnected(auth_client):
    submit(auth_client)
    account_id = auth_client.get("/api/mt5/accounts").json()[0]["id"]
    response = auth_client.delete(f"/api/mt5/accounts/{account_id}")
    assert response.status_code == 409
    assert response.json()["code"] == "account_has_active_trades"


# ---------------------------------------------------------------------------
# configuration and journal
# ---------------------------------------------------------------------------
def test_risk_profile_changes_change_the_sizing(auth_client):
    auth_client.put(
        "/api/rules/profile",
        json={"lots_per_1000": 0.01, "max_risk_pct": 1.0, "ladder_preset": "standard_1_2_3"},
    )
    plan = preview(auth_client, stop_points=400)["plan"]
    assert plan["volume"] == pytest.approx(0.1)
    assert plan["max_risk_money"] == pytest.approx(100.0)


def test_dangerous_profiles_are_refused(auth_client):
    response = auth_client.put(
        "/api/rules/profile", json={"lots_per_1000": 0.9, "max_risk_pct": 2.0}
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "invalid_profile"
    assert "extreme" in body["message"]

    # The schema rejects values outside its own bounds before the service runs.
    assert (
        auth_client.put(
            "/api/rules/profile", json={"lots_per_1000": 0.02, "max_risk_pct": 90}
        ).status_code
        == 422
    )


def test_fixed_capital_basis_requires_a_figure(auth_client):
    response = auth_client.put(
        "/api/rules/profile",
        json={
            "lots_per_1000": 0.02,
            "max_risk_pct": 2.0,
            "capital_basis": "fixed",
            "fixed_capital": 0,
        },
    )
    assert response.status_code == 422
    assert "positive capital figure" in response.json()["message"]


def test_fixed_capital_basis_overrides_the_broker_balance(auth_client):
    auth_client.put(
        "/api/rules/profile",
        json={
            "lots_per_1000": 0.02,
            "max_risk_pct": 2.0,
            "capital_basis": "fixed",
            "fixed_capital": 5_000,
        },
    )
    plan = preview(auth_client, stop_points=400)["plan"]
    assert plan["capital"] == pytest.approx(5_000.0)
    assert plan["volume"] == pytest.approx(0.1)
    assert plan["max_risk_money"] == pytest.approx(100.0)


def test_performance_summary_tracks_rule_adherence(auth_client, broker):
    submit(auth_client, stop_points=1500)  # rejected
    trade_id = submit(auth_client)["trade"]["id"]
    broker.set_price("EURUSD", 1.10012)
    run_monitor()
    broker.set_price("EURUSD", 1.10512)
    run_monitor()

    body = auth_client.get("/api/journal/performance").json()
    assert body["closed_trades"] == 1
    assert body["wins"] == 1
    assert body["net_pl"] == pytest.approx(150.0)
    assert body["decisions_approved"] == 1
    assert body["decisions_rejected"] == 1
    assert body["rule_adherence_pct"] == pytest.approx(50.0)
    assert body["top_rejections"][0]["codes"] == "RULE3_MAX_RISK"
    assert auth_client.get(f"/api/trades/{trade_id}").json()["status"] == "closed"


def test_decision_detail_preserves_the_full_plan(auth_client):
    submit(auth_client, stop_points=1500)
    decision_id = auth_client.get("/api/journal/decisions").json()[0]["id"]
    detail = auth_client.get(f"/api/journal/decisions/{decision_id}").json()

    assert detail["plan"]["symbol"] == "EURUSD"
    assert detail["plan"]["max_loss"] == pytest.approx(300.0)
    codes = {c["code"] for c in detail["checks"]}
    assert "RULE3_MAX_RISK" in codes
    failed = next(c for c in detail["checks"] if c["code"] == "RULE3_MAX_RISK")
    assert failed["passed"] is False
    assert "Move the stop no further than" in failed["message"]


# ---------------------------------------------------------------------------
# auth boundaries
# ---------------------------------------------------------------------------
def test_endpoints_require_authentication(client):
    for path in ("/api/mt5/accounts", "/api/positions", "/api/trades"):
        assert client.get(path).status_code == 401, path

    posted = client.post("/api/calculator/preview", json={"symbol": "EURUSD", "side": "buy"})
    assert posted.status_code == 401


def test_one_user_cannot_see_another_users_trades(auth_client, client):
    trade_id = submit(auth_client)["trade"]["id"]

    other = client.post(
        "/api/auth/register",
        json={"email": "other@example.com", "password": "another-long-pw-1"},
    ).json()["access_token"]

    response = client.get(
        f"/api/trades/{trade_id}", headers={"Authorization": f"Bearer {other}"}
    )
    assert response.status_code == 404
