"""Tests for the nested bundle-input directory layout (PR #205).

Each service resolves its bundle seed state from
``<BUNDLEDIR>/services/<name>/state.json`` (preferred), else the first
``*.json`` in that subdir, else falls back to ``<INPUTDIR>/*.json``.
"""

import json

import pytest

from shopify import state as shopify_state

# A minimal but non-trivial valid seed: one product with one variant. Both the
# bundle path and the INPUTDIR path are seeded with this identical payload so a
# round-trip equivalence comparison is meaningful.
_SEED = {
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
    }
}


def _reset_store_registry(monkeypatch):
    """Clear the in-memory store registry and disable the workspace state file.

    Patching ``_STATE_FILE`` to None ensures ``_get_state_file()`` returns None
    so the agent-workspace branch in ``load_state()`` never interferes with the
    bundle/INPUTDIR precedence under test.
    """
    monkeypatch.setattr(shopify_state, "_STATE_FILE", None)
    shopify_state._stores.clear()
    shopify_state._current_state = None
    shopify_state._active_store_id = "default"


def test_resolve_bundle_state_path_prefers_state_json(tmp_path, monkeypatch):
    service_dir = tmp_path / "services" / "shopify"
    service_dir.mkdir(parents=True)
    state_json = service_dir / "state.json"
    state_json.write_text(json.dumps(_SEED))
    (service_dir / "products.json").write_text(json.dumps(_SEED))

    monkeypatch.setenv("BUNDLEDIR", str(tmp_path))

    assert shopify_state.resolve_bundle_state_path() == state_json


def test_resolve_bundle_state_path_globs_when_no_state_json(tmp_path, monkeypatch):
    service_dir = tmp_path / "services" / "shopify"
    service_dir.mkdir(parents=True)
    a_json = service_dir / "a.json"
    a_json.write_text(json.dumps(_SEED))
    (service_dir / "b.json").write_text(json.dumps(_SEED))

    monkeypatch.setenv("BUNDLEDIR", str(tmp_path))

    assert shopify_state.resolve_bundle_state_path() == a_json


def test_resolve_bundle_state_path_missing_subdir(tmp_path, monkeypatch):
    # services/ exists but no services/shopify subdir.
    (tmp_path / "services").mkdir()
    monkeypatch.setenv("BUNDLEDIR", str(tmp_path))
    assert shopify_state.resolve_bundle_state_path() is None

    # BUNDLEDIR unset entirely.
    monkeypatch.delenv("BUNDLEDIR", raising=False)
    assert shopify_state.resolve_bundle_state_path() is None


def test_resolve_bundle_output_path(tmp_path, monkeypatch):
    output_dir = tmp_path / "services" / "shopify"
    monkeypatch.setenv("BUNDLE_OUTPUT_DIR", str(output_dir))
    assert shopify_state.resolve_bundle_output_path() == output_dir / "state.json"

    monkeypatch.delenv("BUNDLE_OUTPUT_DIR", raising=False)
    assert shopify_state.resolve_bundle_output_path() is None


def test_bundle_state_json_matches_inputdir(tmp_path, monkeypatch):
    # Write the identical seed to both the nested bundle state.json and the
    # legacy INPUTDIR glob target.
    bundle_dir = tmp_path / "bundle"
    bundle_service_dir = bundle_dir / "services" / "shopify"
    bundle_service_dir.mkdir(parents=True)
    (bundle_service_dir / "state.json").write_text(json.dumps(_SEED))

    inputdir = tmp_path / "inputdir"
    inputdir.mkdir()
    (inputdir / "shopify.json").write_text(json.dumps(_SEED))

    # Keep snapshot writes from touching the filesystem during load.
    monkeypatch.delenv("OUTPUTDIR", raising=False)
    monkeypatch.delenv("BUNDLE_OUTPUT_DIR", raising=False)

    # --- Load via BUNDLEDIR only ---
    _reset_store_registry(monkeypatch)
    monkeypatch.setenv("BUNDLEDIR", str(bundle_dir))
    monkeypatch.delenv("INPUTDIR", raising=False)
    shopify_state.load_state()
    bundle_json = shopify_state.state_to_json()

    # --- Load via INPUTDIR only ---
    _reset_store_registry(monkeypatch)
    monkeypatch.delenv("BUNDLEDIR", raising=False)
    monkeypatch.setenv("INPUTDIR", str(inputdir))
    shopify_state.load_state()
    inputdir_json = shopify_state.state_to_json()

    assert bundle_json == inputdir_json
    # Sanity: the seed actually loaded (non-empty product map).
    assert bundle_json["products"]


# Two distinguishable single-store seeds (different product) so a coalesced
# multi-store load is observably the union of both.
_STORE_A = {
    "products": {
        "product-a": {
            "id": "product-a",
            "title": "Store A Widget",
            "variants": [
                {
                    "id": "variant-a",
                    "title": "Default",
                    "price": {"amount": "10.00", "currencyCode": "USD"},
                    "availableForSale": True,
                }
            ],
        }
    }
}

