"""Inventory and collection tool handlers."""

import contextlib
import re
from datetime import UTC, datetime

from shopify.models import (
    AddToCollectionArgs,
    CreateCollectionArgs,
    GetCollectionArgs,
    GetInventoryArgs,
    ListCollectionsArgs,
    RemoveFromCollectionArgs,
    UpdateInventoryArgs,
)
from shopify.state import (
    LooseCollection,
    get_all_collections,
    get_all_variants_with_inventory,
    get_collection_by_id,
    get_next_collection_id,
    get_state,
    get_variant_by_id,
    save_state,
)


def handle_add_to_collection(args: AddToCollectionArgs) -> dict:
    """Add products to a collection."""
    collection = get_collection_by_id(args.collection_id)
    if collection is None:
        return {
            "collection": None,
            "userErrors": [{"field": "collection_id", "message": f"Collection not found: {args.collection_id}"}],
        }

    state = get_state()
    added = []
    already_in = []
    not_found = []
    planned_product_ids = set(collection.productIds)

    for pid in args.product_ids:
        if pid not in state.products:
            not_found.append(pid)
        elif pid in planned_product_ids:
            already_in.append(pid)
        else:
            added.append(pid)
            planned_product_ids.add(pid)

    if added:
        collection.productIds.extend(added)
        collection.updatedAt = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        save_state()

    errors = []
    for pid in not_found:
        errors.append({"field": "product_ids", "message": f"Product not found: {pid}"})

    return {
        "collection": collection,
        "added": added,
        "alreadyInCollection": already_in,
        "userErrors": errors,
    }


def handle_create_collection(args: CreateCollectionArgs) -> dict:
    """Create a new collection."""
    # Check for duplicate title
    for coll in get_all_collections():
        if coll.get("title", "").lower() == args.title.lower():
            return {
                "collection": None,
                "userErrors": [{"field": "title", "message": f"Collection '{args.title}' already exists"}],
            }

    handle = re.sub(r"[^a-z0-9]+", "-", args.title.lower()).strip("-")
    if not handle:
        return {
            "collection": None,
            "userErrors": [
                {"field": "title", "message": "Collection title must contain at least one letter or number"}
            ],
        }

    # Validate product IDs if provided
    product_ids = []
    state = get_state()
    if args.product_ids:
        not_found = [pid for pid in args.product_ids if pid not in state.products]
        if not_found:
            return {
                "collection": None,
                "userErrors": [{"field": "product_ids", "message": f"Product not found: {pid}"} for pid in not_found],
            }
        for pid in args.product_ids:
            if pid not in product_ids:
                product_ids.append(pid)

    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    collection_id = get_next_collection_id()

    collection = {
        "id": collection_id,
        "title": args.title,
        "description": args.description,
        "handle": handle,
        "productIds": product_ids,
        "createdAt": now,
        "updatedAt": now,
        "sortOrder": args.sort_order,
        "image": None,
    }

    state.collections[collection_id] = LooseCollection.model_validate(collection)
    save_state()

    return {"collection": collection, "userErrors": []}


def handle_get_collection(args: GetCollectionArgs) -> dict:
    """Retrieve a collection with its products."""
    collection = get_collection_by_id(args.collection_id)
    if collection is None:
        return {
            "collection": None,
            "userErrors": [{"field": "collection_id", "message": f"Collection not found: {args.collection_id}"}],
        }

    # Resolve product details
    state = get_state()
    products = []
    for product_id in collection.productIds:
        product = state.products.get(product_id)
        if product:
            products.append(product)

    return {
        "collection": collection,
        "products": products,
        "productCount": len(products),
        "userErrors": [],
    }


def handle_get_inventory(args: GetInventoryArgs) -> dict:
    """Get inventory levels, optionally filtered by product or low stock threshold."""
    variants = get_all_variants_with_inventory()

    # Filter by product
    if args.product_id:
        variants = [v for v in variants if v["productId"] == args.product_id]
        if not variants:
            # Check if the product exists at all
            state = get_state()
            if args.product_id not in state.products:
                return {
                    "inventory": [],
                    "totalCount": 0,
                    "userErrors": [{"field": "product_id", "message": f"Product not found: {args.product_id}"}],
                }

    # Filter by low stock threshold
    if args.low_stock_threshold is not None:
        variants = [
            v
            for v in variants
            if v["quantityAvailable"] is not None and v["quantityAvailable"] <= args.low_stock_threshold
        ]

    return {
        "inventory": variants,
        "totalCount": len(variants),
        "userErrors": [],
    }


def handle_list_collections(args: ListCollectionsArgs) -> dict:
    """List all collections with pagination."""
    collections = get_all_collections()
    collections.sort(key=lambda collection: collection.title.lower())

    total_count = len(collections)

    start_idx = 0
    if args.after:
        with contextlib.suppress(ValueError):
            start_idx = int(args.after) + 1

    end_idx = start_idx + args.limit
    paginated = collections[start_idx:end_idx]

    has_next = end_idx < total_count
    end_cursor = str(end_idx - 1) if paginated else None

    # Add product count to each collection summary
    summaries = []
    for collection in paginated:
        summaries.append(
            {
                "id": collection.id,
                "title": collection.title,
                "description": collection.description,
                "handle": collection.handle or "",
                "productCount": len(collection.productIds),
                "sortOrder": collection.sortOrder,
                "createdAt": collection.createdAt,
                "updatedAt": collection.updatedAt,
            }
        )

    return {
        "collections": summaries,
        "pageInfo": {
            "hasNextPage": has_next,
            "hasPreviousPage": start_idx > 0,
            "startCursor": str(start_idx) if paginated else None,
            "endCursor": end_cursor,
        },
        "totalCount": total_count,
    }


def handle_remove_from_collection(args: RemoveFromCollectionArgs) -> dict:
    """Remove products from a collection."""
    collection = get_collection_by_id(args.collection_id)
    if collection is None:
        return {
            "collection": None,
            "userErrors": [{"field": "collection_id", "message": f"Collection not found: {args.collection_id}"}],
        }

    removed = []
    not_in = []
    remaining_product_ids = list(collection.productIds)

    for pid in args.product_ids:
        if pid in remaining_product_ids:
            remaining_product_ids.remove(pid)
            removed.append(pid)
        else:
            not_in.append(pid)

    if removed:
        collection.productIds = remaining_product_ids
        collection.updatedAt = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        save_state()

    return {
        "collection": collection,
        "removed": removed,
        "notInCollection": not_in,
        "userErrors": [],
    }


def handle_update_inventory(args: UpdateInventoryArgs) -> dict:
    """Update the quantity available for a product variant."""
    product, variant = get_variant_by_id(args.variant_id)
    if variant is None:
        return {
            "inventoryItem": None,
            "userErrors": [{"field": "variant_id", "message": f"Variant not found: {args.variant_id}"}],
        }

    old_quantity = variant.quantityAvailable
    variant.quantityAvailable = args.quantity
    variant.currentlyNotInStock = args.quantity <= 0
    variant.availableForSale = args.quantity > 0

    # Update product-level totalInventory
    if product:
        total = sum(product_variant.quantityAvailable or 0 for product_variant in product.variants)
        product.totalInventory = total
        # Update product availableForSale based on any variant being available
        product.availableForSale = any(product_variant.availableForSale for product_variant in product.variants)

    save_state()

    return {
        "inventoryItem": {
            "variantId": args.variant_id,
            "previousQuantity": old_quantity,
            "newQuantity": args.quantity,
            "productId": product.id if product else None,
            "sku": variant.sku,
        },
        "userErrors": [],
    }
