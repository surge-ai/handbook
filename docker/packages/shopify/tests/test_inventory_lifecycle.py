"""Tests for inventory reduction on order creation and restoration on cancel/refund."""

import json
from typing import Any

import pytest

from shopify import state as shopify_state
from shopify.models import (
    CancelOrderArgs,
    CreateOrderArgs,
    CreateReturnArgs,
    CreateReturnLineItemInput,
    LooseCart,
    UpdateOrderArgs,
    UpdateReturnArgs,
)
from shopify.tools.orders import handle_cancel_order, handle_create_order, handle_update_order
from shopify.tools.reviews_returns import handle_create_return, handle_update_return

VALID_CC = {"type": "credit_card", "card_number": "4111111111111111", "cvv": "123", "expiry": "12/26"}
VALID_ADDR = {"address1": "1 Main St", "city": "Portland", "countryCode": "US"}


def _order_args(**overrides: Any) -> CreateOrderArgs:
    return CreateOrderArgs.model_validate(
        {
            "cart_id": overrides.get("cart_id", "cart-1"),
            "payment_method": overrides.get("payment_method", VALID_CC),
            "shipping_address": overrides.get("shipping_address", VALID_ADDR),
            "billing_address": overrides.get("billing_address", VALID_ADDR),
            "shipping_method_id": overrides.get("shipping_method_id", "standard"),
            "email": overrides.get("email"),
        }
    )


@pytest.fixture
def shopify_data(tmp_path):
    """Seed state with products having tracked inventory and a cart."""
    data_file = tmp_path / "shopify_data.json"
    data_file.write_text(
        json.dumps(
            {
                "products": {
                    "product-1": {
                        "id": "product-1",
                        "title": "Widget",
                        "handle": "widget",
                        "availableForSale": True,
                        "totalInventory": 10,
                        "variants": [
                            {
                                "id": "variant-1",
                                "title": "Default",
                                "price": {"amount": "20.00", "currencyCode": "USD"},
                                "availableForSale": True,
                                "quantityAvailable": 10,
                                "currentlyNotInStock": False,
                            }
                        ],
                    }
                },
                "carts": {
                    "cart-1": {
                        "id": "cart-1",
                        "lines": [
                            {
                                "id": "line-1",
                                "quantity": 3,
                                "merchandise": {
                                    "id": "variant-1",
                                    "title": "Default",
                                    "product": {"id": "product-1", "title": "Widget"},
                                    "price": {"amount": "20.00", "currencyCode": "USD"},
                                },
                                "cost": {
                                    "amountPerQuantity": {"amount": "20.00", "currencyCode": "USD"},
                                    "subtotalAmount": {"amount": "60.00", "currencyCode": "USD"},
                                    "totalAmount": {"amount": "60.00", "currencyCode": "USD"},
                                },
                            }
                        ],
                    }
                },
                "orders": {},
                "customers": {},
                "collections": {},
                "reviews": {},
                "returns": {},
                "discount_codes": {},
                "shipping_methods": {
                    "standard": {
                        "id": "standard",
                        "title": "Standard",
                        "price": {"amount": "5.00", "currencyCode": "USD"},
                        "active": True,
                    }
                },
                "policies": [],
                "counters": {
                    "cart_id": 1001,
                    "line_id": 1001,
                    "order_id": 2001,
                    "line_item_id": 3001,
                    "customer_id": 4001,
                    "return_id": 7001,
                },
            }
        )
    )
    return data_file


@pytest.fixture(autouse=True)
def _patch_state(shopify_data, monkeypatch):
    monkeypatch.setattr(shopify_state, "_STATE_FILE", shopify_data)
    shopify_state._current_state = None
    shopify_state._stores.clear()
    shopify_state._active_store_id = "default"
    shopify_state.load_state()


def _get_variant() -> Any:
    _, variant = shopify_state.get_variant_by_id("variant-1")
    assert variant is not None
    return variant


def _get_product() -> Any:
    state = shopify_state.get_state()
    return state.products["product-1"]


