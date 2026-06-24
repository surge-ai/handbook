"""Cart tool handlers."""

from datetime import UTC, datetime
from typing import Any

from shopify.models import (
    AppliedGiftCard,
    CartBuyerIdentity,
    CartDeliveryGroup,
    CartDeliveryOption,
    CartDiscountCode,
    GetCartArgs,
    MoneyV2,
    UpdateCartArgs,
)
from shopify.state import (
    add_line_to_cart,
    create_cart,
    get_all_carts,
    get_all_stores,
    get_cart_by_id,
    get_customer_by_email,
    get_discount_by_code,
    get_gift_card_by_code,
    get_state,
    remove_lines_from_cart,
    save_state,
    update_line_in_cart,
)
from shopify.tools.loyalty import compute_tier
from shopify.tools.order_effects import applied_gift_card_code

get_cart_tool = {
    "name": "get_cart",
    "description": "Get the cart including items, shipping options, discount info, and checkout url for a given cart id",
    "inputSchema": {
        "type": "object",
        "properties": {
            "cart_id": {
                "type": "string",
                "description": "Shopify cart id, formatted like: gid://shopify/Cart/c1-66330c6d752c2b242bb8487474949791?key=fa8913e951098d30d68033cf6b7b50f3",
            }
        },
        "required": ["cart_id"],
    },
}


def handle_get_cart(args: GetCartArgs) -> Any:
    """
    Handle get_cart tool call.

    Retrieves a cart by ID from local state.
    """
    cart = get_cart_by_id(args.cart_id)

    if cart is None:
        return {"cart": None, "userErrors": [{"field": ["cart_id"], "message": f"Cart {args.cart_id} not found"}]}

    return cart


def handle_list_stores() -> dict:
    """Return summary information for every loaded store."""
    stores = get_all_stores()
    result = [
        {
            "store_id": store_id,
            "product_count": len(state.products),
            "order_count": len(state.orders),
            "customer_count": len(state.customers),
        }
        for store_id, state in stores.items()
    ]
    return {"stores": result, "total": len(result)}


def handle_list_carts() -> dict:
    """Return summary information for all carts in the active store."""
    cart_summaries = [
        {
            "id": cart.id,
            "totalQuantity": cart.totalQuantity,
            "itemCount": len(cart.lines),
            "totalAmount": cart.cost.totalAmount.model_dump(mode="json") if cart.cost else {},
            "createdAt": cart.createdAt,
            "updatedAt": cart.updatedAt,
            "note": cart.note,
        }
        for cart in get_all_carts()
    ]
    return {"carts": cart_summaries, "totalCount": len(cart_summaries)}


