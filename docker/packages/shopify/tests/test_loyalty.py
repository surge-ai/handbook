"""Tests for loyalty program tools and create_order integration."""

import json
from typing import Any

import pytest

from shopify import state as shopify_state
from shopify.models import (
    AwardPointsArgs,
    ConfigureLoyaltyProgramArgs,
    CreateDiscountCodeArgs,
    CreateOrderArgs,
    GetLoyaltyBalanceArgs,
    GetLoyaltyProgramArgs,
    GetLoyaltyTierArgs,
    ListLoyaltyTiersArgs,
    LoyaltyTier,
    RedeemPointsArgs,
    UpdateDiscountCodeArgs,
)
from shopify.tools.discounts import handle_create_discount_code, handle_update_discount_code
from shopify.tools.loyalty import (
    compute_tier,
    handle_award_points,
    handle_configure_loyalty_program,
    handle_get_loyalty_balance,
    handle_get_loyalty_program,
    handle_get_loyalty_tier,
    handle_list_loyalty_tiers,
    handle_redeem_points,
)
from shopify.tools.orders import handle_create_order


@pytest.fixture
def shopify_data(tmp_path):
    data_file = tmp_path / "shopify_data.json"
    data_file.write_text(
        json.dumps(
            {
                "products": {
                    "gid://shopify/Product/1": {
                        "id": "gid://shopify/Product/1",
                        "title": "Widget",
                        "variants": [
                            {
                                "id": "gid://shopify/ProductVariant/1",
                                "title": "Default",
                                "price": {"amount": "100.00", "currencyCode": "USD"},
                                "availableForSale": True,
                            }
                        ],
                    }
                },
                "carts": {
                    "gid://shopify/Cart/c1001": {
                        "id": "gid://shopify/Cart/c1001",
                        "lines": [
                            {
                                "id": "gid://shopify/CartLine/1001",
                                "quantity": 1,
                                "merchandise": {
                                    "id": "gid://shopify/ProductVariant/1",
                                    "title": "Default",
                                    "price": {"amount": "100.00", "currencyCode": "USD"},
                                    "product": {"id": "gid://shopify/Product/1", "title": "Widget"},
                                },
                                "cost": {
                                    "amountPerQuantity": {"amount": "100.00", "currencyCode": "USD"},
                                    "subtotalAmount": {"amount": "100.00", "currencyCode": "USD"},
                                    "totalAmount": {"amount": "100.00", "currencyCode": "USD"},
                                },
                            }
                        ],
                    }
                },
                "orders": {},
                "customers": {
                    "gid://shopify/Customer/5001": {
                        "id": "gid://shopify/Customer/5001",
                        "firstName": "Jane",
                        "lastName": "Doe",
                        "email": "jane@example.com",
                        "phone": None,
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
                    }
                },
                "collections": {},
                "reviews": {},
                "returns": {},
                "discount_codes": {},
                "shipping_methods": {
                    "standard": {
                        "id": "standard",
                        "title": "Standard Shipping",
                        "price": {"amount": "5.00", "currencyCode": "USD"},
                        "estimatedDays": "5-7",
                        "active": True,
                    }
                },
                "loyalty_program": {
                    "enabled": True,
                    "earn_rate": 1,
                    "redemption_rate": 100,
                    "max_redemption_percent": 50,
                    "tiers": [
                        {"name": "Bronze", "min_lifetime_points": 0, "discount_percent": 5},
                        {"name": "Silver", "min_lifetime_points": 1000, "discount_percent": 10},
                        {"name": "Gold", "min_lifetime_points": 5000, "discount_percent": 15},
                    ],
                },
                "policies": [],
                "counters": {
                    "cart_id": 1001,
                    "line_id": 1001,
                    "order_id": 2001,
                    "line_item_id": 3001,
                    "customer_id": 5001,
                    "collection_id": 6001,
                    "review_id": 7001,
                    "return_id": 8001,
                    "product_id": 9001,
                    "variant_id": 10001,
                    "discount_id": 11001,
                    "policy_id": 12001,
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


# ============================================
# compute_tier UNIT TESTS
# ============================================


class TestComputeTier:
    def test_no_tiers_returns_none(self):
        assert compute_tier(1000, []) is None

    def test_below_lowest_threshold_returns_none(self):
        tiers = [LoyaltyTier(name="Silver", min_lifetime_points=1000, discount_percent=10)]
        assert compute_tier(500, tiers) is None

    def test_highest_matching_tier(self):
        tiers = [
            LoyaltyTier(name="Bronze", min_lifetime_points=0, discount_percent=5),
            LoyaltyTier(name="Silver", min_lifetime_points=1000, discount_percent=10),
            LoyaltyTier(name="Gold", min_lifetime_points=5000, discount_percent=15),
        ]
        result = compute_tier(6000, tiers)
        assert result is not None
        assert result.name == "Gold"

    def test_exact_threshold_qualifies(self):
        tiers = [LoyaltyTier(name="Silver", min_lifetime_points=1000, discount_percent=10)]
        result = compute_tier(1000, tiers)
        assert result is not None
        assert result.name == "Silver"


# ============================================
# PROGRAM CONFIG TESTS
# ============================================


class TestConfigureProgram:
    def test_toggle_enabled(self):
        result = handle_configure_loyalty_program(ConfigureLoyaltyProgramArgs(enabled=False))
        assert result["userErrors"] == []
        assert result["program"]["enabled"] is False

    def test_update_earn_rate(self):
        result = handle_configure_loyalty_program(ConfigureLoyaltyProgramArgs(earn_rate=2))
        assert result["program"]["earn_rate"] == 2

    def test_replace_tiers(self):
        new_tiers = [LoyaltyTier(name="VIP", min_lifetime_points=100, discount_percent=25)]
        result = handle_configure_loyalty_program(ConfigureLoyaltyProgramArgs(tiers=new_tiers))
        assert len(result["program"]["tiers"]) == 1
        assert result["program"]["tiers"][0]["name"] == "VIP"

    def test_rejects_invalid_earn_rate(self):
        result = handle_configure_loyalty_program(ConfigureLoyaltyProgramArgs(earn_rate=-1))
        assert result["program"] is None
        assert len(result["userErrors"]) == 1

    def test_rejects_invalid_config_before_any_mutation(self):
        state = shopify_state.get_state()
        result = handle_configure_loyalty_program(ConfigureLoyaltyProgramArgs(enabled=False, earn_rate=-1))
        assert result["program"] is None
        assert state.loyalty_program.enabled is True
        assert state.loyalty_program.earn_rate == 1

    def test_rejects_invalid_redemption_rate(self):
        result = handle_configure_loyalty_program(ConfigureLoyaltyProgramArgs(redemption_rate=0))
        assert result["program"] is None

    def test_rejects_invalid_percent(self):
        result = handle_configure_loyalty_program(ConfigureLoyaltyProgramArgs(max_redemption_percent=150))
        assert result["program"] is None


class TestGetProgram:
    def test_read_program(self):
        result = handle_get_loyalty_program(GetLoyaltyProgramArgs())
        assert result["program"]["enabled"] is True
        assert result["program"]["earn_rate"] == 1


class TestListTiers:
    def test_sorted_ascending(self):
        result = handle_list_loyalty_tiers(ListLoyaltyTiersArgs())
        assert result["totalCount"] == 3
        names = [t["name"] for t in result["tiers"]]
        assert names == ["Bronze", "Silver", "Gold"]


# ============================================
# BALANCE / TIER LOOKUP
# ============================================


class TestGetBalance:
    def test_new_customer_zero_balance(self):
        result = handle_get_loyalty_balance(GetLoyaltyBalanceArgs(customer_id="gid://shopify/Customer/5001"))
        assert result["userErrors"] == []
        assert result["balance"]["pointsBalance"] == 0
        assert result["balance"]["tier"] is None

    def test_nonexistent_customer(self):
        result = handle_get_loyalty_balance(GetLoyaltyBalanceArgs(customer_id="nope"))
        assert result["balance"] is None
        assert len(result["userErrors"]) == 1


class TestGetTier:
    def test_customer_with_lifetime_points(self):
        handle_award_points(AwardPointsArgs(customer_id="gid://shopify/Customer/5001", points=1200))
        result = handle_get_loyalty_tier(GetLoyaltyTierArgs(customer_id="gid://shopify/Customer/5001"))
        assert result["tier"]["name"] == "Silver"

    def test_customer_no_tier(self):
        result = handle_get_loyalty_tier(GetLoyaltyTierArgs(customer_id="gid://shopify/Customer/5001"))
        # 0 lifetime points still qualifies for Bronze (min_lifetime_points=0)
        assert result["tier"]["name"] == "Bronze"


# ============================================
# AWARD / REDEEM
# ============================================


class TestAwardPoints:
    def test_award_grows_both_balances(self):
        result = handle_award_points(
            AwardPointsArgs(customer_id="gid://shopify/Customer/5001", points=500, reason="welcome bonus")
        )
        assert result["userErrors"] == []
        assert result["balance"]["pointsBalance"] == 500
        assert result["balance"]["lifetimePoints"] == 500

    def test_award_updates_tier(self):
        handle_award_points(AwardPointsArgs(customer_id="gid://shopify/Customer/5001", points=1500))
        customer = shopify_state.get_customer_by_id("gid://shopify/Customer/5001")
        assert customer is not None
        assert customer["tier"] == "Silver"

    def test_reject_negative_points(self):
        result = handle_award_points(AwardPointsArgs(customer_id="gid://shopify/Customer/5001", points=-10))
        assert result["balance"] is None


class TestRedeemPoints:
    def test_redeem_reduces_balance(self):
        handle_award_points(AwardPointsArgs(customer_id="gid://shopify/Customer/5001", points=1000))
        result = handle_redeem_points(RedeemPointsArgs(customer_id="gid://shopify/Customer/5001", points=500))
        assert result["userErrors"] == []
        assert result["redemption"]["pointsBalance"] == 500
        assert result["redemption"]["dollarValue"]["amount"] == "5.00"

    def test_redeem_preserves_lifetime_points(self):
        handle_award_points(AwardPointsArgs(customer_id="gid://shopify/Customer/5001", points=2000))
        handle_redeem_points(RedeemPointsArgs(customer_id="gid://shopify/Customer/5001", points=500))
        customer = shopify_state.get_customer_by_id("gid://shopify/Customer/5001")
        assert customer is not None
        assert customer["lifetimePoints"] == 2000
        assert customer["pointsBalance"] == 1500

    def test_insufficient_balance(self):
        result = handle_redeem_points(RedeemPointsArgs(customer_id="gid://shopify/Customer/5001", points=100))
        assert result["redemption"] is None


# ============================================
# CREATE_ORDER INTEGRATION
# ============================================


def _default_order_args(**overrides):
    base: dict[str, Any] = {
        "cart_id": "gid://shopify/Cart/c1001",
        "payment_method": {
            "type": "credit_card",
            "card_number": "4111111111111111",
            "cvv": "123",
            "expiry": "12/26",
        },
        "shipping_address": {"address1": "1 Main", "city": "Portland", "countryCode": "US"},
        "billing_address": {"address1": "1 Main", "city": "Portland", "countryCode": "US"},
        "shipping_method_id": "standard",
        "email": "jane@example.com",
    }
    base.update(overrides)
    return CreateOrderArgs.model_validate(base)


class TestOrderLoyaltyIntegration:
    def test_order_awards_points(self):
        result = handle_create_order(_default_order_args())
        assert result["userErrors"] == []
        order = result["order"]
        # Bronze tier = 5% off $100 subtotal = $5 tier discount, post-tier-discount = $95 → 95 points
        assert order["loyaltyPointsEarned"] == 95

    def test_order_applies_tier_discount(self):
        result = handle_create_order(_default_order_args())
        order = result["order"]
        assert order["tierDiscount"]["name"] == "Bronze"
        assert order["tierDiscountAmount"]["amount"] == "5.00"
        # total = 100 - 5 tier + 5 shipping = 100
        assert order["totalPrice"]["amount"] == "100.00"

    def test_tier_discount_can_be_disabled(self):
        result = handle_create_order(_default_order_args(apply_tier_discount=False))
        order = result["order"]
        assert order["tierDiscount"] is None
        assert order["tierDiscountAmount"]["amount"] == "0.00"

    def test_order_redeems_points(self):
        handle_award_points(AwardPointsArgs(customer_id="gid://shopify/Customer/5001", points=1000))
        result = handle_create_order(_default_order_args(redeem_points=1000))
        order = result["order"]
        assert order["loyaltyPointsRedeemed"] == 1000
        # Bronze tier still qualifies (1000 lifetime points = Silver actually)
        # Silver = 10% off, so subtotal after tier = 90, redemption_cap = 45 (50% of 90)
        # $10 redemption requested (1000/100), capped at $45 → $10 applied
        assert order["loyaltyRedemptionAmount"]["amount"] == "10.00"

    @pytest.mark.asyncio
    async def test_public_create_order_exposes_redeem_points(self):
        from shopify.server import create_order

        handle_award_points(AwardPointsArgs(customer_id="gid://shopify/Customer/5001", points=1000))

        result = await create_order(
            cart_id="gid://shopify/Cart/c1001",
            payment_method={
                "type": "credit_card",
                "card_number": "4111111111111111",
                "cvv": "123",
                "expiry": "12/26",
            },
            shipping_address={"address1": "1 Main", "city": "Portland", "countryCode": "US"},
            billing_address={"address1": "1 Main", "city": "Portland", "countryCode": "US"},
            shipping_method_id="standard",
            email="jane@example.com",
            redeem_points=1000,
        )

        order = result["order"]
        assert result["userErrors"] == []
        assert order["loyaltyPointsRedeemed"] == 1000
        assert order["loyaltyRedemptionAmount"]["amount"] == "10.00"

    @pytest.mark.asyncio
    async def test_public_create_order_falls_back_to_current_customer_email(self):
        from shopify.server import create_order

        shopify_state.get_state().current_customer_email = "jane@example.com"

        result = await create_order(
            cart_id="gid://shopify/Cart/c1001",
            payment_method={
                "type": "credit_card",
                "card_number": "4111111111111111",
                "cvv": "123",
                "expiry": "12/26",
            },
            shipping_address={"address1": "1 Main", "city": "Portland", "countryCode": "US"},
            billing_address={"address1": "1 Main", "city": "Portland", "countryCode": "US"},
            shipping_method_id="standard",
        )

        assert result["userErrors"] == []
        assert result["order"]["email"] == "jane@example.com"
        assert shopify_state.get_state().customers["gid://shopify/Customer/5001"].ordersCount == 1

    def test_redeem_respects_max_percent_cap(self):
        # Award 50000 points ($500 of value), subtotal only $100
        handle_award_points(AwardPointsArgs(customer_id="gid://shopify/Customer/5001", points=50000))
        result = handle_create_order(_default_order_args(redeem_points=10000))
        order = result["order"]
        # 50000 lifetime = Gold tier (15% off), subtotal after tier = $85
        # Max redemption = 50% of 85 = $42.50
        # Requested $100 (10000/100), capped at $42.50
        assert order["loyaltyRedemptionAmount"]["amount"] == "42.50"
        # Only the points that became discount should be deducted: $42.50 * 100 = 4250.
        # Burning the full 10000 would cost the customer 5750 points of value for
        # nothing.
        assert order["loyaltyPointsRedeemed"] == 4250

    def test_redeem_requires_enabled_program(self):
        handle_award_points(AwardPointsArgs(customer_id="gid://shopify/Customer/5001", points=1000))
        handle_configure_loyalty_program(ConfigureLoyaltyProgramArgs(enabled=False))
        result = handle_create_order(_default_order_args(redeem_points=500))
        assert result["order"] is None
        assert any(e["field"] == "redeem_points" for e in result["userErrors"])

    def test_redeem_requires_customer_lookup(self):
        # Email with no matching customer
        result = handle_create_order(_default_order_args(redeem_points=500, email="unknown@example.com"))
        assert result["order"] is None

    def test_order_updates_customer_totals(self):
        handle_create_order(_default_order_args())
        customer = shopify_state.get_customer_by_id("gid://shopify/Customer/5001")
        assert customer is not None
        assert customer["ordersCount"] == 1
        assert float(customer["totalSpent"]["amount"]) == 100.0
        assert customer["pointsBalance"] == 95

    def test_unknown_email_no_loyalty_effects(self):
        result = handle_create_order(_default_order_args(email="ghost@example.com"))
        order = result["order"]
        assert order["loyaltyPointsEarned"] == 0
        assert order["tierDiscount"] is None

    def test_disabled_program_no_loyalty_effects(self):
        handle_configure_loyalty_program(ConfigureLoyaltyProgramArgs(enabled=False))
        result = handle_create_order(_default_order_args())
        order = result["order"]
        assert order["loyaltyPointsEarned"] == 0
        assert order["tierDiscount"] is None


# ============================================
# TIER-GATED DISCOUNT CODES
# ============================================


class TestTierGatedDiscountCodes:
    def test_create_code_with_minimum_tier(self):
        result = handle_create_discount_code(
            CreateDiscountCodeArgs(
                code="GOLD25",
                value="25",
                discount_type="PERCENTAGE",
                minimum_tier="Gold",
            )
        )
        assert result["userErrors"] == []
        assert result["discountCode"]["minimumTier"] == "Gold"

    def test_update_clears_tier_restriction(self):
        handle_create_discount_code(
            CreateDiscountCodeArgs(code="TIERED", value="10", discount_type="PERCENTAGE", minimum_tier="Silver")
        )
        result = handle_update_discount_code(UpdateDiscountCodeArgs(code="TIERED", minimum_tier=""))
        assert result["discountCode"]["minimumTier"] is None

    def test_order_with_qualified_customer_succeeds(self):
        handle_create_discount_code(
            CreateDiscountCodeArgs(code="SILVERONLY", value="15", discount_type="PERCENTAGE", minimum_tier="Silver")
        )
        # Award enough points to reach Silver (1000 lifetime)
        handle_award_points(AwardPointsArgs(customer_id="gid://shopify/Customer/5001", points=1200))

        result = handle_create_order(_default_order_args(discount_code="SILVERONLY"))
        assert result["userErrors"] == []
        order = result["order"]
        assert order["discount"]["code"] == "SILVERONLY"
        assert order["discount"]["minimumTier"] == "Silver"

    def test_order_with_underqualified_customer_rejected(self):
        handle_create_discount_code(
            CreateDiscountCodeArgs(code="GOLDONLY", value="20", discount_type="PERCENTAGE", minimum_tier="Gold")
        )
        # Customer is only Bronze (0 lifetime points)
        result = handle_create_order(_default_order_args(discount_code="GOLDONLY"))
        assert result["order"] is None
        assert any("Gold" in e["message"] for e in result["userErrors"])

    def test_higher_tier_qualifies_for_lower_code(self):
        handle_create_discount_code(
            CreateDiscountCodeArgs(code="BRONZEANDUP", value="5", discount_type="PERCENTAGE", minimum_tier="Bronze")
        )
        # Customer reaches Gold (5000+ lifetime)
        handle_award_points(AwardPointsArgs(customer_id="gid://shopify/Customer/5001", points=6000))

        result = handle_create_order(_default_order_args(discount_code="BRONZEANDUP"))
        assert result["userErrors"] == []

    def test_anonymous_customer_rejected_by_tier_code(self):
        handle_create_discount_code(
            CreateDiscountCodeArgs(code="BRONZEUP", value="5", discount_type="PERCENTAGE", minimum_tier="Bronze")
        )
        # Use email that doesn't match any customer
        result = handle_create_order(_default_order_args(discount_code="BRONZEUP", email="ghost@example.com"))
        assert result["order"] is None
        assert any("Bronze" in e["message"] for e in result["userErrors"])

    def test_unknown_tier_rejected(self):
        result = handle_create_discount_code(
            CreateDiscountCodeArgs(code="PLATONLY", value="30", discount_type="PERCENTAGE", minimum_tier="Platinum")
        )
        assert result["discountCode"] is None
        assert any("Platinum" in e["message"] for e in result["userErrors"])