class TestStockReductionOnOrder:
    def test_order_reduces_variant_stock(self):
        assert _get_variant()["quantityAvailable"] == 10
        result = handle_create_order(_order_args())
        assert result["userErrors"] == []
        assert _get_variant()["quantityAvailable"] == 7

    def test_order_updates_product_total_inventory(self):
        handle_create_order(_order_args())
        assert _get_product()["totalInventory"] == 7

    def test_order_rejects_insufficient_inventory(self):
        # Preset stock lower than the 3-unit order
        shopify_state.adjust_variant_stock("variant-1", -8)  # 10 - 8 = 2
        state = shopify_state.get_state()
        before_order_id = state.counters.order_id
        before_line_item_id = state.counters.line_item_id

        result = handle_create_order(_order_args())  # orders 3 more

        assert result["order"] is None
        assert "Insufficient inventory" in result["userErrors"][0]["message"]
        assert _get_variant()["quantityAvailable"] == 2
        assert _get_variant()["currentlyNotInStock"] is False
        assert _get_variant()["availableForSale"] is True
        state = shopify_state.get_state()
        assert state.counters.order_id == before_order_id
        assert state.counters.line_item_id == before_line_item_id

    def test_failed_order_does_not_reduce_stock(self):
        result = handle_create_order(_order_args(cart_id="nonexistent"))
        assert result["order"] is None
        assert _get_variant()["quantityAvailable"] == 10


class TestStockRestoredOnCancelBeforeShip:
    def test_cancel_unfulfilled_order_restores_stock(self):
        order = handle_create_order(_order_args())["order"]
        assert _get_variant()["quantityAvailable"] == 7
        handle_cancel_order(CancelOrderArgs(order_id=order["id"]))
        assert _get_variant()["quantityAvailable"] == 10

    def test_cancel_fulfilled_order_does_not_restore(self):
        order = handle_create_order(_order_args())["order"]
        handle_update_order(UpdateOrderArgs(order_id=order["id"], fulfillment_status="FULFILLED"))
        handle_cancel_order(CancelOrderArgs(order_id=order["id"]))
        # Stock stays reduced — physical product is out in the wild
        assert _get_variant()["quantityAvailable"] == 7

    def test_cancel_partially_fulfilled_does_not_restore(self):
        order = handle_create_order(_order_args())["order"]
        handle_update_order(UpdateOrderArgs(order_id=order["id"], fulfillment_status="PARTIALLY_FULFILLED"))
        handle_cancel_order(CancelOrderArgs(order_id=order["id"]))
        assert _get_variant()["quantityAvailable"] == 7

    def test_cancel_after_partial_refund_only_restores_unreturned_quantity(self):
        order = handle_create_order(_order_args())["order"]
        order_line_id = order["lineItems"][0]["id"]
        ret = handle_create_return(
            CreateReturnArgs(
                order_id=order["id"],
                line_items=[
                    CreateReturnLineItemInput(
                        orderLineItemId=order_line_id,
                        quantity=1,
                        reason="changed mind",
                    )
                ],
            )
        )["return"]
        handle_update_return(UpdateReturnArgs(return_id=ret["id"], status="REFUNDED"))
        assert _get_variant()["quantityAvailable"] == 8

        handle_cancel_order(CancelOrderArgs(order_id=order["id"]))

        assert _get_variant()["quantityAvailable"] == 10


