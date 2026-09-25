"""
Shopify MCP Server - Mock implementation for testing.
Supports stdio and HTTP transports.
"""

import functools
import inspect
from typing import Annotated

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .models import (
    AddToCollectionArgs,
    Attribute,
    AwardPointsArgs,
    CancelOrderArgs,
    CartBuyerIdentityInput,
    CartLineInput,
    CartLineUpdateInput,
    CartSelectableAddressInput,
    CartSelectedDeliveryOptionInput,
    ConfigureLoyaltyProgramArgs,
    CreateCollectionArgs,
    CreateCustomerArgs,
    CreateDiscountCodeArgs,
    CreateOrderArgs,
    CreatePolicyArgs,
    CreateProductArgs,
    CreateReturnArgs,
    CreateReviewArgs,
    CreateShippingMethodArgs,
    DeleteDiscountCodeArgs,
    DeletePolicyArgs,
    DeleteProductArgs,
    DeleteReviewArgs,
    DeleteShippingMethodArgs,
    GetCartArgs,
    GetCollectionArgs,
    GetCustomerArgs,
    GetDiscountCodeArgs,
    GetInventoryArgs,
    GetLoyaltyBalanceArgs,
    GetLoyaltyProgramArgs,
    GetLoyaltyTierArgs,
    GetOrderArgs,
    GetProductDetailsArgs,
    GetProductReviewsArgs,
    GetReturnArgs,
    ListCollectionsArgs,
    ListCustomersArgs,
    ListDiscountCodesArgs,
    ListLoyaltyTiersArgs,
    ListOrdersArgs,
    ListPoliciesArgs,
    ListReturnsArgs,
    ListShippingMethodsArgs,
    LoyaltyTier,
    MailingAddress,
    RedeemPointsArgs,
    RemoveFromCollectionArgs,
    SearchCustomersArgs,
    SearchFilter,
    SearchShopCatalogArgs,
    SearchShopPoliciesAndFaqsArgs,
    UpdateCartArgs,
    UpdateCustomerArgs,
    UpdateDiscountCodeArgs,
    UpdateInventoryArgs,
    UpdateOrderArgs,
    UpdatePolicyArgs,
    UpdateProductArgs,
    UpdateReturnArgs,
    UpdateReviewArgs,
    UpdateShippingMethodArgs,
)
from .state import (
    ShopifyStateModel,
    restore_state_snapshot,
    set_active_store,
    state_from_json,
    state_snapshot_for_rollback,
    state_to_json,
    write_snapshots,
)
from .tools import (
    cart,
    catalog,
    customers,
    discounts,
    inventory_collections,
    loyalty,
    orders,
    policies,
    reviews_returns,
    shipping,
)
from .tools import self as self_tools

ReviewRatingArg = Annotated[int, Field(ge=1, le=5)]
OptionalReviewRatingArg = Annotated[int | None, Field(ge=1, le=5)]


def _is_hard_error_result(result) -> bool:
    """Return True for error-shaped tool results that should not commit mutations."""
    if not isinstance(result, dict) or not result.get("userErrors"):
        return False
    return any(key != "userErrors" and value is None for key, value in result.items())


