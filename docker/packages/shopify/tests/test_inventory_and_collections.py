"""Tests for inventory management and collection tools."""

import json

import pytest

from shopify import state as shopify_state
from shopify.models import (
    AddToCollectionArgs,
    CreateCollectionArgs,
    CreateDiscountCodeArgs,
    CreateShippingMethodArgs,
    DeleteProductArgs,
    GetCollectionArgs,
    GetInventoryArgs,
    ListCollectionsArgs,
    RemoveFromCollectionArgs,
    UpdateInventoryArgs,
)
from shopify.tools.catalog import handle_delete_product
from shopify.tools.discounts import handle_create_discount_code
from shopify.tools.inventory_collections import (
    handle_add_to_collection,
    handle_create_collection,
    handle_get_collection,
    handle_get_inventory,
    handle_list_collections,
    handle_remove_from_collection,
    handle_update_inventory,
)
from shopify.tools.shipping import handle_create_shipping_method


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
                        "handle": "widget",
                        "availableForSale": True,
                        "totalInventory": 60,
                        "variants": [
                            {
                                "id": "variant-1a",
                                "title": "Small",
                                "price": {"amount": "10.00", "currencyCode": "USD"},
                                "sku": "WIDGET-S",
                                "quantityAvailable": 50,
                                "currentlyNotInStock": False,
                                "availableForSale": True,
                            },
                            {
                                "id": "variant-1b",
                                "title": "Large",
                                "price": {"amount": "20.00", "currencyCode": "USD"},
                                "sku": "WIDGET-L",
                                "quantityAvailable": 10,
                                "currentlyNotInStock": False,
                                "availableForSale": True,
                            },
                        ],
                    },
                    "product-2": {
                        "id": "product-2",
                        "title": "Gadget",
                        "handle": "gadget",
                        "availableForSale": True,
                        "totalInventory": 3,
                        "variants": [
                            {
                                "id": "variant-2a",
                                "title": "Default",
                                "price": {"amount": "30.00", "currencyCode": "USD"},
                                "sku": "GADGET-1",
                                "quantityAvailable": 3,
                                "currentlyNotInStock": False,
                                "availableForSale": True,
                            },
                        ],
                    },
                },
                "carts": {},
                "orders": {},
                "customers": {},
                "collections": {},
                "policies": [],
                "counters": {
                    "cart_id": 1000,
                    "line_id": 1000,
                    "order_id": 2000,
                    "line_item_id": 3000,
                    "customer_id": 4000,
                    "collection_id": 5000,
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
# INVENTORY TESTS
# ============================================


class TestGetInventory:
    def test_get_all_inventory(self):
        result = handle_get_inventory(GetInventoryArgs())
        assert result["totalCount"] == 3  # 2 variants for product-1, 1 for product-2

    def test_get_inventory_by_product(self):
        result = handle_get_inventory(GetInventoryArgs(product_id="product-1"))
        assert result["totalCount"] == 2
        assert all(v["productId"] == "product-1" for v in result["inventory"])

    def test_get_inventory_low_stock(self):
        result = handle_get_inventory(GetInventoryArgs(low_stock_threshold=5))
        assert result["totalCount"] == 1
        assert result["inventory"][0]["sku"] == "GADGET-1"

    def test_get_inventory_nonexistent_product(self):
        result = handle_get_inventory(GetInventoryArgs(product_id="nonexistent"))
        assert result["totalCount"] == 0
        assert len(result["userErrors"]) == 1


class TestUpdateInventory:
    def test_update_quantity(self):
        result = handle_update_inventory(UpdateInventoryArgs(variant_id="variant-1a", quantity=100))
        assert result["userErrors"] == []
        item = result["inventoryItem"]
        assert item["previousQuantity"] == 50
        assert item["newQuantity"] == 100

    def test_update_to_zero_marks_out_of_stock(self):
        handle_update_inventory(UpdateInventoryArgs(variant_id="variant-2a", quantity=0))
        state = shopify_state.get_state()
        variant = state.products["product-2"]["variants"][0]
        assert variant["quantityAvailable"] == 0
        assert variant["currentlyNotInStock"] is True
        assert variant["availableForSale"] is False

    def test_update_updates_product_totals(self):
        handle_update_inventory(UpdateInventoryArgs(variant_id="variant-1a", quantity=200))
        state = shopify_state.get_state()
        product = state.products["product-1"]
        assert product["totalInventory"] == 210  # 200 + 10

    def test_update_nonexistent_variant(self):
        result = handle_update_inventory(UpdateInventoryArgs(variant_id="nonexistent", quantity=10))
        assert result["inventoryItem"] is None
        assert len(result["userErrors"]) == 1


# ============================================
# COLLECTION TESTS
# ============================================


class TestCreateCollection:
    def test_create_collection(self):
        result = handle_create_collection(CreateCollectionArgs(title="Summer Sale"))
        assert result["userErrors"] == []
        c = result["collection"]
        assert c["id"].startswith("gid://shopify/Collection/")
        assert c["title"] == "Summer Sale"
        assert c["handle"] == "summer-sale"
        assert c["productIds"] == []

    def test_create_with_products(self):
        result = handle_create_collection(
            CreateCollectionArgs(title="Featured", product_ids=["product-1", "product-2"])
        )
        assert result["collection"]["productIds"] == ["product-1", "product-2"]

    def test_create_rejects_title_with_empty_handle(self):
        before_counter = shopify_state.get_state().counters.collection_id

        result = handle_create_collection(CreateCollectionArgs(title="!!!"))

        assert result["collection"] is None
        assert result["userErrors"][0]["field"] == "title"
        assert shopify_state.get_state().counters.collection_id == before_counter

    def test_create_rejects_invalid_products(self):
        before_counter = shopify_state.get_state().counters.collection_id

        result = handle_create_collection(CreateCollectionArgs(title="Test", product_ids=["product-1", "nonexistent"]))

        assert result["collection"] is None
        assert "Product not found: nonexistent" in result["userErrors"][0]["message"]
        assert shopify_state.get_state().counters.collection_id == before_counter

    def test_create_duplicate_title(self):
        handle_create_collection(CreateCollectionArgs(title="Sale"))
        result = handle_create_collection(CreateCollectionArgs(title="Sale"))
        assert result["collection"] is None
        assert len(result["userErrors"]) == 1


class TestGetCollection:
    def test_get_existing(self):
        create_result = handle_create_collection(CreateCollectionArgs(title="Test", product_ids=["product-1"]))
        coll_id = create_result["collection"]["id"]

        result = handle_get_collection(GetCollectionArgs(collection_id=coll_id))
        assert result["userErrors"] == []
        assert result["collection"]["title"] == "Test"
        assert result["productCount"] == 1
        assert result["products"][0]["title"] == "Widget"

    def test_get_nonexistent(self):
        result = handle_get_collection(GetCollectionArgs(collection_id="nonexistent"))
        assert result["collection"] is None


class TestListCollections:
    def test_list_empty(self):
        result = handle_list_collections(ListCollectionsArgs())
        assert result["totalCount"] == 0

    def test_list_with_collections(self):
        handle_create_collection(CreateCollectionArgs(title="A Collection"))
        handle_create_collection(CreateCollectionArgs(title="B Collection"))
        result = handle_list_collections(ListCollectionsArgs())
        assert result["totalCount"] == 2
        # Sorted by title
        assert result["collections"][0]["title"] == "A Collection"

    def test_list_pagination(self):
        handle_create_collection(CreateCollectionArgs(title="One"))
        handle_create_collection(CreateCollectionArgs(title="Two"))
        result = handle_list_collections(ListCollectionsArgs(limit=1))
        assert len(result["collections"]) == 1
        assert result["pageInfo"]["hasNextPage"] is True


class TestAddToCollection:
    def test_add_products(self):
        create_result = handle_create_collection(CreateCollectionArgs(title="Test"))
        coll_id = create_result["collection"]["id"]

        result = handle_add_to_collection(
            AddToCollectionArgs(collection_id=coll_id, product_ids=["product-1", "product-2"])
        )
        assert result["added"] == ["product-1", "product-2"]
        assert len(result["collection"]["productIds"]) == 2

    def test_add_already_present(self):
        create_result = handle_create_collection(CreateCollectionArgs(title="Test", product_ids=["product-1"]))
        coll_id = create_result["collection"]["id"]

        result = handle_add_to_collection(AddToCollectionArgs(collection_id=coll_id, product_ids=["product-1"]))
        assert result["added"] == []
        assert result["alreadyInCollection"] == ["product-1"]

    def test_add_nonexistent_product(self):
        create_result = handle_create_collection(CreateCollectionArgs(title="Test"))
        coll_id = create_result["collection"]["id"]

        result = handle_add_to_collection(AddToCollectionArgs(collection_id=coll_id, product_ids=["nonexistent"]))
        assert result["added"] == []
        assert len(result["userErrors"]) == 1

    def test_add_mixed_products_applies_valid_additions_after_scan(self):
        create_result = handle_create_collection(CreateCollectionArgs(title="Test"))
        coll_id = create_result["collection"]["id"]

        result = handle_add_to_collection(
            AddToCollectionArgs(collection_id=coll_id, product_ids=["product-1", "nonexistent", "product-2"])
        )

        assert result["added"] == ["product-1", "product-2"]
        assert len(result["userErrors"]) == 1
        assert result["collection"]["productIds"] == ["product-1", "product-2"]

    def test_add_to_nonexistent_collection(self):
        result = handle_add_to_collection(AddToCollectionArgs(collection_id="nonexistent", product_ids=["product-1"]))
        assert result["collection"] is None


class TestRemoveFromCollection:
    def test_remove_products(self):
        create_result = handle_create_collection(
            CreateCollectionArgs(title="Test", product_ids=["product-1", "product-2"])
        )
        coll_id = create_result["collection"]["id"]

        result = handle_remove_from_collection(
            RemoveFromCollectionArgs(collection_id=coll_id, product_ids=["product-1"])
        )
        assert result["removed"] == ["product-1"]
        assert result["collection"]["productIds"] == ["product-2"]

    def test_remove_not_in_collection(self):
        create_result = handle_create_collection(CreateCollectionArgs(title="Test"))
        coll_id = create_result["collection"]["id"]

        result = handle_remove_from_collection(
            RemoveFromCollectionArgs(collection_id=coll_id, product_ids=["product-1"])
        )
        assert result["removed"] == []
        assert result["notInCollection"] == ["product-1"]

    def test_remove_from_nonexistent_collection(self):
        result = handle_remove_from_collection(
            RemoveFromCollectionArgs(collection_id="nonexistent", product_ids=["product-1"])
        )
        assert result["collection"] is None


class TestDeleteProductReferences:
    def test_delete_product_cleans_collection_and_discount_references(self):
        collection = handle_create_collection(CreateCollectionArgs(title="Test", product_ids=["product-1"]))[
            "collection"
        ]
        handle_create_discount_code(
            CreateDiscountCodeArgs(
                code="PRODUCT10", value="10", discount_type="FIXED_AMOUNT", product_ids=["product-1"]
            )
        )
        state = shopify_state.get_state()
        state.collections[collection["id"]].products.append("product-1")

        result = handle_delete_product(DeleteProductArgs(product_id="product-1"))

        assert result["userErrors"] == []
        assert "product-1" not in state.products
        assert state.collections[collection["id"]].productIds == []
        assert state.collections[collection["id"]].products == []
        assert state.discount_codes["gid://shopify/DiscountCode/10001"].productIds is None


class TestShippingMethods:
    def test_create_shipping_method_rejects_title_with_empty_id(self):
        result = handle_create_shipping_method(CreateShippingMethodArgs(title="!!!", price="5.00"))
        assert result["shippingMethod"] is None
        assert result["userErrors"][0]["field"] == "title"