class TestStockRestoredOnRefundBeforeShip:
    def test_refund_on_unfulfilled_restores_stock(self):
        order = handle_create_order(_order_args())["order"]
        assert _get_variant()["quantityAvailable"] == 7
        # Create return for 2 of the 3 units
        order_line_id = order["lineItems"][0]["id"]
        ret = handle_create_return(
            CreateReturnArgs(
                order_id=order["id"],
                line_items=[
                    CreateReturnLineItemInput(
                        orderLineItemId=order_line_id,
                        quantity=2,
                        reason="changed mind",
                    )
                ],
            )
        )["return"]
        # No stock change yet — status is still REQUESTED
        assert _get_variant()["quantityAvailable"] == 7

        handle_update_return(UpdateReturnArgs(return_id=ret["id"], status="REFUNDED"))
        assert _get_variant()["quantityAvailable"] == 9

    def test_refund_on_fulfilled_does_not_restore(self):
        order = handle_create_order(_order_args())["order"]
        handle_update_order(UpdateOrderArgs(order_id=order["id"], fulfillment_status="FULFILLED"))
        order_line_id = order["lineItems"][0]["id"]
        ret = handle_create_return(
            CreateReturnArgs(
                order_id=order["id"],
                line_items=[
                    CreateReturnLineItemInput(
                        orderLineItemId=order_line_id,
                        quantity=2,
                        reason="defective",
                    )
                ],
            )
        )["return"]
        handle_update_return(UpdateReturnArgs(return_id=ret["id"], status="REFUNDED"))
        # Stock stays reduced — physical return needed, out of mock scope
        assert _get_variant()["quantityAvailable"] == 7

    def test_rejected_return_does_not_restore(self):
        order = handle_create_order(_order_args())["order"]
        order_line_id = order["lineItems"][0]["id"]
        ret = handle_create_return(
            CreateReturnArgs(
                order_id=order["id"],
                line_items=[
                    CreateReturnLineItemInput(
                        orderLineItemId=order_line_id,
                        quantity=1,
                        reason="wrong color",
                    )
                ],
            )
        )["return"]
        handle_update_return(UpdateReturnArgs(return_id=ret["id"], status="REJECTED"))
        assert _get_variant()["quantityAvailable"] == 7

    def test_double_refund_restores_only_once(self):
        order = handle_create_order(_order_args())["order"]
        order_line_id = order["lineItems"][0]["id"]
        ret = handle_create_return(
            CreateReturnArgs(
                order_id=order["id"],
                line_items=[
                    CreateReturnLineItemInput(
                        orderLineItemId=order_line_id,
                        quantity=2,
                        reason="changed mind",
                    )
                ],
            )
        )["return"]
        handle_update_return(UpdateReturnArgs(return_id=ret["id"], status="REFUNDED"))
        handle_update_return(UpdateReturnArgs(return_id=ret["id"], status="REFUNDED"))
        assert _get_variant()["quantityAvailable"] == 9


class TestTrackingNumber:
    def test_new_order_has_no_tracking(self):
        order = handle_create_order(_order_args())["order"]
        assert order["trackingNumber"] is None
        assert order["trackingUrl"] is None

    def test_fulfillment_assigns_tracking(self):
        order = handle_create_order(_order_args())["order"]
        result = handle_update_order(UpdateOrderArgs(order_id=order["id"], fulfillment_status="FULFILLED"))
        updated = result["order"]
        assert updated["trackingNumber"] is not None
        assert updated["trackingNumber"].startswith("TRK-")
        assert updated["trackingUrl"] == f"https://track.example.com/{updated['trackingNumber']}"

    def test_partial_fulfillment_assigns_tracking(self):
        order = handle_create_order(_order_args())["order"]
        result = handle_update_order(UpdateOrderArgs(order_id=order["id"], fulfillment_status="PARTIALLY_FULFILLED"))
        assert result["order"]["trackingNumber"] is not None

    def test_tracking_is_stable_after_assignment(self):
        order = handle_create_order(_order_args())["order"]
        first = handle_update_order(UpdateOrderArgs(order_id=order["id"], fulfillment_status="PARTIALLY_FULFILLED"))[
            "order"
        ]["trackingNumber"]
        # Flipping to FULFILLED after already being (partially) fulfilled keeps the same tracking
        second = handle_update_order(UpdateOrderArgs(order_id=order["id"], fulfillment_status="FULFILLED"))["order"][
            "trackingNumber"
        ]
        assert first == second

    def test_unfulfilled_status_does_not_assign_tracking(self):
        order = handle_create_order(_order_args())["order"]
        result = handle_update_order(UpdateOrderArgs(order_id=order["id"], fulfillment_status="UNFULFILLED"))
        assert result["order"]["trackingNumber"] is None

    def test_tracking_numbers_are_unique_across_orders(self):
        # Seed a second cart so we can create two orders
        state = shopify_state.get_state()
        state.carts["cart-2"] = LooseCart.model_validate(
            {
                "id": "cart-2",
                "lines": state.carts["cart-1"]["lines"],
            }
        )
        shopify_state.save_state()

        o1 = handle_create_order(_order_args())["order"]
        o2 = handle_create_order(_order_args(cart_id="cart-2"))["order"]
        handle_update_order(UpdateOrderArgs(order_id=o1["id"], fulfillment_status="FULFILLED"))
        handle_update_order(UpdateOrderArgs(order_id=o2["id"], fulfillment_status="FULFILLED"))
        state = shopify_state.get_state()
        t1 = state.orders[o1["id"]]["trackingNumber"]
        t2 = state.orders[o2["id"]]["trackingNumber"]
        assert t1 != t2
