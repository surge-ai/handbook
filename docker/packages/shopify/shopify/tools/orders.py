"""Order tool handlers."""

import contextlib
import re
import secrets
from datetime import UTC, datetime

from shopify.models import (
    CancelOrderArgs,
    CreateOrderArgs,
    CreditCardPaymentMethod,
    GetOrderArgs,
    ListOrdersArgs,
    MailingAddress,
    MoneyV2,
    PaymentMethodInput,
    UpdateOrderArgs,
)
from shopify.state import (
    LooseOrder,
    adjust_variant_stock,
    get_all_orders,
    get_cart_by_id,
    get_customer_by_email,
    get_discount_by_code,
    get_gift_card_by_code,
    get_next_line_item_id,
    get_next_order_id,
    get_order_by_id,
    get_shipping_method_by_id,
    get_state,
    get_variant_by_id,
    save_state,
)
from shopify.tools.loyalty import compute_tier
from shopify.tools.order_effects import applied_gift_card_code, reverse_order_effects


def handle_cancel_order(args: CancelOrderArgs) -> dict:
    """Cancel an order."""
    order = get_order_by_id(args.order_id)
    if order is None:
        return {
            "order": None,
            "userErrors": [{"field": "order_id", "message": f"Order not found: {args.order_id}"}],
        }

    if order.cancelledAt is not None:
        return {
            "order": None,
            "userErrors": [{"field": "order_id", "message": "Order is already cancelled"}],
        }

    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    order.cancelledAt = now
    order.financialStatus = "VOIDED" if order.financialStatus == "PENDING" else "REFUNDED"
    order.updatedAt = now

    if args.reason:
        existing_note = order.note or ""
        order.note = f"{existing_note}\nCancellation reason: {args.reason}".strip()

    # Restore stock if the order never shipped. If it was fulfilled (or partially),
    # returning physical product is beyond the scope of this mock.
    if order.fulfillmentStatus == "UNFULFILLED":
        already_returned_by_line_id: dict[str, int] = {}
        for return_obj in get_state().returns.values():
            if return_obj.orderId != args.order_id or return_obj.status != "REFUNDED":
                continue
            for return_line in return_obj.lineItems:
                already_returned_by_line_id[return_line.orderLineItemId] = (
                    already_returned_by_line_id.get(return_line.orderLineItemId, 0) + return_line.quantity
                )

        for line_item in order.lineItems:
            variant_id = line_item.variantId
            qty = line_item.quantity - already_returned_by_line_id.get(line_item.id, 0)
            if variant_id and qty > 0:
                adjust_variant_stock(variant_id, qty)

    reverse_order_effects(order, reversed_at=now)

    save_state()

    return {"order": order, "userErrors": []}


def _validate_payment_method(payment: PaymentMethodInput) -> list[dict]:
    """Validate payment method and return list of errors (empty if valid)."""
    errors = []

    if isinstance(payment, CreditCardPaymentMethod):
        # Validate card number: 13-19 digits after stripping spaces/dashes
        card_number = re.sub(r"[\s\-]", "", payment.card_number)
        if not card_number.isdigit() or not (13 <= len(card_number) <= 19):
            errors.append(
                {
                    "field": "payment_method.card_number",
                    "message": "Card number must be 13-19 digits",
                }
            )

        # Validate CVV: 3-4 digits
        cvv = payment.cvv
        if not cvv.isdigit() or not (3 <= len(cvv) <= 4):
            errors.append(
                {
                    "field": "payment_method.cvv",
                    "message": "CVV must be 3-4 digits",
                }
            )

        # Validate expiry: MM/YY format
        expiry = payment.expiry
        if not re.match(r"^\d{2}/\d{2}$", expiry):
            errors.append(
                {
                    "field": "payment_method.expiry",
                    "message": "Expiry must be in MM/YY format",
                }
            )
    return errors


def _build_payment_display(payment: PaymentMethodInput) -> dict:
    """Build safe display info for the payment method (no sensitive data)."""
    ptype = payment.type

    if isinstance(payment, CreditCardPaymentMethod):
        card_number = re.sub(r"[\s\-]", "", payment.card_number)
        last4 = card_number[-4:] if len(card_number) >= 4 else card_number

        # Simple brand detection
        if card_number.startswith("4"):
            brand = "visa"
        elif card_number.startswith("5"):
            brand = "mastercard"
        elif card_number.startswith("3"):
            brand = "amex"
        else:
            brand = "unknown"

        return {"type": "credit_card", "last4": last4, "brand": brand}
    else:
        return {"type": ptype, "email": payment.email}


