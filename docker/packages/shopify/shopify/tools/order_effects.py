"""Helpers for reversing side effects created during checkout."""

from typing import Any

from shopify.models import MoneyV2
from shopify.state import get_customer_by_email, get_discount_by_code, get_gift_card_by_code, get_state
from shopify.tools.loyalty import compute_tier


def _value_get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    if hasattr(value, "get"):
        return value.get(key, default)
    return getattr(value, key, default)


def _money_amount(value: Any) -> float:
    if isinstance(value, MoneyV2):
        return float(value.amount)
    if isinstance(value, dict):
        return float(value.get("amount", "0") or 0)
    return float(getattr(value, "amount", "0") or 0)


def applied_gift_card_code(applied_card: Any) -> str:
    """Return the backing gift-card code for an applied cart/order card."""
    code = _value_get(applied_card, "code")
    if code:
        return str(code)
    card_id = _value_get(applied_card, "id")
    return str(card_id).rsplit("/", 1)[-1] if card_id else ""


def _order_effect_totals(order: Any) -> dict[str, Any]:
    totals = order.get("reversedEffects") or {}
    if not isinstance(totals, dict):
        totals = {}
    totals.setdefault("customerSpendAmount", "0.00")
    totals.setdefault("giftCardAmount", "0.00")
    totals.setdefault("loyaltyPointsEarned", 0)
    totals.setdefault("loyaltyPointsRedeemed", 0)
    order["reversedEffects"] = totals
    return totals


def _add_reversed_money(order: Any, key: str, amount: float) -> None:
    totals = _order_effect_totals(order)
    current = float(totals.get(key, "0") or 0)
    totals[key] = f"{current + amount:.2f}"


def _add_reversed_points(order: Any, key: str, points: int) -> None:
    totals = _order_effect_totals(order)
    totals[key] = int(totals.get(key) or 0) + points


def _total_gift_card_amount(order: Any) -> float:
    return sum(
        _money_amount(_value_get(applied_card, "amountUsed", {}))
        for applied_card in order.get("appliedGiftCards", []) or []
    )


def _restore_gift_card_balances(order: Any, amount: float) -> float:
    remaining = max(amount, 0.0)
    restored = 0.0
    for applied_card in order.get("appliedGiftCards", []) or []:
        if remaining <= 0:
            break
        code = applied_gift_card_code(applied_card)
        if not code:
            continue
        gift_card = get_gift_card_by_code(code)
        if gift_card is None:
            continue
        amount_used = _money_amount(_value_get(applied_card, "amountUsed", {}))
        if amount_used <= 0:
            continue
        restore_amount = min(amount_used, remaining)
        balance = float(gift_card.balance.amount)
        gift_card.balance = MoneyV2(
            amount=f"{balance + restore_amount:.2f}",
            currencyCode=gift_card.balance.currencyCode,
        )
        remaining -= restore_amount
        restored += restore_amount
    return restored


def _reverse_customer_amount(order: Any, amount: float, *, reversed_at: str) -> float:
    customer = get_customer_by_email(order.email) if order.email else None
    if customer is None or amount <= 0:
        return 0.0
    prior_spent = float(customer.totalSpent.amount) if customer.totalSpent is not None else 0.0
    reversed_total = min(prior_spent, amount)
    customer.totalSpent = MoneyV2(
        amount=f"{prior_spent - reversed_total:.2f}",
        currencyCode=order.totalPrice.currencyCode,
    )
    customer.updatedAt = reversed_at
    return reversed_total


def _reverse_loyalty_points(order: Any, earned: int, redeemed: int, *, reversed_at: str) -> tuple[int, int]:
    customer = get_customer_by_email(order.email) if order.email else None
    if customer is None or (earned <= 0 and redeemed <= 0):
        return (0, 0)

    customer.pointsBalance = max(0, customer.pointsBalance - earned + redeemed)
    customer.lifetimePoints = max(0, customer.lifetimePoints - earned)
    tier_obj = compute_tier(customer.lifetimePoints, get_state().loyalty_program.tiers)
    customer.tier = tier_obj.name if tier_obj else None
    customer.updatedAt = reversed_at
    return (earned, redeemed)