update_cart_tool = {
    "name": "update_cart",
    "description": "Perform updates to a cart, including adding/removing/updating line items, buyer information, shipping details, discount codes, gift cards and notes in one consolidated call. Shipping options become available after adding items and delivery address. When creating a new cart, only addItems is required.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "cart_id": {
                "type": "string",
                "description": "Identifier for the cart being updated. If not provided, a new cart will be created.",
            },
            "add_items": {
                "type": "array",
                "description": "Items to add to the cart. Required when creating a new cart.",
                "items": {
                    "type": "object",
                    "required": ["product_variant_id", "quantity"],
                    "properties": {
                        "product_variant_id": {"type": "string"},
                        "quantity": {"type": "integer", "minimum": 1},
                    },
                },
            },
            "update_items": {
                "type": "array",
                "description": "Existing cart line items to update quantities for. Use quantity 0 to remove an item.",
                "items": {
                    "type": "object",
                    "required": ["id", "quantity"],
                    "properties": {"id": {"type": "string"}, "quantity": {"type": "integer", "minimum": 0}},
                },
            },
            "remove_line_ids": {
                "type": "array",
                "description": "List of line item IDs to remove explicitly.",
                "items": {"type": "string"},
            },
            "buyer_identity": {
                "type": "object",
                "description": "Information about the buyer including email, phone, and delivery address.",
                "properties": {
                    "email": {"type": "string", "format": "email"},
                    "phone": {"type": "string"},
                    "country_code": {"type": "string", "description": "ISO country code, used for regional pricing."},
                },
            },
            "delivery_addresses_to_add": {
                "type": "array",
                "description": "Information about the delivery addresses to add.",
                "items": {
                    "type": "object",
                    "properties": {
                        "selected": {"type": "boolean", "description": "Should this address be selected for delivery."},
                        "delivery_address": {
                            "type": "object",
                            "properties": {
                                "first_name": {"type": "string"},
                                "last_name": {"type": "string"},
                                "phone": {"type": "string"},
                                "address1": {"type": "string"},
                                "address2": {"type": "string"},
                                "city": {"type": "string"},
                                "province_code": {"type": "string"},
                                "zip": {"type": "string"},
                                "country_code": {"type": "string"},
                            },
                        },
                    },
                },
            },
            "delivery_addresses_to_replace": {
                "type": "array",
                "description": "Delivery addresses to apply to the cart, replaces all existing cart delivery addresses.",
                "items": {
                    "type": "object",
                    "properties": {
                        "selected": {"type": "boolean"},
                        "delivery_address": {
                            "type": "object",
                            "properties": {
                                "first_name": {"type": "string"},
                                "last_name": {"type": "string"},
                                "phone": {"type": "string"},
                                "address1": {"type": "string"},
                                "address2": {"type": "string"},
                                "city": {"type": "string"},
                                "province_code": {"type": "string"},
                                "zip": {"type": "string"},
                                "country_code": {"type": "string"},
                            },
                        },
                    },
                },
            },
            "selected_delivery_options": {
                "type": "array",
                "description": "The delivery options to select for the cart.",
                "items": {
                    "type": "object",
                    "required": ["group_id", "option_handle"],
                    "properties": {
                        "group_id": {"type": "string", "description": "The ID of the delivery group."},
                        "option_handle": {"type": "string", "description": "The handle of the delivery option."},
                    },
                },
            },
            "discount_codes": {
                "type": "array",
                "description": "Discount or promo codes to apply to the cart.",
                "items": {"type": "string"},
            },
            "gift_card_codes": {
                "type": "array",
                "description": "Gift card codes to apply to the cart.",
                "items": {"type": "string"},
            },
            "note": {"type": "string", "description": "A note or special instructions for the cart."},
        },
    },
}


