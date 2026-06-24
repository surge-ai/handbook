"""Customer-scoped self-service tool handlers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from shopify.models import (
    CreateReturnArgs,
    CreateReviewArgs,
    GetOrderArgs,
    MailingAddress,
    RedeemPointsArgs,
)
from shopify.state import (
    get_customer_by_email,
    get_order_by_id,
    get_state,
    save_state,
)
from shopify.tools.customers import _set_default_address
from shopify.tools.loyalty import compute_tier


def _current_customer() -> tuple[Any | None, str | None]:
    """Resolve the current-customer record from state.

    Returns (customer, error_message). If the store has no
    ``current_customer_email`` set, or the referenced customer doesn't exist,
    returns (None, reason).
    """
    state = get_state()
    email = state.current_customer_email
    if not email:
        return None, "Customer identity not set for this store (current_customer_email is unset)"
    customer = get_customer_by_email(email)
    if customer is None:
        return None, f"Customer '{email}' not found in store"
    return customer, None


def _customer_error(message: str) -> dict[str, Any]:
    return {"customer": None, "userErrors": [{"field": "current_customer_email", "message": message}]}


def handle_get_my_customer() -> dict[str, Any]:
    """Return the current customer's own profile."""
    customer, err = _current_customer()
    if customer is None:
        return _customer_error(err or "Customer not found")
    return {"customer": customer, "userErrors": []}


def handle_update_my_customer(
    first_name: str | None = None,
    last_name: str | None = None,
    phone: str | None = None,
    address: dict | None = None,
    accepts_marketing: bool | None = None,
) -> dict[str, Any]:
    """Update a limited set of fields on the current customer's own profile.

    Admin-managed fields (tags, note, state, ordersCount/totalSpent, loyalty
    balances) are not accessible here — those remain on the admin
    `update_customer` tool.
    """
    customer, err = _current_customer()
    if customer is None:
        return _customer_error(err or "Customer not found")

    address_model = MailingAddress.model_validate(address) if address is not None else None

    if first_name is not None:
        customer.firstName = first_name
    if last_name is not None:
        customer.lastName = last_name
    if phone is not None:
        customer.phone = phone
    if accepts_marketing is not None:
        customer.acceptsMarketing = accepts_marketing
    if address_model is not None:
        _set_default_address(customer, address_model)

    customer.updatedAt = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    save_state()
    return {"customer": customer, "userErrors": []}


def handle_get_my_loyalty_balance() -> dict[str, Any]:
    """Return the current customer's loyalty balance/tier."""
    customer, err = _current_customer()
    if customer is None:
        return {
            "balance": None,
            "userErrors": [{"field": "current_customer_email", "message": err or "Customer not found"}],
        }
    return {
        "balance": {
            "customerId": customer.id,
            "pointsBalance": customer.pointsBalance,
            "lifetimePoints": customer.lifetimePoints,
            "tier": customer.tier,
            "loyaltyJoinedAt": customer.loyaltyJoinedAt,
        },
        "userErrors": [],
    }


def handle_get_my_loyalty_tier() -> dict[str, Any]:
    """Return the full tier object for the current customer's tier (or null)."""
    customer, err = _current_customer()
    if customer is None:
        return {
            "tier": None,
            "userErrors": [{"field": "current_customer_email", "message": err or "Customer not found"}],
        }
    state = get_state()
    tier_obj = compute_tier(customer.lifetimePoints, state.loyalty_program.tiers)
    return {"tier": tier_obj, "userErrors": []}


def handle_redeem_my_points(points: int) -> dict[str, Any]:
    """Redeem loyalty points from the current customer's balance."""
    customer, err = _current_customer()
    if customer is None:
        return {
            "redemption": None,
            "userErrors": [{"field": "current_customer_email", "message": err or "Customer not found"}],
        }
    # Reuse the admin handler's validation/logic by calling it with the
    # resolved customer's id. The admin tool already enforces balance limits.
    from shopify.tools.loyalty import handle_redeem_points

    return handle_redeem_points(RedeemPointsArgs(customer_id=customer.id, points=points))


