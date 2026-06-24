"""Catalog tool handlers for products, catalog search, and policy/FAQ search."""

import re
from datetime import UTC, datetime

from shopify.models import (
    CreateProductArgs,
    DeleteProductArgs,
    GetProductDetailsArgs,
    SearchShopCatalogArgs,
    SearchShopPoliciesAndFaqsArgs,
    UpdateProductArgs,
)
from shopify.state import (
    LooseProduct,
    get_next_product_id,
    get_next_variant_id,
    get_product_by_id,
    get_product_filters,
    get_state,
    save_state,
    search_faqs,
    search_policies,
    search_products,
    update_cart_totals,
)


def _product_with_handle_exists(handle: str) -> bool:
    return any(product.handle == handle for product in get_state().products.values())


def handle_create_product(args: CreateProductArgs) -> dict:
    """Create a new product with optional variants."""
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    handle = re.sub(r"[^a-z0-9]+", "-", args.title.lower()).strip("-")
    if not handle:
        return {
            "product": None,
            "userErrors": [{"field": "title", "message": "Product title must contain at least one letter or number"}],
        }
    if _product_with_handle_exists(handle):
        return {
            "product": None,
            "userErrors": [{"field": "title", "message": f"Product handle '{handle}' already exists"}],
        }

    product_id = get_next_product_id()
    product_num = product_id.rsplit("/", 1)[-1]

    # Build variants
    variants = []
    min_price = float("inf")
    max_price = 0.0
    currency = "USD"
    total_inventory = 0

    if args.variants:
        for v in args.variants:
            title = v.title
            price_amount = str(v.price)
            sku = v.sku
            qty = v.quantityAvailable
            slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "default"
            variant_id = get_next_variant_id(product_num, slug)

            price_val = float(price_amount)
            min_price = min(min_price, price_val)
            max_price = max(max_price, price_val)
            currency = v.currencyCode
            total_inventory += qty or 0

            variants.append(
                {
                    "id": variant_id,
                    "title": title,
                    "price": {"amount": f"{price_val:.2f}", "currencyCode": currency},
                    "compareAtPrice": None,
                    "availableForSale": (qty or 0) > 0,
                    "sku": sku,
                    "selectedOptions": [],
                    "quantityAvailable": qty,
                    "currentlyNotInStock": (qty or 0) <= 0,
                    "requiresShipping": True,
                    "taxable": True,
                }
            )
    else:
        # Default single variant
        variant_id = get_next_variant_id(product_num, "default")
        variants.append(
            {
                "id": variant_id,
                "title": "Default",
                "price": {"amount": "0.00", "currencyCode": "USD"},
                "compareAtPrice": None,
                "availableForSale": False,
                "sku": None,
                "selectedOptions": [],
                "quantityAvailable": 0,
                "currentlyNotInStock": True,
                "requiresShipping": True,
                "taxable": True,
            }
        )
        min_price = 0.0
        max_price = 0.0

    if min_price == float("inf"):
        min_price = 0.0

    product = {
        "id": product_id,
        "title": args.title,
        "description": args.description,
        "descriptionHtml": f"<p>{args.description}</p>" if args.description else "",
        "handle": handle,
        "productType": args.product_type,
        "vendor": args.vendor,
        "tags": args.tags or [],
        "availableForSale": any(v.get("availableForSale", False) for v in variants),
        "priceRange": {
            "minVariantPrice": {"amount": f"{min_price:.2f}", "currencyCode": currency},
            "maxVariantPrice": {"amount": f"{max_price:.2f}", "currencyCode": currency},
        },
        "featuredImage": None,
        "images": [],
        "options": [],
        "variants": variants,
        "seo": None,
        "onlineStoreUrl": None,
        "createdAt": now,
        "updatedAt": now,
        "publishedAt": now,
        "isGiftCard": False,
        "totalInventory": total_inventory,
    }

    state = get_state()
    state.products[product_id] = LooseProduct.model_validate(product)
    save_state()

    return {"product": product, "userErrors": []}


