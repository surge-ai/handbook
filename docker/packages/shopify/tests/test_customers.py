"""Tests for customer management tools."""

import json

import pytest

from shopify import state as shopify_state
from shopify.models import (
    CreateCustomerArgs,
    GetCustomerArgs,
    ListCustomersArgs,
    SearchCustomersArgs,
    UpdateCustomerArgs,
)
from shopify.tools.customers import (
    handle_create_customer,
    handle_get_customer,
    handle_list_customers,
    handle_search_customers,
    handle_update_customer,
)


@pytest.fixture
def shopify_data(tmp_path):
    """Seed state with existing customers."""
    data_file = tmp_path / "shopify_data.json"
    data_file.write_text(
        json.dumps(
            {
                "products": {},
                "carts": {},
                "orders": {},
                "customers": {
                    "cust-1": {
                        "id": "cust-1",
                        "firstName": "Alice",
                        "lastName": "Smith",
                        "email": "alice@example.com",
                        "phone": "+1111111111",
                        "createdAt": "2024-01-01T00:00:00Z",
                        "updatedAt": "2024-01-01T00:00:00Z",
                        "defaultAddress": {"address1": "123 Main St", "city": "Springfield"},
                        "addresses": [{"address1": "123 Main St", "city": "Springfield"}],
                        "ordersCount": 5,
                        "totalSpent": {"amount": "250.00", "currencyCode": "USD"},
                        "tags": ["vip", "wholesale"],
                        "note": None,
                        "acceptsMarketing": True,
                        "state": "ENABLED",
                    },
                    "cust-2": {
                        "id": "cust-2",
                        "firstName": "Bob",
                        "lastName": "Jones",
                        "email": "bob@example.com",
                        "phone": "+2222222222",
                        "createdAt": "2024-02-01T00:00:00Z",
                        "updatedAt": "2024-02-01T00:00:00Z",
                        "defaultAddress": None,
                        "addresses": [],
                        "ordersCount": 0,
                        "totalSpent": {"amount": "0.00", "currencyCode": "USD"},
                        "tags": [],
                        "note": None,
                        "acceptsMarketing": False,
                        "state": "ENABLED",
                    },
                },
                "policies": [],
                "counters": {
                    "cart_id": 1000,
                    "line_id": 1000,
                    "order_id": 2000,
                    "line_item_id": 3000,
                    "customer_id": 4000,
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


class TestCreateCustomer:
    def test_create_customer(self):
        result = handle_create_customer(
            CreateCustomerArgs(email="carol@example.com", first_name="Carol", last_name="White")
        )
        assert result["userErrors"] == []
        c = result["customer"]
        assert c["id"].startswith("gid://shopify/Customer/")
        assert c["email"] == "carol@example.com"
        assert c["firstName"] == "Carol"
        assert c["state"] == "ENABLED"
        assert c["ordersCount"] == 0

    def test_create_customer_with_address(self):
        addr = {"firstName": "Carol", "address1": "456 Oak Ave", "city": "Portland"}
        result = handle_create_customer(
            CreateCustomerArgs.model_validate({"email": "carol@example.com", "address": addr})
        )
        c = result["customer"]
        assert c["defaultAddress"]["address1"] == "456 Oak Ave"
        assert len(c["addresses"]) == 1

    def test_create_customer_with_tags(self):
        result = handle_create_customer(CreateCustomerArgs(email="carol@example.com", tags=["new", "referral"]))
        assert result["customer"]["tags"] == ["new", "referral"]

    def test_create_customer_duplicate_email(self):
        result = handle_create_customer(CreateCustomerArgs(email="alice@example.com"))
        assert result["customer"] is None
        assert len(result["userErrors"]) == 1
        assert "already exists" in result["userErrors"][0]["message"]


class TestGetCustomer:
    def test_get_existing(self):
        result = handle_get_customer(GetCustomerArgs(customer_id="cust-1"))
        assert result["userErrors"] == []
        assert result["customer"]["email"] == "alice@example.com"

    def test_get_nonexistent(self):
        result = handle_get_customer(GetCustomerArgs(customer_id="nonexistent"))
        assert result["customer"] is None
        assert len(result["userErrors"]) == 1


class TestListCustomers:
    def test_list_all(self):
        result = handle_list_customers(ListCustomersArgs())
        assert result["totalCount"] == 2

    def test_list_with_query(self):
        result = handle_list_customers(ListCustomersArgs(query="alice"))
        assert result["totalCount"] == 1
        assert result["customers"][0]["email"] == "alice@example.com"

    def test_list_with_tag(self):
        result = handle_list_customers(ListCustomersArgs(tag="vip"))
        assert result["totalCount"] == 1
        assert result["customers"][0]["id"] == "cust-1"

    def test_list_no_match(self):
        result = handle_list_customers(ListCustomersArgs(query="zzz"))
        assert result["totalCount"] == 0

    def test_list_pagination(self):
        result = handle_list_customers(ListCustomersArgs(limit=1))
        assert len(result["customers"]) == 1
        assert result["totalCount"] == 2
        assert result["pageInfo"]["hasNextPage"] is True


class TestUpdateCustomer:
    def test_update_name(self):
        result = handle_update_customer(UpdateCustomerArgs(customer_id="cust-1", first_name="Alicia"))
        assert result["customer"]["firstName"] == "Alicia"
        assert result["customer"]["lastName"] == "Smith"  # unchanged

    def test_update_tags(self):
        result = handle_update_customer(UpdateCustomerArgs(customer_id="cust-2", tags=["new-tag"]))
        assert result["customer"]["tags"] == ["new-tag"]

    def test_update_email_rejects_duplicate(self):
        result = handle_update_customer(UpdateCustomerArgs(customer_id="cust-2", email="alice@example.com"))
        assert result["customer"] is None
        assert result["userErrors"][0]["field"] == "email"
        assert "already exists" in result["userErrors"][0]["message"]
        assert shopify_state.get_state().customers["cust-2"].email == "bob@example.com"

    def test_update_email_rejects_duplicate_before_any_mutation(self):
        result = handle_update_customer(
            UpdateCustomerArgs(
                customer_id="cust-2",
                first_name="Robert",
                last_name="Changed",
                email="alice@example.com",
                tags=["mutated"],
                note="should not stick",
                accepts_marketing=True,
            )
        )

        customer = shopify_state.get_state().customers["cust-2"]
        assert result["customer"] is None
        assert result["userErrors"][0]["field"] == "email"
        assert customer.firstName == "Bob"
        assert customer.lastName == "Jones"
        assert customer.email == "bob@example.com"
        assert customer.tags == []
        assert customer.note is None
        assert customer.acceptsMarketing is False

    def test_update_address(self):
        addr = {"address1": "789 Elm St", "city": "Denver"}
        result = handle_update_customer(UpdateCustomerArgs.model_validate({"customer_id": "cust-2", "address": addr}))
        assert result["customer"]["defaultAddress"]["address1"] == "789 Elm St"
        assert len(result["customer"]["addresses"]) == 1

    def test_update_address_dedupes_existing_default_address(self):
        addr = {"address1": "789 Elm St", "city": "Denver"}
        args = UpdateCustomerArgs.model_validate({"customer_id": "cust-2", "address": addr})

        handle_update_customer(args)
        result = handle_update_customer(args)

        assert result["customer"]["defaultAddress"]["address1"] == "789 Elm St"
        assert len(result["customer"]["addresses"]) == 1

    def test_update_nonexistent(self):
        result = handle_update_customer(UpdateCustomerArgs(customer_id="nonexistent", first_name="X"))
        assert result["customer"] is None
        assert len(result["userErrors"]) == 1


class TestSearchCustomers:
    def test_search_by_name(self):
        result = handle_search_customers(SearchCustomersArgs(query="alice"))
        assert result["totalCount"] == 1
        assert result["customers"][0]["firstName"] == "Alice"

    def test_search_by_email(self):
        result = handle_search_customers(SearchCustomersArgs(query="bob@example"))
        assert result["totalCount"] == 1
        assert result["customers"][0]["firstName"] == "Bob"

    def test_search_by_phone(self):
        result = handle_search_customers(SearchCustomersArgs(query="+1111"))
        assert result["totalCount"] == 1

    def test_search_no_match(self):
        result = handle_search_customers(SearchCustomersArgs(query="zzzzz"))
        assert result["totalCount"] == 0

    def test_search_case_insensitive(self):
        result = handle_search_customers(SearchCustomersArgs(query="ALICE"))
        assert result["totalCount"] == 1

    def test_search_word_and_first_and_last(self):
        # Multi-word query — all words must appear; "Alice Smith" matches.
        result = handle_search_customers(SearchCustomersArgs(query="alice smith"))
        assert result["totalCount"] == 1
        assert result["customers"][0]["firstName"] == "Alice"

    def test_search_misses_when_one_word_absent(self):
        # "alice jones" — neither customer has both words present in their record.
        result = handle_search_customers(SearchCustomersArgs(query="alice jones"))
        assert result["totalCount"] == 0

    def test_search_quoted_phrase_requires_adjacency(self):
        # Full adjacent phrase hits; reversed phrase misses.
        hit = handle_search_customers(SearchCustomersArgs(query='"alice smith"'))
        assert hit["totalCount"] == 1

        miss = handle_search_customers(SearchCustomersArgs(query='"smith alice"'))
        assert miss["totalCount"] == 0
