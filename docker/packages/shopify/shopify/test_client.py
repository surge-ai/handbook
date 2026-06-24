#!/usr/bin/env python3
"""
Simple test client for the Shopify MCP server.

Usage:
    uv run python packages/shopify/shopify/test_client.py
"""

import asyncio
import json
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Sample test data to load before testing
SAMPLE_DATA = {
    "products": {
        "gid://shopify/Product/1001": {
            "id": "gid://shopify/Product/1001",
            "title": "Hydrating Face Moisturizer",
            "description": "A lightweight, hydrating moisturizer for all skin types.",
            "descriptionHtml": "<p>A lightweight, hydrating moisturizer for all skin types.</p>",
            "handle": "hydrating-face-moisturizer",
            "productType": "Skincare",
            "vendor": "RhodeSkin",
            "tags": ["moisturizer", "hydrating", "face", "skincare"],
            "availableForSale": True,
            "priceRange": {
                "minVariantPrice": {"amount": "29.00", "currencyCode": "USD"},
                "maxVariantPrice": {"amount": "29.00", "currencyCode": "USD"},
            },
            "featuredImage": {
                "id": "gid://shopify/Image/1",
                "url": "https://example.com/moisturizer.jpg",
                "altText": "Hydrating Face Moisturizer",
            },
            "images": [
                {
                    "id": "gid://shopify/Image/1",
                    "url": "https://example.com/moisturizer.jpg",
                    "altText": "Hydrating Face Moisturizer",
                }
            ],
            "options": [{"id": "opt1", "name": "Size", "values": ["30ml", "50ml"]}],
            "variants": [
                {
                    "id": "gid://shopify/ProductVariant/1001-30",
                    "title": "30ml",
                    "price": {"amount": "29.00", "currencyCode": "USD"},
                    "availableForSale": True,
                    "sku": "MOIST-30",
                    "selectedOptions": [{"name": "Size", "value": "30ml"}],
                },
                {
                    "id": "gid://shopify/ProductVariant/1001-50",
                    "title": "50ml",
                    "price": {"amount": "45.00", "currencyCode": "USD"},
                    "availableForSale": True,
                    "sku": "MOIST-50",
                    "selectedOptions": [{"name": "Size", "value": "50ml"}],
                },
            ],
        },
        "gid://shopify/Product/1002": {
            "id": "gid://shopify/Product/1002",
            "title": "Lip Treatment Oil",
            "description": "Nourishing lip oil for soft, glossy lips.",
            "descriptionHtml": "<p>Nourishing lip oil for soft, glossy lips.</p>",
            "handle": "lip-treatment-oil",
            "productType": "Lip Care",
            "vendor": "RhodeSkin",
            "tags": ["lip", "oil", "treatment", "gloss"],
            "availableForSale": True,
            "priceRange": {
                "minVariantPrice": {"amount": "18.00", "currencyCode": "USD"},
                "maxVariantPrice": {"amount": "18.00", "currencyCode": "USD"},
            },
            "featuredImage": {
                "id": "gid://shopify/Image/2",
                "url": "https://example.com/lipoil.jpg",
                "altText": "Lip Treatment Oil",
            },
            "images": [
                {"id": "gid://shopify/Image/2", "url": "https://example.com/lipoil.jpg", "altText": "Lip Treatment Oil"}
            ],
            "options": [{"id": "opt2", "name": "Shade", "values": ["Clear", "Rose", "Berry"]}],
            "variants": [
                {
                    "id": "gid://shopify/ProductVariant/1002-clear",
                    "title": "Clear",
                    "price": {"amount": "18.00", "currencyCode": "USD"},
                    "availableForSale": True,
                    "sku": "LIP-CLEAR",
                    "selectedOptions": [{"name": "Shade", "value": "Clear"}],
                },
                {
                    "id": "gid://shopify/ProductVariant/1002-rose",
                    "title": "Rose",
                    "price": {"amount": "18.00", "currencyCode": "USD"},
                    "availableForSale": True,
                    "sku": "LIP-ROSE",
                    "selectedOptions": [{"name": "Shade", "value": "Rose"}],
                },
                {
                    "id": "gid://shopify/ProductVariant/1002-berry",
                    "title": "Berry",
                    "price": {"amount": "18.00", "currencyCode": "USD"},
                    "availableForSale": False,
                    "sku": "LIP-BERRY",
                    "selectedOptions": [{"name": "Shade", "value": "Berry"}],
                },
            ],
        },
    },
    "carts": {},
    "policies": [
        {
            "id": "gid://shopify/ShopPolicy/1",
            "title": "Return Policy",
            "body": "We offer a 30-day return policy for all unused products in original packaging. Returns are free for orders within the US.",
            "url": "https://shop.example.com/policies/return",
        },
        {
            "id": "gid://shopify/ShopPolicy/2",
            "title": "Shipping Policy",
            "body": "Free shipping on orders over $50. Standard shipping takes 5-7 business days. Express shipping available for $12.99.",
            "url": "https://shop.example.com/policies/shipping",
        },
    ],
    "counters": {"cart_id": 1000, "line_id": 1000},
}


