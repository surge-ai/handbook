"""Review and return tool handlers."""

import contextlib
from datetime import UTC, datetime
from typing import cast

from shopify.models import (
    CreateReturnArgs,
    CreateReviewArgs,
    DeleteReviewArgs,
    FinancialStatus,
    GetProductReviewsArgs,
    GetReturnArgs,
    ListReturnsArgs,
    UpdateReturnArgs,
    UpdateReviewArgs,
)
from shopify.state import (
    LooseReturn,
    LooseReview,
    adjust_variant_stock,
    get_all_returns,
    get_next_return_id,
    get_next_review_id,
    get_order_by_id,
    get_product_by_id,
    get_return_by_id,
    get_review_by_id,
    get_reviews_for_product,
    get_state,
    save_state,
)
from shopify.tools.order_effects import reverse_order_effects, reverse_return_effects


def _value_get(value, key: str, default=None):
    if isinstance(value, dict):
        return value.get(key, default)
    if hasattr(value, "get"):
        return value.get(key, default)
    return getattr(value, key, default)


def _money_amount(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, dict):
        return float(value.get("amount", "0") or 0)
    return float(getattr(value, "amount", "0") or 0)


def _effective_item_refund_ratio(order) -> float:
    subtotal = _money_amount(_value_get(order, "subtotalPrice"))
    if subtotal <= 0:
        return 1.0
    gift_card_total = sum(
        _money_amount(_value_get(card, "amountUsed", {})) for card in _value_get(order, "appliedGiftCards", []) or []
    )
    shipping_total = _money_amount(_value_get(order, "shippingPrice"))
    settled_item_total = _money_amount(_value_get(order, "totalPrice")) + gift_card_total - shipping_total
    return min(1.0, max(0.0, settled_item_total / subtotal))


def _returned_quantities_for_order(order_id: str) -> dict[str, int]:
    """Return quantities already tied up in non-rejected returns for an order."""
    state = get_state()
    quantities: dict[str, int] = {}
    for return_obj in state.returns.values():
        if return_obj.orderId != order_id or return_obj.status == "REJECTED":
            continue
        for line_item in return_obj.lineItems:
            quantities[line_item.orderLineItemId] = quantities.get(line_item.orderLineItemId, 0) + line_item.quantity
    return quantities


def handle_create_return(args: CreateReturnArgs) -> dict:
    """Create a return request linked to an order."""
    order = get_order_by_id(args.order_id)
    if order is None:
        return {
            "return": None,
            "userErrors": [{"field": "order_id", "message": f"Order not found: {args.order_id}"}],
        }

    if order.cancelledAt is not None:
        return {
            "return": None,
            "userErrors": [{"field": "order_id", "message": "Cannot return a cancelled order"}],
        }

    # Validate line items reference real order line items and cannot exceed
    # the quantity still available for return across existing non-rejected
    # return requests.
    order_lines_by_id = {line_item.id: line_item for line_item in order.lineItems}
    already_returned = _returned_quantities_for_order(args.order_id)
    requested_quantities: dict[str, int] = {}
    parsed_line_items = []
    errors = []

    for item in args.line_items:
        line_item_id = item.orderLineItemId
        order_line = order_lines_by_id.get(line_item_id)
        if order_line is None:
            errors.append({"field": "line_items", "message": f"Order line item not found: {line_item_id}"})
            continue

        requested_so_far = requested_quantities.get(line_item_id, 0)
        remaining_quantity = order_line.quantity - already_returned.get(line_item_id, 0) - requested_so_far
        if item.quantity > remaining_quantity:
            errors.append(
                {
                    "field": "line_items",
                    "message": (
                        f"Return quantity {item.quantity} exceeds remaining returnable quantity "
                        f"{max(remaining_quantity, 0)} for order line item {line_item_id}"
                    ),
                }
            )
            continue

        requested_quantities[line_item_id] = requested_so_far + item.quantity
        parsed_line_items.append(
            {
                "orderLineItemId": line_item_id,
                "quantity": item.quantity,
                "reason": item.reason,
            }
        )

    if errors:
        return {"return": None, "userErrors": errors}

    if not parsed_line_items:
        return {
            "return": None,
            "userErrors": [{"field": "line_items", "message": "No valid line items to return"}],
        }

    # Calculate item refund amount from the customer-paid effective price.
    # order.lineItems keep pre-discount unit prices, while order.totalPrice plus
    # gift cards is the total settled value after discounts/redemptions.
    item_refund_ratio = _effective_item_refund_ratio(order)
    refund_total = 0.0
    currency = "USD"
    for ret_item in parsed_line_items:
        order_li = order_lines_by_id[ret_item["orderLineItemId"]]
        unit_price = float(order_li.price.amount) * item_refund_ratio
        refund_total += unit_price * ret_item["quantity"]
        currency = order_li.price.currencyCode

    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return_id = get_next_return_id()

    return_obj = {
        "id": return_id,
        "orderId": args.order_id,
        "status": "REQUESTED",
        "lineItems": parsed_line_items,
        "refundAmount": {"amount": f"{refund_total:.2f}", "currencyCode": currency},
        "reason": args.reason,
        "note": args.note,
        "createdAt": now,
        "updatedAt": now,
    }

    state = get_state()
    state.returns[return_id] = LooseReturn.model_validate(return_obj)
    save_state()

    return {"return": return_obj, "userErrors": []}


