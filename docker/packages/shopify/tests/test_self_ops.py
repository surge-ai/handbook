"""Tests for the customer self-only tool handlers.

These verify that every operation is scoped to the current customer only —
a shopper cannot read or mutate other customers' data even by passing the
wrong IDs, and all self-tools error clearly when no current customer is set.
"""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from shopify import state as shopify_state
from shopify.models import CreateReturnArgs, CreateReturnLineItemInput, GetOrderArgs
from shopify.tools.self import (
    handle_create_my_return,
    handle_create_my_review,
    handle_get_my_customer,
    handle_get_my_loyalty_balance,
    handle_get_my_loyalty_tier,
    handle_get_my_order,
    handle_list_my_orders,
    handle_redeem_my_points,
    handle_update_my_customer,
)


def _base_data(current_customer_email: str | None = "alice@example.com") -> dict[str, Any]:
    return {
        "products": {
            "p1": {
                "id": "p1",
                "title": "Widget",
                "handle": "widget",
                "variants": [
                    {
                        "id": "v1",
                        "title": "Default",
                        "price": {"amount": "25.00", "currencyCode": "USD"},
                        "availableForSale": True,
                    }
                ],
            }
        },
        "carts": {},
        "orders": {
            # Order belonging to alice
            "o1": {
                "id": "o1",
                "name": "#o1",
                "email": "alice@example.com",
                "createdAt": "2026-03-10T10:00:00Z",
                "updatedAt": "2026-03-10T10:00:00Z",
                "cancelledAt": None,
                "financialStatus": "PAID",
                "fulfillmentStatus": "UNFULFILLED",
                "lineItems": [
                    {
                        "id": "li1",
                        "title": "Widget",
                        "variantId": "v1",
                        "productId": "p1",
                        "quantity": 1,
                        "price": {"amount": "25.00", "currencyCode": "USD"},
                        "totalPrice": {"amount": "25.00", "currencyCode": "USD"},
                    }
                ],
                "subtotalPrice": {"amount": "25.00", "currencyCode": "USD"},
                "totalPrice": {"amount": "25.00", "currencyCode": "USD"},
                "shippingAddress": None,
                "billingAddress": None,
            },
            # Order belonging to bob — alice should not be able to access.
            "o2": {
                "id": "o2",
                "name": "#o2",
                "email": "bob@example.com",
                "createdAt": "2026-03-11T10:00:00Z",
                "updatedAt": "2026-03-11T10:00:00Z",
                "cancelledAt": None,
                "financialStatus": "PAID",
                "fulfillmentStatus": "UNFULFILLED",
                "lineItems": [
                    {
                        "id": "li2",
                        "title": "Widget",
                        "variantId": "v1",
                        "productId": "p1",
                        "quantity": 1,
                        "price": {"amount": "25.00", "currencyCode": "USD"},
                        "totalPrice": {"amount": "25.00", "currencyCode": "USD"},
                    }
                ],
                "subtotalPrice": {"amount": "25.00", "currencyCode": "USD"},
                "totalPrice": {"amount": "25.00", "currencyCode": "USD"},
                "shippingAddress": None,
                "billingAddress": None,
            },
        },
        "customers": {
            "c1": {
                "id": "c1",
                "firstName": "Alice",
                "lastName": "Smith",
                "email": "alice@example.com",
                "phone": "+1111",
                "createdAt": "2026-01-01T00:00:00Z",
                "updatedAt": "2026-01-01T00:00:00Z",
                "defaultAddress": None,
                "addresses": [],
                "ordersCount": 1,
                "totalSpent": {"amount": "25.00", "currencyCode": "USD"},
                "tags": ["vip"],
                "note": "internal note",
                "acceptsMarketing": True,
                "state": "ENABLED",
                "pointsBalance": 500,
                "lifetimePoints": 1500,
                "tier": "Silver",
            },
            "c2": {
                "id": "c2",
                "firstName": "Bob",
                "lastName": "Jones",
                "email": "bob@example.com",
                "phone": "+2222",
                "createdAt": "2026-01-01T00:00:00Z",
                "updatedAt": "2026-01-01T00:00:00Z",
                "defaultAddress": None,
                "addresses": [],
                "ordersCount": 0,
                "totalSpent": None,
                "tags": [],
                "note": None,
                "acceptsMarketing": False,
                "state": "ENABLED",
                "pointsBalance": 9999,
                "lifetimePoints": 9999,
                "tier": "Gold",
            },
        },
        "collections": {},
        "reviews": {},
        "returns": {},
        "discount_codes": {},
        "shipping_methods": {},
        "loyalty_program": {
            "enabled": True,
            "earn_rate": 1,
            "redemption_rate": 100,
            "max_redemption_percent": 50,
            "tiers": [
                {"name": "Bronze", "min_lifetime_points": 0, "discount_percent": 5},
                {"name": "Silver", "min_lifetime_points": 1000, "discount_percent": 10},
            ],
        },
        "current_customer_email": current_customer_email,
        "policies": [],
        "counters": {"cart_id": 1000, "line_id": 1000, "order_id": 2000, "return_id": 7000},
    }


