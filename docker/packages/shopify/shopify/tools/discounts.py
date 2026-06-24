"""Discount code tool handlers."""

from datetime import UTC, datetime

from shopify.models import (
    CreateDiscountCodeArgs,
    DeleteDiscountCodeArgs,
    GetDiscountCodeArgs,
    ListDiscountCodesArgs,
    MoneyV2,
    UpdateDiscountCodeArgs,
)
from shopify.state import (
    LooseDiscountCode,
    get_all_discount_codes,
    get_discount_by_code,
    get_next_discount_id,
    get_state,
    save_state,
)


def _missing_product_errors(product_ids: list[str] | None) -> list[dict]:
    if product_ids is None:
        return []
    state = get_state()
    seen = set()
    errors = []
    for product_id in product_ids:
        if product_id in seen:
            continue
        seen.add(product_id)
        if product_id not in state.products:
            errors.append({"field": "product_ids", "message": f"Product not found: {product_id}"})
    return errors


def _minimum_tier_errors(minimum_tier: str | None) -> list[dict]:
    if not minimum_tier:
        return []
    tiers = get_state().loyalty_program.tiers
    if any(tier.name == minimum_tier for tier in tiers):
        return []
    return [{"field": "minimum_tier", "message": f"Loyalty tier not found: {minimum_tier}"}]


def handle_create_discount_code(args: CreateDiscountCodeArgs) -> dict:
    """Create a new discount code."""
    # Check for duplicate
    existing = get_discount_by_code(args.code)
    if existing is not None:
        return {
            "discountCode": None,
            "userErrors": [{"field": "code", "message": f"Discount code '{args.code}' already exists"}],
        }
    product_errors = _missing_product_errors(args.product_ids)
    if product_errors:
        return {"discountCode": None, "userErrors": product_errors}
    tier_errors = _minimum_tier_errors(args.minimum_tier)
    if tier_errors:
        return {"discountCode": None, "userErrors": tier_errors}

    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    discount_id = get_next_discount_id()

    dc = {
        "id": discount_id,
        "code": args.code.upper(),
        "discountType": args.discount_type,
        "value": args.value,
        "minimumPurchase": {"amount": f"{args.minimum_purchase:.2f}", "currencyCode": "USD"}
        if args.minimum_purchase
        else None,
        "minimumTier": args.minimum_tier,
        "usageLimit": args.usage_limit,
        "usageCount": 0,
        "productIds": list(dict.fromkeys(args.product_ids)) if args.product_ids else None,
        "active": True,
        "createdAt": now,
        "updatedAt": now,
    }

    state = get_state()
    discount = LooseDiscountCode.model_validate(dc)
    state.discount_codes[discount_id] = discount
    save_state()

    return {"discountCode": discount, "userErrors": []}


def handle_get_discount_code(args: GetDiscountCodeArgs) -> dict:
    """Look up a discount code."""
    dc = get_discount_by_code(args.code)
    if dc is None:
        return {
            "discountCode": None,
            "userErrors": [{"field": "code", "message": f"Discount code '{args.code}' not found"}],
        }
    return {"discountCode": dc, "userErrors": []}


def handle_list_discount_codes(args: ListDiscountCodesArgs) -> dict:
    """List all discount codes."""
    codes = get_all_discount_codes()
    if args.active_only:
        codes = [code for code in codes if code.active]
    codes.sort(key=lambda code: code.code)
    return {"discountCodes": codes, "totalCount": len(codes)}


def handle_update_discount_code(args: UpdateDiscountCodeArgs) -> dict:
    """Update a discount code."""
    dc = get_discount_by_code(args.code)
    if dc is None:
        return {
            "discountCode": None,
            "userErrors": [{"field": "code", "message": f"Discount code '{args.code}' not found"}],
        }
    product_errors = _missing_product_errors(args.product_ids)
    if product_errors:
        return {"discountCode": None, "userErrors": product_errors}
    tier_errors = _minimum_tier_errors(args.minimum_tier)
    if tier_errors:
        return {"discountCode": None, "userErrors": tier_errors}

    if args.active is not None:
        dc.active = args.active
    if args.value is not None:
        dc.value = args.value
    if args.usage_limit is not None:
        dc.usageLimit = args.usage_limit
    if args.minimum_purchase is not None:
        dc.minimumPurchase = MoneyV2(amount=f"{args.minimum_purchase:.2f}", currencyCode="USD")
    if args.product_ids is not None:
        dc.productIds = list(dict.fromkeys(args.product_ids)) or None
    if args.minimum_tier is not None:
        # Empty string clears the restriction; any other value sets it
        dc.minimumTier = args.minimum_tier or None

    dc.updatedAt = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    save_state()

    return {"discountCode": dc, "userErrors": []}


def handle_delete_discount_code(args: DeleteDiscountCodeArgs) -> dict:
    """Delete a discount code."""
    dc = get_discount_by_code(args.code)
    if dc is None:
        return {
            "deletedCode": None,
            "userErrors": [{"field": "code", "message": f"Discount code '{args.code}' not found"}],
        }

    state = get_state()
    del state.discount_codes[dc.id]
    save_state()

    return {"deletedCode": args.code.upper(), "userErrors": []}