def handle_delete_product(args: DeleteProductArgs) -> dict:
    """Delete a product and clean up references."""
    product = get_product_by_id(args.product_id)
    if product is None:
        return {
            "deletedProductId": None,
            "userErrors": [{"field": "product_id", "message": f"Product not found: {args.product_id}"}],
        }

    state = get_state()
    variant_ids = {variant.id for variant in product.variants}

    # Remove from products
    del state.products[args.product_id]

    # Remove from any collections
    for collection in state.collections.values():
        if args.product_id in collection.productIds:
            collection.productIds.remove(args.product_id)
        if args.product_id in collection.products:
            collection.products.remove(args.product_id)

    # Remove associated reviews
    review_ids_to_delete = [
        review_id for review_id, review in state.reviews.items() if review.productId == args.product_id
    ]
    for rid in review_ids_to_delete:
        del state.reviews[rid]

    for discount_code in state.discount_codes.values():
        if discount_code.productIds and args.product_id in discount_code.productIds:
            discount_code.productIds = [
                product_id for product_id in discount_code.productIds if product_id != args.product_id
            ]
            if not discount_code.productIds:
                discount_code.productIds = None

    for cart in state.carts.values():
        original_count = len(cart.lines)
        cart.lines = [line for line in cart.lines if line.merchandise.id not in variant_ids]
        if len(cart.lines) != original_count:
            update_cart_totals(cart)
            cart.deliveryGroups = []

    save_state()

    return {"deletedProductId": args.product_id, "userErrors": []}


get_product_details_tool = {
    "name": "get_product_details",
    "description": "Look up a product by ID and optionally specify variant options to select a specific variant.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "product_id": {"type": "string", "description": "The product ID, e.g. gid://shopify/Product/123"},
            "options": {
                "type": "object",
                "description": 'Optional variant options to select a specific variant, e.g. {"Size": "10", "Color": "Black"}',
            },
            "country": {
                "type": "string",
                "description": "ISO 3166-1 alpha-2 country code for which to return localized results (e.g., 'US', 'CA', 'GB').",
            },
            "language": {
                "type": "string",
                "description": "ISO 639-1 language code for which to return localized results (e.g., 'EN', 'FR', 'DE').",
            },
        },
        "required": ["product_id"],
    },
}


def handle_get_product_details(args: GetProductDetailsArgs) -> dict:
    """
    Handle get_product_details tool call.

    Retrieves a product by ID from local state.
    Optionally selects a specific variant based on provided options.
    """
    product = get_product_by_id(args.product_id)

    if product is None:
        return {
            "product": None,
            "userErrors": [{"field": ["product_id"], "message": f"Product {args.product_id} not found"}],
        }

    # If options provided, find matching variant
    selected_variant = None
    if args.options and product.variants:
        for variant in product.variants:
            variant_options = {option.name: option.value for option in variant.selectedOptions}
            # Check if all requested options match
            if all(variant_options.get(name) == value for name, value in args.options.items()):
                selected_variant = variant
                break

    # Build response
    result: dict[str, object] = {
        "product": product,
    }

    if args.options:
        if selected_variant:
            result["selectedVariant"] = selected_variant
        else:
            result["selectedVariant"] = None
            result["userErrors"] = [
                {"field": ["options"], "message": f"No variant found matching options: {args.options}"}
            ]

    if args.country or args.language:
        result["localization"] = {"country": args.country, "language": args.language, "applied": False}

    return result