@pytest.fixture
def shopify_data(tmp_path) -> Any:
    data_file = tmp_path / "shopify_data.json"
    data_file.write_text(json.dumps(_base_data()))
    return data_file


@pytest.fixture(autouse=True)
def _patch_state(shopify_data, monkeypatch):
    monkeypatch.setattr(shopify_state, "_STATE_FILE", shopify_data)
    shopify_state._current_state = None
    shopify_state._stores.clear()
    shopify_state._active_store_id = "default"
    shopify_state.load_state()


class TestGetMyCustomer:
    def test_returns_current_customer(self):
        result = handle_get_my_customer()
        assert result["userErrors"] == []
        assert result["customer"]["email"] == "alice@example.com"

    def test_errors_when_unset(self, tmp_path, monkeypatch):
        # Re-init with no current_customer_email
        data_file = tmp_path / "shopify_data.json"
        data_file.write_text(json.dumps(_base_data(current_customer_email=None)))
        monkeypatch.setattr(shopify_state, "_STATE_FILE", data_file)
        shopify_state._current_state = None
        shopify_state._stores.clear()
        shopify_state._active_store_id = "default"
        shopify_state.load_state()

        result = handle_get_my_customer()
        assert result["customer"] is None
        assert "identity not set" in result["userErrors"][0]["message"].lower()


class TestUpdateMyCustomer:
    def test_updates_allowed_fields(self):
        result = handle_update_my_customer(first_name="Alicia", phone="+9999", accepts_marketing=False)
        assert result["userErrors"] == []
        assert result["customer"]["firstName"] == "Alicia"
        assert result["customer"]["phone"] == "+9999"
        assert result["customer"]["acceptsMarketing"] is False

    def test_appends_address_and_sets_default(self):
        addr = {"address1": "1 Main St", "city": "Portland", "countryCode": "US"}
        result = handle_update_my_customer(address=addr)
        customer = result["customer"]
        default_address = customer["defaultAddress"].model_dump(mode="json", exclude_none=True)
        appended_address = customer["addresses"][-1].model_dump(mode="json", exclude_none=True)
        assert {key: default_address[key] for key in addr} == addr
        assert {key: appended_address[key] for key in addr} == addr

    def test_update_address_dedupes_existing_default_address(self):
        addr = {"address1": "1 Main St", "city": "Portland", "countryCode": "US"}

        handle_update_my_customer(address=addr)
        result = handle_update_my_customer(address=addr)

        default_address = result["customer"]["defaultAddress"].model_dump(mode="json", exclude_none=True)
        assert {key: default_address[key] for key in addr} == addr
        assert len(result["customer"]["addresses"]) == 1

    def test_update_invalid_address_validates_before_any_mutation(self):
        customer = shopify_state.get_state().customers["c1"]
        original_updated_at = customer.updatedAt
        original_phone = customer.phone

        with pytest.raises(ValidationError):
            handle_update_my_customer(
                first_name="Alicia",
                last_name="Changed",
                phone="+9999",
                accepts_marketing=False,
                address={"countryCode": 123},
            )

        assert customer.firstName == "Alice"
        assert customer.lastName == "Smith"
        assert customer.phone == original_phone
        assert customer.acceptsMarketing is True
        assert customer.defaultAddress is None
        assert customer.addresses == []
        assert customer.updatedAt == original_updated_at

    def test_cannot_touch_admin_fields(self):
        # Even if the function accepted them, the signature doesn't expose
        # tags/note/state — this test documents that. Sanity check that the
        # call doesn't accept unknown kwargs.
        with pytest.raises(TypeError):
            handle_update_my_customer(tags=["admin_set_tag"])  # ty: ignore[unknown-argument]