def reverse_return_effects(order: Any, return_obj: Any, *, reversed_at: str) -> bool:
    """Undo proportional checkout effects for one newly-refunded return."""
    if return_obj.get("sideEffectsReversedAt") is not None:
        return False

    refund_amount = _money_amount(return_obj.refundAmount)
    if refund_amount <= 0:
        return_obj["sideEffectsReversedAt"] = reversed_at
        return False

    totals = _order_effect_totals(order)
    order_total = float(order.totalPrice.amount)
    gift_card_total = _total_gift_card_amount(order)
    settlement_total = order_total + gift_card_total
    customer_remaining = order_total - float(totals["customerSpendAmount"])
    gift_card_remaining = gift_card_total - float(totals["giftCardAmount"])

    if settlement_total > 0 and gift_card_total > 0:
        gift_card_restore = min(gift_card_remaining, refund_amount * (gift_card_total / settlement_total))
        customer_restore = min(customer_remaining, refund_amount * (order_total / settlement_total))
    else:
        gift_card_restore = 0.0
        customer_restore = min(customer_remaining, refund_amount)

    restored_gift_card = _restore_gift_card_balances(order, gift_card_restore)
    restored_customer_spend = _reverse_customer_amount(order, customer_restore, reversed_at=reversed_at)

    subtotal = _money_amount(order.subtotalPrice)
    ratio = min(refund_amount / subtotal, 1.0) if subtotal > 0 else 0.0
    earned_total = int(order.get("loyaltyPointsEarned") or 0)
    redeemed_total = int(order.get("loyaltyPointsRedeemed") or 0)
    earned_to_reverse = min(
        earned_total - int(totals["loyaltyPointsEarned"]),
        round(earned_total * ratio),
    )
    redeemed_to_reverse = min(
        redeemed_total - int(totals["loyaltyPointsRedeemed"]),
        round(redeemed_total * ratio),
    )
    reversed_earned, reversed_redeemed = _reverse_loyalty_points(
        order,
        earned_to_reverse,
        redeemed_to_reverse,
        reversed_at=reversed_at,
    )

    _add_reversed_money(order, "giftCardAmount", restored_gift_card)
    _add_reversed_money(order, "customerSpendAmount", restored_customer_spend)
    _add_reversed_points(order, "loyaltyPointsEarned", reversed_earned)
    _add_reversed_points(order, "loyaltyPointsRedeemed", reversed_redeemed)
    return_obj["sideEffectsReversedAt"] = reversed_at
    return_obj["reversedEffects"] = {
        "customerSpendAmount": f"{restored_customer_spend:.2f}",
        "giftCardAmount": f"{restored_gift_card:.2f}",
        "loyaltyPointsEarned": reversed_earned,
        "loyaltyPointsRedeemed": reversed_redeemed,
    }
    return True


def reverse_order_effects(order: Any, *, reversed_at: str) -> bool:
    """Undo remaining checkout effects for a fully cancelled/refunded order.

    Returns True when this call performed the reversal. Returns False when the
    order had already been reversed, which makes cancellation/refund paths
    idempotent across later state transitions.
    """
    if order.get("sideEffectsReversedAt") is not None:
        return False

    totals = _order_effect_totals(order)
    gift_card_remaining = max(0.0, _total_gift_card_amount(order) - float(totals["giftCardAmount"]))
    restored_gift_card = _restore_gift_card_balances(order, gift_card_remaining)
    _add_reversed_money(order, "giftCardAmount", restored_gift_card)

    discount_entries = order.get("discounts", []) or []
    if not discount_entries and order.get("discount") is not None:
        discount_entries = [order.get("discount")]
    reversed_discount_codes: set[str] = set()
    for discount_entry in discount_entries:
        code = _value_get(discount_entry, "code")
        if not code:
            continue
        normalized_code = str(code).upper()
        if normalized_code in reversed_discount_codes:
            continue
        discount = get_discount_by_code(str(code))
        if discount is not None:
            discount.usageCount = max(0, discount.usageCount - 1)
        reversed_discount_codes.add(normalized_code)

    customer = get_customer_by_email(order.email) if order.email else None
    if customer is not None:
        customer.ordersCount = max(0, customer.ordersCount - 1)
        customer_spend_remaining = max(0.0, float(order.totalPrice.amount) - float(totals["customerSpendAmount"]))
        reversed_spend = _reverse_customer_amount(order, customer_spend_remaining, reversed_at=reversed_at)
        _add_reversed_money(order, "customerSpendAmount", reversed_spend)

        points_earned = int(order.get("loyaltyPointsEarned") or 0)
        points_redeemed = int(order.get("loyaltyPointsRedeemed") or 0)
        earned_remaining = max(0, points_earned - int(totals["loyaltyPointsEarned"]))
        redeemed_remaining = max(0, points_redeemed - int(totals["loyaltyPointsRedeemed"]))
        reversed_earned, reversed_redeemed = _reverse_loyalty_points(
            order,
            earned_remaining,
            redeemed_remaining,
            reversed_at=reversed_at,
        )
        _add_reversed_points(order, "loyaltyPointsEarned", reversed_earned)
        _add_reversed_points(order, "loyaltyPointsRedeemed", reversed_redeemed)
        customer.updatedAt = reversed_at

    order["sideEffectsReversedAt"] = reversed_at
    return True
