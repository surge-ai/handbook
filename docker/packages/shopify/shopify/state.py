"""
Shopify MCP State Management.

Manages local state stored in shopify_data.json.
"""

import contextlib
import copy
import json
import os
import re
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, cast

from .models import (
    CartCost,
    CartLine,
    CategoryFilter,
    LooseCart,
    LooseCollection,
    LooseCustomer,
    LooseDiscountCode,
    LooseFAQ,
    LooseGiftCard,
    LooseOrder,
    LoosePolicy,
    LooseProduct,
    LooseReturn,
    LooseReview,
    LooseShippingMethod,
    LoyaltyProgram,
    MoneyV2,
    ProductVariant,
    SearchFilter,
    ShopifyStateCounters,
    ShopifyStateModel,
    VariantOptionFilter,
)
from .utils import get_shopify_state_path

_SERVICE_NAME = "shopify"

_QUERY_TOKEN_RE = re.compile(r'"([^"]+)"|\S+')


def resolve_bundle_state_paths() -> list[Path]:
    """Resolve the seed-state files inside this service's bundle subdir.

    The folder ``<BUNDLEDIR>/services/<name>/`` is the unit: everything in it
    is this service's seed. Prefer the canonical single-file ``state.json``
    (the output round-trip shape); otherwise hand back ALL ``*.json`` in the
    folder (the raw entities layout), coalesced by the loader.
    """
    bundle_dir = os.environ.get("BUNDLEDIR")
    if not bundle_dir:
        return []
    service_dir = Path(bundle_dir) / "services" / _SERVICE_NAME
    state_file = service_dir / "state.json"
    if state_file.is_file():
        return [state_file]
    if service_dir.is_dir():
        return sorted(service_dir.glob("*.json"))
    return []


def resolve_bundle_state_path() -> Path | None:
    """Back-compat single-file view of :func:`resolve_bundle_state_paths`."""
    paths = resolve_bundle_state_paths()
    return paths[0] if paths else None


