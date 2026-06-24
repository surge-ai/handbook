"""Tests for the _snapshot_on_write decorator — final.json written after every write tool call."""

import json

import pytest


@pytest.fixture
def shopify_data(tmp_path):
    """Seed minimal Shopify state."""
    data_file = tmp_path / "shopify_data.json"
    data_file.write_text(
        json.dumps(
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
                                "availableForSale": True,
                            }
                        ],
                    }
                },
                "carts": {},
                "shipping_methods": {
                    "standard": {
                        "id": "standard",
                        "title": "Standard",
                        "price": {"amount": "5.99", "currencyCode": "USD"},
                        "active": True,
                    },
                },
                "policies": [],
                "faqs": [],
            }
        )
    )
    return data_file


@pytest.fixture
def outputdir(tmp_path):
    out = tmp_path / "output" / "shopify"
    out.mkdir(parents=True)
    return out


@pytest.fixture(autouse=True)
def _patch_globals(shopify_data, outputdir, monkeypatch):
    """Patch Shopify state globals for isolated testing."""
    from shopify import state as shopify_state

    # Reset state and load from our test data
    monkeypatch.setattr(shopify_state, "_STATE_FILE", shopify_data)
    shopify_state._current_state = None
    shopify_state._stores.clear()
    shopify_state._active_store_id = "default"
    shopify_state.set_snapshot_paths(final_path=None, bundle_state_path=None)
    shopify_state.load_state()

    shopify_state.set_snapshot_paths(final_path=outputdir / "final.json")
    yield
    shopify_state.set_snapshot_paths(final_path=None, bundle_state_path=None)


@pytest.mark.asyncio
async def test_update_cart_writes_final_json(outputdir):
    from shopify.server import update_cart

    final = outputdir / "final.json"
    assert not final.exists()

    result = await update_cart(
        add_items=[{"merchandiseId": "variant-1", "quantity": 1}],
    )
    assert "id" in result  # cart was created
    assert final.exists(), "final.json must be written after update_cart"

    snapshot = json.loads(final.read_text())
    assert len(snapshot.get("carts", {})) > 0


@pytest.mark.asyncio
async def test_search_does_not_write_final_json(outputdir):
    from shopify.server import search_shop_catalog

    final = outputdir / "final.json"
    await search_shop_catalog(query="test", context="browsing")

    assert not final.exists(), "final.json must NOT be written after a read-only tool"


@pytest.mark.asyncio
async def test_create_order_writes_final_json(outputdir):
    from shopify.server import create_order, update_cart

    final = outputdir / "final.json"

    # First create a cart with items
    cart = await update_cart(add_items=[{"merchandiseId": "variant-1", "quantity": 1}])
    # Clear final from cart creation
    if final.exists():
        final.unlink()

    result = await create_order(
        cart_id=cart["id"],
        payment_method={"type": "credit_card", "card_number": "4111111111111111", "cvv": "123", "expiry": "12/26"},
        shipping_address={"address1": "123 Main St", "city": "Test", "countryCode": "US"},
        billing_address={"address1": "123 Main St", "city": "Test", "countryCode": "US"},
        shipping_method_id="standard",
    )
    assert result.get("order") is not None
    assert final.exists(), "final.json must be written after create_order"


@pytest.mark.asyncio
async def test_get_order_does_not_write_final_json(outputdir):
    from shopify.server import get_order

    final = outputdir / "final.json"
    await get_order(order_id="nonexistent")
    assert not final.exists(), "final.json must NOT be written after a read-only tool"


@pytest.mark.asyncio
async def test_no_final_path_skips_snapshot(outputdir):
    from shopify import state as shopify_state
    from shopify.server import update_cart

    shopify_state.set_snapshot_paths(final_path=None)

    result = await update_cart(
        add_items=[{"merchandiseId": "variant-1", "quantity": 1}],
    )
    assert "id" in result

    final = outputdir / "final.json"
    assert not final.exists()


def test_init_state_writes_initial_and_bundle_not_final(shopify_data, tmp_path, monkeypatch):
    from shopify import state as shopify_state

    outputdir = tmp_path / "output"
    bundledir = tmp_path / "bundle"
    monkeypatch.setenv("OUTPUTDIR", str(outputdir))
    monkeypatch.setenv("BUNDLE_OUTPUT_DIR", str(bundledir))

    shopify_state._stores.clear()
    shopify_state._current_state = None
    shopify_state._active_store_id = "default"
    monkeypatch.setattr(shopify_state, "_STATE_FILE", shopify_data)
    shopify_state.set_snapshot_paths(final_path=None, bundle_state_path=None)

    shopify_state.init_state()

    assert (outputdir / "initial.json").exists()
    assert not (outputdir / "final.json").exists()
    assert (bundledir / "state.json").exists()


@pytest.mark.asyncio
async def test_snapshot_on_write_restores_state_when_tool_raises(shopify_data, outputdir):
    from shopify import server as srv
    from shopify import state as shopify_state

    @srv._snapshot_on_write
    def mutates_then_raises():
        shopify_state.get_state().products["product-1"].title = "Mutated"
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await mutates_then_raises()

    assert shopify_state.get_state().products["product-1"].title == "Test Product"
    persisted = json.loads(shopify_data.read_text())
    assert persisted["products"]["product-1"]["title"] == "Test Product"
    assert not (outputdir / "final.json").exists()


@pytest.mark.asyncio
async def test_snapshot_on_write_restores_state_on_hard_error_result(shopify_data, outputdir):
    from shopify import server as srv
    from shopify import state as shopify_state

    @srv._snapshot_on_write
    def mutates_then_returns_hard_error():
        shopify_state.get_state().products["product-1"].title = "Mutated"
        return {"product": None, "userErrors": [{"field": "title", "message": "invalid"}]}

    result = await mutates_then_returns_hard_error()

    assert result["product"] is None
    assert shopify_state.get_state().products["product-1"].title == "Test Product"
    persisted = json.loads(shopify_data.read_text())
    assert persisted["products"]["product-1"]["title"] == "Test Product"
    assert not (outputdir / "final.json").exists()


@pytest.mark.asyncio
async def test_snapshot_on_write_keeps_partial_success_result(outputdir):
    from shopify import server as srv
    from shopify import state as shopify_state

    @srv._snapshot_on_write
    def mutates_then_returns_partial_success():
        product = shopify_state.get_state().products["product-1"]
        product.title = "Partially Mutated"
        return {
            "product": product,
            "userErrors": [{"field": "optional_field", "message": "ignored"}],
        }

    result = await mutates_then_returns_partial_success()

    assert result["product"].title == "Partially Mutated"
    assert shopify_state.get_state().products["product-1"].title == "Partially Mutated"
    assert (outputdir / "final.json").exists()