def _validate_address(address: MailingAddress, field_name: str) -> list[dict]:
    """Validate that an address has required fields."""
    errors = []
    if not address.address1:
        errors.append({"field": f"{field_name}.address1", "message": f"{field_name} requires address1"})
    if not address.city:
        errors.append({"field": f"{field_name}.city", "message": f"{field_name} requires city"})
    if not address.countryCode:
        errors.append({"field": f"{field_name}.countryCode", "message": f"{field_name} requires countryCode"})
    return errors


def _selected_shipping_method_id(cart) -> str | None:
    for group in cart.deliveryGroups:
        selected = group.selectedDeliveryOption
        if selected is not None:
            return selected.handle
    return None


def _discount_combination_class(discount) -> str:
    if discount.discountType == "FREE_SHIPPING":
        return "shippingDiscounts"
    if discount.productIds:
        return "productDiscounts"
    return "orderDiscounts"


def _discounts_can_combine(first, second) -> bool:
    first_allows_second = getattr(first.combinesWith, _discount_combination_class(second))
    second_allows_first = getattr(second.combinesWith, _discount_combination_class(first))
    return first_allows_second and second_allows_first


def handle_create_order(args: CreateOrderArgs) -> dict:
    """Convert a cart into an order with payment validation."""
    cart = get_cart_by_id(args.cart_id)
    if cart is None:
        return {
            "order": None,
            "userErrors": [{"field": "cart_id", "message": f"Cart not found: {args.cart_id}"}],
        }

    lines = cart.lines
    if not lines:
        return {
            "order": None,
            "userErrors": [{"field": "cart_id", "message": "Cart is empty"}],
        }

    # Validate payment method
    payment_errors = _validate_payment_method(args.payment_method)
    if payment_errors:
        return {"order": None, "userErrors": payment_errors}

    # Validate addresses
    address_errors = []
    address_errors.extend(_validate_address(args.shipping_address, "shipping_address"))
    address_errors.extend(_validate_address(args.billing_address, "billing_address"))
    if address_errors:
        return {"order": None, "userErrors": address_errors}

    shipping_method_id = args.shipping_method_id or _selected_shipping_method_id(cart)
    if shipping_method_id is None:
        return {
            "order": None,
            "userErrors": [
                {
                    "field": "shipping_method_id",
                    "message": "shipping_method_id is required unless the cart has a selected delivery option",
                }
            ],
        }

    # Validate shipping method
    shipping_method = get_shipping_method_by_id(shipping_method_id)
    if shipping_method is None:
        return {
            "order": None,
            "userErrors": [
                {
                    "field": "shipping_method_id",
                    "message": f"Shipping method not found: '{shipping_method_id}'. Use list_shipping_methods to see available options.",
                }
            ],
        }
    if not shipping_method.active:
        return {
            "order": None,
            "userErrors": [
                {"field": "shipping_method_id", "message": f"Shipping method '{shipping_method_id}' is not active"}
            ],
        }

    for cart_line in lines:
        merch = cart_line.merchandise
        _, variant = get_variant_by_id(merch.id)
        if (
            variant is not None
            and variant.quantityAvailable is not None
            and cart_line.quantity > variant.quantityAvailable
        ):
            return {
                "order": None,
                "userErrors": [
                    {
                        "field": "cart_id",
                        "message": (
                            f"Insufficient inventory for variant {merch.id}: "
                            f"requested {cart_line.quantity}, available {variant.quantityAvailable}"
                        ),
                    }
                ],
            }

    # Pull buyer identity from cart if not overridden
    buyer = cart.buyerIdentity
    state = get_state()
    email = args.email or buyer.email or state.current_customer_email
    phone = args.phone or buyer.phone

    # Convert cart lines to order line items
    order_line_items = []
    subtotal = 0.0
    currency = "USD"

    for cart_line in lines:
        merch = cart_line.merchandise
        product_info = merch.product or {}
        unit_price = cart_line.cost.amountPerQuantity
        quantity = cart_line.quantity
        price_amount = float(unit_price.amount)
        currency = unit_price.currencyCode
        total_price = price_amount * quantity

        line_item = {
            "title": product_info.get("title", merch.title),
            "variantTitle": merch.title,
            "quantity": quantity,
            "sku": None,
            "variantId": merch.id,
            "productId": product_info.get("id"),
            "price": unit_price.model_dump(mode="json"),
            "totalPrice": {"amount": f"{total_price:.2f}", "currencyCode": currency},
            "image": merch.image,
        }
        order_line_items.append(line_item)
        subtotal += total_price

    # Calculate shipping cost
    shipping_cost = float(shipping_method.price.amount)

    # Look up customer by email (enables loyalty effects)
    program = state.loyalty_program
    loyalty_enabled = program.enabled
    customer = get_customer_by_email(email) if email else None

    # Apply tier discount before discount code (loyalty tier is a separate discount layer)
    tier_discount_amount = 0.0
    tier_info = None
    if loyalty_enabled and args.apply_tier_discount and customer is not None:
        tier_obj = compute_tier(customer.lifetimePoints, program.tiers)
        if tier_obj and tier_obj.discount_percent > 0:
            tier_discount_amount = subtotal * (tier_obj.discount_percent / 100)
            tier_info = {
                "name": tier_obj.name,
                "discountPercent": tier_obj.discount_percent,
                "discountAmount": {"amount": f"{tier_discount_amount:.2f}", "currencyCode": currency},
            }

    # Redeem loyalty points (converts to fixed-amount discount)
    points_redeemed = 0
    redemption_amount = 0.0
    if args.redeem_points:
        if not loyalty_enabled:
            return {
                "order": None,
                "userErrors": [{"field": "redeem_points", "message": "Loyalty program is not enabled"}],
            }
        if customer is None:
            return {
                "order": None,
                "userErrors": [
                    {"field": "redeem_points", "message": "Cannot redeem points without a matching customer email"}
                ],
            }
        if args.redeem_points <= 0:
            return {
                "order": None,
                "userErrors": [{"field": "redeem_points", "message": "redeem_points must be positive"}],
            }
        balance = customer.pointsBalance
        if args.redeem_points > balance:
            return {
                "order": None,
                "userErrors": [
                    {
                        "field": "redeem_points",
                        "message": f"Insufficient balance: requested {args.redeem_points}, available {balance}",
                    }
                ],
            }
        redemption_rate = program.redemption_rate
        raw_value = args.redeem_points / redemption_rate
        # Cap redemption at max_redemption_percent of the post-tier-discount subtotal
        cap = (subtotal - tier_discount_amount) * (program.max_redemption_percent / 100)
        redemption_amount = min(raw_value, cap)
        # Only deduct the points that actually became discount. When raw_value
        # exceeds cap, the extra points would otherwise be burned for no value.
        points_redeemed = (
            round(redemption_amount * redemption_rate) if redemption_amount < raw_value else args.redeem_points
        )

    validated_applied_gift_cards = []
    for applied_card in cart.appliedGiftCards:
        code = applied_gift_card_code(applied_card)
        gift_card = get_gift_card_by_code(code)
        if gift_card is None:
            return {
                "order": None,
                "userErrors": [{"field": "gift_card_codes", "message": f"Gift card '{code}' not found"}],
            }
        if not gift_card.active:
            return {
                "order": None,
                "userErrors": [{"field": "gift_card_codes", "message": f"Gift card '{code}' is not active"}],
            }
        balance = float(gift_card.balance.amount)
        if balance <= 0:
            return {
                "order": None,
                "userErrors": [{"field": "gift_card_codes", "message": f"Gift card '{code}' has no remaining balance"}],
            }
        validated_applied_gift_cards.append((applied_card, gift_card))

    # Apply explicit checkout discount, or fall back to applicable cart-level
    # discount codes already applied through update_cart.
    effective_discount_codes = (
        [args.discount_code]
        if args.discount_code
        else [cart_discount.code for cart_discount in cart.discountCodes if cart_discount.applicable]
    )

    # Apply discount codes if provided
    discount_infos = []
    discount_amount = 0.0
    item_discount = 0.0
    applied_discount_models = []

    for effective_discount_code in effective_discount_codes:
        if not effective_discount_code:
            continue
        dc = get_discount_by_code(effective_discount_code)
        if dc is None:
            return {
                "order": None,
                "userErrors": [
                    {"field": "discount_code", "message": f"Discount code '{effective_discount_code}' not found"}
                ],
            }
        if not dc.active:
            return {
                "order": None,
                "userErrors": [
                    {"field": "discount_code", "message": f"Discount code '{effective_discount_code}' is not active"}
                ],
            }
        if dc.usageLimit and dc.usageCount >= dc.usageLimit:
            return {
                "order": None,
                "userErrors": [
                    {
                        "field": "discount_code",
                        "message": f"Discount code '{effective_discount_code}' has reached its usage limit",
                    }
                ],
            }
        incompatible = next(
            (existing for existing in applied_discount_models if not _discounts_can_combine(existing, dc)),
            None,
        )
        if incompatible is not None:
            return {
                "order": None,
                "userErrors": [
                    {
                        "field": "discount_code",
                        "message": f"Discount code '{effective_discount_code}' cannot be combined with '{incompatible.code}'",
                    }
                ],
            }

        # Check tier gate (loyalty-restricted codes)
        required_tier_name = dc.minimumTier
        if required_tier_name:
            program_tiers = program.tiers
            required_tier = next((tier for tier in program_tiers if tier.name == required_tier_name), None)
            if required_tier is None:
                return {
                    "order": None,
                    "userErrors": [
                        {
                            "field": "discount_code",
                            "message": (
                                f"Discount code '{effective_discount_code}' requires tier "
                                f"'{required_tier_name}' which is not configured on this store"
                            ),
                        }
                    ],
                }
            customer_tier = compute_tier(customer.lifetimePoints, program_tiers) if customer else None
            customer_threshold = customer_tier.min_lifetime_points if customer_tier else -1
            if customer_threshold < required_tier.min_lifetime_points:
                return {
                    "order": None,
                    "userErrors": [
                        {
                            "field": "discount_code",
                            "message": (
                                f"Discount code '{effective_discount_code}' requires '{required_tier_name}' tier or higher"
                            ),
                        }
                    ],
                }

        # Check minimum purchase (against subtotal, not including shipping)
        min_purchase = dc.minimumPurchase
        if min_purchase and subtotal < float(min_purchase.amount):
            return {
                "order": None,
                "userErrors": [
                    {
                        "field": "discount_code",
                        "message": f"Minimum purchase of ${min_purchase.amount} required (subtotal: ${subtotal:.2f})",
                    }
                ],
            }

        discount_type = dc.discountType
        discount_value = float(dc.value)
        product_ids_filter = dc.productIds
        code_discount_amount = 0.0

        if discount_type == "FREE_SHIPPING":
            # Free shipping — savings come from zeroing shipping, not from subtotal
            code_discount_amount = shipping_cost
            shipping_cost = 0.0
            # Don't set item_discount — FREE_SHIPPING only affects shipping
        elif discount_type == "PERCENTAGE":
            # Percentage off eligible items
            if product_ids_filter:
                # Only discount matching products
                eligible_total = sum(
                    float(li.get("totalPrice", {}).get("amount", "0"))
                    for li in order_line_items
                    if li.get("productId") in product_ids_filter
                )
            else:
                eligible_total = subtotal
            code_discount_amount = eligible_total * (discount_value / 100)
            item_discount += code_discount_amount
        elif discount_type == "FIXED_AMOUNT":
            if product_ids_filter:
                eligible_total = sum(
                    float(li.get("totalPrice", {}).get("amount", "0"))
                    for li in order_line_items
                    if li.get("productId") in product_ids_filter
                )
                code_discount_amount = min(discount_value, eligible_total)
            else:
                code_discount_amount = min(discount_value, subtotal)
            item_discount += code_discount_amount

        discount_amount += code_discount_amount
        applied_discount_models.append(dc)

        discount_infos.append(
            {
                "code": dc.code,
                "type": discount_type,
                "value": dc.value,
                "discountAmount": {"amount": f"{code_discount_amount:.2f}", "currencyCode": currency},
                "productIds": product_ids_filter,
                "minimumTier": dc.minimumTier,
            }
        )

    discount_info = discount_infos[0] if discount_infos else None
    total = subtotal - tier_discount_amount - redemption_amount - item_discount + shipping_cost
    # Floor at zero — defensive guard for stacked discounts
    if total < 0:
        total = 0.0

    applied_gift_cards = []
    gift_card_balance_updates = []
    gift_card_amount = 0.0
    for applied_card, gift_card in validated_applied_gift_cards:
        balance = float(gift_card.balance.amount)
        amount_used = min(balance, total)
        total -= amount_used
        gift_card_amount += amount_used
        new_balance = MoneyV2(amount=f"{balance - amount_used:.2f}", currencyCode=gift_card.balance.currencyCode)
        gift_card_balance_updates.append((gift_card, new_balance))
        applied_gift_cards.append(
            {
                "id": applied_card.id,
                "code": applied_gift_card_code(applied_card),
                "lastCharacters": applied_card.lastCharacters,
                "amountUsed": {"amount": f"{amount_used:.2f}", "currencyCode": gift_card.balance.currencyCode},
                "balance": new_balance.model_dump(mode="json"),
                "presentmentAmountUsed": {
                    "amount": f"{amount_used:.2f}",
                    "currencyCode": gift_card.balance.currencyCode,
                },
            }
        )

    # Award points based on post-discount, pre-shipping subtotal
    points_earned = 0
    if loyalty_enabled and customer is not None:
        earn_basis = max(subtotal - tier_discount_amount - redemption_amount - item_discount, 0.0)
        points_earned = int(earn_basis * program.earn_rate)

    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    order_id = get_next_order_id()
    order_num = order_id.rsplit("/", 1)[-1]
    order_line_items = [{"id": get_next_line_item_id(), **line_item} for line_item in order_line_items]

    order = {
        "id": order_id,
        "name": f"#{order_num}",
        "email": email,
        "phone": phone,
        "createdAt": now,
        "updatedAt": now,
        "cancelledAt": None,
        "financialStatus": "PAID",
        "fulfillmentStatus": "UNFULFILLED",
        "trackingNumber": None,
        "trackingUrl": None,
        "paymentMethod": _build_payment_display(args.payment_method),
        "shippingMethod": {
            "id": shipping_method.id,
            "title": shipping_method.title,
            "price": shipping_method.price.model_dump(mode="json"),
        },
        "lineItems": order_line_items,
        "subtotalPrice": {"amount": f"{subtotal:.2f}", "currencyCode": currency},
        "shippingPrice": {"amount": f"{shipping_cost:.2f}", "currencyCode": currency},
        "discount": discount_info,
        "discounts": discount_infos,
        "discountAmount": {"amount": f"{discount_amount:.2f}", "currencyCode": currency},
        "tierDiscount": tier_info,
        "tierDiscountAmount": {"amount": f"{tier_discount_amount:.2f}", "currencyCode": currency},
        "loyaltyPointsRedeemed": points_redeemed,
        "loyaltyRedemptionAmount": {"amount": f"{redemption_amount:.2f}", "currencyCode": currency},
        "loyaltyPointsEarned": points_earned,
        "appliedGiftCards": applied_gift_cards,
        "giftCardAmount": {"amount": f"{gift_card_amount:.2f}", "currencyCode": currency},
        "totalPrice": {"amount": f"{total:.2f}", "currencyCode": currency},
        "totalTax": None,
        "shippingAddress": args.shipping_address.model_dump(mode="json", exclude_none=True),
        "billingAddress": args.billing_address.model_dump(mode="json", exclude_none=True),
        "note": args.note or cart.note,
        "tags": args.tags or [],
        "cartId": args.cart_id,
    }

    state.orders[order_id] = LooseOrder.model_validate(order)
    for discount_model in applied_discount_models:
        discount_model.usageCount += 1
    for gift_card, new_balance in gift_card_balance_updates:
        gift_card.balance = new_balance

    # Reduce stock for each ordered line item (reservation happens on order
    # confirmation; floors at 0 so overselling doesn't produce negative inventory)
    for li in order_line_items:
        variant_id = li.get("variantId")
        qty = int(li.get("quantity", 0) or 0)
        if isinstance(variant_id, str) and qty > 0:
            adjust_variant_stock(variant_id, -qty)

    # Post-order customer updates: orders count, lifetime spend, loyalty balances, tier
    if customer is not None:
        customer.ordersCount += 1
        prior_spent = 0.0
        if customer.totalSpent is not None:
            prior_spent = float(customer.totalSpent.amount)
        customer.totalSpent = MoneyV2(amount=f"{prior_spent + total:.2f}", currencyCode=currency)
        customer.updatedAt = now

        if loyalty_enabled:
            if customer.loyaltyJoinedAt is None:
                customer.loyaltyJoinedAt = now
            customer.pointsBalance = customer.pointsBalance + points_earned - points_redeemed
            customer.lifetimePoints = customer.lifetimePoints + points_earned
            tier_obj = compute_tier(customer.lifetimePoints, program.tiers)
            customer.tier = tier_obj.name if tier_obj else None

    save_state()

    return {"order": order, "userErrors": []}