class TestLoyaltySelf:
    def test_get_my_loyalty_balance(self):
        result = handle_get_my_loyalty_balance()
        assert result["balance"]["customerId"] == "c1"
        assert result["balance"]["pointsBalance"] == 500
        assert result["balance"]["lifetimePoints"] == 1500

    def test_get_my_loyalty_tier(self):
        result = handle_get_my_loyalty_tier()
        assert result["tier"]["name"] == "Silver"

    def test_redeem_my_points_succeeds(self):
        result = handle_redeem_my_points(200)
        assert result["userErrors"] == []
        assert result["redemption"]["pointsRedeemed"] == 200
        state = shopify_state.get_state()
        assert state.customers["c1"]["pointsBalance"] == 300

    def test_redeem_insufficient_balance_errors(self):
        result = handle_redeem_my_points(999_999)
        assert result["redemption"] is None
        assert len(result["userErrors"]) == 1

    def test_self_loyalty_does_not_touch_other_customer(self):
        # Bob has 9999 points but alice is current — redeeming 100 should
        # come from alice's 500, not bob's 9999.
        handle_redeem_my_points(100)
        state = shopify_state.get_state()
        assert state.customers["c1"]["pointsBalance"] == 400
        assert state.customers["c2"]["pointsBalance"] == 9999


class TestOrdersSelf:
    def test_list_my_orders_returns_only_own(self):
        result = handle_list_my_orders()
        assert result["userErrors"] == []
        assert result["totalCount"] == 1
        assert result["orders"][0]["id"] == "o1"

    def test_get_my_order_returns_own(self):
        result = handle_get_my_order(GetOrderArgs(order_id="o1"))
        assert result["userErrors"] == []
        assert result["order"]["id"] == "o1"

    def test_get_my_order_rejects_other_customer_order(self):
        # Bob's order — alice should not be able to fetch it.
        result = handle_get_my_order(GetOrderArgs(order_id="o2"))
        assert result["order"] is None
        assert "does not belong" in result["userErrors"][0]["message"]

    def test_get_my_order_nonexistent(self):
        result = handle_get_my_order(GetOrderArgs(order_id="missing"))
        assert result["order"] is None
        assert "not found" in result["userErrors"][0]["message"].lower()


class TestReturnSelf:
    def test_creates_return_on_own_order(self):
        result = handle_create_my_return(
            CreateReturnArgs(
                order_id="o1",
                line_items=[
                    CreateReturnLineItemInput(
                        orderLineItemId="li1",
                        quantity=1,
                        reason="changed mind",
                    )
                ],
            )
        )
        assert result["userErrors"] == []
        assert result["return"]["orderId"] == "o1"

    def test_rejects_other_customer_order(self):
        result = handle_create_my_return(
            CreateReturnArgs(
                order_id="o2",
                line_items=[
                    CreateReturnLineItemInput(
                        orderLineItemId="li2",
                        quantity=1,
                        reason="changed mind",
                    )
                ],
            )
        )
        assert result["return"] is None
        assert "does not belong" in result["userErrors"][0]["message"]


class TestReviewSelf:
    def test_fills_author_and_email_from_current_customer(self):
        result = handle_create_my_review(product_id="p1", rating=5, title="Great", body="Love it")
        assert result["userErrors"] == []
        review = result["review"]
        assert review["author"] == "Alice Smith"
        assert review["email"] == "alice@example.com"

    def test_invalid_rating_returns_user_errors(self):
        result = handle_create_my_review(product_id="p1", rating=6, title="Bad rating", body="")

        assert result["review"] is None
        assert result["userErrors"][0]["field"] == "rating"

    def test_errors_when_unset(self, tmp_path, monkeypatch):
        data_file = tmp_path / "shopify_data.json"
        data_file.write_text(json.dumps(_base_data(current_customer_email=None)))
        monkeypatch.setattr(shopify_state, "_STATE_FILE", data_file)
        shopify_state._current_state = None
        shopify_state._stores.clear()
        shopify_state._active_store_id = "default"
        shopify_state.load_state()

        result = handle_create_my_review(product_id="p1", rating=5, title="t", body="b")
        assert result["review"] is None
        assert len(result["userErrors"]) == 1
