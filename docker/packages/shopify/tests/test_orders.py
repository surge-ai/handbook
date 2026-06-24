"""Tests for order management tools."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from shopify import state as shopify_state
from shopify.models import (
    AppliedGiftCard,
    CancelOrderArgs,
    CreateOrderArgs,
    GetOrderArgs,
    ListOrdersArgs,
    LooseCustomer,
    LooseGiftCard,
    LooseShippingMethod,
    MoneyV2,
    UpdateCartArgs,
    UpdateOrderArgs,
)
from shopify.tools.cart import handle_update_cart
from shopify.tools.orders import (
    handle_cancel_order,
    handle_create_order,
    handle_get_order,
    handle_list_orders,
    handle_update_order,
)

# Reusable test fixtures for required fields
VALID_CC = {"type": "credit_card", "card_number": "4111111111111111", "cvv": "123", "expiry": "12/26"}
VALID_PAYPAL = {"type": "paypal", "email": "buyer@example.com"}
VALID_GPAY = {"type": "google_pay", "email": "buyer@gmail.com"}
VALID_APPLEPAY = {"type": "apple_pay", "email": "buyer@icloud.com"}
VALID_ADDR = {
    "firstName": "Alice",
    "lastName": "Smith",
    "address1": "123 Main St",
    "city": "Springfield",
    "countryCode": "US",
    "zip": "62701",
}


def _order_args(**overrides: Any) -> CreateOrderArgs:
    """Build CreateOrderArgs with sensible defaults."""
    return CreateOrderArgs.model_validate(
        {
            "cart_id": overrides.get("cart_id", "cart-1"),
            "payment_method": overrides.get("payment_method", VALID_CC),
            "shipping_address": overrides.get("shipping_address", VALID_ADDR),
            "billing_address": overrides.get("billing_address", VALID_ADDR),
            "shipping_method_id": overrides.get("shipping_method_id", "standard"),
            "discount_code": overrides.get("discount_code"),
            "email": overrides.get("email"),
            "phone": overrides.get("phone"),
            "note": overrides.get("note"),
            "tags": overrides.get("tags"),
        }
    )


@pytest.fixture
def shopify_data(tmp_path):
    """Seed state with a product and a cart containing items."""
    data_file = tmp_path / "shopify_data.json"
    data_file.write_text(
        json.dumps(
            {
                "products": {
                    "product-1": {
                        "id": "product-1",
                        "title": "Test Widget",
                        "handle": "test-widget",
                        "variants": [
                            {
                                "id": "variant-1",
                                "title": "Small",
                                "price": {"amount": "25.00", "currencyCode": "USD"},
                                "availableForSale": True,
                                "sku": "WIDGET-S",
                            },
                        ],
                    }
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
                                "quantity": 2,
                                "merchandise": {
                                    "id": "variant-1",
                                    "title": "Small",
                                    "product": {"id": "product-1", "title": "Test Widget", "handle": "test-widget"},
                                    "price": {"amount": "25.00", "currencyCode": "USD"},
                                    "selectedOptions": [{"name": "Size", "value": "Small"}],
                                },
                                "cost": {
                                    "amountPerQuantity": {"amount": "25.00", "currencyCode": "USD"},
                                    "subtotalAmount": {"amount": "50.00", "currencyCode": "USD"},
                                    "totalAmount": {"amount": "50.00", "currencyCode": "USD"},
                                },
                                "attributes": [],
                                "discountAllocations": [],
                            }
                        ],
                        "cost": {
                            "subtotalAmount": {"amount": "50.00", "currencyCode": "USD"},
                            "totalAmount": {"amount": "50.00", "currencyCode": "USD"},
                            "checkoutChargeAmount": {"amount": "50.00", "currencyCode": "USD"},
                        },
                        "buyerIdentity": {"email": "alice@example.com", "phone": "+1234567890"},
                        "note": "Please gift wrap",
                        "totalQuantity": 2,
                    },
                    "cart-empty": {
                        "id": "cart-empty",
                        "checkoutUrl": "https://shop.example.com/checkout/cart-empty",
                        "createdAt": "2024-01-01T00:00:00Z",
                        "updatedAt": "2024-01-01T00:00:00Z",
                        "lines": [],
                        "cost": {
                            "subtotalAmount": {"amount": "0.00", "currencyCode": "USD"},
                            "totalAmount": {"amount": "0.00", "currencyCode": "USD"},
                            "checkoutChargeAmount": {"amount": "0.00", "currencyCode": "USD"},
                        },
                        "buyerIdentity": {},
                        "totalQuantity": 0,
                    },
                },
                "orders": {},
                "customers": {},
                "collections": {},
                "reviews": {},
                "returns": {},
                "discount_codes": {
                    "dc-1": {
                        "id": "dc-1",
                        "code": "SAVE20",
                        "discountType": "PERCENTAGE",
                        "value": "20",
                        "minimumPurchase": None,
                        "usageLimit": None,
                        "usageCount": 0,
                        "productIds": None,
                        "active": True,
                        "createdAt": "2024-01-01T00:00:00Z",
                        "updatedAt": "2024-01-01T00:00:00Z",
                    },
                    "dc-2": {
                        "id": "dc-2",
                        "code": "FREESHIP",
                        "discountType": "FREE_SHIPPING",
                        "value": "0",
                        "minimumPurchase": None,
                        "usageLimit": None,
                        "usageCount": 0,
                        "productIds": None,
                        "active": True,
                        "createdAt": "2024-01-01T00:00:00Z",
                        "updatedAt": "2024-01-01T00:00:00Z",
                    },
                    "dc-3": {
                        "id": "dc-3",
                        "code": "WIDGET10",
                        "discountType": "FIXED_AMOUNT",
                        "value": "10.00",
                        "minimumPurchase": None,
                        "usageLimit": 1,
                        "usageCount": 0,
                        "productIds": ["product-1"],
                        "active": True,
                        "createdAt": "2024-01-01T00:00:00Z",
                        "updatedAt": "2024-01-01T00:00:00Z",
                    },
                    "dc-4": {
                        "id": "dc-4",
                        "code": "INACTIVE",
                        "discountType": "PERCENTAGE",
                        "value": "50",
                        "minimumPurchase": None,
                        "usageLimit": None,
                        "usageCount": 0,
                        "productIds": None,
                        "active": False,
                        "createdAt": "2024-01-01T00:00:00Z",
                        "updatedAt": "2024-01-01T00:00:00Z",
                    },
                },
                "gift_cards": {
                    "GIFT1234": {
                        "id": "gid://shopify/GiftCard/GIFT1234",
                        "code": "GIFT1234",
                        "balance": {"amount": "20.00", "currencyCode": "USD"},
                        "initialValue": {"amount": "20.00", "currencyCode": "USD"},
                        "active": True,
                    },
                    "EMPTYCARD": {
                        "id": "gid://shopify/GiftCard/EMPTYCARD",
                        "code": "EMPTYCARD",
                        "balance": {"amount": "0.00", "currencyCode": "USD"},
                        "active": True,
                    },
                },
                "shipping_methods": {
                    "standard": {
                        "id": "standard",
                        "title": "Standard Shipping",
                        "price": {"amount": "5.99", "currencyCode": "USD"},
                        "estimatedDays": "5-7 business days",
                        "active": True,
                    },
                    "express": {
                        "id": "express",
                        "title": "Express Shipping",
                        "price": {"amount": "14.99", "currencyCode": "USD"},
                        "estimatedDays": "1-2 business days",
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
                    "product_id": 8000,
                    "variant_id": 9000,
                    "discount_id": 10000,
                    "policy_id": 11000,
                },
            }
        )
    )
    return data_file


@pytest.fixture(autouse=True)
def _patch_state(shopify_data, monkeypatch):
    """Reset state for each test."""
    monkeypatch.setattr(shopify_state, "_STATE_FILE", shopify_data)
    shopify_state._current_state = None
    shopify_state._stores.clear()
    shopify_state._active_store_id = "default"
    shopify_state.load_state()


class TestCreateOrder:
    def test_create_order_with_credit_card(self):
        result = handle_create_order(_order_args())
        assert result["userErrors"] == []
        order = result["order"]
        assert order["id"].startswith("gid://shopify/Order/")
        assert order["financialStatus"] == "PAID"
        assert order["fulfillmentStatus"] == "UNFULFILLED"
        assert order["email"] == "alice@example.com"
        assert order["paymentMethod"]["type"] == "credit_card"
        assert order["paymentMethod"]["last4"] == "1111"
        assert order["paymentMethod"]["brand"] == "visa"
        assert order["shippingAddress"]["address1"] == "123 Main St"
        assert order["billingAddress"]["city"] == "Springfield"
        assert order["shippingMethod"]["id"] == "standard"
        assert order["shippingPrice"]["amount"] == "5.99"
        assert order["totalPrice"]["amount"] == "55.99"  # 50.00 + 5.99 shipping

    def test_create_order_with_paypal(self):
        result = handle_create_order(_order_args(payment_method=VALID_PAYPAL))
        order = result["order"]
        assert order["paymentMethod"]["type"] == "paypal"
        assert order["paymentMethod"]["email"] == "buyer@example.com"
        assert order["financialStatus"] == "PAID"

    def test_create_order_with_google_pay(self):
        result = handle_create_order(_order_args(payment_method=VALID_GPAY))
        assert result["order"]["paymentMethod"]["type"] == "google_pay"

    def test_create_order_with_apple_pay(self):
        result = handle_create_order(_order_args(payment_method=VALID_APPLEPAY))
        assert result["order"]["paymentMethod"]["type"] == "apple_pay"

    def test_mastercard_brand_detection(self):
        mc = {"type": "credit_card", "card_number": "5500000000000004", "cvv": "123", "expiry": "12/26"}
        result = handle_create_order(_order_args(payment_method=mc))
        assert result["order"]["paymentMethod"]["brand"] == "mastercard"

    def test_amex_brand_detection(self):
        amex = {"type": "credit_card", "card_number": "371449635398431", "cvv": "1234", "expiry": "12/26"}
        result = handle_create_order(_order_args(payment_method=amex))
        assert result["order"]["paymentMethod"]["brand"] == "amex"

    def test_card_number_with_spaces(self):
        spaced = {"type": "credit_card", "card_number": "4111 1111 1111 1111", "cvv": "123", "expiry": "12/26"}
        result = handle_create_order(_order_args(payment_method=spaced))
        assert result["order"]["paymentMethod"]["last4"] == "1111"

    def test_card_number_with_dashes(self):
        dashed = {"type": "credit_card", "card_number": "4111-1111-1111-1111", "cvv": "123", "expiry": "12/26"}
        result = handle_create_order(_order_args(payment_method=dashed))
        assert result["order"]["paymentMethod"]["last4"] == "1111"

    def test_invalid_card_number_too_short(self):
        bad = {"type": "credit_card", "card_number": "411111", "cvv": "123", "expiry": "12/26"}
        result = handle_create_order(_order_args(payment_method=bad))
        assert result["order"] is None
        assert any("card_number" in e["field"] for e in result["userErrors"])

    def test_invalid_cvv(self):
        bad = {"type": "credit_card", "card_number": "4111111111111111", "cvv": "12", "expiry": "12/26"}
        result = handle_create_order(_order_args(payment_method=bad))
        assert result["order"] is None
        assert any("cvv" in e["field"] for e in result["userErrors"])

    def test_invalid_expiry(self):
        bad = {"type": "credit_card", "card_number": "4111111111111111", "cvv": "123", "expiry": "2026-12"}
        result = handle_create_order(_order_args(payment_method=bad))
        assert result["order"] is None
        assert any("expiry" in e["field"] for e in result["userErrors"])

    def test_invalid_payment_type(self):
        with pytest.raises(ValidationError):
            _order_args(payment_method={"type": "bitcoin"})

    def test_paypal_missing_email(self):
        with pytest.raises(ValidationError):
            _order_args(payment_method={"type": "paypal"})

    @pytest.mark.parametrize("email", ["@", "@@", " @", "a@", "buyer@example"])
    def test_digital_wallet_invalid_email(self, email):
        with pytest.raises(ValidationError):
            _order_args(payment_method={"type": "paypal", "email": email})

    def test_missing_shipping_address_field(self):
        bad_addr = {"firstName": "Alice"}  # missing address1, city, countryCode
        result = handle_create_order(_order_args(shipping_address=bad_addr))
        assert result["order"] is None
        assert any("shipping_address" in e["field"] for e in result["userErrors"])

    def test_missing_billing_address_field(self):
        bad_addr = {"address1": "123 Main St"}  # missing city, countryCode
        result = handle_create_order(_order_args(billing_address=bad_addr))
        assert result["order"] is None
        assert any("billing_address" in e["field"] for e in result["userErrors"])

    def test_create_order_empty_cart(self):
        result = handle_create_order(_order_args(cart_id="cart-empty"))
        assert result["order"] is None
        assert "empty" in result["userErrors"][0]["message"]

    def test_create_order_nonexistent_cart(self):
        result = handle_create_order(_order_args(cart_id="nonexistent"))
        assert result["order"] is None

    def test_create_order_overrides_buyer_identity(self):
        result = handle_create_order(_order_args(email="bob@example.com", phone="+9999999999"))
        order = result["order"]
        assert order["email"] == "bob@example.com"
        assert order["phone"] == "+9999999999"

    def test_create_order_with_tags(self):
        result = handle_create_order(_order_args(tags=["vip", "rush"]))
        assert result["order"]["tags"] == ["vip", "rush"]

    def test_create_order_generates_line_item_ids(self):
        result = handle_create_order(_order_args())
        line = result["order"]["lineItems"][0]
        assert line["id"].startswith("gid://shopify/OrderLineItem/")


class TestGetOrder:
    def test_get_existing_order(self):
        create_result = handle_create_order(_order_args())
        order_id = create_result["order"]["id"]
        result = handle_get_order(GetOrderArgs(order_id=order_id))
        assert result["userErrors"] == []
        assert result["order"]["id"] == order_id

    def test_get_nonexistent_order(self):
        result = handle_get_order(GetOrderArgs(order_id="nonexistent"))
        assert result["order"] is None


class TestListOrders:
    def test_list_empty(self):
        result = handle_list_orders(ListOrdersArgs())
        assert result["totalCount"] == 0

    def test_list_with_orders(self):
        handle_create_order(_order_args())
        result = handle_list_orders(ListOrdersArgs())
        assert result["totalCount"] == 1

    def test_list_filter_by_financial_status(self):
        handle_create_order(_order_args())
        result = handle_list_orders(ListOrdersArgs(status="PAID"))
        assert result["totalCount"] == 1
        result = handle_list_orders(ListOrdersArgs(status="PENDING"))
        assert result["totalCount"] == 0

    def test_list_filter_by_fulfillment_status(self):
        handle_create_order(_order_args())
        result = handle_list_orders(ListOrdersArgs(status="UNFULFILLED"))
        assert result["totalCount"] == 1

    def test_list_pagination(self):
        handle_create_order(_order_args())
        handle_create_order(_order_args())
        result = handle_list_orders(ListOrdersArgs(limit=1))
        assert len(result["orders"]) == 1
        assert result["totalCount"] == 2
        assert result["pageInfo"]["hasNextPage"] is True


class TestUpdateOrder:
    def test_update_financial_status_to_paid(self):
        create_result = handle_create_order(_order_args())
        order_id = create_result["order"]["id"]
        shopify_state.get_state().orders[order_id].financialStatus = "PENDING"
        result = handle_update_order(UpdateOrderArgs(order_id=order_id, financial_status="PAID"))
        assert result["userErrors"] == []
        assert result["order"]["financialStatus"] == "PAID"

    @pytest.mark.parametrize("status", ["VOIDED", "REFUNDED", "PARTIALLY_REFUNDED"])
    def test_update_financial_status_rejects_side_effect_statuses(self, status):
        create_result = handle_create_order(_order_args())
        order_id = create_result["order"]["id"]
        result = handle_update_order(UpdateOrderArgs(order_id=order_id, financial_status=status))
        assert result["order"] is None
        assert result["userErrors"][0]["field"] == "financial_status"
        assert shopify_state.get_state().orders[order_id].financialStatus == "PAID"

    @pytest.mark.parametrize("status", ["VOIDED", "REFUNDED", "PARTIALLY_REFUNDED"])
    def test_update_financial_status_rejects_leaving_side_effect_statuses(self, status):
        create_result = handle_create_order(_order_args())
        order_id = create_result["order"]["id"]
        shopify_state.get_state().orders[order_id].financialStatus = status
        result = handle_update_order(UpdateOrderArgs(order_id=order_id, financial_status="PAID"))
        assert result["order"] is None
        assert result["userErrors"][0]["field"] == "financial_status"
        assert shopify_state.get_state().orders[order_id].financialStatus == status

    def test_update_fulfillment_status(self):
        create_result = handle_create_order(_order_args())
        order_id = create_result["order"]["id"]
        result = handle_update_order(UpdateOrderArgs(order_id=order_id, fulfillment_status="FULFILLED"))
        assert result["order"]["fulfillmentStatus"] == "FULFILLED"

    def test_update_invalid_status(self):
        create_result = handle_create_order(_order_args())
        order_id = create_result["order"]["id"]
        with pytest.raises(ValidationError):
            UpdateOrderArgs.model_validate({"order_id": order_id, "financial_status": "INVALID"})

    def test_update_note_and_tags(self):
        create_result = handle_create_order(_order_args())
        order_id = create_result["order"]["id"]
        result = handle_update_order(UpdateOrderArgs(order_id=order_id, note="Updated note", tags=["priority"]))
        assert result["order"]["note"] == "Updated note"
        assert result["order"]["tags"] == ["priority"]

    def test_update_rejects_customer_email_reassignment(self):
        create_result = handle_create_order(_order_args())
        order_id = create_result["order"]["id"]
        result = handle_update_order(UpdateOrderArgs(order_id=order_id, email="new@example.com"))
        assert result["order"] is None
        assert result["userErrors"][0]["field"] == "email"
        assert shopify_state.get_state().orders[order_id].email == "alice@example.com"

    def test_update_rejects_email_reassignment_before_any_mutation(self):
        create_result = handle_create_order(_order_args())
        order_id = create_result["order"]["id"]
        order = shopify_state.get_state().orders[order_id]
        original_updated_at = order.updatedAt

        result = handle_update_order(
            UpdateOrderArgs(
                order_id=order_id,
                financial_status="PAID",
                fulfillment_status="FULFILLED",
                note="Updated note",
                tags=["urgent"],
                email="new@example.com",
            )
        )

        assert result["order"] is None
        assert result["userErrors"][0]["field"] == "email"
        assert order.financialStatus == "PAID"
        assert order.fulfillmentStatus == "UNFULFILLED"
        assert order.trackingNumber is None
        assert order.trackingUrl is None
        assert order.note == "Please gift wrap"
        assert order.tags == []
        assert order.email == "alice@example.com"
        assert order.updatedAt == original_updated_at

    def test_update_nonexistent_order(self):
        result = handle_update_order(UpdateOrderArgs(order_id="nonexistent"))
        assert result["order"] is None


class TestCancelOrder:
    def test_cancel_order(self):
        create_result = handle_create_order(_order_args())
        order_id = create_result["order"]["id"]
        result = handle_cancel_order(CancelOrderArgs(order_id=order_id))
        assert result["userErrors"] == []
        assert result["order"]["cancelledAt"] is not None
        assert result["order"]["financialStatus"] == "REFUNDED"

    def test_cancel_pending_order_voids_payment(self):
        create_result = handle_create_order(_order_args())
        order_id = create_result["order"]["id"]
        shopify_state.get_state().orders[order_id].financialStatus = "PENDING"

        result = handle_cancel_order(CancelOrderArgs(order_id=order_id))

        assert result["userErrors"] == []
        assert result["order"]["cancelledAt"] is not None
        assert result["order"]["financialStatus"] == "VOIDED"

    def test_cancel_reverses_checkout_side_effects(self):
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
        handle_update_cart(UpdateCartArgs(cart_id="cart-1", gift_card_codes=["GIFT1234"]))
        create_result = handle_create_order(
            CreateOrderArgs.model_validate(
                {
                    "cart_id": "cart-1",
                    "payment_method": VALID_CC,
                    "shipping_address": VALID_ADDR,
                    "billing_address": VALID_ADDR,
                    "shipping_method_id": "standard",
                    "discount_code": "WIDGET10",
                    "email": "alice@example.com",
                    "redeem_points": 100,
                }
            )
        )
        order_id = create_result["order"]["id"]
        customer = state.customers["cust-1"]
        assert state.discount_codes["dc-3"].usageCount == 1
        assert state.gift_cards["GIFT1234"].balance.amount == "0.00"
        assert customer.ordersCount == 1
        assert customer.totalSpent is not None
        assert customer.totalSpent.amount == "24.99"
        assert customer.pointsBalance == 939
        assert customer.lifetimePoints == 1039

        result = handle_cancel_order(CancelOrderArgs(order_id=order_id))

        assert result["userErrors"] == []
        assert result["order"]["sideEffectsReversedAt"] is not None
        assert state.discount_codes["dc-3"].usageCount == 0
        assert state.gift_cards["GIFT1234"].balance.amount == "20.00"
        assert customer.ordersCount == 0
        assert customer.totalSpent is not None
        assert customer.totalSpent.amount == "0.00"
        assert customer.pointsBalance == 1000
        assert customer.lifetimePoints == 1000

    def test_cancel_with_reason(self):
        create_result = handle_create_order(_order_args())
        order_id = create_result["order"]["id"]
        result = handle_cancel_order(CancelOrderArgs(order_id=order_id, reason="Customer changed mind"))
        assert "Customer changed mind" in result["order"]["note"]

    def test_cancel_already_cancelled(self):
        create_result = handle_create_order(_order_args())
        order_id = create_result["order"]["id"]
        handle_cancel_order(CancelOrderArgs(order_id=order_id))
        result = handle_cancel_order(CancelOrderArgs(order_id=order_id))
        assert result["order"] is None
        assert "already cancelled" in result["userErrors"][0]["message"]

    def test_cancel_nonexistent_order(self):
        result = handle_cancel_order(CancelOrderArgs(order_id="nonexistent"))
        assert result["order"] is None


class TestDiscountApplication:
    def test_percentage_discount(self):
        # SAVE20 = 20% off subtotal ($50) = $10 off → total = 50 - 10 + 5.99 = 45.99
        result = handle_create_order(_order_args(discount_code="SAVE20"))
        order = result["order"]
        assert order["discount"]["code"] == "SAVE20"
        assert order["discountAmount"]["amount"] == "10.00"
        assert order["subtotalPrice"]["amount"] == "50.00"
        assert order["shippingPrice"]["amount"] == "5.99"
        assert order["totalPrice"]["amount"] == "45.99"

    def test_free_shipping_discount(self):
        # FREESHIP removes shipping cost → total = 50 + 0 = 50.00
        result = handle_create_order(_order_args(discount_code="FREESHIP"))
        order = result["order"]
        assert order["shippingPrice"]["amount"] == "0.00"
        assert order["discountAmount"]["amount"] == "5.99"
        assert order["totalPrice"]["amount"] == "50.00"

    def test_product_scoped_fixed_discount(self):
        # WIDGET10 = $10 off product-1 items only → 50 - 10 + 5.99 = 45.99
        result = handle_create_order(_order_args(discount_code="WIDGET10"))
        order = result["order"]
        assert order["discountAmount"]["amount"] == "10.00"
        assert order["totalPrice"]["amount"] == "45.99"

    def test_discount_increments_usage(self):
        handle_create_order(_order_args(discount_code="SAVE20"))
        from shopify.state import get_discount_by_code

        dc = get_discount_by_code("SAVE20")
        assert dc is not None
        assert dc["usageCount"] == 1

    def test_usage_limit_enforced(self):
        # WIDGET10 has usageLimit=1
        handle_create_order(_order_args(discount_code="WIDGET10"))
        result = handle_create_order(_order_args(discount_code="WIDGET10"))
        assert result["order"] is None
        assert "usage limit" in result["userErrors"][0]["message"]

    def test_inactive_discount_rejected(self):
        state = shopify_state.get_state()
        before_order_id = state.counters.order_id
        before_line_item_id = state.counters.line_item_id

        result = handle_create_order(_order_args(discount_code="INACTIVE"))

        assert result["order"] is None
        assert "not active" in result["userErrors"][0]["message"]
        assert state.counters.order_id == before_order_id
        assert state.counters.line_item_id == before_line_item_id

    def test_nonexistent_discount_rejected(self):
        result = handle_create_order(_order_args(discount_code="DOESNTEXIST"))
        assert result["order"] is None
        assert "not found" in result["userErrors"][0]["message"]

    def test_no_discount_no_discount_info(self):
        result = handle_create_order(_order_args())
        assert result["order"]["discount"] is None
        assert result["order"]["discountAmount"]["amount"] == "0.00"

    def test_cart_discount_applies_when_checkout_has_no_explicit_discount(self):
        handle_update_cart(UpdateCartArgs(cart_id="cart-1", discount_codes=["SAVE20"]))

        result = handle_create_order(_order_args())

        order = result["order"]
        assert order["discount"]["code"] == "SAVE20"
        assert order["discountAmount"]["amount"] == "10.00"
        assert order["totalPrice"]["amount"] == "45.99"

    def test_cart_marks_non_combinable_second_discount_not_applicable(self):
        result = handle_update_cart(UpdateCartArgs(cart_id="cart-1", discount_codes=["SAVE20", "FREESHIP"]))

        assert result["cart"]["discountCodes"][0]["applicable"] is True
        assert result["cart"]["discountCodes"][1]["applicable"] is False
        assert "cannot be combined" in result["userErrors"][0]["message"]

    def test_cart_applies_multiple_combinable_discounts_at_checkout(self):
        state = shopify_state.get_state()
        state.discount_codes["dc-1"].combinesWith.shippingDiscounts = True
        state.discount_codes["dc-2"].combinesWith.orderDiscounts = True

        cart_result = handle_update_cart(UpdateCartArgs(cart_id="cart-1", discount_codes=["SAVE20", "FREESHIP"]))
        result = handle_create_order(_order_args())

        order = result["order"]
        assert [code["applicable"] for code in cart_result["discountCodes"]] == [True, True]
        assert order["discount"]["code"] == "SAVE20"
        assert [discount["code"] for discount in order["discounts"]] == ["SAVE20", "FREESHIP"]
        assert order["discountAmount"]["amount"] == "15.99"
        assert order["shippingPrice"]["amount"] == "0.00"
        assert order["totalPrice"]["amount"] == "40.00"

    def test_update_cart_marks_valid_discount_applicable(self):
        result = handle_update_cart(UpdateCartArgs(cart_id="cart-1", discount_codes=["SAVE20"]))

        assert result["discountCodes"][0]["code"] == "SAVE20"
        assert result["discountCodes"][0]["applicable"] is True

    def test_update_cart_marks_nonexistent_discount_not_applicable(self):
        result = handle_update_cart(UpdateCartArgs(cart_id="cart-1", discount_codes=["DOESNTEXIST"]))

        assert result["cart"]["discountCodes"][0]["code"] == "DOESNTEXIST"
        assert result["cart"]["discountCodes"][0]["applicable"] is False
        assert "not found" in result["userErrors"][0]["message"]

    def test_update_cart_marks_inactive_discount_not_applicable(self):
        result = handle_update_cart(UpdateCartArgs(cart_id="cart-1", discount_codes=["INACTIVE"]))

        assert result["cart"]["discountCodes"][0]["applicable"] is False
        assert "not active" in result["userErrors"][0]["message"]

    def test_update_cart_marks_minimum_purchase_discount_not_applicable(self):
        discount = shopify_state.get_state().discount_codes["dc-1"]
        discount.code = "MIN100"
        discount.minimumPurchase = MoneyV2(amount="100.00", currencyCode="USD")

        result = handle_update_cart(UpdateCartArgs(cart_id="cart-1", discount_codes=["MIN100"]))

        assert result["cart"]["discountCodes"][0]["applicable"] is False
        assert "Minimum purchase" in result["userErrors"][0]["message"]

    def test_cart_delivery_options_and_gift_cards_are_stored(self):
        result = handle_update_cart(
            UpdateCartArgs.model_validate(
                {
                    "cart_id": "cart-1",
                    "delivery_addresses_to_add": [
                        {
                            "address": {
                                "address1": "123 Main St",
                                "city": "Springfield",
                                "countryCode": "US",
                            }
                        }
                    ],
                    "gift_card_codes": ["GIFT1234"],
                }
            )
        )
        cart = result
        assert cart["buyerIdentity"]["deliveryAddressPreferences"][0]["address1"] == "123 Main St"
        assert cart["deliveryGroups"][0]["id"].endswith("/delivery-group/1")
        assert cart["deliveryGroups"][0]["deliveryOptions"][0]["handle"] == "standard"
        assert cart["deliveryGroups"][0]["deliveryOptions"][0]["estimatedCost"]["amount"] == "5.99"
        assert cart["deliveryGroups"][0]["deliveryOptions"][1]["handle"] == "express"
        assert cart["deliveryGroups"][0]["deliveryOptions"][1]["estimatedCost"]["amount"] == "14.99"
        assert cart["appliedGiftCards"][0]["code"] == "GIFT1234"
        assert cart["appliedGiftCards"][0]["lastCharacters"] == "1234"
        assert cart["appliedGiftCards"][0]["amountUsed"]["amount"] == "20.00"
        assert cart["appliedGiftCards"][0]["balance"]["amount"] == "0.00"
        assert cart["cost"]["checkoutChargeAmount"]["amount"] == "30.00"

        selected_cart = handle_update_cart(
            UpdateCartArgs.model_validate(
                {
                    "cart_id": "cart-1",
                    "selected_delivery_options": [
                        {
                            "deliveryGroupId": "cart-1/delivery-group/1",
                            "deliveryOptionHandle": "express",
                        }
                    ],
                }
            )
        )
        assert selected_cart["deliveryGroups"][0]["selectedDeliveryOption"]["handle"] == "express"

    def test_cart_delivery_options_use_configured_active_shipping_methods(self):
        state = shopify_state.get_state()
        state.shipping_methods["standard"].active = False
        state.shipping_methods["local-bike"] = LooseShippingMethod.model_validate(
            {
                "id": "local-bike",
                "title": "Local Bike Courier",
                "price": {"amount": "3.50", "currencyCode": "USD"},
                "estimatedDays": "Same day",
                "active": True,
            }
        )

        result = handle_update_cart(
            UpdateCartArgs.model_validate(
                {
                    "cart_id": "cart-1",
                    "delivery_addresses_to_add": [
                        {
                            "address": {
                                "address1": "123 Main St",
                                "city": "Springfield",
                                "countryCode": "US",
                            }
                        }
                    ],
                }
            )
        )

        options = result["deliveryGroups"][0]["deliveryOptions"]
        assert [option["handle"] for option in options] == ["express", "local-bike"]
        assert options[0]["estimatedCost"]["amount"] == "14.99"
        assert options[1]["estimatedCost"]["amount"] == "3.50"

    def test_create_order_uses_selected_cart_delivery_option_when_shipping_omitted(self):
        handle_update_cart(
            UpdateCartArgs.model_validate(
                {
                    "cart_id": "cart-1",
                    "delivery_addresses_to_add": [
                        {
                            "address": {
                                "address1": "123 Main St",
                                "city": "Springfield",
                                "countryCode": "US",
                            }
                        }
                    ],
                }
            )
        )
        handle_update_cart(
            UpdateCartArgs.model_validate(
                {
                    "cart_id": "cart-1",
                    "selected_delivery_options": [
                        {
                            "deliveryGroupId": "cart-1/delivery-group/1",
                            "deliveryOptionHandle": "express",
                        }
                    ],
                }
            )
        )

        result = handle_create_order(
            CreateOrderArgs.model_validate(
                {
                    "cart_id": "cart-1",
                    "payment_method": VALID_CC,
                    "shipping_address": VALID_ADDR,
                    "billing_address": VALID_ADDR,
                }
            )
        )

        assert result["userErrors"] == []
        assert result["order"]["shippingMethod"]["id"] == "express"
        assert result["order"]["shippingPrice"]["amount"] == "14.99"

    def test_cart_rejects_unknown_and_empty_gift_cards(self):
        result = handle_update_cart(UpdateCartArgs(cart_id="cart-1", gift_card_codes=["NOPE", "EMPTYCARD"]))

        assert result["cart"]["appliedGiftCards"] == []
        assert len(result["userErrors"]) == 2
        assert "not found" in result["userErrors"][0]["message"]
        assert "no remaining balance" in result["userErrors"][1]["message"]

    def test_create_order_applies_gift_card_balance(self):
        handle_update_cart(UpdateCartArgs(cart_id="cart-1", gift_card_codes=["GIFT1234"]))

        result = handle_create_order(_order_args())

        order = result["order"]
        assert order["giftCardAmount"]["amount"] == "20.00"
        assert order["appliedGiftCards"][0]["code"] == "GIFT1234"
        assert order["appliedGiftCards"][0]["amountUsed"]["amount"] == "20.00"
        assert order["totalPrice"]["amount"] == "35.99"
        assert shopify_state.get_state().gift_cards["GIFT1234"].balance.amount == "0.00"

    def test_create_order_applies_gift_card_code_with_slash(self):
        state = shopify_state.get_state()
        state.gift_cards["GIFT/2024"] = LooseGiftCard.model_validate(
            {
                "id": "gid://shopify/GiftCard/GIFT/2024",
                "code": "GIFT/2024",
                "balance": {"amount": "10.00", "currencyCode": "USD"},
                "initialValue": {"amount": "10.00", "currencyCode": "USD"},
                "active": True,
            }
        )
        handle_update_cart(UpdateCartArgs(cart_id="cart-1", gift_card_codes=["GIFT/2024"]))

        result = handle_create_order(_order_args())

        assert result["userErrors"] == []
        assert result["order"]["appliedGiftCards"][0]["code"] == "GIFT/2024"
        assert result["order"]["giftCardAmount"]["amount"] == "10.00"
        assert state.gift_cards["GIFT/2024"].balance.amount == "0.00"

    def test_create_order_validates_all_gift_cards_before_mutating(self):
        state = shopify_state.get_state()
        handle_update_cart(UpdateCartArgs(cart_id="cart-1", gift_card_codes=["GIFT1234"]))
        state.gift_cards["STALE"] = LooseGiftCard.model_validate(
            {
                "id": "gid://shopify/GiftCard/STALE",
                "code": "STALE",
                "balance": {"amount": "10.00", "currencyCode": "USD"},
                "active": False,
            }
        )
        state.carts["cart-1"].appliedGiftCards.append(
            AppliedGiftCard.model_validate(
                {
                    "id": "gid://shopify/AppliedGiftCard/STALE",
                    "code": "STALE",
                    "lastCharacters": "TALE",
                    "amountUsed": {"amount": "10.00", "currencyCode": "USD"},
                    "balance": {"amount": "0.00", "currencyCode": "USD"},
                    "presentmentAmountUsed": {"amount": "10.00", "currencyCode": "USD"},
                }
            )
        )

        result = handle_create_order(_order_args(discount_code="WIDGET10"))

        assert result["order"] is None
        assert "not active" in result["userErrors"][0]["message"]
        assert state.gift_cards["GIFT1234"].balance.amount == "20.00"
        assert state.discount_codes["dc-3"].usageCount == 0
        assert state.counters.order_id == 2000
        assert state.counters.line_item_id == 3000

    def test_order_without_discount_code(self):
        # Verify orders still work fine without any discount
        result = handle_create_order(_order_args())
        assert result["userErrors"] == []
        assert result["order"]["totalPrice"]["amount"] == "55.99"  # 50 + 5.99