def _snapshot_on_write(fn):
    """Decorator: dual-write the post-tool snapshot.

    Writes ``<BUNDLE_OUTPUT_DIR>/state.json`` (per-service bundle subdir,
    nested ``services/<name>/state.json`` layout) and the legacy
    ``final.json`` so consumers on either convention keep working.
    """

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        rollback_snapshot = state_snapshot_for_rollback()
        try:
            result = fn(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            restore_state_snapshot(rollback_snapshot)
            raise
        if _is_hard_error_result(result):
            restore_state_snapshot(rollback_snapshot)
            return result
        write_snapshots()
        return result

    return wrapper


def _with_store(fn):
    """Decorator: extract store_id from kwargs and set active store before calling handler."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        store_id = kwargs.pop("store_id", "default")
        set_active_store(store_id)
        result = fn(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    return wrapper


# Initialize FastMCP server
mcp = FastMCP("shopify")


# ============================================
# TOOL REGISTRATIONS
# ============================================


@mcp.tool()
async def list_stores() -> dict:
    """List all available stores.

    Returns:
        Store IDs with product counts and basic info.
    """
    return cart.handle_list_stores()


@mcp.tool()
@_with_store
def search_shop_catalog(
    query: str,
    context: str,
    filters: list[dict] | None = None,
    country: str | None = None,
    language: str | None = None,
    limit: Annotated[int, Field(ge=0)] = 10,
    after: str | None = None,
    store_id: str = "default",
) -> dict:
    """
    Search for products from the online store, hosted on Shopify.

    This tool can be used to search for products using natural language queries,
    specific filter criteria, or both.

    Best practices:
    - Searches return available_filters which can be used for refined follow-up searches
    - When filtering, use ONLY the filters from available_filters in follow-up searches
    - For specific filter searches, use simple terms without the filter name
    - Results are paginated, with initial results limited to improve experience
    - Use the after parameter with endCursor to fetch additional pages

    Args:
        query: A natural language query
        context: Additional information about the request such as user demographics, mood, location
        filters: Filters to apply to the search from available_filters in previous response
        country: ISO 3166-1 alpha-2 country code for localized results (e.g., 'US', 'CA')
        language: ISO 639-1 language code for localized results (e.g., 'EN', 'FR')
        limit: Maximum number of products to return (default 10, max 250)
        after: Pagination cursor to fetch the next page of results

    Returns:
        Search results including products, available filters, and pagination info
    """
    # Parse filters if provided
    parsed_filters = None
    if filters:
        parsed_filters = [SearchFilter(**f) for f in filters]

    args = SearchShopCatalogArgs(
        query=query,
        context=context,
        filters=parsed_filters,
        country=country,
        language=language,
        limit=limit,
        after=after,
    )
    return catalog.handle_search_shop_catalog(args)


@mcp.tool()
@_with_store
def get_cart(cart_id: str, store_id: str = "default") -> dict:
    """
    Get the cart including items, shipping options, discount info, and checkout url.

    Args:
        cart_id: Shopify cart id, formatted like: gid://shopify/Cart/c1-66330c6d752c2b242bb8487474949791?key=fa8913e951098d30d68033cf6b7b50f3

    Returns:
        Cart details including items, costs, delivery options, and checkout URL
    """
    args = GetCartArgs(cart_id=cart_id)
    return cart.handle_get_cart(args)


@mcp.tool()
@_with_store
def list_carts(store_id: str = "default") -> dict:
    """
    List all shopping carts.

    Use this tool to discover existing cart IDs before using get_cart or update_cart.
    Returns a list of all carts with their IDs, item counts, and totals.

    Args:
        store_id: Which store to query in multi-store worlds. Defaults to "default".

    Returns:
        List of carts with summary information including cart IDs
    """
    return cart.handle_list_carts()


@mcp.tool()
@_with_store
@_snapshot_on_write
def update_cart(
    cart_id: str | None = None,
    add_items: list[dict] | None = None,
    update_items: list[dict] | None = None,
    remove_line_ids: list[str] | None = None,
    buyer_identity: dict | None = None,
    delivery_addresses_to_add: list[dict] | None = None,
    delivery_addresses_to_replace: list[dict] | None = None,
    selected_delivery_options: list[dict] | None = None,
    discount_codes: list[str] | None = None,
    gift_card_codes: list[str] | None = None,
    note: str | None = None,
    store_id: str = "default",
) -> dict:
    """
    Perform updates to a cart, including adding/removing/updating line items,
    buyer information, shipping details, discount codes, gift cards and notes.

    Shipping options become available after adding items and delivery address.
    When creating a new cart, only add_items is required.

    Args:
        cart_id: Identifier for the cart. If not provided, a new cart will be created
        add_items: Items to add to the cart (list of {merchandiseId, quantity})
        update_items: Items to update (list of {id, quantity}). Use quantity 0 to remove
        remove_line_ids: List of line item IDs to remove explicitly
        buyer_identity: Buyer info including email, phone, countryCode
        delivery_addresses_to_add: Delivery addresses to add
        delivery_addresses_to_replace: Delivery addresses to replace all existing
        selected_delivery_options: Delivery options to select
        discount_codes: Discount or promo codes to apply
        gift_card_codes: Gift card codes to apply
        note: A note or special instructions for the cart

    Returns:
        Updated cart details
    """
    # Parse add_items
    parsed_add_items = None
    if add_items:
        parsed_add_items = [
            CartLineInput(
                merchandiseId=item.get("merchandiseId") or item.get("product_variant_id", ""),
                quantity=item.get("quantity", 1),
                attributes=[Attribute(**a) for a in item.get("attributes", [])],
            )
            for item in add_items
        ]

    # Parse update_items
    parsed_update_items = None
    if update_items:
        parsed_update_items = [
            CartLineUpdateInput(
                id=item["id"],
                quantity=item["quantity"],
                merchandiseId=item.get("merchandiseId"),
                attributes=[Attribute(**a) for a in item.get("attributes", [])] if item.get("attributes") else None,
            )
            for item in update_items
        ]

    # Parse buyer_identity
    parsed_buyer_identity = None
    if buyer_identity:
        parsed_buyer_identity = CartBuyerIdentityInput(
            email=buyer_identity.get("email"),
            phone=buyer_identity.get("phone"),
            countryCode=buyer_identity.get("country_code") or buyer_identity.get("countryCode"),
        )

    # Parse delivery addresses to add
    parsed_delivery_addresses_to_add = None
    if delivery_addresses_to_add:
        parsed_delivery_addresses_to_add = []
        for addr in delivery_addresses_to_add:
            da_dict = addr.get("delivery_address") or addr.get("address", {})
            da = CartSelectableAddressInput(
                selected=addr.get("selected"),
                address=MailingAddress(
                    firstName=da_dict.get("first_name") or da_dict.get("firstName"),
                    lastName=da_dict.get("last_name") or da_dict.get("lastName"),
                    phone=da_dict.get("phone"),
                    address1=da_dict.get("address1"),
                    address2=da_dict.get("address2"),
                    city=da_dict.get("city"),
                    provinceCode=da_dict.get("province_code") or da_dict.get("provinceCode"),
                    zip=da_dict.get("zip"),
                    countryCode=da_dict.get("country_code") or da_dict.get("countryCode"),
                ),
            )
            parsed_delivery_addresses_to_add.append(da)

    # Parse delivery addresses to replace
    parsed_delivery_addresses_to_replace = None
    if delivery_addresses_to_replace:
        parsed_delivery_addresses_to_replace = []
        for addr in delivery_addresses_to_replace:
            da_dict = addr.get("delivery_address") or addr.get("address", {})
            da = CartSelectableAddressInput(
                selected=addr.get("selected"),
                address=MailingAddress(
                    firstName=da_dict.get("first_name") or da_dict.get("firstName"),
                    lastName=da_dict.get("last_name") or da_dict.get("lastName"),
                    phone=da_dict.get("phone"),
                    address1=da_dict.get("address1"),
                    address2=da_dict.get("address2"),
                    city=da_dict.get("city"),
                    provinceCode=da_dict.get("province_code") or da_dict.get("provinceCode"),
                    zip=da_dict.get("zip"),
                    countryCode=da_dict.get("country_code") or da_dict.get("countryCode"),
                ),
            )
            parsed_delivery_addresses_to_replace.append(da)

    # Parse selected delivery options
    parsed_selected_delivery_options = None
    if selected_delivery_options:
        parsed_selected_delivery_options = [
            CartSelectedDeliveryOptionInput(
                deliveryGroupId=opt.get("group_id") or opt.get("deliveryGroupId", ""),
                deliveryOptionHandle=opt.get("option_handle") or opt.get("deliveryOptionHandle", ""),
            )
            for opt in selected_delivery_options
        ]

    args = UpdateCartArgs(
        cart_id=cart_id,
        add_items=parsed_add_items,
        update_items=parsed_update_items,
        remove_line_ids=remove_line_ids,
        buyer_identity=parsed_buyer_identity,
        delivery_addresses_to_add=parsed_delivery_addresses_to_add,
        delivery_addresses_to_replace=parsed_delivery_addresses_to_replace,
        selected_delivery_options=parsed_selected_delivery_options,
        discount_codes=discount_codes,
        gift_card_codes=gift_card_codes,
        note=note,
    )
    return cart.handle_update_cart(args)


@mcp.tool()
@_with_store
def search_shop_policies_and_faqs(
    query: str,
    context: str | None = None,
    store_id: str = "default",
) -> dict:
    """
    Get facts about the store's policies, products, or services.

    Examples of questions you can ask:
    - What is your return policy?
    - What is your shipping policy?
    - What is your phone number?
    - What are your hours of operation?

    Args:
        query: A natural language query
        context: Additional information about the request

    Returns:
        Matching policies and FAQ answers
    """
    args = SearchShopPoliciesAndFaqsArgs(query=query, context=context)
    return catalog.handle_search_shop_policies_and_faqs(args)


@mcp.tool()
@_with_store
def get_product_details(
    product_id: str,
    options: dict | None = None,
    country: str | None = None,
    language: str | None = None,
    store_id: str = "default",
) -> dict:
    """
    Look up a product by ID and optionally specify variant options to select a specific variant.

    Args:
        product_id: The product ID, e.g. gid://shopify/Product/123
        options: Optional variant options to select a specific variant, e.g. {"Size": "10", "Color": "Black"}
        country: ISO 3166-1 alpha-2 country code for localized results
        language: ISO 639-1 language code for localized results

    Returns:
        Product details including variants, images, and pricing
    """
    args = GetProductDetailsArgs(
        product_id=product_id,
        options=options,
        country=country,
        language=language,
    )
    return catalog.handle_get_product_details(args)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
async def export_state() -> ShopifyStateModel:
    """Export the full shopify state as JSON.

    Single-store worlds emit the flat shape; multi-store worlds emit a
    ``{"stores": {store_id: ...}}`` wrapper. Round-trips with import_state.
    """
    return ShopifyStateModel.model_validate(state_to_json())


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True))
@_snapshot_on_write
def import_state(state: ShopifyStateModel) -> dict:
    """Replace the shopify state with the provided JSON.

    For synthetic-data injection and test setup. Accepts either the flat
    single-store shape or the multi-store ``{"stores": {...}}`` wrapper.
    Round-trips with export_state.
    """
    state_from_json(state.model_dump(exclude_unset=True))
    return {"ok": True}


# ============================================
# PRODUCT MANAGEMENT TOOLS
# ============================================


@mcp.tool()
@_with_store
@_snapshot_on_write
def create_product(
    title: str,
    description: str = "",
    product_type: str = "",
    vendor: str = "",
    tags: list[str] | None = None,
    variants: list[dict] | None = None,
    store_id: str = "default",
) -> dict:
    """
    Create a new product in the store.

    Args:
        title: Product title
        description: Product description
        product_type: Product type (e.g., 'Electronics', 'Apparel')
        vendor: Brand/vendor name
        tags: Searchable tags (e.g., ['wireless', 'headphones'])
        variants: Product variants, each with title, price, sku, quantityAvailable

    Returns:
        The created product with its variants
    """
    args = CreateProductArgs.model_validate(
        {
            "title": title,
            "description": description,
            "product_type": product_type,
            "vendor": vendor,
            "tags": tags,
            "variants": variants,
        }
    )
    return catalog.handle_create_product(args)


@mcp.tool()
@_with_store
@_snapshot_on_write
def update_product(
    product_id: str,
    title: str | None = None,
    description: str | None = None,
    product_type: str | None = None,
    vendor: str | None = None,
    tags: list[str] | None = None,
    store_id: str = "default",
) -> dict:
    """
    Update an existing product's details.

    Args:
        product_id: The product ID to update
        title: New title
        description: New description
        product_type: New product type
        vendor: New vendor name
        tags: New tags (replaces existing)

    Returns:
        The updated product
    """
    args = UpdateProductArgs(
        product_id=product_id,
        title=title,
        description=description,
        product_type=product_type,
        vendor=vendor,
        tags=tags,
    )
    return catalog.handle_update_product(args)


@mcp.tool()
@_with_store
@_snapshot_on_write
def delete_product(product_id: str, store_id: str = "default") -> dict:
    """
    Delete a product from the store. Also removes it from any collections
    and deletes associated reviews.

    Args:
        product_id: The product ID to delete

    Returns:
        The deleted product ID
    """
    args = DeleteProductArgs(product_id=product_id)
    return catalog.handle_delete_product(args)


# ============================================
# ORDER TOOLS
# ============================================


@mcp.tool()
@_with_store
@_snapshot_on_write
def create_order(
    cart_id: str,
    payment_method: dict,
    shipping_address: dict,
    billing_address: dict,
    shipping_method_id: str | None = None,
    discount_code: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    note: str | None = None,
    tags: list[str] | None = None,
    redeem_points: int | None = None,
    apply_tier_discount: bool = True,
    store_id: str = "default",
) -> dict:
    """
    Place an order from an existing cart. Requires payment, addresses, and shipping method.

    Args:
        cart_id: The cart ID to convert into an order
        payment_method: Payment info. For credit card: {type: "credit_card", card_number: "4111111111111111", cvv: "123", expiry: "12/26"}. For digital wallets: {type: "google_pay"|"apple_pay"|"paypal", email: "user@example.com"}
        shipping_address: Shipping address (required: address1, city, countryCode)
        billing_address: Billing address (same format as shipping)
        shipping_method_id: Shipping method ID (e.g., 'standard', 'express'). If omitted, checkout uses the cart's selected delivery option.
        discount_code: Discount code to apply (optional). PERCENTAGE/FIXED_AMOUNT discounts reduce the item subtotal. FREE_SHIPPING removes shipping cost. Product-scoped discounts only apply to matching items.
        email: Customer email (falls back to cart buyer identity email, then current customer email)
        phone: Customer phone (falls back to cart buyer identity phone)
        note: Order note or special instructions
        tags: Tags for categorizing the order
        redeem_points: Loyalty points to redeem on this order. Requires a matching customer email.
        apply_tier_discount: Whether to auto-apply the customer's loyalty tier discount.

    Returns:
        The created order with line items, totals, shipping, discount, payment info, and status
    """
    args = CreateOrderArgs.model_validate(
        {
            "cart_id": cart_id,
            "payment_method": payment_method,
            "shipping_address": shipping_address,
            "billing_address": billing_address,
            "shipping_method_id": shipping_method_id,
            "discount_code": discount_code,
            "email": email,
            "phone": phone,
            "note": note,
            "tags": tags,
            "redeem_points": redeem_points,
            "apply_tier_discount": apply_tier_discount,
        }
    )
    return orders.handle_create_order(args)


@mcp.tool()
@_with_store
def get_order(order_id: str, store_id: str = "default") -> dict:
    """
    Get an order by its ID.

    Args:
        order_id: The order ID, e.g. gid://shopify/Order/o2001

    Returns:
        Order details including line items, status, and totals
    """
    args = GetOrderArgs(order_id=order_id)
    return orders.handle_get_order(args)


@mcp.tool()
@_with_store
def list_orders(
    status: str | None = None,
    limit: int = 20,
    after: str | None = None,
    store_id: str = "default",
) -> dict:
    """
    List orders with optional filtering by status.

    Args:
        status: Filter by financial status (PENDING, PAID, REFUNDED, PARTIALLY_REFUNDED) or fulfillment status (UNFULFILLED, FULFILLED, PARTIALLY_FULFILLED)
        limit: Maximum number of orders to return (default 20)
        after: Pagination cursor from a previous call

    Returns:
        List of orders with pagination info
    """
    args = ListOrdersArgs.model_validate({"status": status, "limit": limit, "after": after})
    return orders.handle_list_orders(args)


@mcp.tool()
@_with_store
@_snapshot_on_write
def update_order(
    order_id: str,
    financial_status: str | None = None,
    fulfillment_status: str | None = None,
    note: str | None = None,
    tags: list[str] | None = None,
    email: str | None = None,
    phone: str | None = None,
    shipping_address: dict | None = None,
    store_id: str = "default",
) -> dict:
    """
    Update an existing order's fulfillment status, contact info, or metadata.

    Args:
        order_id: The order ID to update
        financial_status: New financial status. Use cancel_order or the return workflow for VOIDED, REFUNDED, or PARTIALLY_REFUNDED.
        fulfillment_status: New fulfillment status (UNFULFILLED, FULFILLED, PARTIALLY_FULFILLED)
        note: Order note
        tags: Order tags
        email: Customer email. Existing order ownership cannot be reassigned.
        phone: Customer phone
        shipping_address: Updated shipping address

    Returns:
        Updated order details
    """
    args = UpdateOrderArgs.model_validate(
        {
            "order_id": order_id,
            "financial_status": financial_status,
            "fulfillment_status": fulfillment_status,
            "note": note,
            "tags": tags,
            "email": email,
            "phone": phone,
            "shipping_address": shipping_address,
        }
    )
    return orders.handle_update_order(args)


@mcp.tool()
@_with_store
@_snapshot_on_write
def cancel_order(
    order_id: str,
    reason: str | None = None,
    store_id: str = "default",
) -> dict:
    """
    Cancel an order. Sets financial status to REFUNDED and records cancellation time.

    Args:
        order_id: The order ID to cancel
        reason: Reason for cancellation (appended to order note)

    Returns:
        Cancelled order details
    """
    args = CancelOrderArgs(order_id=order_id, reason=reason)
    return orders.handle_cancel_order(args)


# ============================================
# CUSTOMER TOOLS
# ============================================


@mcp.tool()
@_with_store
@_snapshot_on_write
def create_customer(
    email: str,
    first_name: str | None = None,
    last_name: str | None = None,
    phone: str | None = None,
    address: dict | None = None,
    tags: list[str] | None = None,
    note: str | None = None,
    accepts_marketing: bool = False,
    store_id: str = "default",
) -> dict:
    """
    Create a new customer account.

    Args:
        email: Customer email address (must be unique)
        first_name: Customer first name
        last_name: Customer last name
        phone: Customer phone number
        address: Default address with fields like firstName, lastName, address1, city, zip, countryCode
        tags: Tags for categorizing the customer (e.g., 'vip', 'wholesale')
        note: Internal note about the customer
        accepts_marketing: Whether customer consents to marketing emails

    Returns:
        The created customer profile
    """
    args = CreateCustomerArgs.model_validate(
        {
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "phone": phone,
            "address": address,
            "tags": tags,
            "note": note,
            "accepts_marketing": accepts_marketing,
        }
    )
    return customers.handle_create_customer(args)


@mcp.tool()
@_with_store
def get_customer(customer_id: str, store_id: str = "default") -> dict:
    """
    Get a customer by their ID.

    Args:
        customer_id: The customer ID, e.g. gid://shopify/Customer/4001

    Returns:
        Customer profile including addresses, order count, and total spent
    """
    args = GetCustomerArgs(customer_id=customer_id)
    return customers.handle_get_customer(args)


@mcp.tool()
@_with_store
def list_customers(
    query: str | None = None,
    tag: str | None = None,
    limit: int = 20,
    after: str | None = None,
    store_id: str = "default",
) -> dict:
    """
    List customers with optional filtering by name/email or tag.

    Args:
        query: Search by name or email (case-insensitive substring match)
        tag: Filter by customer tag
        limit: Maximum number of customers to return (default 20)
        after: Pagination cursor from a previous call

    Returns:
        List of customers with pagination info
    """
    args = ListCustomersArgs(query=query, tag=tag, limit=limit, after=after)
    return customers.handle_list_customers(args)


@mcp.tool()
@_with_store
@_snapshot_on_write
def update_customer(
    customer_id: str,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    address: dict | None = None,
    tags: list[str] | None = None,
    note: str | None = None,
    accepts_marketing: bool | None = None,
    store_id: str = "default",
) -> dict:
    """
    Update an existing customer's profile.

    Args:
        customer_id: The customer ID to update
        first_name: New first name
        last_name: New last name
        email: New email address
        phone: New phone number
        address: New or updated address (sets as default and adds to address list)
        tags: Customer tags (replaces existing)
        note: Internal note
        accepts_marketing: Marketing consent

    Returns:
        Updated customer profile
    """
    args = UpdateCustomerArgs.model_validate(
        {
            "customer_id": customer_id,
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "phone": phone,
            "address": address,
            "tags": tags,
            "note": note,
            "accepts_marketing": accepts_marketing,
        }
    )
    return customers.handle_update_customer(args)


@mcp.tool()
@_with_store
def search_customers(query: str, limit: int = 20, store_id: str = "default") -> dict:
    """
    Search customers by name, email, or phone number.

    Args:
        query: Search string (case-insensitive, matches name, email, or phone)
        limit: Maximum number of results (default 20)

    Returns:
        Matching customers sorted by relevance
    """
    args = SearchCustomersArgs(query=query, limit=limit)
    return customers.handle_search_customers(args)


# ============================================
# INVENTORY TOOLS
# ============================================


@mcp.tool()
@_with_store
def get_inventory(
    product_id: str | None = None,
    low_stock_threshold: int | None = None,
    store_id: str = "default",
) -> dict:
    """
    Get inventory levels for product variants.

    Args:
        product_id: Filter to variants of a specific product (optional)
        low_stock_threshold: Only show variants at or below this quantity (optional)

    Returns:
        List of variants with quantity available, SKU, and stock status
    """
    args = GetInventoryArgs(product_id=product_id, low_stock_threshold=low_stock_threshold)
    return inventory_collections.handle_get_inventory(args)


@mcp.tool()
@_with_store
@_snapshot_on_write
def update_inventory(variant_id: str, quantity: int, store_id: str = "default") -> dict:
    """
    Update the inventory quantity for a product variant.

    Args:
        variant_id: The product variant ID to update
        quantity: New quantity available (0 marks as out of stock)

    Returns:
        Updated inventory item with previous and new quantity
    """
    args = UpdateInventoryArgs(variant_id=variant_id, quantity=quantity)
    return inventory_collections.handle_update_inventory(args)


# ============================================
# COLLECTION TOOLS
# ============================================


@mcp.tool()
@_with_store
@_snapshot_on_write
def create_collection(
    title: str,
    description: str = "",
    product_ids: list[str] | None = None,
    sort_order: str = "MANUAL",
    store_id: str = "default",
) -> dict:
    """
    Create a new product collection (e.g., 'Summer Sale', 'Best Sellers').

    Args:
        title: Collection title (must be unique)
        description: Collection description
        product_ids: Product IDs to include initially (optional)
        sort_order: Sort order (MANUAL, BEST_SELLING, ALPHA_ASC, ALPHA_DESC, PRICE_ASC, PRICE_DESC, CREATED_DESC)

    Returns:
        The created collection
    """
    args = CreateCollectionArgs.model_validate(
        {"title": title, "description": description, "product_ids": product_ids, "sort_order": sort_order}
    )
    return inventory_collections.handle_create_collection(args)


@mcp.tool()
@_with_store
def get_collection(collection_id: str, store_id: str = "default") -> dict:
    """
    Get a collection by ID, including its products.

    Args:
        collection_id: The collection ID

    Returns:
        Collection details with resolved product list
    """
    args = GetCollectionArgs(collection_id=collection_id)
    return inventory_collections.handle_get_collection(args)


@mcp.tool()
@_with_store
def list_collections(limit: int = 20, after: str | None = None, store_id: str = "default") -> dict:
    """
    List all product collections.

    Args:
        limit: Maximum collections to return (default 20)
        after: Pagination cursor from a previous call

    Returns:
        List of collections with product counts
    """
    args = ListCollectionsArgs(limit=limit, after=after)
    return inventory_collections.handle_list_collections(args)


@mcp.tool()
@_with_store
@_snapshot_on_write
def add_to_collection(collection_id: str, product_ids: list[str], store_id: str = "default") -> dict:
    """
    Add products to a collection.

    Args:
        collection_id: The collection ID
        product_ids: Product IDs to add

    Returns:
        Updated collection with lists of added/already-present products
    """
    args = AddToCollectionArgs(collection_id=collection_id, product_ids=product_ids)
    return inventory_collections.handle_add_to_collection(args)


@mcp.tool()
@_with_store
@_snapshot_on_write
def remove_from_collection(collection_id: str, product_ids: list[str], store_id: str = "default") -> dict:
    """
    Remove products from a collection.

    Args:
        collection_id: The collection ID
        product_ids: Product IDs to remove

    Returns:
        Updated collection with lists of removed products
    """
    args = RemoveFromCollectionArgs(collection_id=collection_id, product_ids=product_ids)
    return inventory_collections.handle_remove_from_collection(args)


# ============================================
# REVIEW TOOLS
# ============================================


@mcp.tool()
@_with_store
@_snapshot_on_write
def create_review(
    product_id: str,
    rating: ReviewRatingArg,
    author: str,
    title: str = "",
    body: str = "",
    email: str | None = None,
    store_id: str = "default",
) -> dict:
    """
    Create a product review.

    Args:
        product_id: The product ID to review
        rating: Rating from 1 to 5
        author: Author name
        title: Review title (optional)
        body: Review text (optional)
        email: Author email (optional)

    Returns:
        The created review
    """
    args = CreateReviewArgs(product_id=product_id, rating=rating, author=author, title=title, body=body, email=email)
    return reviews_returns.handle_create_review(args)


@mcp.tool()
@_with_store
def get_product_reviews(
    product_id: str,
    status: str | None = None,
    limit: int = 20,
    after: str | None = None,
    store_id: str = "default",
) -> dict:
    """
    Get reviews for a product. Returns reviews with average rating.

    Args:
        product_id: The product ID
        status: Filter by review status (PENDING, PUBLISHED, HIDDEN)
        limit: Maximum reviews to return (default 20)
        after: Pagination cursor from a previous call

    Returns:
        Reviews with average rating and pagination info
    """
    args = GetProductReviewsArgs.model_validate(
        {"product_id": product_id, "status": status, "limit": limit, "after": after}
    )
    return reviews_returns.handle_get_product_reviews(args)


@mcp.tool()
@_with_store
@_snapshot_on_write
def update_review(
    review_id: str,
    status: str | None = None,
    title: str | None = None,
    body: str | None = None,
    rating: OptionalReviewRatingArg = None,
    store_id: str = "default",
) -> dict:
    """
    Update or moderate a review. Use status to publish, hide, or mark as pending.

    Args:
        review_id: The review ID to update
        status: New status (PENDING, PUBLISHED, HIDDEN)
        title: New review title
        body: New review text
        rating: New rating (1-5)

    Returns:
        Updated review
    """
    args = UpdateReviewArgs.model_validate(
        {"review_id": review_id, "status": status, "title": title, "body": body, "rating": rating}
    )
    return reviews_returns.handle_update_review(args)


@mcp.tool()
@_with_store
@_snapshot_on_write
def delete_review(review_id: str, store_id: str = "default") -> dict:
    """
    Delete a product review.

    Args:
        review_id: The review ID to delete

    Returns:
        The deleted review ID
    """
    args = DeleteReviewArgs(review_id=review_id)
    return reviews_returns.handle_delete_review(args)


# ============================================
# RETURN TOOLS
# ============================================


@mcp.tool()
@_with_store
@_snapshot_on_write
def create_return(
    order_id: str,
    line_items: list[dict],
    reason: str = "",
    note: str | None = None,
    store_id: str = "default",
) -> dict:
    """
    Create a return request for an order. Validates line items against the order.

    Args:
        order_id: The order ID to return against
        line_items: Items to return, each with orderLineItemId, quantity, and optional reason
        reason: Overall reason for the return
        note: Internal note about the return

    Returns:
        The created return with calculated refund amount
    """
    args = CreateReturnArgs.model_validate(
        {"order_id": order_id, "line_items": line_items, "reason": reason, "note": note}
    )
    return reviews_returns.handle_create_return(args)


@mcp.tool()
@_with_store
def get_return(return_id: str, store_id: str = "default") -> dict:
    """
    Get a return by its ID.

    Args:
        return_id: The return ID

    Returns:
        Return details including status, line items, and refund amount
    """
    args = GetReturnArgs(return_id=return_id)
    return reviews_returns.handle_get_return(args)


@mcp.tool()
@_with_store
def list_returns(
    order_id: str | None = None,
    status: str | None = None,
    limit: int = 20,
    after: str | None = None,
    store_id: str = "default",
) -> dict:
    """
    List returns with optional filtering by order or status.

    Args:
        order_id: Filter to returns for a specific order
        status: Filter by return status (REQUESTED, APPROVED, RECEIVED, REFUNDED, REJECTED)
        limit: Maximum returns to show (default 20)
        after: Pagination cursor

    Returns:
        List of returns with pagination info
    """
    args = ListReturnsArgs.model_validate({"order_id": order_id, "status": status, "limit": limit, "after": after})
    return reviews_returns.handle_list_returns(args)


@mcp.tool()
@_with_store
@_snapshot_on_write
def update_return(
    return_id: str,
    status: str | None = None,
    note: str | None = None,
    store_id: str = "default",
) -> dict:
    """
    Update a return's status or note. When status is set to REFUNDED, the
    linked order's financial status is automatically updated to REFUNDED or
    PARTIALLY_REFUNDED based on the refund amount.

    Args:
        return_id: The return ID to update
        status: New status (REQUESTED, APPROVED, RECEIVED, REFUNDED, REJECTED)
        note: Internal note

    Returns:
        Updated return details
    """
    args = UpdateReturnArgs.model_validate({"return_id": return_id, "status": status, "note": note})
    return reviews_returns.handle_update_return(args)


# ============================================
# DISCOUNT CODE TOOLS
# ============================================


@mcp.tool()
@_with_store
@_snapshot_on_write
def create_discount_code(
    code: str,
    value: str,
    discount_type: str = "PERCENTAGE",
    minimum_purchase: float | None = None,
    usage_limit: int | None = None,
    product_ids: list[str] | None = None,
    minimum_tier: str | None = None,
    store_id: str = "default",
) -> dict:
    """
    Create a new discount code for the store.

    Args:
        code: The discount code string (e.g., 'SUMMER20', 'WELCOME10')
        value: Discount value (e.g., '20' for 20% off, '10.00' for $10 off)
        discount_type: Type of discount — PERCENTAGE, FIXED_AMOUNT, or FREE_SHIPPING
        minimum_purchase: Minimum purchase amount to qualify (optional)
        usage_limit: Maximum number of times the code can be used (optional, null = unlimited)
        product_ids: Product IDs this discount applies to (optional, null = all products)
        minimum_tier: Loyalty tier name required to use this code (optional, null = no tier gate)

    Returns:
        The created discount code
    """
    args = CreateDiscountCodeArgs.model_validate(
        {
            "code": code,
            "value": value,
            "discount_type": discount_type,
            "minimum_purchase": minimum_purchase,
            "usage_limit": usage_limit,
            "product_ids": product_ids,
            "minimum_tier": minimum_tier,
        }
    )
    return discounts.handle_create_discount_code(args)


@mcp.tool()
@_with_store
def get_discount_code(code: str, store_id: str = "default") -> dict:
    """
    Look up a discount code by its code string.

    Args:
        code: The discount code to look up (case-insensitive)

    Returns:
        Discount code details including type, value, and usage stats
    """
    args = GetDiscountCodeArgs(code=code)
    return discounts.handle_get_discount_code(args)


@mcp.tool()
@_with_store
def list_discount_codes(active_only: bool = False, store_id: str = "default") -> dict:
    """
    List all discount codes in the store.

    Args:
        active_only: If true, only return currently active codes

    Returns:
        List of discount codes sorted by code
    """
    args = ListDiscountCodesArgs(active_only=active_only)
    return discounts.handle_list_discount_codes(args)


@mcp.tool()
@_with_store
@_snapshot_on_write
def update_discount_code(
    code: str,
    active: bool | None = None,
    value: str | None = None,
    usage_limit: int | None = None,
    minimum_purchase: float | None = None,
    minimum_tier: str | None = None,
    store_id: str = "default",
) -> dict:
    """
    Update a discount code's settings.

    Args:
        code: The discount code to update
        active: Enable or disable the code
        value: New discount value
        usage_limit: New usage limit
        minimum_purchase: New minimum purchase amount
        minimum_tier: New minimum tier name (empty string to clear the restriction)

    Returns:
        The updated discount code
    """
    args = UpdateDiscountCodeArgs(
        code=code,
        active=active,
        value=value,
        usage_limit=usage_limit,
        minimum_purchase=minimum_purchase,
        minimum_tier=minimum_tier,
    )
    return discounts.handle_update_discount_code(args)


@mcp.tool()
@_with_store
@_snapshot_on_write
def delete_discount_code(code: str, store_id: str = "default") -> dict:
    """
    Delete a discount code from the store.

    Args:
        code: The discount code to delete

    Returns:
        The deleted code
    """
    args = DeleteDiscountCodeArgs(code=code)
    return discounts.handle_delete_discount_code(args)


# ============================================
# POLICY TOOLS
# ============================================


@mcp.tool()
@_with_store
@_snapshot_on_write
def create_policy(title: str, body: str, store_id: str = "default") -> dict:
    """
    Create a new store policy.

    Args:
        title: Policy title (e.g., 'Return Policy', 'Shipping Policy')
        body: Policy content (HTML)

    Returns:
        The created policy
    """
    args = CreatePolicyArgs(title=title, body=body)
    return policies.handle_create_policy(args)


@mcp.tool()
@_with_store
def list_policies(store_id: str = "default") -> dict:
    """
    List all store policies.

    Args:
        store_id: Which store to query in multi-store worlds. Defaults to "default".

    Returns:
        All policies with their titles and content
    """
    args = ListPoliciesArgs()
    return policies.handle_list_policies(args)


@mcp.tool()
@_with_store
@_snapshot_on_write
def update_policy(
    policy_id: str,
    title: str | None = None,
    body: str | None = None,
    store_id: str = "default",
) -> dict:
    """
    Update a store policy's title or content.

    Args:
        policy_id: The policy ID to update
        title: New title
        body: New content (HTML)

    Returns:
        The updated policy
    """
    args = UpdatePolicyArgs(policy_id=policy_id, title=title, body=body)
    return policies.handle_update_policy(args)


@mcp.tool()
@_with_store
@_snapshot_on_write
def delete_policy(policy_id: str, store_id: str = "default") -> dict:
    """
    Delete a store policy.

    Args:
        policy_id: The policy ID to delete

    Returns:
        The deleted policy ID
    """
    args = DeletePolicyArgs(policy_id=policy_id)
    return policies.handle_delete_policy(args)


# ============================================
# SHIPPING METHOD TOOLS
# ============================================


@mcp.tool()
@_with_store
@_snapshot_on_write
def create_shipping_method(
    title: str,
    price: str,
    estimated_days: str = "",
    store_id: str = "default",
) -> dict:
    """
    Create a new shipping method for the store.

    Args:
        title: Display name (e.g., 'Standard Shipping', 'Express')
        price: Shipping cost as string (e.g., '5.99', '0.00' for free)
        estimated_days: Estimated delivery time (e.g., '5-7 business days')

    Returns:
        The created shipping method
    """
    args = CreateShippingMethodArgs(title=title, price=price, estimated_days=estimated_days)
    return shipping.handle_create_shipping_method(args)


@mcp.tool()
@_with_store
def list_shipping_methods(active_only: bool = False, store_id: str = "default") -> dict:
    """
    List all available shipping methods, sorted by price.

    Args:
        active_only: If true, only return active methods

    Returns:
        List of shipping methods with prices and estimated delivery times
    """
    args = ListShippingMethodsArgs(active_only=active_only)
    return shipping.handle_list_shipping_methods(args)


@mcp.tool()
@_with_store
@_snapshot_on_write
def update_shipping_method(
    shipping_method_id: str,
    title: str | None = None,
    price: str | None = None,
    estimated_days: str | None = None,
    active: bool | None = None,
    store_id: str = "default",
) -> dict:
    """
    Update a shipping method's details.

    Args:
        shipping_method_id: The shipping method ID to update
        title: New display name
        price: New price
        estimated_days: New estimated delivery time
        active: Enable or disable this method

    Returns:
        The updated shipping method
    """
    args = UpdateShippingMethodArgs(
        shipping_method_id=shipping_method_id,
        title=title,
        price=price,
        estimated_days=estimated_days,
        active=active,
    )
    return shipping.handle_update_shipping_method(args)


@mcp.tool()
@_with_store
@_snapshot_on_write
def delete_shipping_method(shipping_method_id: str, store_id: str = "default") -> dict:
    """
    Delete a shipping method from the store.

    Args:
        shipping_method_id: The shipping method ID to delete

    Returns:
        The deleted method ID
    """
    args = DeleteShippingMethodArgs(shipping_method_id=shipping_method_id)
    return shipping.handle_delete_shipping_method(args)


# ============================================
# LOYALTY PROGRAM TOOLS
# ============================================


@mcp.tool()
@_with_store
@_snapshot_on_write
def configure_loyalty_program(
    enabled: bool | None = None,
    earn_rate: float | None = None,
    redemption_rate: int | None = None,
    max_redemption_percent: float | None = None,
    tiers: list[dict] | None = None,
    store_id: str = "default",
) -> dict:
    """
    Configure the store's loyalty program.

    Args:
        enabled: Whether the program is active.
        earn_rate: Points earned per $1 of order subtotal (e.g., 1 = 1 point per dollar).
        redemption_rate: Points needed to equal $1 when redeeming (e.g., 100 = 100pts per dollar off).
        max_redemption_percent: Cap on what percent of an order's subtotal can be paid with points (0-100).
        tiers: List of {name, min_lifetime_points, discount_percent}. Replaces existing tiers.

    Returns:
        The updated program configuration.
    """
    tier_objs = None
    if tiers is not None:
        tier_objs = [LoyaltyTier(**t) for t in tiers]
    args = ConfigureLoyaltyProgramArgs(
        enabled=enabled,
        earn_rate=earn_rate,
        redemption_rate=redemption_rate,
        max_redemption_percent=max_redemption_percent,
        tiers=tier_objs,
    )
    return loyalty.handle_configure_loyalty_program(args)


@mcp.tool()
@_with_store
def get_loyalty_program(store_id: str = "default") -> dict:
    """Read the current loyalty program configuration."""
    return loyalty.handle_get_loyalty_program(GetLoyaltyProgramArgs())


@mcp.tool()
@_with_store
def list_loyalty_tiers(store_id: str = "default") -> dict:
    """List all loyalty tiers sorted by threshold ascending."""
    return loyalty.handle_list_loyalty_tiers(ListLoyaltyTiersArgs())


@mcp.tool()
@_with_store
def get_loyalty_balance(customer_id: str, store_id: str = "default") -> dict:
    """
    Get a customer's loyalty balance, lifetime points, and current tier.

    Args:
        customer_id: The customer's GID.
    """
    return loyalty.handle_get_loyalty_balance(GetLoyaltyBalanceArgs(customer_id=customer_id))


@mcp.tool()
@_with_store
def get_loyalty_tier(customer_id: str, store_id: str = "default") -> dict:
    """
    Get the full tier object (name, threshold, discount percent) for a customer's current tier.

    Args:
        customer_id: The customer's GID.
    """
    return loyalty.handle_get_loyalty_tier(GetLoyaltyTierArgs(customer_id=customer_id))


@mcp.tool()
@_with_store
@_snapshot_on_write
def award_points(
    customer_id: str,
    points: int,
    reason: str | None = None,
    store_id: str = "default",
) -> dict:
    """
    Manually award loyalty points to a customer. Grows both spendable balance and lifetime points.

    Args:
        customer_id: The customer's GID.
        points: Positive integer of points to award.
        reason: Optional reason for the award (e.g., 'Birthday bonus').
    """
    return loyalty.handle_award_points(AwardPointsArgs(customer_id=customer_id, points=points, reason=reason))


@mcp.tool()
@_with_store
@_snapshot_on_write
def redeem_points(
    customer_id: str,
    points: int,
    store_id: str = "default",
) -> dict:
    """
    Redeem loyalty points for dollar value. Deducts from spendable balance; lifetime points unchanged.
    For use with orders, pass `redeem_points` to `create_order` instead of calling this directly.

    Args:
        customer_id: The customer's GID.
        points: Positive integer of points to redeem.
    """
    return loyalty.handle_redeem_points(RedeemPointsArgs(customer_id=customer_id, points=points))


# ============================================
# CUSTOMER SELF-OPS TOOLS
# ============================================
# Scoped to the current customer (state.current_customer_email). Worlds
# running in customer mode expose these instead of the admin variants so
# the agent cannot read or modify another customer's data.


@mcp.tool()
@_with_store
def get_my_customer(store_id: str = "default") -> dict:
    """Get the current customer's own profile."""
    return self_tools.handle_get_my_customer()


@mcp.tool()
@_with_store
@_snapshot_on_write
def update_my_customer(
    first_name: str | None = None,
    last_name: str | None = None,
    phone: str | None = None,
    address: dict | None = None,
    accepts_marketing: bool | None = None,
    store_id: str = "default",
) -> dict:
    """
    Update the current customer's own profile. Only customer-editable fields
    are accepted — admin fields like tags, note, ordersCount, totalSpent, and
    loyalty balances cannot be changed here.
    """
    return self_tools.handle_update_my_customer(
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        address=address,
        accepts_marketing=accepts_marketing,
    )


@mcp.tool()
@_with_store
def get_my_loyalty_balance(store_id: str = "default") -> dict:
    """Get the current customer's loyalty balance, lifetime points, and tier."""
    return self_tools.handle_get_my_loyalty_balance()


@mcp.tool()
@_with_store
def get_my_loyalty_tier(store_id: str = "default") -> dict:
    """Get the full tier object for the current customer's tier."""
    return self_tools.handle_get_my_loyalty_tier()


@mcp.tool()
@_with_store
@_snapshot_on_write
def redeem_my_points(points: int, store_id: str = "default") -> dict:
    """Redeem loyalty points from the current customer's balance."""
    return self_tools.handle_redeem_my_points(points)


@mcp.tool()
@_with_store
def list_my_orders(limit: int = 20, after: str | None = None, store_id: str = "default") -> dict:
    """List orders belonging to the current customer, newest first."""
    return self_tools.handle_list_my_orders(limit=limit, after=after)


@mcp.tool()
@_with_store
def get_my_order(order_id: str, store_id: str = "default") -> dict:
    """Get a single order, only if it belongs to the current customer."""
    return self_tools.handle_get_my_order(GetOrderArgs(order_id=order_id))


@mcp.tool()
@_with_store
@_snapshot_on_write
def create_my_return(
    order_id: str,
    line_items: list[dict],
    reason: str = "",
    note: str | None = None,
    store_id: str = "default",
) -> dict:
    """Create a return request, only if the order belongs to the current customer."""
    args = CreateReturnArgs.model_validate(
        {"order_id": order_id, "line_items": line_items, "reason": reason, "note": note}
    )
    return self_tools.handle_create_my_return(args)


@mcp.tool()
@_with_store
@_snapshot_on_write
def create_my_review(
    product_id: str,
    rating: ReviewRatingArg,
    title: str = "",
    body: str = "",
    store_id: str = "default",
) -> dict:
    """
    Post a product review as the current customer. Author name and email are
    filled in automatically from the customer's profile.
    """
    return self_tools.handle_create_my_review(product_id=product_id, rating=rating, title=title, body=body)