def handle_get_order(args: GetOrderArgs) -> dict:
    """Retrieve an order by its ID."""
    order = get_order_by_id(args.order_id)
    if order is None:
        return {
            "order": None,
            "userErrors": [{"field": "order_id", "message": f"Order not found: {args.order_id}"}],
        }
    return {"order": order, "userErrors": []}


def handle_list_orders(args: ListOrdersArgs) -> dict:
    """List orders, optionally filtered by status."""
    orders = get_all_orders()

    # Filter by status (matches against both financialStatus and fulfillmentStatus)
    if args.status:
        orders = [
            order for order in orders if order.financialStatus == args.status or order.fulfillmentStatus == args.status
        ]

    # Sort by creation date descending (newest first)
    orders.sort(key=lambda order: order.createdAt, reverse=True)

    total_count = len(orders)

    # Pagination (cursor = index, same pattern as search_products)
    start_idx = 0
    if args.after:
        with contextlib.suppress(ValueError):
            start_idx = int(args.after) + 1

    end_idx = start_idx + args.limit
    paginated = orders[start_idx:end_idx]

    has_next = end_idx < total_count
    end_cursor = str(end_idx - 1) if paginated else None

    return {
        "orders": paginated,
        "pageInfo": {
            "hasNextPage": has_next,
            "hasPreviousPage": start_idx > 0,
            "startCursor": str(start_idx) if paginated else None,
            "endCursor": end_cursor,
        },
        "totalCount": total_count,
    }


