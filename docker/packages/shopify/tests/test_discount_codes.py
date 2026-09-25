"""Tests for discount code tools."""

import json

import pytest
from pydantic import ValidationError

from shopify import state as shopify_state
from shopify.models import (
    CreateDiscountCodeArgs,
    DeleteDiscountCodeArgs,
    GetDiscountCodeArgs,
    ListDiscountCodesArgs,
    UpdateDiscountCodeArgs,
)
from shopify.tools.discounts import (
    handle_create_discount_code,
    handle_delete_discount_code,
    handle_get_discount_code,
    handle_list_discount_codes,
    handle_update_discount_code,
)


@pytest.fixture
def shopify_data(tmp_path):
    data_file = tmp_path / "shopify_data.json"
    data_file.write_text(
        json.dumps(
            {
                "products": {},
                "carts": {},
                "orders": {},
                "customers": {},
                "collections": {},
                "reviews": {},
                "returns": {},
                "discount_codes": {},
                "loyalty_program": {
                    "enabled": True,
                    "tiers": [
                        {"name": "Bronze", "min_lifetime_points": 0, "discount_percent": 5},
                        {"name": "Gold", "min_lifetime_points": 5000, "discount_percent": 15},
                    ],
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


class TestCreateDiscountCode:
    def test_create_percentage(self):
        result = handle_create_discount_code(
            CreateDiscountCodeArgs(code="SUMMER20", value="20", discount_type="PERCENTAGE")
        )
        assert result["userErrors"] == []
        dc = result["discountCode"]
        assert dc["code"] == "SUMMER20"
        assert dc["discountType"] == "PERCENTAGE"
        assert dc["value"] == "20"
        assert dc["active"] is True
        assert dc["usageCount"] == 0

    def test_create_fixed_amount(self):
        result = handle_create_discount_code(
            CreateDiscountCodeArgs(code="SAVE10", value="10.00", discount_type="FIXED_AMOUNT")
        )
        assert result["discountCode"]["discountType"] == "FIXED_AMOUNT"

    def test_create_free_shipping(self):
        result = handle_create_discount_code(
            CreateDiscountCodeArgs(code="FREESHIP", value="0", discount_type="FREE_SHIPPING")
        )
        assert result["discountCode"]["discountType"] == "FREE_SHIPPING"

    def test_create_with_minimum_purchase(self):
        result = handle_create_discount_code(CreateDiscountCodeArgs(code="BIG20", value="20", minimum_purchase=50.0))
        assert result["discountCode"]["minimumPurchase"]["amount"] == "50.00"

    def test_create_with_usage_limit(self):
        result = handle_create_discount_code(CreateDiscountCodeArgs(code="LIMITED", value="10", usage_limit=100))
        assert result["discountCode"]["usageLimit"] == 100

    def test_create_duplicate(self):
        handle_create_discount_code(CreateDiscountCodeArgs(code="DUP", value="10"))
        result = handle_create_discount_code(CreateDiscountCodeArgs(code="DUP", value="20"))
        assert result["discountCode"] is None
        assert "already exists" in result["userErrors"][0]["message"]

    def test_create_invalid_type(self):
        with pytest.raises(ValidationError):
            CreateDiscountCodeArgs.model_validate({"code": "BAD", "value": "10", "discount_type": "INVALID"})

    def test_code_uppercased(self):
        result = handle_create_discount_code(CreateDiscountCodeArgs(code="summer20", value="20"))
        assert result["discountCode"]["code"] == "SUMMER20"

    def test_create_rejects_missing_product_restriction(self):
        result = handle_create_discount_code(CreateDiscountCodeArgs(code="BADPRODUCT", value="10", product_ids=["p1"]))
        assert result["discountCode"] is None
        assert "Product not found: p1" in result["userErrors"][0]["message"]

    def test_create_rejects_missing_minimum_tier(self):
        result = handle_create_discount_code(CreateDiscountCodeArgs(code="VIP", value="10", minimum_tier="Platinum"))
        assert result["discountCode"] is None
        assert result["userErrors"][0]["field"] == "minimum_tier"

    def test_create_accepts_configured_minimum_tier(self):
        result = handle_create_discount_code(CreateDiscountCodeArgs(code="GOLD", value="10", minimum_tier="Gold"))
        assert result["userErrors"] == []
        assert result["discountCode"]["minimumTier"] == "Gold"


class TestGetDiscountCode:
    def test_get_existing(self):
        handle_create_discount_code(CreateDiscountCodeArgs(code="TEST10", value="10"))
        result = handle_get_discount_code(GetDiscountCodeArgs(code="TEST10"))
        assert result["discountCode"]["code"] == "TEST10"

    def test_get_case_insensitive(self):
        handle_create_discount_code(CreateDiscountCodeArgs(code="TEST10", value="10"))
        result = handle_get_discount_code(GetDiscountCodeArgs(code="test10"))
        assert result["discountCode"]["code"] == "TEST10"

    def test_get_nonexistent(self):
        result = handle_get_discount_code(GetDiscountCodeArgs(code="NOPE"))
        assert result["discountCode"] is None


class TestListDiscountCodes:
    def test_list_empty(self):
        result = handle_list_discount_codes(ListDiscountCodesArgs())
        assert result["totalCount"] == 0

    def test_list_all(self):
        handle_create_discount_code(CreateDiscountCodeArgs(code="A10", value="10"))
        handle_create_discount_code(CreateDiscountCodeArgs(code="B20", value="20"))
        result = handle_list_discount_codes(ListDiscountCodesArgs())
        assert result["totalCount"] == 2

    def test_list_active_only(self):
        handle_create_discount_code(CreateDiscountCodeArgs(code="ACTIVE", value="10"))
        handle_create_discount_code(CreateDiscountCodeArgs(code="INACTIVE", value="20"))
        handle_update_discount_code(UpdateDiscountCodeArgs(code="INACTIVE", active=False))
        result = handle_list_discount_codes(ListDiscountCodesArgs(active_only=True))
        assert result["totalCount"] == 1
        assert result["discountCodes"][0]["code"] == "ACTIVE"


class TestUpdateDiscountCode:
    def test_deactivate(self):
        handle_create_discount_code(CreateDiscountCodeArgs(code="TEST", value="10"))
        result = handle_update_discount_code(UpdateDiscountCodeArgs(code="TEST", active=False))
        assert result["discountCode"]["active"] is False

    def test_update_value(self):
        handle_create_discount_code(CreateDiscountCodeArgs(code="TEST", value="10"))
        result = handle_update_discount_code(UpdateDiscountCodeArgs(code="TEST", value="25"))
        assert result["discountCode"]["value"] == "25"

    def test_update_nonexistent(self):
        result = handle_update_discount_code(UpdateDiscountCodeArgs(code="NOPE", active=False))
        assert result["discountCode"] is None

    def test_update_rejects_missing_product_restriction(self):
        handle_create_discount_code(CreateDiscountCodeArgs(code="TEST", value="10"))
        result = handle_update_discount_code(UpdateDiscountCodeArgs(code="TEST", product_ids=["p1"]))
        assert result["discountCode"] is None
        assert "Product not found: p1" in result["userErrors"][0]["message"]

    def test_update_rejects_missing_minimum_tier(self):
        handle_create_discount_code(CreateDiscountCodeArgs(code="TEST", value="10"))
        result = handle_update_discount_code(UpdateDiscountCodeArgs(code="TEST", minimum_tier="Platinum"))
        assert result["discountCode"] is None
        assert result["userErrors"][0]["field"] == "minimum_tier"

    def test_update_accepts_configured_minimum_tier(self):
        handle_create_discount_code(CreateDiscountCodeArgs(code="TEST", value="10"))
        result = handle_update_discount_code(UpdateDiscountCodeArgs(code="TEST", minimum_tier="Gold"))
        assert result["userErrors"] == []
        assert result["discountCode"]["minimumTier"] == "Gold"

    def test_update_clears_minimum_tier_with_empty_string(self):
        handle_create_discount_code(CreateDiscountCodeArgs(code="TEST", value="10", minimum_tier="Gold"))
        result = handle_update_discount_code(UpdateDiscountCodeArgs(code="TEST", minimum_tier=""))
        assert result["userErrors"] == []
        assert result["discountCode"]["minimumTier"] is None


class TestDeleteDiscountCode:
    def test_delete(self):
        handle_create_discount_code(CreateDiscountCodeArgs(code="TODELETE", value="10"))
        result = handle_delete_discount_code(DeleteDiscountCodeArgs(code="TODELETE"))
        assert result["deletedCode"] == "TODELETE"
        # Verify gone
        get_result = handle_get_discount_code(GetDiscountCodeArgs(code="TODELETE"))
        assert get_result["discountCode"] is None

    def test_delete_nonexistent(self):
        result = handle_delete_discount_code(DeleteDiscountCodeArgs(code="NOPE"))
        assert result["deletedCode"] is None