search_shop_catalog_tool = {
    "name": "search_shop_catalog",
    "description": """Search for products from the online store, hosted on Shopify.

This tool can be used to search for products using natural language queries, specific filter criteria, or both.

Mock search behavior:
- An empty query searches all products before filters are applied
- Multiple filters are combined with AND semantics; OR, negation, and exclude filters are not supported
- The category filter matches product category fields or collection membership by collection id, title, or handle
- country and language are accepted for schema compatibility but do not localize catalog data; the response reports them as unapplied hints when provided
- Unsupported filter fields are reported in warnings rather than silently looking like no matches

Best practices:
- Searches return available_filters which can be used for refined follow-up searches
- When filtering, use ONLY the filters from available_filters in follow-up searches
- For specific filter searches (category, variant option, product type, etc.), use simple terms without the filter name (e.g., "red" not "red color")
- For filter-specific searches (e.g., "find burton in snowboards" or "show me all available products in gray / green color"), use a two-step approach:
  1. Perform a normal search to discover available filters
  2. If relevant filters are returned, do a second search using the proper filter (productType, category, variantOption, etc.) with just the specific search term
- Results are paginated, with initial results limited to improve experience
- Use the after parameter with endCursor to fetch additional pages when users request more results

The response includes product details, available variants, filter options, and pagination info.""",
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "A natural language query."},
            "filters": {
                "type": "array",
                "description": "Filters to apply to the search. Only apply filters from the available_filters returned in a previous response.",
                "items": {
                    "type": "object",
                    "properties": {
                        "available": {
                            "type": "boolean",
                            "description": "Filter on if the product is available for sale",
                        },
                        "category": {"type": "object", "properties": {"id": {"type": "string"}}},
                        "price": {
                            "type": "object",
                            "properties": {"min": {"type": "number"}, "max": {"type": "number"}},
                        },
                        "productMetafield": {
                            "type": "object",
                            "properties": {
                                "key": {"type": "string"},
                                "namespace": {"type": "string"},
                                "value": {"type": "string"},
                            },
                        },
                        "productType": {"type": "string", "description": "Product type to filter by"},
                        "productVendor": {"type": "string", "description": "Product vendor to filter by"},
                        "tag": {"type": "string", "description": "Tag to filter by"},
                        "taxonomyMetafield": {
                            "type": "object",
                            "properties": {
                                "key": {"type": "string"},
                                "namespace": {"type": "string"},
                                "value": {"type": "string"},
                            },
                        },
                        "variantMetafield": {
                            "type": "object",
                            "properties": {
                                "key": {"type": "string"},
                                "namespace": {"type": "string"},
                                "value": {"type": "string"},
                            },
                        },
                        "variantOption": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}, "value": {"type": "string"}},
                        },
                    },
                },
            },
            "country": {
                "type": "string",
                "description": "ISO 3166-1 alpha-2 country code hint. Accepted for compatibility; catalog localization is not available.",
            },
            "language": {
                "type": "string",
                "description": "ISO 639-1 language code hint. Accepted for compatibility; catalog localization is not available.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of products to return. Defaults to 10, maximum is 250.",
                "default": 10,
            },
            "after": {"type": "string", "description": "Pagination cursor to fetch the next page of results."},
            "context": {
                "type": "string",
                "description": "Additional information about the request such as user demographics, mood, location, or other relevant details.",
            },
        },
        "required": ["query", "context"],
    },
}


def _catalog_search_warnings(args: SearchShopCatalogArgs) -> list[str]:
    warnings: list[str] = []

    def add(message: str) -> None:
        if message not in warnings:
            warnings.append(message)

    extra_args = getattr(args, "model_extra", None) or {}
    for field in sorted(extra_args):
        add(f"Unsupported catalog search argument '{field}' was ignored.")
    for search_filter in args.filters or []:
        filter_extra = getattr(search_filter, "model_extra", None) or {}
        for field in sorted(filter_extra):
            add(f"Unsupported catalog filter field '{field}' was ignored.")
        for field in ("productMetafield", "taxonomyMetafield", "variantMetafield"):
            if getattr(search_filter, field, None) is not None:
                add(f"Catalog filter '{field}' is accepted by the schema but is not available in catalog search.")
        if (
            search_filter.price
            and search_filter.price.min is not None
            and search_filter.price.max is not None
            and search_filter.price.min > search_filter.price.max
        ):
            add("Price filter min is greater than max; no products can match that filter.")

    return warnings


def _normalize_catalog_pagination(args: SearchShopCatalogArgs) -> tuple[int, str | None, list[str]]:
    warnings: list[str] = []
    limit = args.limit
    after = args.after

    if limit > 250:
        warnings.append("limit exceeds the maximum of 250; using 250.")
        limit = 250

    if after is not None:
        try:
            if int(after) < 0:
                warnings.append("after cursor must be non-negative; using the first page.")
                after = None
        except ValueError:
            warnings.append(f"Invalid after cursor '{after}'; using the first page.")
            after = None

    return limit, after, warnings