_SIDE_EFFECT_FINANCIAL_STATUSES = {"VOIDED", "REFUNDED", "PARTIALLY_REFUNDED"}


def _generate_tracking() -> tuple[str, str]:
    """Generate a random mock tracking number and a URL that references it."""
    # 12 hex chars keeps it short and distinctive while staying unique in practice
    number = f"TRK-{secrets.token_hex(6).upper()}"
    url = f"https://track.example.com/{number}"
    return number, url


def handle_update_order(args: UpdateOrderArgs) -> dict:
    """Update fields on an existing order."""
    order = get_order_by_id(args.order_id)
    if order is None:
        return {
            "order": None,
            "userErrors": [{"field": "order_id", "message": f"Order not found: {args.order_id}"}],
        }

    if (
        args.financial_status is not None
        and args.financial_status != order.financialStatus
        and (
            args.financial_status in _SIDE_EFFECT_FINANCIAL_STATUSES
            or order.financialStatus in _SIDE_EFFECT_FINANCIAL_STATUSES
        )
    ):
        return {
            "order": None,
            "userErrors": [
                {
                    "field": "financial_status",
                    "message": (
                        "Use cancel_order or the return workflow for VOIDED, REFUNDED, "
                        "or PARTIALLY_REFUNDED so inventory, customer, loyalty, discount, "
                        "and gift-card side effects stay consistent"
                    ),
                }
            ],
        }

    if args.email is not None and args.email != order.email:
        return {
            "order": None,
            "userErrors": [
                {
                    "field": "email",
                    "message": "Order customer email cannot be reassigned after checkout",
                }
            ],
        }

    if args.financial_status is not None:
        order.financialStatus = args.financial_status

    if args.fulfillment_status is not None:
        new_status = args.fulfillment_status
        order.fulfillmentStatus = new_status
        # Generate tracking info when an order is first marked (partially) fulfilled
        if new_status in {"FULFILLED", "PARTIALLY_FULFILLED"} and not order.trackingNumber:
            number, url = _generate_tracking()
            order.trackingNumber = number
            order.trackingUrl = url

    if args.note is not None:
        order.note = args.note
    if args.tags is not None:
        order.tags = args.tags
    if args.email is not None:
        order.email = args.email
    if args.phone is not None:
        order.phone = args.phone
    if args.shipping_address is not None:
        order.shippingAddress = args.shipping_address

    order.updatedAt = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    save_state()

    return {"order": order, "userErrors": []}
