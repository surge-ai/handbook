"""Shipping method tool handlers."""

import re

from shopify.models import (
    CreateShippingMethodArgs,
    DeleteShippingMethodArgs,
    ListShippingMethodsArgs,
    MoneyV2,
    UpdateShippingMethodArgs,
)
from shopify.state import (
    LooseShippingMethod,
    get_all_shipping_methods,
    get_shipping_method_by_id,
    get_state,
    save_state,
)


def handle_create_shipping_method(args: CreateShippingMethodArgs) -> dict:
    """Create a new shipping method."""
    method_id = re.sub(r"[^a-z0-9]+", "-", args.title.lower()).strip("-")
    if not method_id:
        return {
            "shippingMethod": None,
            "userErrors": [
                {"field": "title", "message": "Shipping method title must contain at least one letter or number"}
            ],
        }

    existing = get_shipping_method_by_id(method_id)
    if existing is not None:
        return {
            "shippingMethod": None,
            "userErrors": [{"field": "title", "message": f"Shipping method '{method_id}' already exists"}],
        }

    method = {
        "id": method_id,
        "title": args.title,
        "price": {"amount": args.price, "currencyCode": "USD"},
        "estimatedDays": args.estimated_days,
        "active": True,
    }

    state = get_state()
    shipping_method = LooseShippingMethod.model_validate(method)
    state.shipping_methods[method_id] = shipping_method
    save_state()

    return {"shippingMethod": shipping_method, "userErrors": []}


def handle_list_shipping_methods(args: ListShippingMethodsArgs) -> dict:
    """List all shipping methods."""
    methods = get_all_shipping_methods()
    if args.active_only:
        methods = [method for method in methods if method.active]
    methods.sort(key=lambda method: float(method.price.amount))
    return {"shippingMethods": methods, "totalCount": len(methods)}


def handle_update_shipping_method(args: UpdateShippingMethodArgs) -> dict:
    """Update a shipping method."""
    method = get_shipping_method_by_id(args.shipping_method_id)
    if method is None:
        return {
            "shippingMethod": None,
            "userErrors": [
                {"field": "shipping_method_id", "message": f"Shipping method not found: {args.shipping_method_id}"}
            ],
        }

    if args.title is not None:
        method.title = args.title
    if args.price is not None:
        method.price = MoneyV2(amount=args.price, currencyCode="USD")
    if args.estimated_days is not None:
        method.estimatedDays = args.estimated_days
    if args.active is not None:
        method.active = args.active

    save_state()
    return {"shippingMethod": method, "userErrors": []}


def handle_delete_shipping_method(args: DeleteShippingMethodArgs) -> dict:
    """Delete a shipping method."""
    method = get_shipping_method_by_id(args.shipping_method_id)
    if method is None:
        return {
            "deletedMethodId": None,
            "userErrors": [
                {"field": "shipping_method_id", "message": f"Shipping method not found: {args.shipping_method_id}"}
            ],
        }

    state = get_state()
    del state.shipping_methods[args.shipping_method_id]
    save_state()

    return {"deletedMethodId": args.shipping_method_id, "userErrors": []}