def handle_create_review(args: CreateReviewArgs) -> dict:
    """Create a new product review."""
    product = get_product_by_id(args.product_id)
    if product is None:
        return {
            "review": None,
            "userErrors": [{"field": "product_id", "message": f"Product not found: {args.product_id}"}],
        }

    if args.rating < 1 or args.rating > 5:
        return {
            "review": None,
            "userErrors": [{"field": "rating", "message": "Rating must be between 1 and 5"}],
        }

    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    review_id = get_next_review_id()

    review = {
        "id": review_id,
        "productId": args.product_id,
        "rating": args.rating,
        "title": args.title,
        "body": args.body,
        "author": args.author,
        "email": args.email,
        "status": "PUBLISHED",
        "createdAt": now,
        "updatedAt": now,
    }

    state = get_state()
    state.reviews[review_id] = LooseReview.model_validate(review)
    save_state()

    return {"review": review, "userErrors": []}


def handle_delete_review(args: DeleteReviewArgs) -> dict:
    """Delete a review."""
    review = get_review_by_id(args.review_id)
    if review is None:
        return {
            "deletedReviewId": None,
            "userErrors": [{"field": "review_id", "message": f"Review not found: {args.review_id}"}],
        }

    state = get_state()
    del state.reviews[args.review_id]
    save_state()

    return {"deletedReviewId": args.review_id, "userErrors": []}


def handle_get_product_reviews(args: GetProductReviewsArgs) -> dict:
    """Get reviews for a product with optional status filter and pagination."""
    product = get_product_by_id(args.product_id)
    if product is None:
        return {
            "reviews": [],
            "totalCount": 0,
            "averageRating": None,
            "userErrors": [{"field": "product_id", "message": f"Product not found: {args.product_id}"}],
        }

    reviews = get_reviews_for_product(args.product_id)

    # Filter by status
    if args.status:
        reviews = [review for review in reviews if review.status == args.status]

    # Sort by date descending
    reviews.sort(key=lambda review: review.createdAt, reverse=True)

    # Calculate average rating (from all published reviews, not just filtered page)
    published = [review for review in get_reviews_for_product(args.product_id) if review.status == "PUBLISHED"]
    avg_rating = sum(review.rating for review in published) / len(published) if published else None

    total_count = len(reviews)

    # Pagination
    start_idx = 0
    if args.after:
        with contextlib.suppress(ValueError):
            start_idx = int(args.after) + 1

    end_idx = start_idx + args.limit
    paginated = reviews[start_idx:end_idx]

    has_next = end_idx < total_count
    end_cursor = str(end_idx - 1) if paginated else None

    return {
        "reviews": paginated,
        "totalCount": total_count,
        "averageRating": round(avg_rating, 1) if avg_rating is not None else None,
        "pageInfo": {
            "hasNextPage": has_next,
            "hasPreviousPage": start_idx > 0,
            "endCursor": end_cursor,
        },
        "userErrors": [],
    }


def handle_get_return(args: GetReturnArgs) -> dict:
    """Retrieve a return by its ID."""
    return_obj = get_return_by_id(args.return_id)
    if return_obj is None:
        return {
            "return": None,
            "userErrors": [{"field": "return_id", "message": f"Return not found: {args.return_id}"}],
        }
    return {"return": return_obj, "userErrors": []}


