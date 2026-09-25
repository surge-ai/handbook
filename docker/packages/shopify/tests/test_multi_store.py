"""Tests for multi-store support."""

import json

import pytest

from shopify import state as shopify_state
from shopify.models import GetProductDetailsArgs, SearchShopCatalogArgs
from shopify.state import get_all_stores, get_state, set_active_store
from shopify.tools.catalog import handle_get_product_details, handle_search_shop_catalog


@pytest.fixture
def multi_store_data(tmp_path):
    data_file = tmp_path / "shopify_data.json"
    data_file.write_text(
        json.dumps(
            {
                "stores": {
                    "jacks-hardware": {
                        "products": {
                            "prod-h1": {
                                "id": "prod-h1",
                                "title": "Hammer",
                                "description": "A sturdy hammer",
                                "handle": "hammer",
                                "productType": "Tools",
                                "vendor": "Jacks",
                                "tags": ["tools", "hardware"],
                                "availableForSale": True,
                                "priceRange": {
                                    "minVariantPrice": {"amount": "15.00", "currencyCode": "USD"},
                                    "maxVariantPrice": {"amount": "15.00", "currencyCode": "USD"},
                                },
                                "variants": [
                                    {
                                        "id": "var-h1",
                                        "title": "Default",
                                        "price": {"amount": "15.00", "currencyCode": "USD"},
                                        "availableForSale": True,
                                    }
                                ],
                            }
                        },
                        "carts": {},
                        "orders": {},
                        "customers": {},
                        "collections": {},
                        "reviews": {},
                        "returns": {},
                        "discount_codes": {},
                        "shipping_methods": {},
                        "policies": [],
                        "counters": {"cart_id": 1000, "line_id": 1000},
                    },
                    "jims-tools": {
                        "products": {
                            "prod-t1": {
                                "id": "prod-t1",
                                "title": "Drill",
                                "description": "A power drill",
                                "handle": "drill",
                                "productType": "Power Tools",
                                "vendor": "Jims",
                                "tags": ["tools", "power"],
                                "availableForSale": True,
                                "priceRange": {
                                    "minVariantPrice": {"amount": "89.00", "currencyCode": "USD"},
                                    "maxVariantPrice": {"amount": "89.00", "currencyCode": "USD"},
                                },
                                "variants": [
                                    {
                                        "id": "var-t1",
                                        "title": "Default",
                                        "price": {"amount": "89.00", "currencyCode": "USD"},
                                        "availableForSale": True,
                                    }
                                ],
                            }
                        },
                        "carts": {},
                        "orders": {},
                        "customers": {},
                        "collections": {},
                        "reviews": {},
                        "returns": {},
                        "discount_codes": {},
                        "shipping_methods": {},
                        "policies": [],
                        "counters": {"cart_id": 1000, "line_id": 1000},
                    },
                }
            }
        )
    )
    return data_file


@pytest.fixture(autouse=True)
def _patch_state(multi_store_data, monkeypatch):
    monkeypatch.setattr(shopify_state, "_STATE_FILE", multi_store_data)
    shopify_state._current_state = None
    shopify_state._stores.clear()
    shopify_state.load_state()


class TestListStores:
    def test_discovers_all_stores(self):
        stores = get_all_stores()
        assert len(stores) == 2
        assert "jacks-hardware" in stores
        assert "jims-tools" in stores


class TestStoreIsolation:
    def test_products_isolated(self):
        set_active_store("jacks-hardware")
        jacks = get_state()
        assert "prod-h1" in jacks.products
        assert "prod-t1" not in jacks.products

        set_active_store("jims-tools")
        jims = get_state()
        assert "prod-t1" in jims.products
        assert "prod-h1" not in jims.products

    def test_search_scoped_to_store(self):
        set_active_store("jacks-hardware")
        result = handle_search_shop_catalog(SearchShopCatalogArgs(query="hammer", context="browsing"))
        assert result["totalCount"] == 1
        assert result["nodes"][0]["title"] == "Hammer"

    def test_search_other_store(self):
        set_active_store("jims-tools")
        result = handle_search_shop_catalog(SearchShopCatalogArgs(query="hammer", context="browsing"))
        assert result["totalCount"] == 0

    def test_get_product_from_correct_store(self):
        set_active_store("jacks-hardware")
        result = handle_get_product_details(GetProductDetailsArgs(product_id="prod-h1"))
        # The handler returns the product dict or a wrapped response
        product = result.get("product", result)
        assert product.get("title") == "Hammer" or result.get("id") == "prod-h1"

    def test_invalid_store(self):
        with pytest.raises(ValueError, match="not found"):
            set_active_store("nonexistent")