def _merge_flat_into(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Merge a flat store seed into ``target``: dicts update, lists extend, scalars overwrite."""
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            target[key].update(value)
        elif isinstance(value, list) and isinstance(target.get(key), list):
            target[key].extend(value)
        else:
            target[key] = value


def _coalesce_store_files(paths: list[Path]) -> dict[str, Any] | None:
    """Coalesce a folder of seed files into one seed dict.

    A single file passes through unchanged (flat single-store or ``{stores:
    {...}}`` wrapper). With multiple files, flat (non-wrapper) files are merged
    into ONE ``default`` store — the raw entities layout splits one store
    across per-entity files, and those belong together, not in separate stores.
    Files carrying an explicit ``{stores: {...}}`` wrapper contribute their
    named stores.
    """
    if not paths:
        return None
    if len(paths) == 1:
        return cast(dict[str, Any], json.loads(paths[0].read_text()))
    stores: dict[str, Any] = {}
    default_store: dict[str, Any] = {}
    for path in paths:
        data = json.loads(path.read_text())
        if isinstance(data, dict) and "stores" in data:
            stores.update(data["stores"])
        elif isinstance(data, dict):
            _merge_flat_into(default_store, data)
    if default_store:
        stores.setdefault("default", default_store)
    return {"stores": stores}


def resolve_bundle_output_path() -> Path | None:
    output_dir = os.environ.get("BUNDLE_OUTPUT_DIR")
    if not output_dir:
        return None
    return Path(output_dir) / "state.json"


def _parse_query_tokens(query: str) -> list[str]:
    """Parse a search query into case-insensitive match tokens.

    Double-quoted segments become phrase tokens that must appear contiguously;
    everything else splits on whitespace. Every token must appear somewhere in
    the searched haystack (word-AND), matching real-world search expectations
    and avoiding the exact-phrase-substring gotcha.
    """
    tokens: list[str] = []
    for m in _QUERY_TOKEN_RE.finditer(query):
        tok = (m.group(1) or m.group(0)).strip().lower()
        if tok:
            tokens.append(tok)
    return tokens


def _state_model_from_data(data: dict[str, Any] | None) -> ShopifyStateModel:
    """Validate state data into the internal Pydantic state model."""
    return ShopifyStateModel.model_validate(data or {})


def _canonicalize_state_data(data: dict[str, Any] | None) -> dict[str, Any]:
    """Validate state data while preserving meaningful omitted product fields."""
    initial = _state_model_from_data(data)
    # Dump then re-validate so mutable in-memory model edits are checked rather
    # than trusted as already-valid Pydantic instances.
    validated = ShopifyStateModel.model_validate(initial.model_dump(mode="json", warnings=False))
    canonical = validated.model_dump(
        mode="json",
        exclude_unset=True,
    )
    for product in canonical.get("products", {}).values():
        if product.get("availableForSale") is None:
            product.pop("availableForSale", None)
    return canonical


_CART_ID_RE = re.compile(r"(?:^|/)Cart/c?(\d+)$")
_CART_LINE_ID_RE = re.compile(r"(?:^|/)CartLine/(\d+)$")
_PRODUCT_ID_RE = re.compile(r"(?:^|/)Product/(\d+)$")
_VARIANT_ID_RE = re.compile(r"(?:^|/)ProductVariant/(\d+)(?:-|$)")
_ORDER_ID_RE = re.compile(r"(?:^|/)Order/o?(\d+)$")
_ORDER_LINE_ITEM_ID_RE = re.compile(r"(?:^|/)OrderLineItem/(\d+)$")
_CUSTOMER_ID_RE = re.compile(r"(?:^|/)Customer/(\d+)$")
_COLLECTION_ID_RE = re.compile(r"(?:^|/)Collection/(\d+)$")
_REVIEW_ID_RE = re.compile(r"(?:^|/)Review/(\d+)$")
_RETURN_ID_RE = re.compile(r"(?:^|/)Return/(\d+)$")
_DISCOUNT_ID_RE = re.compile(r"(?:^|/)DiscountCode/(\d+)$")
_POLICY_ID_RE = re.compile(r"(?:^|/)ShopPolicy/(\d+)$")


def _max_generated_id(values: Iterable[str | None], pattern: re.Pattern[str]) -> int | None:
    max_value: int | None = None
    for value in values:
        if value is None:
            continue
        match = pattern.search(value)
        if match is None:
            continue
        numeric_value = int(match.group(1))
        if max_value is None or numeric_value > max_value:
            max_value = numeric_value
    return max_value


def _recover_counter(
    counter: ShopifyStateCounters, attr: str, values: Iterable[str | None], pattern: re.Pattern[str]
) -> None:
    existing_max = _max_generated_id(values, pattern)
    if existing_max is not None:
        setattr(counter, attr, max(getattr(counter, attr), existing_max))


def _recover_counters_from_entities(state: ShopifyStateModel) -> None:
    """Advance counters past IDs already present in seeded state."""
    counter = state.counters
    _recover_counter(
        counter, "cart_id", [*state.carts.keys(), *(cart.id for cart in state.carts.values())], _CART_ID_RE
    )
    _recover_counter(
        counter,
        "line_id",
        (line.id for cart in state.carts.values() for line in cart.lines),
        _CART_LINE_ID_RE,
    )
    _recover_counter(
        counter,
        "product_id",
        [*state.products.keys(), *(product.id for product in state.products.values())],
        _PRODUCT_ID_RE,
    )
    _recover_counter(
        counter,
        "variant_id",
        (variant.id for product in state.products.values() for variant in product.variants),
        _VARIANT_ID_RE,
    )
    _recover_counter(
        counter, "order_id", [*state.orders.keys(), *(order.id for order in state.orders.values())], _ORDER_ID_RE
    )
    _recover_counter(
        counter,
        "line_item_id",
        (line.id for order in state.orders.values() for line in order.lineItems),
        _ORDER_LINE_ITEM_ID_RE,
    )
    _recover_counter(
        counter,
        "customer_id",
        [*state.customers.keys(), *(customer.id for customer in state.customers.values())],
        _CUSTOMER_ID_RE,
    )
    _recover_counter(
        counter,
        "collection_id",
        [*state.collections.keys(), *(collection.id for collection in state.collections.values())],
        _COLLECTION_ID_RE,
    )
    _recover_counter(
        counter, "review_id", [*state.reviews.keys(), *(review.id for review in state.reviews.values())], _REVIEW_ID_RE
    )
    _recover_counter(
        counter,
        "return_id",
        [*state.returns.keys(), *(return_obj.id for return_obj in state.returns.values())],
        _RETURN_ID_RE,
    )
    _recover_counter(
        counter,
        "discount_id",
        [*state.discount_codes.keys(), *(discount.id for discount in state.discount_codes.values())],
        _DISCOUNT_ID_RE,
    )
    _recover_counter(counter, "policy_id", (policy.id for policy in state.policies), _POLICY_ID_RE)


# State file path - can be set via set_agent_workspace()
_STATE_FILE: Path | None = None
_AGENT_WORKSPACE: str | None = None
_bundle_state_path: Path | None = None
_final_path: Path | None = None
_UNSET = object()


def set_agent_workspace(agent_workspace: str) -> None:
    """Set the agent workspace path, which determines where state is stored."""
    global _STATE_FILE, _AGENT_WORKSPACE, _current_state, _active_store_id, _last_valid_state_snapshot

    _AGENT_WORKSPACE = agent_workspace
    _STATE_FILE = get_shopify_state_path(agent_workspace)

    # Ensure the directory exists
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Drop every cached store so load_state()'s early-exit on `_stores` doesn't
    # serve up the previous workspace's data. The old code only reset
    # _current_state, which is no longer the source of truth post multi-store.
    _stores.clear()
    _current_state = None
    _last_valid_state_snapshot = None
    _active_store_id = "default"
    print(f"Shopify state file: {_STATE_FILE}", file=sys.stderr)


def set_snapshot_paths(
    *,
    final_path: Path | str | None | object = _UNSET,
    bundle_state_path: Path | str | None | object = _UNSET,
) -> None:
    """Set optional post-tool snapshot destinations.

    Omitted keyword arguments preserve the existing value; pass None
    explicitly to clear a path.
    """
    global _bundle_state_path, _final_path
    if final_path is not _UNSET:
        _final_path = Path(cast(str | Path, final_path)) if final_path is not None else None
    if bundle_state_path is not _UNSET:
        _bundle_state_path = Path(cast(str | Path, bundle_state_path)) if bundle_state_path is not None else None


def configure_snapshots_from_env() -> None:
    outputdir = os.environ.get("OUTPUTDIR")
    set_snapshot_paths(
        final_path=Path(outputdir) / "final.json" if outputdir else None,
        bundle_state_path=resolve_bundle_output_path(),
    )


def write_snapshots() -> None:
    if _bundle_state_path is not None:
        dump_state(_bundle_state_path, "bundle")
    if _final_path is not None:
        dump_state(_final_path, "final")


def _get_state_file() -> Path | None:
    """Get the state file path. ``None`` means in-memory only.

    Returning a path is gated on ``set_agent_workspace()`` (or a test
    patching ``_STATE_FILE`` directly). Never default to a path inside the
    source tree — without a workspace, ``packages/shopify/`` was getting
    clobbered on every server startup by the ``state_from_json`` →
    ``save_state`` round-trip.
    """
    return _STATE_FILE


# ============================================
# STATE INTERFACE
# ============================================


class ShopifyState:
    """Shopify state container."""

    def __init__(self, data: dict[str, Any] | None = None):
        state = _state_model_from_data(data)
        _recover_counters_from_entities(state)
        self.products: dict[str, LooseProduct] = state.products
        self.carts: dict[str, LooseCart] = state.carts
        self.orders: dict[str, LooseOrder] = state.orders
        self.customers: dict[str, LooseCustomer] = state.customers
        self.collections: dict[str, LooseCollection] = state.collections
        self.reviews: dict[str, LooseReview] = state.reviews
        self.returns: dict[str, LooseReturn] = state.returns
        self.discount_codes: dict[str, LooseDiscountCode] = state.discount_codes
        self.gift_cards: dict[str, LooseGiftCard] = state.gift_cards
        self.shipping_methods: dict[str, LooseShippingMethod] = state.shipping_methods
        self.loyalty_program: LoyaltyProgram = state.loyalty_program
        # Identifies which customer the agent is acting as in customer-mode
        # worlds. Self-only tools (get_my_customer, redeem_my_points, etc.)
        # look up this customer; unset means customer-mode tools error clearly.
        self.current_customer_email: str | None = state.current_customer_email
        self.policies: list[LoosePolicy] = state.policies
        self.faqs: list[LooseFAQ] = state.faqs
        self.counters: ShopifyStateCounters = state.counters

    def to_dict(self) -> dict[str, Any]:
        data = {
            "products": self.products,
            "carts": self.carts,
            "orders": self.orders,
            "customers": self.customers,
            "collections": self.collections,
            "reviews": self.reviews,
            "returns": self.returns,
            "discount_codes": self.discount_codes,
            "gift_cards": self.gift_cards,
            "shipping_methods": self.shipping_methods,
            "loyalty_program": self.loyalty_program,
            "current_customer_email": self.current_customer_email,
            "policies": self.policies,
            "faqs": self.faqs,
            "counters": self.counters,
        }
        return _canonicalize_state_data(data)


# ============================================
# STATE MANAGEMENT — Multi-Store Support
# ============================================

# Store registry: maps store_id → ShopifyState
_stores: dict[str, ShopifyState] = {}

# Legacy single-state reference for backward compat in save_state file writes
_current_state: ShopifyState | None = None
_last_valid_state_snapshot: dict[str, Any] | None = None


def _state_snapshot() -> dict[str, Any]:
    """Return the current store registry as validated JSON-native state."""
    if len(_stores) == 1 and "default" in _stores:
        return _stores["default"].to_dict()
    return {"stores": {sid: s.to_dict() for sid, s in _stores.items()}}


def _stores_from_json(data: dict[str, Any]) -> dict[str, ShopifyState]:
    """Build a store registry from JSON-native state without mutating globals."""
    if "stores" in data:
        return {sid: ShopifyState(sdata) for sid, sdata in data["stores"].items()}
    return {"default": ShopifyState(data)}


def _install_stores(stores: dict[str, ShopifyState]) -> None:
    """Replace the global store registry."""
    global _current_state
    _stores.clear()
    _stores.update(stores)
    _current_state = _stores.get("default", next(iter(_stores.values()), None))


def _restore_last_valid_state() -> None:
    if _last_valid_state_snapshot is not None:
        _install_stores(_stores_from_json(copy.deepcopy(_last_valid_state_snapshot)))


def state_snapshot_for_rollback() -> dict[str, Any]:
    """Capture a validated snapshot that can restore a failed write tool."""
    return copy.deepcopy(_state_snapshot())


def restore_state_snapshot(snapshot: dict[str, Any]) -> None:
    """Restore a previously captured state snapshot and persist it."""
    _install_stores(_stores_from_json(copy.deepcopy(snapshot)))
    save_state()


def state_to_json() -> dict[str, Any]:
    """Return the full state as a JSON-native dict.

    Flat single-store shape when only ``default`` is configured (back-compat
    with pre-multi-store fixtures); multi-store worlds emit ``{"stores": {...}}``.
    Round-trips with state_from_json.
    """
    return _state_snapshot()


def state_from_json(data: dict[str, Any]) -> None:
    """Full-replace the state from a JSON-native dict.

    Accepts either the flat single-store shape (keys at top level → loaded
    into the ``default`` store) or the multi-store wrapper (``{"stores": {sid:
    {...}}}``).
    """
    _install_stores(_stores_from_json(data))
    save_state()


def dump_state(dest: Path, label: str) -> None:
    """Write a JSON snapshot of all stores to *dest*."""
    if not _stores:
        return
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if len(_stores) == 1 and "default" in _stores:
            # Single store — dump flat format for backward compat
            snapshot = _stores["default"].to_dict()
        else:
            # Multi-store — dump under "stores" key
            snapshot = {"stores": {sid: s.to_dict() for sid, s in _stores.items()}}
        with open(dest, "w") as f:
            json.dump(snapshot, f, indent=2)
            f.write("\n")
        print(f"Wrote {label} state snapshot to {dest}", file=sys.stderr)
    except Exception as e:
        print(f"Error writing {label} state snapshot to {dest}: {e}", file=sys.stderr)


def load_state() -> ShopifyState:
    """Load state from JSON file. Returns the default store for backward compat."""
    global _current_state

    if _stores:
        return _stores.get("default", next(iter(_stores.values())))

    print(f"BUNDLEDIR={os.environ.get('BUNDLEDIR', '<unset>')}", file=sys.stderr)
    print(f"INPUTDIR={os.environ.get('INPUTDIR', '<unset>')}", file=sys.stderr)
    print(f"OUTPUTDIR={os.environ.get('OUTPUTDIR', '<unset>')}", file=sys.stderr)

    # The bundle folder is read in full and coalesced (a lone state.json is a
    # one-element list); otherwise fall back to the first INPUTDIR/*.json
    # (legacy first-file contract, see scripts/validate_external_services.py).
    bundle_paths = resolve_bundle_state_paths()
    inputdir = os.environ.get("INPUTDIR")
    data: dict[str, Any] | None = None
    if bundle_paths:
        try:
            data = _coalesce_store_files(bundle_paths)
            print(f"Loaded state from {[str(p) for p in bundle_paths]}", file=sys.stderr)
        except Exception as e:
            print(f"Error loading state from {[str(p) for p in bundle_paths]}: {e}", file=sys.stderr)
    elif inputdir and Path(inputdir).is_dir():
        json_files = sorted(Path(inputdir).glob("*.json"))
        if json_files:
            try:
                with open(json_files[0]) as f:
                    data = json.load(f)
                print(f"Loaded state from {json_files[0]}", file=sys.stderr)
            except Exception as e:
                print(f"Error loading state from {json_files[0]}: {e}", file=sys.stderr)

    if data is None:
        state_file = _get_state_file()
        if state_file is None:
            data = {}
            print("No agent workspace configured, starting empty (in-memory only)", file=sys.stderr)
        else:
            try:
                if state_file.exists():
                    with open(state_file) as f:
                        data = json.load(f)
                    print(f"Loaded state from {state_file}", file=sys.stderr)
                else:
                    data = {}
                    print(f"No state file found at {state_file}, starting empty", file=sys.stderr)
            except Exception as e:
                print(f"Error loading state: {e}", file=sys.stderr)
                data = {}

    # Route through the shared codec — multi-store-aware, populates _stores
    # and _current_state, and persists via save_state().
    state_from_json(data)
    print(f"Shopify initialized with {len(_stores)} store(s)", file=sys.stderr)

    assert _current_state is not None
    return _current_state


def init_state(agent_workspace: str | None = None) -> None:
    """Initialize state and startup snapshots for the Shopify MCP server."""
    if agent_workspace:
        set_agent_workspace(agent_workspace)
    load_state()
    configure_snapshots_from_env()
    if _bundle_state_path is not None:
        dump_state(_bundle_state_path, "bundle")
    if outputdir := os.environ.get("OUTPUTDIR"):
        dump_state(Path(outputdir) / "initial.json", "initial")


def save_state() -> None:
    """Save state to JSON file.

    State serialization validates the full in-memory state through
    ShopifyStateModel. Let failures propagate so tools do not report success
    after producing invalid or unwritable state.
    """
    if not _stores:
        return

    state_file = _get_state_file()
    if state_file is None:
        return  # in-memory only — no workspace configured

    global _last_valid_state_snapshot
    try:
        snapshot = _state_snapshot()
        with open(state_file, "w") as f:
            json.dump(snapshot, f, indent=2)
            f.write("\n")
    except Exception:
        _restore_last_valid_state()
        raise
    else:
        _last_valid_state_snapshot = copy.deepcopy(snapshot)


# Active store context — set by server.py before calling tool handlers.
# This avoids passing store_id through every helper function.
_active_store_id: str = "default"


def set_active_store(store_id: str) -> None:
    """Set the active store for subsequent get_state()/save_state() calls."""
    global _active_store_id
    if not _stores:
        load_state()
    if store_id not in _stores:
        available = ", ".join(sorted(_stores.keys()))
        raise ValueError(f"Store '{store_id}' not found. Available: {available}")
    _active_store_id = store_id


def get_state() -> ShopifyState:
    """Get the active store's state, loading if necessary."""
    if not _stores:
        load_state()
    if _active_store_id not in _stores:
        available = ", ".join(sorted(_stores.keys()))
        raise ValueError(f"Store '{_active_store_id}' not found. Available: {available}")
    return _stores[_active_store_id]


def get_all_stores() -> dict[str, ShopifyState]:
    """Get all store states."""
    if not _stores:
        load_state()
    return _stores


def reset_state() -> None:
    """Reset state to empty."""
    global _current_state
    _stores.clear()
    _stores["default"] = ShopifyState()
    _current_state = _stores["default"]
    save_state()
    print("State reset to empty", file=sys.stderr)


# ============================================
# COUNTER HELPERS
# ============================================


def get_next_cart_id() -> str:
    """Generate a new cart ID."""
    state = get_state()
    state.counters.cart_id += 1
    cart_num = state.counters.cart_id
    save_state()
    return f"gid://shopify/Cart/c{cart_num}"


def get_next_line_id() -> str:
    """Generate a new cart line ID."""
    state = get_state()
    state.counters.line_id += 1
    line_num = state.counters.line_id
    save_state()
    return f"gid://shopify/CartLine/{line_num}"


# ============================================
# PRODUCT HELPERS
# ============================================


def get_next_product_id() -> str:
    """Generate a new product ID."""
    state = get_state()
    state.counters.product_id += 1
    num = state.counters.product_id
    save_state()
    return f"gid://shopify/Product/{num}"


def get_next_variant_id(product_num: str, option_slug: str) -> str:
    """Generate a new variant ID."""
    state = get_state()
    state.counters.variant_id += 1
    num = state.counters.variant_id
    save_state()
    return f"gid://shopify/ProductVariant/{num}-{option_slug}"


def get_product_by_id(product_id: str) -> LooseProduct | None:
    """Get a product by its ID."""
    state = get_state()
    return state.products.get(product_id)


def get_variant_by_id(variant_id: str) -> tuple[LooseProduct | None, ProductVariant | None]:
    """Get a variant by its ID, returning (product, variant) tuple."""
    state = get_state()

    for product in state.products.values():
        for variant in product.variants:
            if variant.id == variant_id:
                return product, variant

    return None, None


def adjust_variant_stock(variant_id: str, delta: int) -> ProductVariant | None:
    """Adjust a variant's quantityAvailable by delta (negative reduces stock on
    order creation; positive restores stock on cancel/refund). Floors at 0 so
    oversold scenarios don't produce negative inventory. Keeps currentlyNotInStock,
    availableForSale, and the product's totalInventory in sync. Returns the
    updated variant dict, or None if the variant was not found. Does not call
    save_state — callers are expected to persist.
    """
    product, variant = get_variant_by_id(variant_id)
    if variant is None:
        return None
    if variant.quantityAvailable is None:
        return variant
    current = variant.quantityAvailable or 0
    new_qty = max(0, current + delta)
    variant.quantityAvailable = new_qty
    variant.currentlyNotInStock = new_qty <= 0
    variant.availableForSale = new_qty > 0
    if product:
        total = sum(v.quantityAvailable or 0 for v in product.variants)
        product.totalInventory = total
        product.availableForSale = any(v.availableForSale for v in product.variants)
    return variant


def search_products(
    query: str,
    filters: Sequence[SearchFilter | dict[str, Any]] | None = None,
    limit: int = 10,
    after: str | None = None,
) -> tuple[list[LooseProduct], bool, str | None, int]:
    """
    Search products by query and filters.

    Returns: (products, has_next_page, end_cursor, total_count)
    """
    state = get_state()
    tokens = _parse_query_tokens(query)

    # Filter products
    matching = []
    for product in state.products.values():
        # Build a single haystack from all searchable fields — prose (title,
        # description, tags, vendor, productType) plus variant SKUs. Every
        # query token must appear somewhere in the haystack (word-AND).
        prose_parts = [
            product.title,
            product.description,
            product.vendor,
            product.productType,
            " ".join(product.tags),
        ]
        sku_parts = [variant.sku for variant in product.variants if variant.sku]
        # Newline boundaries prevent quoted phrases from spanning fields.
        haystack = "\n".join(prose_parts + sku_parts).lower()

        if not tokens or all(t in haystack for t in tokens):
            matching.append(product)

    # Apply filters
    if filters:
        for raw_filter in filters:
            search_filter = _search_filter(raw_filter)
            if search_filter.productType:
                matching = [p for p in matching if p.productType.lower() == search_filter.productType.lower()]
            if search_filter.productVendor:
                matching = [p for p in matching if p.vendor.lower() == search_filter.productVendor.lower()]
            if search_filter.tag:
                matching = [p for p in matching if search_filter.tag.lower() in [tag.lower() for tag in p.tags]]
            if search_filter.available is not None:
                matching = [p for p in matching if _product_is_available(p) == search_filter.available]
            if search_filter.price:
                min_price = search_filter.price.min
                max_price = search_filter.price.max

                def price_in_range(p: Any) -> bool:
                    for amount in _product_prices(p):
                        if min_price is not None and amount < min_price:
                            continue
                        if max_price is not None and amount > max_price:
                            continue
                        return True
                    return False

                matching = [p for p in matching if price_in_range(p)]
            if search_filter.variantOption:
                matching = [p for p in matching if _product_matches_variant_option(p, search_filter.variantOption)]
            if search_filter.category:
                matching = [
                    p for p in matching if _product_matches_category(p, search_filter.category, state.collections)
                ]

    total_count = len(matching)

    # Handle pagination (simple cursor = index)
    start_idx = 0
    if after:
        with contextlib.suppress(ValueError):
            start_idx = int(after) + 1

    end_idx = start_idx + limit
    paginated = matching[start_idx:end_idx]

    has_next = end_idx < total_count
    end_cursor = str(end_idx - 1) if paginated else None

    return paginated, has_next, end_cursor, total_count


def _search_filter(search_filter: SearchFilter | dict[str, Any]) -> SearchFilter:
    if isinstance(search_filter, SearchFilter):
        return search_filter
    return SearchFilter.model_validate(search_filter)


def _product_is_available(product: LooseProduct) -> bool:
    if "availableForSale" in product:
        return bool(product.availableForSale)
    return any(variant.availableForSale for variant in product.variants)


def _product_prices(product: LooseProduct) -> list[float]:
    prices = []
    for variant in product.variants:
        with contextlib.suppress(TypeError, ValueError):
            prices.append(float(variant.price.amount))

    if prices:
        return prices

    if product.priceRange is not None:
        for value in (product.priceRange.minVariantPrice, product.priceRange.maxVariantPrice):
            with contextlib.suppress(TypeError, ValueError):
                prices.append(float(value.amount))

    return prices


def _product_matches_variant_option(product: LooseProduct, option_filter: VariantOptionFilter) -> bool:
    name = option_filter.name.strip().lower()
    value = option_filter.value.strip().lower()
    if not name or not value:
        return False

    for option in product.options:
        option_name = option.name.strip().lower()
        option_values = [str(v).strip().lower() for v in option.values]
        if option_name == name and value in option_values:
            return True

    for variant in product.variants:
        for selected in variant.selectedOptions:
            selected_name = selected.name.strip().lower()
            selected_value = selected.value.strip().lower()
            if selected_name == name and selected_value == value:
                return True
        # Some fixtures represent single-option variants only via the variant
        # title. Keep this as a fallback after structured selectedOptions.
        if name == "title" and variant.title.strip().lower() == value:
            return True

    return False


def _category_values(product: LooseProduct) -> set[str]:
    values = set()
    for raw in (product.category, product.categoryId, product.productCategory):
        if isinstance(raw, dict):
            values.update(str(v).strip().lower() for v in raw.values() if v)
        elif raw:
            values.add(str(raw).strip().lower())
    return values


def _product_matches_category(
    product: LooseProduct,
    category_filter: CategoryFilter,
    collections: dict[str, LooseCollection],
) -> bool:
    category_id = category_filter.id.strip().lower()
    if not category_id:
        return False

    return _product_matches_category_field(product, category_id) or _product_matches_collection_membership(
        product, category_id, collections
    )


def _product_matches_category_field(product: LooseProduct, category_id: str) -> bool:
    return category_id in _category_values(product)


def _product_matches_collection_membership(
    product: LooseProduct,
    category_id: str,
    collections: dict[str, LooseCollection],
) -> bool:
    product_id = product.id.strip().lower()
    if not product_id:
        return False

    for collection_id, collection in collections.items():
        collection_values = {
            str(collection_id).strip().lower(),
            collection.id.strip().lower(),
            collection.title.strip().lower(),
            (collection.handle or "").strip().lower(),
        }
        product_ids = collection.productIds or collection.products
        normalized_product_ids = {str(pid).strip().lower() for pid in product_ids if pid}
        if category_id in collection_values and product_id in normalized_product_ids:
            return True

    return False


def get_product_filters(products: list[LooseProduct]) -> list[dict]:
    """Generate available filters from a list of products."""
    filters = []

    # Collect unique values
    vendors: dict[str, int] = {}
    product_types: dict[str, int] = {}
    tags: dict[str, int] = {}

    for p in products:
        vendor = p.vendor
        if vendor:
            vendors[vendor] = vendors.get(vendor, 0) + 1

        ptype = p.productType
        if ptype:
            product_types[ptype] = product_types.get(ptype, 0) + 1

        for tag in p.tags:
            tags[tag] = tags.get(tag, 0) + 1

    # Build filter objects
    if vendors:
        filters.append(
            {
                "id": "filter.v.vendor",
                "label": "Vendor",
                "type": "LIST",
                "values": [{"id": v, "label": v, "count": c} for v, c in vendors.items()],
            }
        )

    if product_types:
        filters.append(
            {
                "id": "filter.p.product_type",
                "label": "Product Type",
                "type": "LIST",
                "values": [{"id": t, "label": t, "count": c} for t, c in product_types.items()],
            }
        )

    if tags:
        filters.append(
            {
                "id": "filter.p.tag",
                "label": "Tag",
                "type": "LIST",
                "values": [{"id": t, "label": t, "count": c} for t, c in tags.items()],
            }
        )

    return filters


# ============================================
# CART HELPERS
# ============================================


def get_cart_by_id(cart_id: str) -> LooseCart | None:
    """Get a cart by its ID."""
    state = get_state()
    return state.carts.get(cart_id)


def get_all_carts() -> list[LooseCart]:
    """Get all carts."""
    state = get_state()
    return list(state.carts.values())


def get_gift_card_by_code(code: str) -> LooseGiftCard | None:
    """Get a gift card by code."""
    state = get_state()
    code_lower = code.lower()
    for gift_card in state.gift_cards.values():
        if gift_card.code.lower() == code_lower:
            return gift_card
    return None


def create_cart(buyer_identity: dict | None = None, note: str | None = None) -> LooseCart:
    """Create a new empty cart."""
    from datetime import UTC, datetime

    state = get_state()
    cart_num = state.counters.cart_id + 1
    cart_id = f"gid://shopify/Cart/c{cart_num}"
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    zero_money = {"amount": "0.00", "currencyCode": "USD"}

    cart = {
        "id": cart_id,
        "checkoutUrl": f"https://shop.example.com/checkout/{cart_id}",
        "createdAt": now,
        "updatedAt": now,
        "lines": [],
        "cost": {
            "subtotalAmount": zero_money.copy(),
            "subtotalAmountEstimated": False,
            "totalAmount": zero_money.copy(),
            "totalAmountEstimated": False,
            "totalTaxAmount": None,
            "totalTaxAmountEstimated": False,
            "checkoutChargeAmount": zero_money.copy(),
        },
        "buyerIdentity": buyer_identity or {},
        "attributes": [],
        "discountCodes": [],
        "discountAllocations": [],
        "appliedGiftCards": [],
        "deliveryGroups": [],
        "note": note,
        "totalQuantity": 0,
    }

    cart_model = LooseCart.model_validate(cart)
    state.counters.cart_id = cart_num
    state.carts[cart_id] = cart_model
    save_state()

    return cart_model


def _cart_money(amount: float | str, currency: str = "USD") -> MoneyV2:
    return MoneyV2(amount=f"{float(amount):.2f}", currencyCode=currency)


def _cart_cost(total: float, currency: str = "USD") -> CartCost:
    money = _cart_money(total, currency)
    return CartCost(
        subtotalAmount=money,
        subtotalAmountEstimated=False,
        totalAmount=money.model_copy(),
        totalAmountEstimated=False,
        totalTaxAmount=None,
        totalTaxAmountEstimated=False,
        checkoutChargeAmount=money.model_copy(),
    )


def update_cart_totals(cart: LooseCart) -> None:
    """Recalculate cart totals based on line items."""
    total = 0.0
    total_quantity = 0
    currency = "USD"

    for line in cart.lines:
        quantity = line.quantity
        total_quantity += quantity

        total_amount = line.cost.totalAmount
        amount = float(total_amount.amount)
        total += amount
        currency = total_amount.currencyCode

    # Update cart cost
    cart.cost = _cart_cost(total, currency)
    cart.totalQuantity = total_quantity


def add_line_to_cart(cart: LooseCart, merchandise_id: str, quantity: int) -> CartLine | None:
    """Add a line item to a cart."""
    from datetime import UTC, datetime

    # Find the variant
    product, variant = get_variant_by_id(merchandise_id)
    if not variant:
        return None

    # Check if already in cart
    for line in cart.lines:
        if line.merchandise.id == merchandise_id:
            # Update quantity
            line.quantity += quantity
            # Update cost
            price = float(variant.price.amount)
            currency = variant.price.currencyCode
            total = _cart_money(price * line.quantity, currency)
            line.cost.totalAmount = total
            line.cost.subtotalAmount = total.model_copy()
            update_cart_totals(cart)
            cart.updatedAt = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            save_state()
            return line

    # Create new line
    line_id = get_next_line_id()
    price = variant.price
    price_amount = float(price.amount)
    currency = price.currencyCode

    line = CartLine.model_validate(
        {
            "id": line_id,
            "quantity": quantity,
            "merchandise": {
                "id": variant.id,
                "title": variant.title,
                "product": {
                    "id": product.id if product else "",
                    "title": product.title if product else "",
                    "handle": product.handle if product else "",
                },
                "image": variant.image,
                "selectedOptions": variant.selectedOptions,
                "price": price,
            },
            "cost": {
                "amountPerQuantity": price.model_dump(mode="json"),
                "compareAtAmountPerQuantity": variant.compareAtPrice,
                "subtotalAmount": {"amount": f"{price_amount * quantity:.2f}", "currencyCode": currency},
                "totalAmount": {"amount": f"{price_amount * quantity:.2f}", "currencyCode": currency},
            },
            "attributes": [],
            "discountAllocations": [],
        }
    )

    cart.lines.append(line)
    update_cart_totals(cart)
    cart.updatedAt = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    save_state()

    return line


def update_line_in_cart(cart: LooseCart, line_id: str, quantity: int) -> bool:
    """Update a line item quantity in a cart. Returns True if found."""
    from datetime import UTC, datetime

    for i, line in enumerate(cart.lines):
        if line.id == line_id:
            if quantity <= 0:
                # Remove the line
                cart.lines.pop(i)
            else:
                # Update quantity
                line.quantity = quantity
                # Recalculate line cost
                price = line.cost.amountPerQuantity
                price_amount = float(price.amount)
                currency = price.currencyCode
                total = _cart_money(price_amount * quantity, currency)
                line.cost.subtotalAmount = total
                line.cost.totalAmount = total.model_copy()

            update_cart_totals(cart)
            cart.updatedAt = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            save_state()
            return True

    return False


def remove_lines_from_cart(cart: LooseCart, line_ids: list[str]) -> int:
    """Remove line items from a cart. Returns count of removed lines."""
    from datetime import UTC, datetime

    original_count = len(cart.lines)
    cart.lines = [line for line in cart.lines if line.id not in line_ids]
    removed = original_count - len(cart.lines)

    if removed > 0:
        update_cart_totals(cart)
        cart.updatedAt = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        save_state()

    return removed


# ============================================
# ORDER HELPERS
# ============================================


def get_next_order_id() -> str:
    """Generate a new order ID."""
    state = get_state()
    state.counters.order_id += 1
    order_num = state.counters.order_id
    save_state()
    return f"gid://shopify/Order/o{order_num}"


def get_next_line_item_id() -> str:
    """Generate a new order line item ID."""
    state = get_state()
    state.counters.line_item_id += 1
    item_num = state.counters.line_item_id
    save_state()
    return f"gid://shopify/OrderLineItem/{item_num}"


def get_order_by_id(order_id: str) -> LooseOrder | None:
    """Get an order by its ID."""
    state = get_state()
    return state.orders.get(order_id)


def get_all_orders() -> list[LooseOrder]:
    """Get all orders."""
    state = get_state()
    return list(state.orders.values())


# ============================================
# CUSTOMER HELPERS
# ============================================


def get_next_customer_id() -> str:
    """Generate a new customer ID."""
    state = get_state()
    state.counters.customer_id += 1
    cust_num = state.counters.customer_id
    save_state()
    return f"gid://shopify/Customer/{cust_num}"


def get_customer_by_id(customer_id: str) -> LooseCustomer | None:
    """Get a customer by their ID."""
    state = get_state()
    return state.customers.get(customer_id)


def get_customer_by_email(email: str) -> LooseCustomer | None:
    """Get a customer by email address (case-insensitive)."""
    state = get_state()
    email_lower = email.lower()
    for customer in state.customers.values():
        if customer.email.lower() == email_lower:
            return customer
    return None


def get_all_customers() -> list[LooseCustomer]:
    """Get all customers."""
    state = get_state()
    return list(state.customers.values())


# ============================================
# COLLECTION HELPERS
# ============================================


def get_next_collection_id() -> str:
    """Generate a new collection ID."""
    state = get_state()
    state.counters.collection_id += 1
    coll_num = state.counters.collection_id
    save_state()
    return f"gid://shopify/Collection/{coll_num}"


def get_collection_by_id(collection_id: str) -> LooseCollection | None:
    """Get a collection by its ID."""
    state = get_state()
    return state.collections.get(collection_id)


def get_all_collections() -> list[LooseCollection]:
    """Get all collections."""
    state = get_state()
    return list(state.collections.values())


# ============================================
# REVIEW HELPERS
# ============================================


def get_next_review_id() -> str:
    """Generate a new review ID."""
    state = get_state()
    state.counters.review_id += 1
    review_num = state.counters.review_id
    save_state()
    return f"gid://shopify/Review/{review_num}"


def get_review_by_id(review_id: str) -> LooseReview | None:
    """Get a review by its ID."""
    state = get_state()
    return state.reviews.get(review_id)


def get_reviews_for_product(product_id: str) -> list[LooseReview]:
    """Get all reviews for a product."""
    state = get_state()
    return [r for r in state.reviews.values() if r.productId == product_id]


# ============================================
# RETURN HELPERS
# ============================================


def get_next_return_id() -> str:
    """Generate a new return ID."""
    state = get_state()
    state.counters.return_id += 1
    return_num = state.counters.return_id
    save_state()
    return f"gid://shopify/Return/{return_num}"


def get_return_by_id(return_id: str) -> LooseReturn | None:
    """Get a return by its ID."""
    state = get_state()
    return state.returns.get(return_id)


def get_all_returns() -> list[LooseReturn]:
    """Get all returns."""
    state = get_state()
    return list(state.returns.values())


# ============================================
# SHIPPING METHOD HELPERS
# ============================================


def get_shipping_method_by_id(method_id: str) -> LooseShippingMethod | None:
    """Get a shipping method by its ID."""
    state = get_state()
    return state.shipping_methods.get(method_id)


def get_all_shipping_methods() -> list[LooseShippingMethod]:
    """Get all shipping methods."""
    state = get_state()
    return list(state.shipping_methods.values())


# ============================================
# DISCOUNT CODE HELPERS
# ============================================


def get_next_discount_id() -> str:
    """Generate a new discount code ID."""
    state = get_state()
    state.counters.discount_id += 1
    num = state.counters.discount_id
    save_state()
    return f"gid://shopify/DiscountCode/{num}"


def get_discount_by_code(code: str) -> LooseDiscountCode | None:
    """Get a discount code by its code string (case-insensitive)."""
    state = get_state()
    code_upper = code.upper()
    for dc in state.discount_codes.values():
        if dc.code.upper() == code_upper:
            return dc
    return None


def get_all_discount_codes() -> list[LooseDiscountCode]:
    """Get all discount codes."""
    state = get_state()
    return list(state.discount_codes.values())


# ============================================
# INVENTORY HELPERS
# ============================================


def get_all_variants_with_inventory() -> list[dict]:
    """Get all product variants with their inventory info and product context."""
    state = get_state()
    result = []
    for product in state.products.values():
        for variant in product.variants:
            result.append(
                {
                    "variantId": variant.id,
                    "variantTitle": variant.title,
                    "productId": product.id,
                    "productTitle": product.title,
                    "sku": variant.sku,
                    "quantityAvailable": variant.quantityAvailable,
                    "currentlyNotInStock": variant.currentlyNotInStock,
                    "price": variant.price,
                }
            )
    return result


# ============================================
# POLICY HELPERS
# ============================================


def get_next_policy_id() -> str:
    """Generate a new policy ID."""
    state = get_state()
    state.counters.policy_id += 1
    num = state.counters.policy_id
    save_state()
    return f"gid://shopify/ShopPolicy/{num}"


def get_policy_by_id(policy_id: str) -> LoosePolicy | None:
    """Get a policy by its ID."""
    state = get_state()
    for p in state.policies:
        if p.id == policy_id:
            return p
    return None


def search_policies(query: str) -> list[dict]:
    """Search policies by query (word-AND; quoted segments match as phrases)."""
    state = get_state()
    tokens = _parse_query_tokens(query)
    if not tokens:
        return []

    matching = []
    for policy in state.policies:
        haystack = f"{policy.title}\n{policy.body}".lower()
        if all(t in haystack for t in tokens):
            matching.append(policy)

    return matching


def search_faqs(query: str) -> list[dict]:
    """Search FAQs by query (word-AND; quoted segments match as phrases)."""
    state = get_state()
    tokens = _parse_query_tokens(query)
    if not tokens:
        return []

    matching = []
    for faq in state.faqs:
        haystack = f"{faq.question}\n{faq.answer}".lower()
        if all(t in haystack for t in tokens):
            matching.append(faq)

    return matching