def handle_update_cart(args: UpdateCartArgs) -> Any:
    """
    Handle update_cart tool call.

    Creates or updates a cart with the specified changes.
    """
    user_errors = []

    # Get or create cart
    if args.cart_id:
        cart = get_cart_by_id(args.cart_id)
        if cart is None:
            return {"cart": None, "userErrors": [{"field": ["cart_id"], "message": f"Cart {args.cart_id} not found"}]}
    else:
        # Create new cart
        buyer_identity_dict = None
        if args.buyer_identity:
            buyer_identity_dict = {
                "email": args.buyer_identity.email,
                "phone": args.buyer_identity.phone,
                "countryCode": args.buyer_identity.countryCode,
            }
        cart = create_cart(buyer_identity=buyer_identity_dict, note=args.note)

    # Add items
    if args.add_items:
        for item in args.add_items:
            merchandise_id = item.merchandiseId
            quantity = item.quantity
            line = add_line_to_cart(cart, merchandise_id, quantity)
            if line is None:
                user_errors.append(
                    {"field": ["add_items", "merchandiseId"], "message": f"Variant {merchandise_id} not found"}
                )
        _sync_delivery_groups(cart)

    # Update items
    if args.update_items:
        for item in args.update_items:
            line_id = item.id
            quantity = item.quantity
            found = update_line_in_cart(cart, line_id, quantity)
            if not found:
                user_errors.append({"field": ["update_items", "id"], "message": f"Line {line_id} not found in cart"})
        _sync_delivery_groups(cart)

    # Remove lines
    if args.remove_line_ids:
        remove_lines_from_cart(cart, args.remove_line_ids)
        _sync_delivery_groups(cart)

    # Update buyer identity
    if args.buyer_identity and args.cart_id:  # Only update if not newly created
        cart.buyerIdentity = CartBuyerIdentity(
            email=args.buyer_identity.email,
            phone=args.buyer_identity.phone,
            countryCode=args.buyer_identity.countryCode,
            deliveryAddressPreferences=list(args.buyer_identity.deliveryAddressPreferences or []),
        )
        cart.updatedAt = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    if args.delivery_addresses_to_replace is not None:
        cart.buyerIdentity.deliveryAddressPreferences = [
            address.address.model_dump(mode="json", exclude_none=True) for address in args.delivery_addresses_to_replace
        ]
        _sync_delivery_groups(cart)
        cart.updatedAt = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    if args.delivery_addresses_to_add:
        existing = list(cart.buyerIdentity.deliveryAddressPreferences)
        existing.extend(
            address.address.model_dump(mode="json", exclude_none=True) for address in args.delivery_addresses_to_add
        )
        cart.buyerIdentity.deliveryAddressPreferences = existing
        _sync_delivery_groups(cart)
        cart.updatedAt = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    if args.selected_delivery_options:
        groups_by_id = {group.id: group for group in cart.deliveryGroups}
        for selected in args.selected_delivery_options:
            group = groups_by_id.get(selected.deliveryGroupId)
            if group is None:
                user_errors.append(
                    {
                        "field": ["selected_delivery_options", "deliveryGroupId"],
                        "message": f"Delivery group {selected.deliveryGroupId} not found",
                    }
                )
                continue
            option = next(
                (option for option in group.deliveryOptions if option.handle == selected.deliveryOptionHandle),
                None,
            )
            if option is None:
                user_errors.append(
                    {
                        "field": ["selected_delivery_options", "deliveryOptionHandle"],
                        "message": (
                            f"Delivery option {selected.deliveryOptionHandle} not found "
                            f"for group {selected.deliveryGroupId}"
                        ),
                    }
                )
                continue
            group.selectedDeliveryOption = option
            cart.updatedAt = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    # Update discount codes
    if args.discount_codes is not None:
        discount_codes = []
        applied_discounts = []
        for code in args.discount_codes:
            applicable, message = _discount_code_applicability(code, cart)
            discount = get_discount_by_code(code)
            if applicable and discount is not None:
                incompatible = next(
                    (existing for existing in applied_discounts if not _discounts_can_combine(existing, discount)),
                    None,
                )
                if incompatible is not None:
                    applicable = False
                    message = f"Discount code '{code}' cannot be combined with '{incompatible.code}'"
                else:
                    applied_discounts.append(discount)
            discount_codes.append(CartDiscountCode(code=code, applicable=applicable))
            if message is not None:
                user_errors.append({"field": ["discount_codes"], "message": message})
        cart.discountCodes = discount_codes
        cart.updatedAt = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    if args.gift_card_codes is not None:
        cart.appliedGiftCards = _apply_gift_cards_to_cart(cart, args.gift_card_codes, user_errors)
        cart.updatedAt = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    elif cart.appliedGiftCards:
        cart.appliedGiftCards = _apply_gift_cards_to_cart(
            cart, [applied_gift_card_code(card) for card in cart.appliedGiftCards], user_errors
        )
        cart.updatedAt = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    # Update note
    if args.note is not None and args.cart_id:  # Only update if not newly created
        cart.note = args.note
        cart.updatedAt = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    save_state()

    # Return cart with any errors
    if user_errors:
        return {"cart": cart, "userErrors": user_errors}

    return cart


def _cart_subtotal(cart: Any) -> float:
    if cart.cost is not None:
        return float(cart.cost.subtotalAmount.amount)
    return sum(float(line.cost.totalAmount.amount) for line in cart.lines)


def _cart_product_ids(cart: Any) -> set[str]:
    product_ids = set()
    for line in cart.lines:
        product = line.merchandise.product or {}
        product_id = product.get("id")
        if product_id:
            product_ids.add(product_id)
    return product_ids


def _discount_code_applicability(code: str, cart: Any) -> tuple[bool, str | None]:
    discount = get_discount_by_code(code)
    if discount is None:
        return False, f"Discount code '{code}' not found"
    if not discount.active:
        return False, f"Discount code '{code}' is not active"
    if discount.usageLimit and discount.usageCount >= discount.usageLimit:
        return False, f"Discount code '{code}' has reached its usage limit"

    min_purchase = discount.minimumPurchase
    if min_purchase and _cart_subtotal(cart) < float(min_purchase.amount):
        return False, f"Minimum purchase of ${min_purchase.amount} required for discount code '{code}'"

    product_ids = discount.productIds
    if product_ids and not _cart_product_ids(cart).intersection(product_ids):
        return False, f"Discount code '{code}' does not apply to any products in this cart"

    required_tier_name = discount.minimumTier
    if required_tier_name:
        state = get_state()
        required_tier = next((tier for tier in state.loyalty_program.tiers if tier.name == required_tier_name), None)
        if required_tier is None:
            return False, f"Discount code '{code}' requires tier '{required_tier_name}' which is not configured"

        email = cart.buyerIdentity.email
        customer = get_customer_by_email(email) if email else None
        customer_tier = compute_tier(customer.lifetimePoints, state.loyalty_program.tiers) if customer else None
        customer_threshold = customer_tier.min_lifetime_points if customer_tier else -1
        if customer_threshold < required_tier.min_lifetime_points:
            return False, f"Discount code '{code}' requires '{required_tier_name}' tier or higher"

    return True, None