class TestStoreIdInToolSchemas:
    """Every MCP tool wrapped with ``@_with_store`` must declare ``store_id``
    in its signature so FastMCP exposes the parameter in the tool's input
    schema. Without that, the agent has no way to pass ``store_id`` and the
    wrapper falls back to ``"default"`` — which throws in worlds whose only
    stores are named (e.g. jacks-hardware / jims-tools)."""

    def test_every_with_store_tool_advertises_store_id(self):
        import asyncio

        from shopify.server import mcp

        tools = asyncio.run(mcp.list_tools())
        # Every tool registered against the multi-store helper must expose
        # store_id; otherwise an agent in a multi-store world has no way to
        # target the right tenant.
        missing = []
        for tool in tools:
            params = (getattr(tool, "parameters", None) or {}).get("properties", {})
            # Tools that legitimately operate above the per-store layer:
            # list_stores enumerates stores; export/import_state round-trip the
            # whole multi-store snapshot. Everything else routes through
            # @_with_store and therefore needs store_id exposed.
            if tool.name in {"list_stores", "export_state", "import_state"}:
                continue
            if "store_id" not in params:
                missing.append(tool.name)
        assert missing == [], f"Tools missing store_id in input schema: {missing}"


class TestWorkspaceSwitch:
    """``set_agent_workspace`` must drop cached stores so the new workspace
    actually loads — not just reset the legacy ``_current_state`` ref."""

    def test_switching_workspace_loads_new_state(self, tmp_path):
        # set_agent_workspace(ws) puts state at ws.parent / "external_services" /
        # "shopify_data.json", so each workspace's parent gets its own
        # external_services dir.
        def _make_ws(name: str, products: dict) -> str:
            base = tmp_path / name
            ws_dir = base / "agent_workspace"
            ws_dir.mkdir(parents=True)
            ext = base / "external_services"
            ext.mkdir(parents=True)
            (ext / "shopify_data.json").write_text(
                json.dumps(
                    {
                        "products": products,
                        "carts": {},
                        "orders": {},
                        "customers": {},
                        "collections": {},
                        "reviews": {},
                        "returns": {},
                        "discount_codes": {},
                        "shipping_methods": {},
                        "policies": [],
                        "counters": {"cart_id": 1000, "line_id": 1000},
                    }
                )
            )
            return str(ws_dir)

        ws_a = _make_ws("a", {"prod-a": {"id": "prod-a", "title": "From A"}})
        ws_b = _make_ws("b", {"prod-b": {"id": "prod-b", "title": "From B"}})

        # Reset so the autouse fixture's load doesn't shadow.
        shopify_state._stores.clear()
        shopify_state._current_state = None

        shopify_state.set_agent_workspace(ws_a)
        shopify_state.load_state()
        assert "prod-a" in shopify_state.get_state().products

        shopify_state.set_agent_workspace(ws_b)
        shopify_state.load_state()
        # Without clearing _stores in set_agent_workspace, load_state's
        # `if _stores: return` early-exit would still serve workspace A's data.
        loaded = shopify_state.get_state().products
        assert "prod-b" in loaded
        assert "prod-a" not in loaded


class TestBackwardCompat:
    def test_single_store_flat_format(self, tmp_path, monkeypatch):
        """Single-store flat format should load as 'default'."""
        flat_file = tmp_path / "flat.json"
        flat_file.write_text(
            json.dumps(
                {
                    "products": {"p1": {"id": "p1", "title": "Widget", "variants": []}},
                    "carts": {},
                    "orders": {},
                    "customers": {},
                    "collections": {},
                    "reviews": {},
                    "returns": {},
                    "discount_codes": {},
                    "shipping_methods": {},
                    "policies": [],
                    "counters": {"cart_id": 1000},
                }
            )
        )
        monkeypatch.setattr(shopify_state, "_STATE_FILE", flat_file)
        shopify_state._current_state = None
        shopify_state._stores.clear()
        shopify_state.load_state()

        stores = get_all_stores()
        assert "default" in stores
        assert len(stores) == 1
        assert "p1" in stores["default"].products