_STORE_B = {
    "products": {
        "product-b": {
            "id": "product-b",
            "title": "Store B Gadget",
            "variants": [
                {
                    "id": "variant-b",
                    "title": "Default",
                    "price": {"amount": "20.00", "currencyCode": "USD"},
                    "availableForSale": True,
                }
            ],
        }
    }
}


def test_resolve_bundle_state_paths_returns_whole_folder(tmp_path, monkeypatch):
    service_dir = tmp_path / "services" / "shopify"
    service_dir.mkdir(parents=True)
    monkeypatch.setenv("BUNDLEDIR", str(tmp_path))

    # No state.json → all *.json, sorted.
    b_json = service_dir / "b.json"
    b_json.write_text(json.dumps(_STORE_B))
    a_json = service_dir / "a.json"
    a_json.write_text(json.dumps(_STORE_A))
    assert shopify_state.resolve_bundle_state_paths() == [a_json, b_json]

    # state.json present → just [state.json], ignoring the others.
    state_json = service_dir / "state.json"
    state_json.write_text(json.dumps(_SEED))
    assert shopify_state.resolve_bundle_state_paths() == [state_json]


def test_bundle_multifile_folder_matches_consolidated_state(tmp_path, monkeypatch):
    # (a) A single consolidated state.json holding both stores explicitly.
    consolidated_dir = tmp_path / "consolidated"
    consolidated_service = consolidated_dir / "services" / "shopify"
    consolidated_service.mkdir(parents=True)
    (consolidated_service / "state.json").write_text(json.dumps({"stores": {"default": _STORE_A, "second": _STORE_B}}))

    # (b) The same two stores split across {stores} wrapper files (no
    # state.json) — wrappers are how a world declares distinct named stores
    # across files (vs. the flat-file case, which merges into one store).
    split_dir = tmp_path / "split"
    split_service = split_dir / "services" / "shopify"
    split_service.mkdir(parents=True)
    (split_service / "a.json").write_text(json.dumps({"stores": {"default": _STORE_A}}))
    (split_service / "b.json").write_text(json.dumps({"stores": {"second": _STORE_B}}))

    # Keep snapshot writes from touching the filesystem during load.
    monkeypatch.delenv("OUTPUTDIR", raising=False)
    monkeypatch.delenv("BUNDLE_OUTPUT_DIR", raising=False)
    monkeypatch.delenv("INPUTDIR", raising=False)

    # --- Load the consolidated single-file bundle ---
    _reset_store_registry(monkeypatch)
    monkeypatch.setenv("BUNDLEDIR", str(consolidated_dir))
    shopify_state.load_state()
    consolidated_json = shopify_state.state_to_json()

    # --- Load the multi-file bundle folder ---
    _reset_store_registry(monkeypatch)
    monkeypatch.setenv("BUNDLEDIR", str(split_dir))
    shopify_state.load_state()
    split_json = shopify_state.state_to_json()

    assert split_json == consolidated_json
    # Sanity: both stores actually present in the coalesced load.
    assert set(split_json["stores"]) == {"default", "second"}


def test_bundle_flat_files_merge_into_one_store(tmp_path, monkeypatch):
    """the raw entities layout splits ONE store across per-entity files (no
    {stores} wrapper). Flat files must merge into a single default store, not
    fragment into a phantom store per file the server never activates."""
    for var in ("BUNDLEDIR", "INPUTDIR", "OUTPUTDIR", "BUNDLE_OUTPUT_DIR"):
        monkeypatch.delenv(var, raising=False)

    product_a = {
        "id": "product-a",
        "title": "Product A",
        "variants": [{"id": "va", "title": "Default", "price": {"amount": "1.00", "currencyCode": "USD"}}],
    }
    product_b = {
        "id": "product-b",
        "title": "Product B",
        "variants": [{"id": "vb", "title": "Default", "price": {"amount": "2.00", "currencyCode": "USD"}}],
    }

    service_dir = tmp_path / "bundle" / "services" / "shopify"
    service_dir.mkdir(parents=True)
    (service_dir / "a_products.json").write_text(json.dumps({"products": {"product-a": product_a}}))
    (service_dir / "b_products.json").write_text(json.dumps({"products": {"product-b": product_b}}))

    _reset_store_registry(monkeypatch)
    monkeypatch.setenv("BUNDLEDIR", str(tmp_path / "bundle"))
    shopify_state.load_state()
    merged = shopify_state.state_to_json()

    # One store (flat single-store shape), with BOTH products merged in.
    assert "stores" not in merged, "flat per-entity files must merge into ONE store"
    assert set(merged["products"]) == {"product-a", "product-b"}


@pytest.fixture(autouse=True)
def _restore_globals(monkeypatch):
    """Leave the shared state module globals clean for other tests."""
    yield
    shopify_state._stores.clear()
    shopify_state._current_state = None
    shopify_state._active_store_id = "default"
    shopify_state._STATE_FILE = None
