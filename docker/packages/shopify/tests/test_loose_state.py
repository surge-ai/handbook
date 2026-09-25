"""LooseProduct / LooseProductOption tolerate minimal synthetic payloads."""

import json

import pytest

from shopify import state as shopify_state
from shopify.models import LooseCart, LooseProduct, LooseProductOption, ShopifyStateModel


def test_loose_product_accepts_minimal_payload_without_handle_or_price_range():
    product = LooseProduct.model_validate(
        {
            "id": "gid://shopify/Product/1",
            "title": "Minimal",
            "variants": [],
        }
    )
    assert product.handle is None
    assert product.priceRange is None


def test_loose_product_option_without_id():
    option = LooseProductOption.model_validate({"name": "Color", "values": ["Red", "Blue"]})
    assert option.id is None


def test_shopify_state_round_trips_minimal_product():
    state = ShopifyStateModel.model_validate(
        {
            "products": {
                "gid://shopify/Product/1": {
                    "id": "gid://shopify/Product/1",
                    "title": "Minimal",
                    "variants": [],
                }
            }
        }
    )
    reloaded = ShopifyStateModel.model_validate(state.model_dump(mode="json", exclude_none=True))
    assert reloaded == state


def test_state_rejects_product_key_mismatch():
    with pytest.raises(ValueError, match="products key"):
        ShopifyStateModel.model_validate(
            {
                "products": {
                    "gid://shopify/Product/wrong": {
                        "id": "gid://shopify/Product/1",
                        "title": "Minimal",
                        "variants": [],
                    }
                }
            }
        )


def test_state_rejects_collection_missing_product_reference():
    with pytest.raises(ValueError, match="references missing product"):
        ShopifyStateModel.model_validate(
            {
                "collections": {
                    "gid://shopify/Collection/1": {
                        "id": "gid://shopify/Collection/1",
                        "title": "Featured",
                        "productIds": ["gid://shopify/Product/missing"],
                    }
                }
            }
        )


def test_save_state_rolls_back_invalid_in_memory_state(tmp_path, monkeypatch):
    data_file = tmp_path / "shopify_data.json"
    monkeypatch.setattr(shopify_state, "_STATE_FILE", data_file)
    shopify_state._stores.clear()
    shopify_state._current_state = None
    shopify_state._last_valid_state_snapshot = None
    shopify_state._active_store_id = "default"
    shopify_state.state_from_json(
        {
            "products": {
                "gid://shopify/Product/1": {
                    "id": "gid://shopify/Product/1",
                    "title": "Valid",
                    "variants": [],
                }
            }
        }
    )

    state = shopify_state.get_state()
    state.products["gid://shopify/Product/1"].id = "gid://shopify/Product/wrong"

    with pytest.raises(ValueError, match="products key"):
        shopify_state.save_state()

    restored = shopify_state.get_state()
    assert restored.products["gid://shopify/Product/1"].id == "gid://shopify/Product/1"
    persisted = json.loads(data_file.read_text())
    assert persisted["products"]["gid://shopify/Product/1"]["id"] == "gid://shopify/Product/1"


def test_create_cart_validates_before_advancing_counter(tmp_path, monkeypatch):
    data_file = tmp_path / "shopify_data.json"
    monkeypatch.setattr(shopify_state, "_STATE_FILE", data_file)
    shopify_state._stores.clear()
    shopify_state._current_state = None
    shopify_state._last_valid_state_snapshot = None
    shopify_state._active_store_id = "default"
    shopify_state.state_from_json({"counters": {"cart_id": 1000}})

    def reject_cart(_data):
        raise ValueError("invalid cart")

    monkeypatch.setattr(LooseCart, "model_validate", reject_cart)

    with pytest.raises(ValueError, match="invalid cart"):
        shopify_state.create_cart()

    state = shopify_state.get_state()
    assert state.counters.cart_id == 1000
    assert state.carts == {}