def handle_list_returns(args: ListReturnsArgs) -> dict:
    """List returns with optional order and status filtering."""
    returns = get_all_returns()

    if args.order_id:
        returns = [return_obj for return_obj in returns if return_obj.orderId == args.order_id]

    if args.status:
        returns = [return_obj for return_obj in returns if return_obj.status == args.status]

    # Sort by creation date descending
    returns.sort(key=lambda return_obj: return_obj.createdAt, reverse=True)

    total_count = len(returns)

    start_idx = 0
    if args.after:
        with contextlib.suppress(ValueError):
            start_idx = int(args.after) + 1

    end_idx = start_idx + args.limit
    paginated = returns[start_idx:end_idx]

    has_next = end_idx < total_count
    end_cursor = str(end_idx - 1) if paginated else None

    return {
        "returns": paginated,
        "pageInfo": {
            "hasNextPage": has_next,
            "hasPreviousPage": start_idx > 0,
            "startCursor": str(start_idx) if paginated else None,
            "endCursor": end_cursor,
        },
        "totalCount": total_count,
    }


_ALLOWED_RETURN_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "REQUESTED": {"APPROVED", "RECEIVED", "REFUNDED", "REJECTED"},
    "APPROVED": {"RECEIVED", "REFUNDED", "REJECTED"},
    "RECEIVED": {"REFUNDED", "REJECTED"},
    "REFUNDED": set(),
    "REJECTED": set(),
}


def handle_update_return(args: UpdateReturnArgs) -> dict:
    """Update a return's status or note. When status moves to REFUNDED, updates the order's financial status."""
    return_obj = get_return_by_id(args.return_id)
    if return_obj is None:
        return {
            "return": None,
            "userErrors": [{"field": "return_id", "message": f"Return not found: {args.return_id}"}],
        }

    if args.status is not None:
        status_upper = args.status
        prev_status = return_obj.status
        if status_upper != prev_status and status_upper not in _ALLOWED_RETURN_STATUS_TRANSITIONS.get(
            prev_status, set()
        ):
            return {
                "return": None,
                "userErrors": [
                    {
                        "field": "status",
                        "message": f"Cannot transition return from {prev_status} to {status_upper}",
                    }
                ],
            }
        return_obj.status = status_upper

        # When return is refunded, update the order's financial status
        if status_upper == "REFUNDED":
            order = get_order_by_id(return_obj.orderId)
            if order is not None:
                order_total = float(order.totalPrice.amount)
                total_refunded = sum(
                    float(ret.refundAmount.amount) if ret.refundAmount is not None else 0
                    for ret in get_state().returns.values()
                    if ret.orderId == return_obj.orderId and ret.status == "REFUNDED"
                )
                if total_refunded >= order_total:
                    order.financialStatus = "REFUNDED"
                    if prev_status != "REFUNDED":
                        reverse_return_effects(
                            order, return_obj, reversed_at=datetime.now(UTC).isoformat().replace("+00:00", "Z")
                        )
                        reverse_order_effects(order, reversed_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"))
                else:
                    order.financialStatus = cast(FinancialStatus, "PARTIALLY_REFUNDED")
                    if prev_status != "REFUNDED":
                        reverse_return_effects(
                            order, return_obj, reversed_at=datetime.now(UTC).isoformat().replace("+00:00", "Z")
                        )
                order.updatedAt = datetime.now(UTC).isoformat().replace("+00:00", "Z")

                # Restore stock if the order never shipped and we're transitioning
                # into REFUNDED for the first time. If fulfilled, the product left
                # the warehouse and a physical return is beyond the scope of the mock.
                if prev_status != "REFUNDED" and order.fulfillmentStatus == "UNFULFILLED":
                    order_lines_by_id = {line_item.id: line_item for line_item in order.lineItems}
                    for ret_li in return_obj.lineItems:
                        order_li = order_lines_by_id.get(ret_li.orderLineItemId)
                        if not order_li:
                            continue
                        variant_id = order_li.variantId
                        qty = ret_li.quantity
                        if variant_id and qty > 0:
                            adjust_variant_stock(variant_id, qty)

    if args.note is not None:
        return_obj.note = args.note

    return_obj.updatedAt = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    save_state()

    return {"return": return_obj, "userErrors": []}


def handle_update_review(args: UpdateReviewArgs) -> dict:
    """Update a review's status, title, body, or rating."""
    review = get_review_by_id(args.review_id)
    if review is None:
        return {
            "review": None,
            "userErrors": [{"field": "review_id", "message": f"Review not found: {args.review_id}"}],
        }

    if args.status is not None:
        review.status = args.status

    if args.title is not None:
        review.title = args.title
    if args.body is not None:
        review.body = args.body
    if args.rating is not None:
        review.rating = args.rating

    review.updatedAt = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    save_state()

    return {"review": review, "userErrors": []}
