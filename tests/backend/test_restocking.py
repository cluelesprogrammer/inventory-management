"""
Tests for the restocking endpoints (recommendations and order placement).

Orders are written to a temp file and the in-memory orders list is restored after each
test, so the tracked server/data/orders.json is never modified by the test suite.
"""
import json

import pytest

import main


@pytest.fixture(autouse=True)
def isolated_orders(tmp_path, monkeypatch):
    """Point ORDERS_FILE at a temp file and restore the in-memory orders afterwards."""
    orders_file = tmp_path / "orders.json"
    orders_file.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(main, "ORDERS_FILE", str(orders_file))
    snapshot = list(main.orders)
    yield orders_file
    main.orders[:] = snapshot


def _first_candidate(client):
    """Return the top recommendation with a generous budget."""
    data = client.get("/api/restocking/recommendations", params={"budget": 10**9}).json()
    assert data["items"], "expected at least one recommendation in demo data"
    return data["items"][0]


class TestRecommendations:

    def test_total_never_exceeds_budget(self, client):
        for budget in [0, 500, 5000, 25000]:
            data = client.get("/api/restocking/recommendations", params={"budget": budget}).json()
            assert data["total_cost"] <= budget
            assert data["remaining"] == pytest.approx(budget - data["total_cost"], abs=0.01)

    def test_zero_budget_returns_no_items(self, client):
        data = client.get("/api/restocking/recommendations", params={"budget": 0}).json()
        assert data["items"] == []
        assert data["total_cost"] == 0

    def test_items_sorted_by_shortfall_desc(self, client):
        data = client.get("/api/restocking/recommendations", params={"budget": 10**9}).json()
        quantities = [item["quantity"] for item in data["items"]]
        assert quantities == sorted(quantities, reverse=True)

    def test_quantity_is_forecast_minus_on_hand(self, client):
        inventory = {i["sku"]: i for i in client.get("/api/inventory").json()}
        forecasts = {f["item_sku"]: f for f in client.get("/api/demand").json()}
        data = client.get("/api/restocking/recommendations", params={"budget": 10**9}).json()
        for item in data["items"]:
            expected = forecasts[item["sku"]]["forecasted_demand"] - inventory[item["sku"]]["quantity_on_hand"]
            assert item["quantity"] == expected
            assert expected > 0

    def test_skips_items_without_shortfall(self, client):
        # PSU-501 has 420 on hand against a forecast of 252, so it must not be recommended
        data = client.get("/api/restocking/recommendations", params={"budget": 10**9}).json()
        assert "PSU-501" not in {item["sku"] for item in data["items"]}

    def test_negative_budget_rejected(self, client):
        response = client.get("/api/restocking/recommendations", params={"budget": -1})
        assert response.status_code == 422


class TestPlaceRestockingOrder:

    def test_creates_submitted_order_with_lead_time(self, client, isolated_orders):
        item = _first_candidate(client)
        payload = {"budget": item["line_total"], "items": [{"sku": item["sku"], "quantity": item["quantity"]}]}

        response = client.post("/api/restocking/orders", json=payload)
        assert response.status_code == 201
        order = response.json()

        assert order["order_type"] == "restock"
        assert order["status"] == "Submitted"
        assert order["customer"] == "Internal Restock"
        assert order["order_number"].startswith("RST-")
        assert order["total_value"] == pytest.approx(item["line_total"], abs=0.01)
        assert order["lead_time_days"] == main.lead_time_for(item["category"])

    def test_expected_delivery_is_order_date_plus_lead_time(self, client):
        from datetime import datetime
        item = _first_candidate(client)
        payload = {"budget": item["line_total"], "items": [{"sku": item["sku"], "quantity": item["quantity"]}]}
        order = client.post("/api/restocking/orders", json=payload).json()

        ordered = datetime.fromisoformat(order["order_date"])
        delivery = datetime.fromisoformat(order["expected_delivery"])
        assert (delivery - ordered).days == order["lead_time_days"]

    def test_order_appears_in_orders_endpoint(self, client):
        item = _first_candidate(client)
        payload = {"budget": item["line_total"], "items": [{"sku": item["sku"], "quantity": item["quantity"]}]}
        created = client.post("/api/restocking/orders", json=payload).json()

        listed = client.get("/api/orders").json()
        match = [o for o in listed if o["id"] == created["id"]]
        assert len(match) == 1
        assert match[0]["status"] == "Submitted"

    def test_order_written_to_file(self, client, isolated_orders):
        item = _first_candidate(client)
        payload = {"budget": item["line_total"], "items": [{"sku": item["sku"], "quantity": item["quantity"]}]}
        created = client.post("/api/restocking/orders", json=payload).json()

        saved = json.loads(isolated_orders.read_text(encoding="utf-8"))
        assert any(o["id"] == created["id"] for o in saved)

    def test_total_over_budget_rejected(self, client):
        item = _first_candidate(client)
        payload = {"budget": item["line_total"] - 0.01, "items": [{"sku": item["sku"], "quantity": item["quantity"]}]}
        response = client.post("/api/restocking/orders", json=payload)
        assert response.status_code == 400

    def test_unknown_sku_rejected(self, client):
        response = client.post("/api/restocking/orders", json={"budget": 1000, "items": [{"sku": "NOPE-999", "quantity": 1}]})
        assert response.status_code == 400

    def test_empty_order_rejected(self, client):
        response = client.post("/api/restocking/orders", json={"budget": 1000, "items": []})
        assert response.status_code == 400

    def test_non_positive_quantity_rejected(self, client):
        response = client.post("/api/restocking/orders", json={"budget": 1000, "items": [{"sku": "PCB-001", "quantity": 0}]})
        assert response.status_code == 400

    def test_duplicate_sku_rejected(self, client):
        line = {"sku": "PCB-001", "quantity": 1}
        response = client.post("/api/restocking/orders", json={"budget": 10000, "items": [line, line]})
        assert response.status_code == 400

    def test_client_price_is_ignored(self, client):
        # The server recomputes cost from inventory; the request has no price field to trust
        response = client.post(
            "/api/restocking/orders",
            json={"budget": 10000, "items": [{"sku": "PCB-001", "quantity": 2, "unit_cost": 0.01}]},
        )
        assert response.status_code == 201
        assert response.json()["total_value"] == pytest.approx(2 * 24.99, abs=0.01)
