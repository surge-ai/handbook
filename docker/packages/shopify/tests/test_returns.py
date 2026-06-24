"""Tests for returns and refunds tools."""

import json

import pytest
from pydantic import ValidationError

from shopify import state as shopify_state
from shopify.models import (
    CreateOrderArgs,
    CreateReturnArgs,
    GetReturnArgs,
    ListReturnsArgs,
    LooseCustomer,
    LooseDiscountCode,
    LooseGiftCard,
    MoneyV2,
    UpdateCartArgs,
    UpdateReturnArgs,
)
from shopify.tools.cart import handle_update_cart
from shopify.tools.orders import handle_create_order
from shopify.tools.reviews_returns import (
    handle_create_return,
    handle_get_return,
    handle_list_returns,
    handle_update_return,
)


def _return_args(**data: object) -> CreateReturnArgs:
    return CreateReturnArgs.model_validate(data)


@pytest.fixture
def shopify_data(tmp_path):
    data_file = tmp_path / "shopify_data.json"
    data_file.write_text(
        json.dumps(
            {
                "products": {
                    "product-1": {
                        "id": "product-1",
                        "title": "Widget",
                        "variants": [
                            {
                                "id": "variant-1",
                                "title": "Default",
                                "price": {"amount": "25.00", "currencyCode": "USD"},
                                "availableForSale": True,
                            }
                        ],
                    },
                },
                "carts": {
                    "cart-1": {
                        "id": "cart-1",
                        "checkoutUrl": "https://shop.example.com/checkout/cart-1",
                        "createdAt": "2024-01-01T00:00:00Z",
                        "updatedAt": "2024-01-01T00:00:00Z",
                        "lines": [
                            {
                                "id": "line-1",
                                "quantity": 3,
                                "merchandise": {
                                    "id": "variant-1",
                                    "title": "Default",
                                    "product": {"id": "product-1", "title": "Widget"},
                                    "price": {"amount": "25.00", "currencyCode": "USD"},
                                },
                                "cost": {
                                    "amountPerQuantity": {"amount": "25.00", "currencyCode": "USD"},
                                    "subtotalAmount": {"amount": "75.00", "currencyCode": "USD"},
                                    "totalAmount": {"amount": "75.00", "currencyCode": "USD"},
                                },
                                "attributes": [],
                                "discountAllocations": [],
                            }
                        ],
                        "cost": {
                            "subtotalAmount": {"amount": "75.00", "currencyCode": "USD"},
                            "totalAmount": {"amount": "75.00", "currencyCode": "USD"},
                            "checkoutChargeAmount": {"amount": "75.00", "currencyCode": "USD"},
                        },
                        "buyerIdentity": {"email": "alice@example.com"},
                        "totalQuantity": 3,
                    },
                },
                "orders": {},
                "customers": {},
                "collections": {},
                "reviews": {},
                "returns": {},
                "shipping_methods": {
                    "standard": {
                        "id": "standard",
                        "title": "Standard Shipping",
                        "price": {"amount": "5.99", "currencyCode": "USD"},
                        "estimatedDays": "5-7 business days",
                        "active": True,
                    },
                },
                "policies": [],
                "counters": {
                    "cart_id": 1000,
                    "line_id": 1000,
                    "order_id": 2000,
                    "line_item_id": 3000,
                    "customer_id": 4000,
                    "collection_id": 5000,
                    "review_id": 6000,
                    "return_id": 7000,
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


@pytest.fixture
def order_with_items():
    """Create an order to test returns against."""
    result = handle_create_order(
        CreateOrderArgs.model_validate(
            {
                "cart_id": "cart-1",
                "payment_method": {
                    "type": "credit_card",
                    "card_number": "4111111111111111",
                    "cvv": "123",
                    "expiry": "12/26",
                },
                "shipping_address": {"address1": "123 Main St", "city": "Springfield", "countryCode": "US"},
                "billing_address": {"address1": "123 Main St", "city": "Springfield", "countryCode": "US"},
                "shipping_method_id": "standard",
            }
        )
    )
    return result["order"]


class TestCreateReturn:
    def test_create_return(self, order_with_items):
        order = order_with_items
        line_item_id = order["lineItems"][0]["id"]

        result = handle_create_return(
            _return_args(
                order_id=order["id"],
                line_items=[{"orderLineItemId": line_item_id, "quantity": 1, "reason": "defective"}],
                reason="Item was defective",
            )
        )
        assert result["userErrors"] == []
        ret = result["return"]
        assert ret["id"].startswith("gid://shopify/Return/")
        assert ret["status"] == "REQUESTED"
        assert ret["orderId"] == order["id"]
        assert len(ret["lineItems"]) == 1
        assert ret["lineItems"][0]["quantity"] == 1
        assert ret["refundAmount"]["amount"] == "25.00"

    def test_create_partial_return(self, order_with_items):
        order = order_with_items
        line_item_id = order["lineItems"][0]["id"]

        result = handle_create_return(
            _return_args(
                order_id=order["id"],
                line_items=[{"orderLineItemId": line_item_id, "quantity": 2}],
            )
        )
        ret = result["return"]
        assert ret["refundAmount"]["amount"] == "50.00"  # 2 * 25.00

    def test_create_return_uses_discounted_effective_line_price(self, order_with_items):
        order = order_with_items
        state = shopify_state.get_state()
        state.orders[order["id"]].totalPrice = MoneyV2(amount="60.74", currencyCode="USD")
        line_item_id = order["lineItems"][0]["id"]

        result = handle_create_return(
            _return_args(
                order_id=order["id"],
                line_items=[{"orderLineItemId": line_item_id, "quantity": 1}],
            )
        )

        assert result["return"]["refundAmount"]["amount"] == "18.25"

    def test_create_return_nonexistent_order(self):
        result = handle_create_return(
            _return_args(
                order_id="nonexistent",
                line_items=[{"orderLineItemId": "x", "quantity": 1}],
            )
        )
        assert result["return"] is None
        assert len(result["userErrors"]) == 1

    def test_create_return_cancelled_order(self, order_with_items):
        order = order_with_items
        # Cancel the order first
        from shopify.models import CancelOrderArgs
        from shopify.tools.orders import handle_cancel_order

        handle_cancel_order(CancelOrderArgs(order_id=order["id"]))

        result = handle_create_return(
            _return_args(
                order_id=order["id"],
                line_items=[{"orderLineItemId": order["lineItems"][0]["id"], "quantity": 1}],
            )
        )
        assert result["return"] is None
        assert "cancelled" in result["userErrors"][0]["message"]

    def test_create_return_invalid_line_item(self, order_with_items):
        result = handle_create_return(
            _return_args(
                order_id=order_with_items["id"],
                line_items=[{"orderLineItemId": "nonexistent", "quantity": 1}],
            )
        )
        assert result["return"] is None
        assert len(result["userErrors"]) == 1

    def test_create_return_rejects_quantity_above_ordered(self, order_with_items):
        order = order_with_items
        line_item_id = order["lineItems"][0]["id"]

        result = handle_create_return(
            _return_args(
                order_id=order["id"],
                line_items=[{"orderLineItemId": line_item_id, "quantity": 4}],
            )
        )

        assert result["return"] is None
        assert "remaining returnable quantity" in result["userErrors"][0]["message"]

    def test_create_return_rejects_cumulative_over_return(self, order_with_items):
        order = order_with_items
        line_item_id = order["lineItems"][0]["id"]
        handle_create_return(
            _return_args(
                order_id=order["id"],
                line_items=[{"orderLineItemId": line_item_id, "quantity": 2}],
            )
        )

        result = handle_create_return(
            _return_args(
                order_id=order["id"],
                line_items=[{"orderLineItemId": line_item_id, "quantity": 2}],
            )
        )

        assert result["return"] is None
        assert "remaining returnable quantity 1" in result["userErrors"][0]["message"]

    def test_rejected_returns_do_not_consume_returnable_quantity(self, order_with_items):
        order = order_with_items
        line_item_id = order["lineItems"][0]["id"]
        create = handle_create_return(
            _return_args(
                order_id=order["id"],
                line_items=[{"orderLineItemId": line_item_id, "quantity": 3}],
            )
        )
        handle_update_return(UpdateReturnArgs(return_id=create["return"]["id"], status="REJECTED"))

        result = handle_create_return(
            _return_args(
                order_id=order["id"],
                line_items=[{"orderLineItemId": line_item_id, "quantity": 3}],
            )
        )

        assert result["userErrors"] == []


class TestGetReturn:
    def test_get_existing(self, order_with_items):
        order = order_with_items
        create = handle_create_return(
            _return_args(
                order_id=order["id"],
                line_items=[{"orderLineItemId": order["lineItems"][0]["id"], "quantity": 1}],
            )
        )
        return_id = create["return"]["id"]

        result = handle_get_return(GetReturnArgs(return_id=return_id))
        assert result["userErrors"] == []
        assert result["return"]["id"] == return_id

    def test_get_nonexistent(self):
        result = handle_get_return(GetReturnArgs(return_id="nonexistent"))
        assert result["return"] is None


class TestListReturns:
    def test_list_empty(self):
        result = handle_list_returns(ListReturnsArgs())
        assert result["totalCount"] == 0

    def test_list_with_returns(self, order_with_items):
        order = order_with_items
        li_id = order["lineItems"][0]["id"]
        handle_create_return(_return_args(order_id=order["id"], line_items=[{"orderLineItemId": li_id, "quantity": 1}]))

        result = handle_list_returns(ListReturnsArgs())
        assert result["totalCount"] == 1

    def test_list_filter_by_order(self, order_with_items):
        order = order_with_items
        li_id = order["lineItems"][0]["id"]
        handle_create_return(_return_args(order_id=order["id"], line_items=[{"orderLineItemId": li_id, "quantity": 1}]))

        result = handle_list_returns(ListReturnsArgs(order_id=order["id"]))
        assert result["totalCount"] == 1

        result = handle_list_returns(ListReturnsArgs(order_id="other-order"))
        assert result["totalCount"] == 0

    def test_list_filter_by_status(self, order_with_items):
        order = order_with_items
        li_id = order["lineItems"][0]["id"]
        handle_create_return(_return_args(order_id=order["id"], line_items=[{"orderLineItemId": li_id, "quantity": 1}]))

        result = handle_list_returns(ListReturnsArgs(status="REQUESTED"))
        assert result["totalCount"] == 1

        result = handle_list_returns(ListReturnsArgs(status="APPROVED"))
        assert result["totalCount"] == 0


class TestUpdateReturn:
    def test_update_status(self, order_with_items):
        order = order_with_items
        create = handle_create_return(
            _return_args(
                order_id=order["id"],
                line_items=[{"orderLineItemId": order["lineItems"][0]["id"], "quantity": 1}],
            )
        )
        return_id = create["return"]["id"]

        result = handle_update_return(UpdateReturnArgs(return_id=return_id, status="APPROVED"))
        assert result["return"]["status"] == "APPROVED"

    def test_update_to_refunded_updates_order(self, order_with_items):
        order = order_with_items
        create = handle_create_return(
            _return_args(
                order_id=order["id"],
                line_items=[{"orderLineItemId": order["lineItems"][0]["id"], "quantity": 1}],
            )
        )
        return_id = create["return"]["id"]

        # Partial return (25 of 75) → PARTIALLY_REFUNDED
        handle_update_return(UpdateReturnArgs(return_id=return_id, status="REFUNDED"))

        from shopify.models import GetOrderArgs
        from shopify.tools.orders import handle_get_order

        order_result = handle_get_order(GetOrderArgs(order_id=order["id"]))
        assert order_result["order"]["financialStatus"] == "PARTIALLY_REFUNDED"

    def test_partial_refund_reverses_proportional_checkout_effects(self):
        state = shopify_state.get_state()
        state.loyalty_program.enabled = True
        state.customers["cust-1"] = LooseCustomer.model_validate(
            {
                "id": "cust-1",
                "email": "alice@example.com",
                "createdAt": "2024-01-01T00:00:00Z",
                "updatedAt": "2024-01-01T00:00:00Z",
                "ordersCount": 0,
                "totalSpent": {"amount": "0.00", "currencyCode": "USD"},
                "pointsBalance": 1000,
                "lifetimePoints": 1000,
            }
        )
        state.gift_cards["GIFT1234"] = LooseGiftCard.model_validate(
            {
                "id": "gid://shopify/GiftCard/GIFT1234",
                "code": "GIFT1234",
                "balance": {"amount": "20.00", "currencyCode": "USD"},
                "initialValue": {"amount": "20.00", "currencyCode": "USD"},
                "active": True,
            }
        )
        handle_update_cart(UpdateCartArgs(cart_id="cart-1", gift_card_codes=["GIFT1234"]))
        order = handle_create_order(
            CreateOrderArgs.model_validate(
                {
                    "cart_id": "cart-1",
                    "payment_method": {
                        "type": "credit_card",
                        "card_number": "4111111111111111",
                        "cvv": "123",
                        "expiry": "12/26",
                    },
                    "shipping_address": {"address1": "123 Main St", "city": "Springfield", "countryCode": "US"},
                    "billing_address": {"address1": "123 Main St", "city": "Springfield", "countryCode": "US"},
                    "shipping_method_id": "standard",
                    "email": "alice@example.com",
                    "redeem_points": 100,
                }
            )
        )["order"]
        ret = handle_create_return(
            _return_args(
                order_id=order["id"],
                line_items=[{"orderLineItemId": order["lineItems"][0]["id"], "quantity": 1}],
            )
        )["return"]

        result = handle_update_return(UpdateReturnArgs(return_id=ret["id"], status="REFUNDED"))

        customer = state.customers["cust-1"]
        assert result["userErrors"] == []
        assert result["return"]["reversedEffects"]["giftCardAmount"] == "6.17"
        assert result["return"]["reversedEffects"]["customerSpendAmount"] == "18.50"
        assert state.gift_cards["GIFT1234"].balance.amount == "6.17"
        assert customer.totalSpent is not None
        assert customer.totalSpent.amount == "41.49"
        assert customer.ordersCount == 1
        assert customer.pointsBalance == 983
        assert customer.lifetimePoints == 1050

    def test_full_refund_updates_order(self, order_with_items):
        order = order_with_items
        create = handle_create_return(
            _return_args(
                order_id=order["id"],
                line_items=[{"orderLineItemId": order["lineItems"][0]["id"], "quantity": 3}],
            )
        )
        return_id = create["return"]["id"]

        # Full item return (75 of 80.99 total including shipping) → PARTIALLY_REFUNDED
        # Returning all items doesn't refund shipping cost
        handle_update_return(UpdateReturnArgs(return_id=return_id, status="REFUNDED"))

        from shopify.models import GetOrderArgs
        from shopify.tools.orders import handle_get_order

        order_result = handle_get_order(GetOrderArgs(order_id=order["id"]))
        assert order_result["order"]["financialStatus"] == "PARTIALLY_REFUNDED"

    def test_cumulative_partial_refunds_can_fully_refund_order(self, order_with_items):
        order = order_with_items
        line_item_id = order["lineItems"][0]["id"]
        first = handle_create_return(
            _return_args(order_id=order["id"], line_items=[{"orderLineItemId": line_item_id, "quantity": 1}])
        )["return"]
        second = handle_create_return(
            _return_args(order_id=order["id"], line_items=[{"orderLineItemId": line_item_id, "quantity": 2}])
        )["return"]
        state = shopify_state.get_state()
        state.returns[first["id"]].refundAmount = MoneyV2(amount="40.00", currencyCode="USD")
        state.returns[second["id"]].refundAmount = MoneyV2(amount="40.99", currencyCode="USD")

        handle_update_return(UpdateReturnArgs(return_id=first["id"], status="REFUNDED"))
        handle_update_return(UpdateReturnArgs(return_id=second["id"], status="REFUNDED"))

        from shopify.models import GetOrderArgs
        from shopify.tools.orders import handle_get_order

        order_result = handle_get_order(GetOrderArgs(order_id=order["id"]))
        assert order_result["order"]["financialStatus"] == "REFUNDED"

    def test_full_refund_reverses_checkout_side_effects(self):
        state = shopify_state.get_state()
        state.customers["cust-1"] = LooseCustomer.model_validate(
            {
                "id": "cust-1",
                "email": "alice@example.com",
                "createdAt": "2024-01-01T00:00:00Z",
                "updatedAt": "2024-01-01T00:00:00Z",
                "ordersCount": 0,
                "totalSpent": {"amount": "0.00", "currencyCode": "USD"},
            }
        )
        state.discount_codes["SAVE5"] = LooseDiscountCode.model_validate(
            {
                "id": "SAVE5",
                "code": "SAVE5",
                "discountType": "FIXED_AMOUNT",
                "value": "5.00",
                "usageCount": 0,
                "active": True,
                "createdAt": "2024-01-01T00:00:00Z",
                "updatedAt": "2024-01-01T00:00:00Z",
            }
        )
        order = handle_create_order(
            CreateOrderArgs.model_validate(
                {
                    "cart_id": "cart-1",
                    "payment_method": {
                        "type": "credit_card",
                        "card_number": "4111111111111111",
                        "cvv": "123",
                        "expiry": "12/26",
                    },
                    "shipping_address": {"address1": "123 Main St", "city": "Springfield", "countryCode": "US"},
                    "billing_address": {"address1": "123 Main St", "city": "Springfield", "countryCode": "US"},
                    "shipping_method_id": "standard",
                    "discount_code": "SAVE5",
                    "email": "alice@example.com",
                }
            )
        )["order"]
        line_item_id = order["lineItems"][0]["id"]
        ret = handle_create_return(
            _return_args(order_id=order["id"], line_items=[{"orderLineItemId": line_item_id, "quantity": 3}])
        )["return"]
        state.returns[ret["id"]].refundAmount = MoneyV2(
            amount=order["totalPrice"]["amount"],
            currencyCode=order["totalPrice"]["currencyCode"],
        )

        result = handle_update_return(UpdateReturnArgs(return_id=ret["id"], status="REFUNDED"))

        customer = state.customers["cust-1"]
        assert result["userErrors"] == []
        assert state.orders[order["id"]]["sideEffectsReversedAt"] is not None
        assert state.discount_codes["SAVE5"].usageCount == 0
        assert customer.ordersCount == 0
        assert customer.totalSpent is not None
        assert customer.totalSpent.amount == "0.00"

    def test_update_invalid_status(self, order_with_items):
        order = order_with_items
        create = handle_create_return(
            _return_args(
                order_id=order["id"],
                line_items=[{"orderLineItemId": order["lineItems"][0]["id"], "quantity": 1}],
            )
        )
        return_id = create["return"]["id"]

        with pytest.raises(ValidationError):
            UpdateReturnArgs.model_validate({"return_id": return_id, "status": "INVALID"})

    def test_update_nonexistent(self):
        result = handle_update_return(UpdateReturnArgs(return_id="nonexistent", status="APPROVED"))
        assert result["return"] is None

    def test_update_note(self, order_with_items):
        order = order_with_items
        create = handle_create_return(
            _return_args(
                order_id=order["id"],
                line_items=[{"orderLineItemId": order["lineItems"][0]["id"], "quantity": 1}],
            )
        )
        return_id = create["return"]["id"]

        result = handle_update_return(UpdateReturnArgs(return_id=return_id, note="Customer called"))
        assert result["return"]["note"] == "Customer called"

    def test_refunded_return_is_terminal(self, order_with_items):
        order = order_with_items
        create = handle_create_return(
            _return_args(
                order_id=order["id"],
                line_items=[{"orderLineItemId": order["lineItems"][0]["id"], "quantity": 1}],
            )
        )
        return_id = create["return"]["id"]

        handle_update_return(UpdateReturnArgs(return_id=return_id, status="REFUNDED"))
        result = handle_update_return(UpdateReturnArgs(return_id=return_id, status="APPROVED"))

        assert result["return"] is None
        assert "Cannot transition" in result["userErrors"][0]["message"]
