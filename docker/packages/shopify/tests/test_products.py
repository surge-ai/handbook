"""Tests for product management tools."""

import json

import pytest

from shopify import state as shopify_state
from shopify.models import (
    CreateProductArgs,
    DeleteProductArgs,
    GetProductDetailsArgs,
    LooseCart,
    UpdateProductArgs,
)
from shopify.tools.catalog import (
    handle_create_product,
    handle_delete_product,
    handle_get_product_details,
    handle_update_product,
)


@pytest.fixture
def shopify_data(tmp_path):
    data_file = tmp_path / "shopify_data.json"
    data_file.write_text(
        json.dumps(
            {
                "products": {
                    "product-1": {
                        "id": "product-1",
                        "title": "Existing Widget",
                        "handle": "existing-widget",
                        "description": "A widget",
                        "productType": "Gadgets",
                        "vendor": "WidgetCo",
                        "tags": ["widget"],
                        "availableForSale": True,
                        "priceRange": {
                            "minVariantPrice": {"amount": "10.00", "currencyCode": "USD"},
                            "maxVariantPrice": {"amount": "10.00", "currencyCode": "USD"},
                        },
                        "variants": [
                            {
                                "id": "variant-1",
                                "title": "Default",
                                "price": {"amount": "10.00", "currencyCode": "USD"},
                                "availableForSale": True,
                                "quantityAvailable": 50,
                            }
                        ],
                    }
                },
                "carts": {},
                "orders": {},
                "customers": {},
                "collections": {
                    "coll-1": {
                        "id": "coll-1",
                        "title": "Featured",
                        "productIds": ["product-1"],
                    }
                },
                "reviews": {
                    "rev-1": {
                        "id": "rev-1",
                        "productId": "product-1",
                        "rating": 5,
                        "author": "Alice",
                    }
                },
                "returns": {},
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


class TestCreateProduct:
    def test_create_basic_product(self):
        result = handle_create_product(
            CreateProductArgs(title="New Gadget", description="A new gadget", vendor="GadgetCo")
        )
        assert result["userErrors"] == []
        p = result["product"]
        assert p["id"].startswith("gid://shopify/Product/")
        assert p["title"] == "New Gadget"
        assert p["handle"] == "new-gadget"
        assert p["vendor"] == "GadgetCo"
        assert len(p["variants"]) == 1  # default variant

    def test_create_with_variants(self):
        result = handle_create_product(
            CreateProductArgs.model_validate(
                {
                    "title": "T-Shirt",
                    "variants": [
                        {"title": "Small", "price": "25.00", "sku": "TS-S", "quantityAvailable": 100},
                        {"title": "Large", "price": "25.00", "sku": "TS-L", "quantityAvailable": 50},
                    ],
                }
            )
        )
        p = result["product"]
        assert len(p["variants"]) == 2
        assert p["variants"][0]["sku"] == "TS-S"
        assert p["priceRange"]["minVariantPrice"]["amount"] == "25.00"
        assert p["totalInventory"] == 150

    def test_create_with_tags(self):
        result = handle_create_product(CreateProductArgs(title="Tagged Item", tags=["sale", "new-arrival"]))
        assert result["product"]["tags"] == ["sale", "new-arrival"]

    def test_create_rejects_title_with_empty_handle(self):
        result = handle_create_product(CreateProductArgs(title="!!!"))
        assert result["product"] is None
        assert result["userErrors"][0]["field"] == "title"

    def test_create_rejects_duplicate_handle(self):
        result = handle_create_product(CreateProductArgs(title="Existing Widget"))
        assert result["product"] is None
        assert result["userErrors"][0]["field"] == "title"
        assert "already exists" in result["userErrors"][0]["message"]

    def test_create_product_in_state(self):
        result = handle_create_product(CreateProductArgs(title="Stored"))
        pid = result["product"]["id"]
        state = shopify_state.get_state()
        assert pid in state.products


class TestUpdateProduct:
    def test_update_title(self):
        result = handle_update_product(UpdateProductArgs(product_id="product-1", title="Updated Widget"))
        assert result["userErrors"] == []
        assert result["product"]["title"] == "Updated Widget"
        assert result["product"]["handle"] == "updated-widget"

    def test_update_rejects_title_with_empty_handle(self):
        result = handle_update_product(UpdateProductArgs(product_id="product-1", title="!!!"))
        assert result["product"] is None
        assert result["userErrors"][0]["field"] == "title"
        assert shopify_state.get_state().products["product-1"].title == "Existing Widget"

    def test_update_rejects_duplicate_handle(self):
        handle_create_product(CreateProductArgs(title="New Gadget"))
        result = handle_update_product(UpdateProductArgs(product_id="product-1", title="New Gadget"))
        assert result["product"] is None
        assert result["userErrors"][0]["field"] == "title"
        assert "already exists" in result["userErrors"][0]["message"]
        assert shopify_state.get_state().products["product-1"].handle == "existing-widget"

    def test_update_description(self):
        result = handle_update_product(UpdateProductArgs(product_id="product-1", description="New description"))
        assert result["product"]["description"] == "New description"

    def test_update_tags(self):
        result = handle_update_product(UpdateProductArgs(product_id="product-1", tags=["new-tag", "updated"]))
        assert result["product"]["tags"] == ["new-tag", "updated"]

    def test_update_nonexistent(self):
        result = handle_update_product(UpdateProductArgs(product_id="nonexistent", title="X"))
        assert result["product"] is None
        assert len(result["userErrors"]) == 1


class TestGetProductDetails:
    def test_country_and_language_are_reported_as_noop_hints(self):
        result = handle_get_product_details(GetProductDetailsArgs(product_id="product-1", country="US", language="EN"))
        assert result["product"] is not None
        assert result["localization"] == {"country": "US", "language": "EN", "applied": False}


class TestDeleteProduct:
    def test_delete_product(self):
        result = handle_delete_product(DeleteProductArgs(product_id="product-1"))
        assert result["deletedProductId"] == "product-1"
        state = shopify_state.get_state()
        assert "product-1" not in state.products

    def test_delete_removes_from_collections(self):
        handle_delete_product(DeleteProductArgs(product_id="product-1"))
        state = shopify_state.get_state()
        assert "product-1" not in state.collections["coll-1"]["productIds"]

    def test_delete_removes_reviews(self):
        handle_delete_product(DeleteProductArgs(product_id="product-1"))
        state = shopify_state.get_state()
        assert "rev-1" not in state.reviews

    def test_delete_removes_deleted_variant_from_open_carts(self):
        state = shopify_state.get_state()
        state.carts["cart-1"] = LooseCart.model_validate(
            {
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
                            "title": "Default",
                            "product": {"id": "product-1", "title": "Existing Widget"},
                            "price": {"amount": "10.00", "currencyCode": "USD"},
                        },
                        "cost": {
                            "amountPerQuantity": {"amount": "10.00", "currencyCode": "USD"},
                            "subtotalAmount": {"amount": "20.00", "currencyCode": "USD"},
                            "totalAmount": {"amount": "20.00", "currencyCode": "USD"},
                        },
                    }
                ],
                "cost": {
                    "subtotalAmount": {"amount": "20.00", "currencyCode": "USD"},
                    "totalAmount": {"amount": "20.00", "currencyCode": "USD"},
                    "checkoutChargeAmount": {"amount": "20.00", "currencyCode": "USD"},
                },
                "buyerIdentity": {},
                "totalQuantity": 2,
            }
        )

        handle_delete_product(DeleteProductArgs(product_id="product-1"))

        cart = shopify_state.get_state().carts["cart-1"]
        assert cart.lines == []
        assert cart.totalQuantity == 0
        assert cart.cost is not None
        assert cart.cost.totalAmount.amount == "0.00"

    def test_delete_nonexistent(self):
        result = handle_delete_product(DeleteProductArgs(product_id="nonexistent"))
        assert result["deletedProductId"] is None
        assert len(result["userErrors"]) == 1