def _discount_combination_class(discount: Any) -> str:
    if discount.discountType == "FREE_SHIPPING":
        return "shippingDiscounts"
    if discount.productIds:
        return "productDiscounts"
    return "orderDiscounts"


def _discounts_can_combine(first: Any, second: Any) -> bool:
    first_allows_second = getattr(first.combinesWith, _discount_combination_class(second))
    second_allows_first = getattr(second.combinesWith, _discount_combination_class(first))
    return first_allows_second and second_allows_first


def _sync_delivery_groups(cart: Any) -> None:
    """Create one delivery group from the store's active shipping methods."""
    if not cart.lines or not cart.buyerIdentity.deliveryAddressPreferences:
        cart.deliveryGroups = []
        return

    selected_option = None
    if cart.deliveryGroups:
        selected_option = cart.deliveryGroups[0].selectedDeliveryOption

    options = [
        CartDeliveryOption(
            handle=method.id,
            title=method.title,
            description=method.estimatedDays,
            estimatedCost=method.price,
            code=method.id,
        )
        for method in get_state().shipping_methods.values()
        if method.active
    ]
    if selected_option is not None and selected_option.handle not in {option.handle for option in options}:
        selected_option = None

    cart.deliveryGroups = [
        CartDeliveryGroup(
            id=f"{cart.id}/delivery-group/1",
            deliveryOptions=options,
            selectedDeliveryOption=selected_option,
            cartLines=list(cart.lines),
        )
    ]


def _apply_gift_cards_to_cart(cart: Any, codes: list[str], user_errors: list[dict]) -> list[AppliedGiftCard]:
    remaining_total = _cart_subtotal(cart)
    applied_cards = []
    currency = cart.cost.subtotalAmount.currencyCode if cart.cost is not None else "USD"
    for code in codes:
        gift_card = get_gift_card_by_code(code)
        if gift_card is None:
            user_errors.append({"field": ["gift_card_codes"], "message": f"Gift card '{code}' not found"})
            continue
        if not gift_card.active:
            user_errors.append({"field": ["gift_card_codes"], "message": f"Gift card '{code}' is not active"})
            continue
        balance = float(gift_card.balance.amount)
        if balance <= 0:
            user_errors.append(
                {"field": ["gift_card_codes"], "message": f"Gift card '{code}' has no remaining balance"}
            )
            continue
        amount_used = min(balance, remaining_total)
        remaining_total -= amount_used
        applied_cards.append(
            _gift_card_from_code(code, amount_used, balance - amount_used, gift_card.balance.currencyCode)
        )
        if remaining_total <= 0:
            remaining_total = 0
    if cart.cost is not None:
        cart.cost.totalAmount = MoneyV2(amount=f"{remaining_total:.2f}", currencyCode=currency)
        cart.cost.checkoutChargeAmount = MoneyV2(amount=f"{remaining_total:.2f}", currencyCode=currency)
    return applied_cards


def _gift_card_from_code(code: str, amount_used: float, balance: float, currency: str) -> AppliedGiftCard:
    last_characters = code[-4:] if len(code) >= 4 else code
    return AppliedGiftCard(
        id=f"gid://shopify/AppliedGiftCard/{code}",
        code=code,
        lastCharacters=last_characters,
        amountUsed=MoneyV2(amount=f"{amount_used:.2f}", currencyCode=currency),
        balance=MoneyV2(amount=f"{balance:.2f}", currencyCode=currency),
        presentmentAmountUsed=MoneyV2(amount=f"{amount_used:.2f}", currencyCode=currency),
    )