def test_state_recovers_counters_from_seeded_ids(tmp_path, monkeypatch):
    data_file = tmp_path / "shopify_data.json"
    monkeypatch.setattr(shopify_state, "_STATE_FILE", data_file)
    shopify_state._stores.clear()
    shopify_state._current_state = None
    shopify_state._last_valid_state_snapshot = None
    shopify_state._active_store_id = "default"

    product_id = "gid://shopify/Product/8500"
    variant_id = "gid://shopify/ProductVariant/9050-default"
    cart_id = "gid://shopify/Cart/c1010"
    cart_line_id = "gid://shopify/CartLine/1025"
    order_id = "gid://shopify/Order/o2020"
    order_line_id = "gid://shopify/OrderLineItem/3030"
    customer_id = "gid://shopify/Customer/4040"
    collection_id = "gid://shopify/Collection/5050"
    review_id = "gid://shopify/Review/6060"
    return_id = "gid://shopify/Return/7070"
    discount_id = "gid://shopify/DiscountCode/10050"
    policy_id = "gid://shopify/ShopPolicy/11050"
    money = {"amount": "10.00", "currencyCode": "USD"}

    shopify_state.state_from_json(
        {
            "products": {
                product_id: {
                    "id": product_id,
                    "title": "Seed Product",
                    "variants": [
                        {
                            "id": variant_id,
                            "title": "Default",
                            "price": money,
                            "availableForSale": True,
                        }
                    ],
                }
            },
            "carts": {
                cart_id: {
                    "id": cart_id,
                    "lines": [
                        {
                            "id": cart_line_id,
                            "quantity": 1,
                            "merchandise": {
                                "id": variant_id,
                                "title": "Default",
                                "price": money,
                                "product": {"id": product_id, "title": "Seed Product"},
                            },
                            "cost": {
                                "amountPerQuantity": money,
                                "subtotalAmount": money,
                                "totalAmount": money,
                            },
                        }
                    ],
                }
            },
            "orders": {
                order_id: {
                    "id": order_id,
                    "name": "#2020",
                    "createdAt": "2026-01-01T00:00:00Z",
                    "updatedAt": "2026-01-01T00:00:00Z",
                    "financialStatus": "PAID",
                    "fulfillmentStatus": "UNFULFILLED",
                    "lineItems": [
                        {
                            "id": order_line_id,
                            "title": "Seed Product",
                            "quantity": 1,
                            "variantId": variant_id,
                            "productId": product_id,
                            "price": money,
                            "totalPrice": money,
                        }
                    ],
                    "subtotalPrice": money,
                    "totalPrice": money,
                }
            },
            "customers": {
                customer_id: {
                    "id": customer_id,
                    "email": "seed@example.com",
                    "createdAt": "2026-01-01T00:00:00Z",
                    "updatedAt": "2026-01-01T00:00:00Z",
                }
            },
            "collections": {
                collection_id: {
                    "id": collection_id,
                    "title": "Seed Collection",
                    "productIds": [product_id],
                }
            },
            "reviews": {
                review_id: {
                    "id": review_id,
                    "productId": product_id,
                    "rating": 5,
                    "author": "Seed Reviewer",
                }
            },
            "returns": {
                return_id: {
                    "id": return_id,
                    "orderId": order_id,
                    "status": "REQUESTED",
                    "lineItems": [{"orderLineItemId": order_line_id, "quantity": 1}],
                    "createdAt": "2026-01-01T00:00:00Z",
                    "updatedAt": "2026-01-01T00:00:00Z",
                }
            },
            "discount_codes": {
                "SAVE10": {
                    "id": discount_id,
                    "code": "SAVE10",
                    "discountType": "FIXED_AMOUNT",
                    "value": "10.00",
                    "createdAt": "2026-01-01T00:00:00Z",
                    "updatedAt": "2026-01-01T00:00:00Z",
                }
            },
            "policies": [{"id": policy_id, "title": "Return Policy", "body": "<p>Returns accepted.</p>"}],
            "counters": {
                "cart_id": 1000,
                "line_id": 1000,
                "product_id": 8000,
                "variant_id": 9000,
                "order_id": 2000,
                "line_item_id": 3000,
                "customer_id": 4000,
                "collection_id": 5000,
                "review_id": 6000,
                "return_id": 7000,
                "discount_id": 10000,
                "policy_id": 11000,
            },
        }
    )

    counters = shopify_state.get_state().counters
    assert counters.cart_id == 1010
    assert counters.line_id == 1025
    assert counters.product_id == 8500
    assert counters.variant_id == 9050
    assert counters.order_id == 2020
    assert counters.line_item_id == 3030
    assert counters.customer_id == 4040
    assert counters.collection_id == 5050
    assert counters.review_id == 6060
    assert counters.return_id == 7070
    assert counters.discount_id == 10050
    assert counters.policy_id == 11050


def test_state_rejects_duplicate_discount_codes():
    discount = {
        "discountType": "FIXED_AMOUNT",
        "value": "10.00",
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
    }

    with pytest.raises(ValueError, match="duplicated"):
        ShopifyStateModel.model_validate(
            {
                "discount_codes": {
                    "gid://shopify/DiscountCode/1": {
                        **discount,
                        "id": "gid://shopify/DiscountCode/1",
                        "code": "SALE",
                    },
                    "sale": {
                        **discount,
                        "id": "gid://shopify/DiscountCode/2",
                        "code": "sale",
                    },
                }
            }
        )


def test_state_rejects_duplicate_gift_card_codes():
    with pytest.raises(ValueError, match="duplicated"):
        ShopifyStateModel.model_validate(
            {
                "gift_cards": {
                    "gid://shopify/GiftCard/1": {
                        "id": "gid://shopify/GiftCard/1",
                        "code": "GIFT",
                        "balance": {"amount": "10.00", "currencyCode": "USD"},
                    },
                    "gift": {
                        "id": "gid://shopify/GiftCard/2",
                        "code": "gift",
                        "balance": {"amount": "20.00", "currencyCode": "USD"},
                    },
                }
            }
        )
