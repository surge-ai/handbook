"""Tests for store policy tools."""

import json

import pytest

from shopify import state as shopify_state
from shopify.models import (
    CreatePolicyArgs,
    DeletePolicyArgs,
    ListPoliciesArgs,
    UpdatePolicyArgs,
)
from shopify.tools.policies import (
    handle_create_policy,
    handle_delete_policy,
    handle_list_policies,
    handle_update_policy,
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
                "policies": [
                    {
                        "id": "gid://shopify/ShopPolicy/1",
                        "title": "Return Policy",
                        "body": "<p>30-day returns on all items.</p>",
                        "url": "https://shop.example.com/policies/1",
                    }
                ],
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
    monkeypatch.setattr(shopify_state, "_STATE_FILE", shopify_data)
    shopify_state._current_state = None
    shopify_state._stores.clear()
    shopify_state._active_store_id = "default"
    shopify_state.load_state()


class TestCreatePolicy:
    def test_create(self):
        result = handle_create_policy(CreatePolicyArgs(title="Shipping Policy", body="<p>Free shipping over $50.</p>"))
        assert result["userErrors"] == []
        p = result["policy"]
        assert p["title"] == "Shipping Policy"
        assert "Free shipping" in p["body"]
        assert p["id"].startswith("gid://shopify/ShopPolicy/")

    def test_create_adds_to_state(self):
        handle_create_policy(CreatePolicyArgs(title="Privacy Policy", body="<p>We respect privacy.</p>"))
        state = shopify_state.get_state()
        assert len(state.policies) == 2


class TestListPolicies:
    def test_list(self):
        result = handle_list_policies(ListPoliciesArgs())
        assert result["totalCount"] == 1
        assert result["policies"][0]["title"] == "Return Policy"


class TestUpdatePolicy:
    def test_update_title(self):
        result = handle_update_policy(
            UpdatePolicyArgs(policy_id="gid://shopify/ShopPolicy/1", title="Updated Return Policy")
        )
        assert result["userErrors"] == []
        assert result["policy"]["title"] == "Updated Return Policy"

    def test_update_body(self):
        result = handle_update_policy(
            UpdatePolicyArgs(policy_id="gid://shopify/ShopPolicy/1", body="<p>60-day returns.</p>")
        )
        assert "60-day" in result["policy"]["body"]

    def test_update_nonexistent(self):
        result = handle_update_policy(UpdatePolicyArgs(policy_id="nonexistent", title="X"))
        assert result["policy"] is None
        assert len(result["userErrors"]) == 1


class TestDeletePolicy:
    def test_delete(self):
        result = handle_delete_policy(DeletePolicyArgs(policy_id="gid://shopify/ShopPolicy/1"))
        assert result["deletedPolicyId"] == "gid://shopify/ShopPolicy/1"
        state = shopify_state.get_state()
        assert len(state.policies) == 0

    def test_delete_nonexistent(self):
        result = handle_delete_policy(DeletePolicyArgs(policy_id="nonexistent"))
        assert result["deletedPolicyId"] is None