def setup_test_data():
    """Write sample data to the state file."""
    state_file = Path(__file__).resolve().parents[1] / "shopify_data.json"
    with open(state_file, "w") as f:
        json.dump(SAMPLE_DATA, f, indent=2)
    print(f"Wrote test data to {state_file}")


async def main():
    # Setup test data first
    setup_test_data()

    # Server command - use uv run to execute the server module
    server_params = StdioServerParameters(
        command="uv",
        args=["run", "python", "-m", "shopify.server"],
        env=None,
    )

    print("\nStarting Shopify MCP server via stdio...")

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # Initialize the session
            await session.initialize()
            print("Session initialized!\n")

            # List available tools
            print("=" * 60)
            print("Available Tools:")
            print("=" * 60)
            tools = await session.list_tools()
            for tool in tools.tools:
                print(f"  - {tool.name}")
            print()

            # Test 1: search_shop_catalog
            print("=" * 60)
            print("Test 1: search_shop_catalog - Search for 'moisturizer'")
            print("=" * 60)
            result = await session.call_tool(
                "search_shop_catalog",
                arguments={
                    "query": "moisturizer",
                    "context": "Looking for face products",
                    "limit": 5,
                },
            )
            data = json.loads(result.content[0].text)  # type: ignore
            print(f"Found {data['totalCount']} products")
            for product in data.get("nodes", []):
                print(f"  - {product['title']} (${product['priceRange']['minVariantPrice']['amount']})")
            print(f"Available filters: {len(data.get('productFilters', []))}")
            print()

            # Test 2: get_product_details
            print("=" * 60)
            print("Test 2: get_product_details - Get moisturizer details")
            print("=" * 60)
            result = await session.call_tool(
                "get_product_details",
                arguments={
                    "product_id": "gid://shopify/Product/1001",
                    "options": {"Size": "50ml"},
                },
            )
            data = json.loads(result.content[0].text)  # type: ignore
            if data.get("product"):
                print(f"Product: {data['product']['title']}")
                print(f"Variants: {len(data['product'].get('variants', []))}")
                if data.get("selectedVariant"):
                    print(
                        f"Selected variant: {data['selectedVariant']['title']} - ${data['selectedVariant']['price']['amount']}"
                    )
            print()

            # Test 3: update_cart - Create new cart and add items
            print("=" * 60)
            print("Test 3: update_cart - Create cart and add moisturizer")
            print("=" * 60)
            result = await session.call_tool(
                "update_cart",
                arguments={
                    "add_items": [
                        {"merchandiseId": "gid://shopify/ProductVariant/1001-50", "quantity": 1},
                        {"merchandiseId": "gid://shopify/ProductVariant/1002-rose", "quantity": 2},
                    ],
                    "note": "Test order",
                },
            )
            data = json.loads(result.content[0].text)  # type: ignore
            cart_id = data.get("id")
            print(f"Cart created: {cart_id}")
            print(f"Lines: {len(data.get('lines', []))}")
            print(f"Total: ${data.get('cost', {}).get('totalAmount', {}).get('amount', '0')}")
            for line in data.get("lines", []):
                print(
                    f"  - {line['merchandise']['title']} x{line['quantity']} = ${line['cost']['totalAmount']['amount']}"
                )
            print()

            # Test 4: get_cart
            print("=" * 60)
            print("Test 4: get_cart - Retrieve the cart we just created")
            print("=" * 60)
            result = await session.call_tool(
                "get_cart",
                arguments={
                    "cart_id": cart_id,
                },
            )
            data = json.loads(result.content[0].text)  # type: ignore
            if data.get("id"):
                print(f"Cart ID: {data['id']}")
                print(f"Checkout URL: {data['checkoutUrl']}")
                print(f"Total Quantity: {data['totalQuantity']}")
                print(f"Total: ${data['cost']['totalAmount']['amount']}")
            else:
                print(f"Error: {data}")
            print()

            # Test 5: update_cart - Update quantity
            print("=" * 60)
            print("Test 5: update_cart - Update quantity of first item")
            print("=" * 60)
            first_line_id = data.get("lines", [{}])[0].get("id") if data.get("lines") else None
            if first_line_id:
                result = await session.call_tool(
                    "update_cart",
                    arguments={
                        "cart_id": cart_id,
                        "update_items": [{"id": first_line_id, "quantity": 3}],
                    },
                )
                data = json.loads(result.content[0].text)  # type: ignore
                print(f"Updated cart total: ${data.get('cost', {}).get('totalAmount', {}).get('amount', '0')}")
                for line in data.get("lines", []):
                    print(f"  - {line['merchandise']['title']} x{line['quantity']}")
            print()

            # Test 6: search_shop_policies_and_faqs
            print("=" * 60)
            print("Test 6: search_shop_policies_and_faqs - Return policy")
            print("=" * 60)
            result = await session.call_tool(
                "search_shop_policies_and_faqs",
                arguments={
                    "query": "return policy",
                },
            )
            data = json.loads(result.content[0].text)  # type: ignore
            print(f"Found {len(data.get('results', []))} policies")
            if data.get("answer"):
                print(f"Answer: {data['answer'][:100]}...")
            print()

            print("=" * 60)
            print("All tests completed!")
            print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
