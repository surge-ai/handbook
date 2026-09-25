from __future__ import annotations

from starlette.testclient import TestClient

from shopify import state as shopify_state
from shopify.viewer import create_shopify_viewer_app


def test_products_api_handles_strict_state_models_with_omitted_optional_fields() -> None:
    shopify_state.state_from_json(
        {
            "products": {
                "product-1": {
                    "id": "product-1",
                    "title": "Test Product",
                    "variants": [
                        {
                            "id": "variant-1",
                            "title": "Default",
                            "price": {"amount": "10.00", "currencyCode": "USD"},
                        }
                    ],
                }
            },
            "carts": {},
            "policies": [],
            "faqs": [],
        }
    )

    client = TestClient(create_shopify_viewer_app(), raise_server_exceptions=False)
    response = client.get("/api/products")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["products"][0]["id"] == "product-1"
    assert payload["products"][0]["price"] is None


def test_policies_and_cart_detail_api_serialize_strict_state_models() -> None:
    shopify_state.state_from_json(
        {
            "products": {},
            "carts": {
                "cart-1": {
                    "id": "cart-1",
                    "totalQuantity": 0,
                    "note": "Viewer smoke cart",
                }
            },
            "policies": [
                {
                    "type": "REFUND_POLICY",
                    "title": "Refund Policy",
                    "body": "Returns accepted within 30 days.",
                }
            ],
            "faqs": [],
        }
    )

    client = TestClient(create_shopify_viewer_app(), raise_server_exceptions=False)

    policies_response = client.get("/api/policies")
    assert policies_response.status_code == 200
    assert policies_response.json()["policies"][0]["title"] == "Refund Policy"

    cart_response = client.get("/api/carts/cart-1")
    assert cart_response.status_code == 200
    assert cart_response.json()["cart"]["note"] == "Viewer smoke cart"