def handle_list_my_orders(limit: int = 20, after: str | None = None) -> dict[str, Any]:
    """List orders belonging to the current customer."""
    customer, err = _current_customer()
    if customer is None:
        return {
            "orders": [],
            "totalCount": 0,
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "userErrors": [{"field": "current_customer_email", "message": err or "Customer not found"}],
        }

    state = get_state()
    email_lower = customer.email.lower()
    mine = [order for order in state.orders.values() if (order.email or "").lower() == email_lower]
    mine.sort(key=lambda order: order.createdAt, reverse=True)

    # Simple cursor = starting index encoded as str.
    start = 0
    if after is not None:
        try:
            start = int(after) + 1
        except ValueError:
            start = 0
    end = start + limit
    paginated = mine[start:end]
    has_next = end < len(mine)
    end_cursor = str(end - 1) if paginated else None

    return {
        "orders": paginated,
        "totalCount": len(mine),
        "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
        "userErrors": [],
    }


def handle_get_my_order(args: GetOrderArgs) -> dict[str, Any]:
    """Return a single order by ID, only if it belongs to the current customer."""
    customer, err = _current_customer()
    if customer is None:
        return {
            "order": None,
            "userErrors": [{"field": "current_customer_email", "message": err or "Customer not found"}],
        }

    order = get_order_by_id(args.order_id)
    if order is None:
        return {"order": None, "userErrors": [{"field": "order_id", "message": f"Order not found: {args.order_id}"}]}
    if (order.email or "").lower() != customer.email.lower():
        return {
            "order": None,
            "userErrors": [{"field": "order_id", "message": "Order does not belong to the current customer"}],
        }
    return {"order": order, "userErrors": []}


def handle_create_my_return(args: CreateReturnArgs) -> dict[str, Any]:
    """Create a return on an order, only if it belongs to the current customer.

    Reuses the admin `handle_create_return` after the ownership check, so
    refund math and validation stay in one place.
    """
    customer, err = _current_customer()
    if customer is None:
        return {
            "return": None,
            "userErrors": [{"field": "current_customer_email", "message": err or "Customer not found"}],
        }

    order = get_order_by_id(args.order_id)
    if order is None:
        return {"return": None, "userErrors": [{"field": "order_id", "message": f"Order not found: {args.order_id}"}]}
    if (order.email or "").lower() != customer.email.lower():
        return {
            "return": None,
            "userErrors": [{"field": "order_id", "message": "Order does not belong to the current customer"}],
        }

    from shopify.tools.reviews_returns import handle_create_return

    return handle_create_return(args)


def handle_create_my_review(
    product_id: str,
    rating: int,
    title: str = "",
    body: str = "",
) -> dict[str, Any]:
    """Post a review as the current customer (author/email filled in automatically)."""
    customer, err = _current_customer()
    if customer is None:
        return {
            "review": None,
            "userErrors": [{"field": "current_customer_email", "message": err or "Customer not found"}],
        }

    # Sign the review with the customer's name+email. Avoid importing the
    # admin create_review handler directly to sidestep its arg model,
    # which demands separate author/email inputs.
    from shopify.tools.reviews_returns import handle_create_review

    display = " ".join(filter(None, [customer.firstName, customer.lastName])) or customer.email
    try:
        args = CreateReviewArgs(
            product_id=product_id,
            rating=rating,
            title=title,
            body=body,
            author=display,
            email=customer.email,
        )
    except ValidationError as exc:
        return {
            "review": None,
            "userErrors": [
                {"field": ".".join(map(str, error["loc"])), "message": error["msg"]} for error in exc.errors()
            ],
        }
    return handle_create_review(args)


__all__ = [
    "handle_create_my_return",
    "handle_create_my_review",
    "handle_get_my_customer",
    "handle_get_my_loyalty_balance",
    "handle_get_my_loyalty_tier",
    "handle_get_my_order",
    "handle_list_my_orders",
    "handle_redeem_my_points",
    "handle_update_my_customer",
]