def handle_search_shop_catalog(args: SearchShopCatalogArgs) -> dict:
    """
    Handle search_shop_catalog tool call.

    Searches products in the local state by query and filters.
    """
    limit, after, pagination_warnings = _normalize_catalog_pagination(args)

    # Search products
    products, has_next_page, end_cursor, total_count = search_products(
        query=args.query,
        filters=args.filters,
        limit=limit,
        after=after,
    )

    # Get available filters from all products (for filter discovery)
    all_products = list(get_state().products.values())
    product_filters = get_product_filters(all_products)

    # Build response
    result = {
        "nodes": products,
        "pageInfo": {
            "hasNextPage": has_next_page,
            "hasPreviousPage": after is not None,
            "startCursor": "0" if products else None,
            "endCursor": end_cursor,
        },
        "productFilters": product_filters,
        "totalCount": total_count,
        "warnings": _catalog_search_warnings(args) + pagination_warnings,
    }

    if args.country or args.language:
        result["localization"] = {"country": args.country, "language": args.language, "applied": False}

    return result


search_shop_policies_and_faqs_tool = {
    "name": "search_shop_policies_and_faqs",
    "description": """Used to get facts about the stores policies, products, or services.
Some examples of questions you can ask are:
  - What is your return policy?
  - What is your shipping policy?
  - What is your phone number?
  - What are your hours of operation?

Returned result objects use a reserved type field normalized to either "policy" or "faq",
even if fixture data contains its own type value.
""",
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "A natural language query."},
            "context": {
                "type": "string",
                "description": "Additional information about the request such as user demographics, mood, location, or other relevant details that could help in tailoring the response appropriately.",
            },
        },
        "required": ["query"],
    },
}


def handle_search_shop_policies_and_faqs(args: SearchShopPoliciesAndFaqsArgs) -> dict:
    """
    Handle search_shop_policies_and_faqs tool call.

    Searches policies in the local state.
    """
    matching_policies = search_policies(args.query)
    matching_faqs = search_faqs(args.query)

    results = [{**policy, "type": "policy"} for policy in matching_policies] + [
        {**faq, "type": "faq"} for faq in matching_faqs
    ]

    # Generate a simple answer if policies found
    answer = None
    if results:
        # Use first matching policy body as the answer
        first = results[0]
        answer = first.get("body") or first.get("answer")

    return {
        "results": results,
        "answer": answer,
    }


def _product_handle_exists_for_other(handle: str, *, excluding_product_id: str) -> bool:
    return any(
        product.id != excluding_product_id and product.handle == handle for product in get_state().products.values()
    )


def handle_update_product(args: UpdateProductArgs) -> dict:
    """Update fields on an existing product."""
    product = get_product_by_id(args.product_id)
    if product is None:
        return {
            "product": None,
            "userErrors": [{"field": "product_id", "message": f"Product not found: {args.product_id}"}],
        }

    if args.title is not None:
        handle = re.sub(r"[^a-z0-9]+", "-", args.title.lower()).strip("-")
        if not handle:
            return {
                "product": None,
                "userErrors": [
                    {"field": "title", "message": "Product title must contain at least one letter or number"}
                ],
            }
        if _product_handle_exists_for_other(handle, excluding_product_id=args.product_id):
            return {
                "product": None,
                "userErrors": [{"field": "title", "message": f"Product handle '{handle}' already exists"}],
            }
        product.title = args.title
        product.handle = handle
    if args.description is not None:
        product.description = args.description
        product.descriptionHtml = f"<p>{args.description}</p>" if args.description else ""
    if args.product_type is not None:
        product.productType = args.product_type
    if args.vendor is not None:
        product.vendor = args.vendor
    if args.tags is not None:
        product.tags = args.tags

    product.updatedAt = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    save_state()

    return {"product": product, "userErrors": []}
