"""
Shopify MCP Type Definitions using Pydantic.

These types match the Shopify Storefront API GraphQL schema.
See: https://shopify.dev/docs/api/storefront/latest
"""

from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, EmailStr, Field, model_validator


class BaseModel(PydanticBaseModel):
    """Pydantic model with temporary dict-style access for legacy handlers."""

    def get(self, key: str, default: Any = None) -> Any:
        if key in self.__class__.model_fields:
            return getattr(self, key)
        if self.model_extra and key in self.model_extra:
            return self.model_extra[key]
        return default

    def setdefault(self, key: str, default: Any = None) -> Any:
        value = self.get(key)
        if value is None:
            self[key] = default
            return default
        return value

    def pop(self, key: str, default: Any = None) -> Any:
        value = self.get(key, default)
        if key in self.__class__.model_fields:
            setattr(self, key, None)
        elif self.model_extra and key in self.model_extra:
            del self.model_extra[key]
        return value

    def __getitem__(self, key: str) -> Any:
        if key in self.__class__.model_fields:
            return getattr(self, key)
        if self.model_extra and key in self.model_extra:
            return self.model_extra[key]
        raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        setattr(self, key, value)

    def __delitem__(self, key: str) -> None:
        if key in self.__class__.model_fields:
            setattr(self, key, None)
            return
        if self.model_extra and key in self.model_extra:
            del self.model_extra[key]
            return
        raise KeyError(key)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and (
            key in self.model_fields_set or bool(self.model_extra and key in self.model_extra)
        )

    def keys(self):
        return self.model_dump(exclude_unset=True).keys()

    def items(self):
        return self.model_dump(exclude_unset=True).items()

    def __iter__(self):
        return iter(self.keys())

    def __len__(self) -> int:
        return len(self.keys())


NonEmptyString = Annotated[str, Field(min_length=1)]
MoneyAmount = Annotated[str, Field(pattern=r"^\d+(?:\.\d{1,2})?$", description="Decimal money amount as string")]
CurrencyCode = Annotated[str, Field(pattern=r"^[A-Z]{3}$", description="ISO 4217 currency code")]
ShopifyDateTime = Annotated[
    str,
    Field(
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$",
        description="RFC 3339 timestamp",
    ),
]
WeightUnit = Literal["GRAMS", "KILOGRAMS", "OUNCES", "POUNDS"]
FilterType = Literal["LIST", "PRICE_RANGE"]
FinancialStatus = Literal["PENDING", "PAID", "VOIDED", "REFUNDED", "PARTIALLY_REFUNDED"]
FulfillmentStatus = Literal["UNFULFILLED", "FULFILLED", "PARTIALLY_FULFILLED"]
CustomerState = Literal["ENABLED", "DISABLED", "INVITED", "DECLINED"]
CollectionSortOrder = Literal[
    "MANUAL", "BEST_SELLING", "ALPHA_ASC", "ALPHA_DESC", "PRICE_ASC", "PRICE_DESC", "CREATED_DESC"
]
ReviewStatus = Literal["PENDING", "PUBLISHED", "HIDDEN"]
DiscountType = Literal["PERCENTAGE", "FIXED_AMOUNT", "FREE_SHIPPING"]
ReturnStatus = Literal["REQUESTED", "APPROVED", "RECEIVED", "REFUNDED", "REJECTED"]
PaymentType = Literal["credit_card", "google_pay", "apple_pay", "paypal"]
OrderStatusFilter = FinancialStatus | FulfillmentStatus

# ============================================
# CORE SHOPIFY TYPES
# ============================================


class MoneyV2(BaseModel):
    """A monetary value with currency (Shopify MoneyV2 type)."""

    amount: MoneyAmount
    currencyCode: CurrencyCode


class Image(BaseModel):
    """An image resource (Shopify Image type)."""

    id: str | None = None
    url: NonEmptyString = Field(..., description="The URL of the image")
    altText: str | None = Field(None, description="Alt text for the image")
    width: int | None = Field(None, ge=0, description="Image width in pixels")
    height: int | None = Field(None, ge=0, description="Image height in pixels")


class SelectedOption(BaseModel):
    """A selected product option (Shopify SelectedOption type)."""

    name: NonEmptyString = Field(..., description="The option name (e.g., 'Size')")
    value: NonEmptyString = Field(..., description="The option value (e.g., 'Large')")


class SEO(BaseModel):
    """SEO information (Shopify SEO type)."""

    title: str | None = None
    description: str | None = None


class PageInfo(BaseModel):
    """Pagination information (Shopify PageInfo type)."""

    hasNextPage: bool = False
    hasPreviousPage: bool = False
    startCursor: str | None = None
    endCursor: str | None = None


# ============================================
# FILTER TYPES (for search_shop_catalog)
# ============================================


class CategoryFilter(BaseModel):
    """Category filter for product search."""

    id: str = Field(..., description="Category ID to filter by")


class PriceFilter(BaseModel):
    """Price range filter."""

    min: float | None = Field(None, description="Minimum price, e.g. 50.0")
    max: float | None = Field(None, description="Maximum price, e.g. 100.0")


class MetafieldFilter(BaseModel):
    """Metafield filter for products or variants."""

    key: str = Field(..., description="The key of the metafield to filter by")
    namespace: str = Field(..., description="The namespace of the metafield")
    value: str = Field(..., description="The value of the metafield to filter by")


class VariantOptionFilter(BaseModel):
    """Variant option filter."""

    name: str = Field(..., description="Name of the variant option, e.g. 'Size'")
    value: str = Field(..., description="Value of the variant option, e.g. 'Large'")


class SearchFilter(BaseModel):
    """Filter object for product search (ProductFilter input)."""

    model_config = ConfigDict(extra="allow")

    available: bool | None = Field(None, description="Filter on product availability")
    category: CategoryFilter | None = None
    price: PriceFilter | None = None
    productMetafield: MetafieldFilter | None = None
    productType: str | None = Field(None, description="Product type to filter by")
    productVendor: str | None = Field(None, description="Product vendor to filter by")
    tag: str | None = Field(None, description="Tag to filter by")
    taxonomyMetafield: MetafieldFilter | None = None
    variantMetafield: MetafieldFilter | None = None
    variantOption: VariantOptionFilter | None = None


# ============================================
# PRODUCT TYPES
# ============================================


class ProductPriceRange(BaseModel):
    """The price range of a product (Shopify ProductPriceRange type)."""

    minVariantPrice: MoneyV2
    maxVariantPrice: MoneyV2


class ProductOption(BaseModel):
    """A product option (Shopify ProductOption type)."""

    id: NonEmptyString
    name: NonEmptyString = Field(..., description="The option name (e.g., 'Size', 'Color')")
    values: list[NonEmptyString] = Field(default_factory=list, description="Available values for this option")


class ProductVariant(BaseModel):
    """A product variant (Shopify ProductVariant type)."""

    id: NonEmptyString = Field(..., description="Globally-unique ID")
    title: NonEmptyString = Field(..., description="The variant's title")
    price: MoneyV2 = Field(..., description="The variant's price")
    compareAtPrice: MoneyV2 | None = Field(None, description="Compare-at price for sale pricing")
    availableForSale: bool = Field(True, description="Whether the variant is available for sale")
    sku: str | None = Field(None, description="SKU (stock keeping unit)")
    barcode: str | None = Field(None, description="Barcode (ISBN, UPC, GTIN)")
    selectedOptions: list[SelectedOption] = Field(default_factory=list, description="Selected options for this variant")
    image: Image | None = Field(None, description="Image associated with the variant")
    weight: float | None = Field(None, ge=0, description="Weight of the variant")
    weightUnit: WeightUnit = Field("KILOGRAMS", description="Unit of weight measurement")
    quantityAvailable: int | None = Field(None, ge=0, description="Quantity available for sale")
    currentlyNotInStock: bool = Field(False, description="Whether out of stock but available for backorder")
    requiresShipping: bool = Field(True, description="Whether shipping is required")
    taxable: bool = Field(True, description="Whether tax is charged")


class Product(BaseModel):
    """A Shopify product (Shopify Product type)."""

    id: NonEmptyString = Field(..., description="Globally-unique ID")
    title: NonEmptyString = Field(..., description="Product title")
    description: str = Field("", description="Product description (plain text)")
    descriptionHtml: str = Field("", description="Product description (HTML)")
    handle: NonEmptyString = Field(..., description="Human-readable URL slug")
    productType: str = Field("", description="Product type defined by merchant")
    vendor: str = Field("", description="Product vendor name")
    tags: list[str] = Field(default_factory=list, description="Searchable tags")
    availableForSale: bool = Field(True, description="Whether any variant is available")
    priceRange: ProductPriceRange = Field(..., description="Min and max prices")
    compareAtPriceRange: ProductPriceRange | None = Field(None, description="Compare-at price range")
    featuredImage: Image | None = Field(None, description="Featured product image")
    images: list[Image] = Field(default_factory=list, description="Product images")
    options: list[ProductOption] = Field(default_factory=list, description="Product options")
    variants: list[ProductVariant] = Field(default_factory=list, description="Product variants")
    seo: SEO | None = Field(None, description="SEO title and description")
    onlineStoreUrl: str | None = Field(None, description="URL on the online store")
    createdAt: ShopifyDateTime | None = Field(None, description="Creation timestamp")
    updatedAt: ShopifyDateTime | None = Field(None, description="Last update timestamp")
    publishedAt: ShopifyDateTime | None = Field(None, description="Publication timestamp")
    isGiftCard: bool = Field(False, description="Whether product is a gift card")
    totalInventory: int | None = Field(None, ge=0, description="Total inventory quantity")
    trackingParameters: str | None = Field(None, description="URL tracking parameters")


class FilterValue(BaseModel):
    """A value within an available filter."""

    id: str
    label: str
    count: int = 0
    input: str | None = Field(None, description="JSON input to use this filter")


class Filter(BaseModel):
    """An available filter from search results (Shopify Filter type)."""

    id: str
    label: str
    type: FilterType = Field(..., description="Filter type")
    values: list[FilterValue] = Field(default_factory=list)


class SearchResultItemConnection(BaseModel):
    """Search result container matching Shopify SearchResultItemConnection."""

    nodes: list[Product] = Field(default_factory=list, description="List of products")
    pageInfo: PageInfo = Field(default_factory=PageInfo)
    productFilters: list[Filter] = Field(default_factory=list, description="Available filters")
    totalCount: int = Field(0, description="Total number of results")


# ============================================
# CART TYPES
# ============================================


class CartBuyerIdentity(BaseModel):
    """Information about the buyer (Shopify CartBuyerIdentity type)."""

    email: str | None = None
    phone: str | None = None
    countryCode: str | None = Field(None, pattern=r"^[A-Z]{2}$", description="ISO country code for regional pricing")
    customer: dict[str, Any] | None = Field(None, description="Associated customer")
    deliveryAddressPreferences: list[dict[str, Any]] = Field(default_factory=list)


class Attribute(BaseModel):
    """A key-value attribute (Shopify Attribute type)."""

    key: NonEmptyString
    value: str | None = None


class CartLineMerchandise(BaseModel):
    """Merchandise in a cart line (typically ProductVariant)."""

    id: NonEmptyString
    title: NonEmptyString
    product: dict[str, Any] | None = Field(None, description="Parent product info")
    image: Image | None = None
    selectedOptions: list[SelectedOption] = Field(default_factory=list)
    price: MoneyV2 = Field(..., description="Variant price")


class CartLineCost(BaseModel):
    """Cost breakdown for a cart line (Shopify CartLineCost type)."""

    amountPerQuantity: MoneyV2
    compareAtAmountPerQuantity: MoneyV2 | None = None
    subtotalAmount: MoneyV2
    totalAmount: MoneyV2


class CartLine(BaseModel):
    """A line item in the cart (Shopify CartLine type)."""

    id: NonEmptyString = Field(..., description="Globally-unique ID")
    quantity: int = Field(..., ge=1, description="Quantity of the item")
    merchandise: CartLineMerchandise = Field(..., description="The merchandise (variant)")
    cost: CartLineCost = Field(..., description="Cost breakdown")
    attributes: list[Attribute] = Field(default_factory=list)
    discountAllocations: list[dict[str, Any]] = Field(default_factory=list)


class CartCost(BaseModel):
    """Cart cost breakdown (Shopify CartCost type)."""

    subtotalAmount: MoneyV2 = Field(..., description="Amount before taxes and discounts")
    subtotalAmountEstimated: bool = Field(False, description="Whether subtotal is estimated")
    totalAmount: MoneyV2 = Field(..., description="Total amount for customer to pay")
    totalAmountEstimated: bool = Field(False, description="Whether total is estimated")
    totalTaxAmount: MoneyV2 | None = Field(None, description="Total tax amount")
    totalTaxAmountEstimated: bool = Field(False)
    checkoutChargeAmount: MoneyV2 = Field(..., description="Amount to pay at checkout")


class CartDiscountCode(BaseModel):
    """A discount code applied to the cart (Shopify CartDiscountCode type)."""

    code: NonEmptyString = Field(..., description="The discount code")
    applicable: bool = Field(True, description="Whether the code is applicable")


class AppliedGiftCard(BaseModel):
    """A gift card applied to the cart (Shopify AppliedGiftCard type)."""

    id: NonEmptyString
    code: NonEmptyString | None = Field(None, description="Gift card code used to look up the backing balance")
    lastCharacters: NonEmptyString = Field(..., description="Last 4 characters of the gift card code")
    amountUsed: MoneyV2 = Field(..., description="Amount used from the gift card")
    balance: MoneyV2 = Field(..., description="Remaining balance")
    presentmentAmountUsed: MoneyV2 = Field(..., description="Amount used in presentment currency")


class GiftCard(BaseModel):
    """A store gift card balance."""

    id: NonEmptyString
    code: NonEmptyString
    balance: MoneyV2
    initialValue: MoneyV2 | None = None
    active: bool = True
    createdAt: ShopifyDateTime | None = None
    updatedAt: ShopifyDateTime | None = None


class CartDeliveryOption(BaseModel):
    """A delivery option (Shopify CartDeliveryOption type)."""

    handle: NonEmptyString = Field(..., description="Unique handle for the option")
    title: str | None = None
    description: str | None = None
    estimatedCost: MoneyV2 = Field(..., description="Estimated delivery cost")
    code: str | None = Field(None, description="Delivery option code")


class CartDeliveryGroup(BaseModel):
    """A delivery group (Shopify CartDeliveryGroup type)."""

    id: NonEmptyString
    deliveryOptions: list[CartDeliveryOption] = Field(default_factory=list)
    selectedDeliveryOption: CartDeliveryOption | None = None
    cartLines: list[CartLine] = Field(default_factory=list, description="Lines in this group")


class MailingAddress(BaseModel):
    """A mailing/delivery address (Shopify MailingAddress type)."""

    firstName: str | None = None
    lastName: str | None = None
    phone: str | None = None
    address1: str | None = None
    address2: str | None = None
    city: str | None = None
    provinceCode: str | None = None
    zip: str | None = None
    countryCode: str | None = Field(None, pattern=r"^[A-Z]{2}$", description="ISO country code")
    company: str | None = None
    country: str | None = None
    province: str | None = None
    formatted: list[str] = Field(default_factory=list, description="Formatted address lines")


class Cart(BaseModel):
    """A shopping cart (Shopify Cart type)."""

    id: NonEmptyString = Field(..., description="Globally-unique ID")
    checkoutUrl: NonEmptyString = Field(..., description="URL for checkout")
    createdAt: ShopifyDateTime = Field(..., description="Creation timestamp")
    updatedAt: ShopifyDateTime = Field(..., description="Last update timestamp")
    lines: list[CartLine] = Field(default_factory=list, description="Cart line items")
    cost: CartCost = Field(..., description="Cost breakdown")
    buyerIdentity: CartBuyerIdentity = Field(default_factory=CartBuyerIdentity)
    attributes: list[Attribute] = Field(default_factory=list)
    discountCodes: list[CartDiscountCode] = Field(default_factory=list)
    discountAllocations: list[dict[str, Any]] = Field(default_factory=list)
    appliedGiftCards: list[AppliedGiftCard] = Field(default_factory=list)
    deliveryGroups: list[CartDeliveryGroup] = Field(default_factory=list)
    note: str | None = None
    totalQuantity: int = Field(0, ge=0, description="Total number of items")


# ============================================
# ORDER TYPES
# ============================================


VALID_FINANCIAL_STATUSES = set(get_args(FinancialStatus))
VALID_FULFILLMENT_STATUSES = set(get_args(FulfillmentStatus))


class OrderLineItem(BaseModel):
    """A line item in an order."""

    id: NonEmptyString = Field(..., description="Globally-unique ID")
    title: NonEmptyString = Field(..., description="Product title")
    variantTitle: str | None = Field(None, description="Variant title")
    quantity: int = Field(..., ge=1, description="Quantity ordered")
    sku: str | None = Field(None, description="SKU")
    variantId: str | None = Field(None, description="Product variant ID")
    productId: str | None = Field(None, description="Product ID")
    price: MoneyV2 = Field(..., description="Unit price")
    totalPrice: MoneyV2 = Field(..., description="Total price (price * quantity)")
    image: Image | None = Field(None, description="Product image")


class Order(BaseModel):
    """A Shopify order."""

    id: NonEmptyString = Field(..., description="Globally-unique ID")
    name: NonEmptyString = Field(..., description="Display name, e.g. '#1001'")
    email: str | None = Field(None, description="Customer email")
    phone: str | None = Field(None, description="Customer phone")
    createdAt: ShopifyDateTime = Field(..., description="Creation timestamp")
    updatedAt: ShopifyDateTime = Field(..., description="Last update timestamp")
    cancelledAt: ShopifyDateTime | None = Field(None, description="Cancellation timestamp")
    financialStatus: FinancialStatus = Field("PENDING", description="Financial status")
    fulfillmentStatus: FulfillmentStatus = Field("UNFULFILLED", description="Fulfillment status")
    trackingNumber: str | None = Field(
        None, description="Tracking number assigned at fulfillment (null until fulfilled)"
    )
    trackingUrl: str | None = Field(None, description="Tracking URL assigned at fulfillment")
    lineItems: list[OrderLineItem] = Field(default_factory=list, description="Order line items")
    subtotalPrice: MoneyV2 = Field(..., description="Subtotal before tax/shipping")
    totalPrice: MoneyV2 = Field(..., description="Total amount")
    totalTax: MoneyV2 | None = Field(None, description="Total tax")
    shippingAddress: MailingAddress | None = Field(None, description="Shipping address")
    billingAddress: MailingAddress | None = Field(None, description="Billing address")
    note: str | None = Field(None, description="Order note")
    tags: list[str] = Field(default_factory=list, description="Order tags")
    cartId: str | None = Field(None, description="Source cart ID")


# ============================================
# CUSTOMER TYPES
# ============================================


class Customer(BaseModel):
    """A Shopify customer."""

    id: NonEmptyString = Field(..., description="Globally-unique ID")
    firstName: str | None = Field(None, description="First name")
    lastName: str | None = Field(None, description="Last name")
    email: NonEmptyString = Field(..., description="Email address")
    phone: str | None = Field(None, description="Phone number")
    createdAt: ShopifyDateTime = Field(..., description="Creation timestamp")
    updatedAt: ShopifyDateTime = Field(..., description="Last update timestamp")
    defaultAddress: MailingAddress | None = Field(None, description="Default address")
    addresses: list[MailingAddress] = Field(default_factory=list, description="All addresses")
    ordersCount: int = Field(0, ge=0, description="Number of orders placed")
    totalSpent: MoneyV2 | None = Field(None, description="Total amount spent")
    tags: list[str] = Field(default_factory=list, description="Customer tags")
    note: str | None = Field(None, description="Internal note about customer")
    acceptsMarketing: bool = Field(False, description="Whether customer accepts marketing")
    state: CustomerState = Field("ENABLED", description="Account state")
    pointsBalance: int = Field(0, ge=0, description="Spendable loyalty points balance")
    lifetimePoints: int = Field(0, ge=0, description="Total loyalty points ever earned (drives tier)")
    tier: str | None = Field(None, description="Current loyalty tier name, or null if program disabled")
    loyaltyJoinedAt: ShopifyDateTime | None = Field(None, description="When customer joined loyalty program")


# ============================================
# COLLECTION TYPES
# ============================================


class Collection(BaseModel):
    """A curated group of products (e.g., 'Summer Sale', 'Best Sellers')."""

    id: NonEmptyString = Field(..., description="Globally-unique ID")
    title: NonEmptyString = Field(..., description="Collection title")
    description: str = Field("", description="Collection description")
    handle: NonEmptyString = Field(..., description="URL-friendly slug")
    productIds: list[NonEmptyString] = Field(default_factory=list, description="Product IDs in this collection")
    createdAt: ShopifyDateTime = Field(..., description="Creation timestamp")
    updatedAt: ShopifyDateTime = Field(..., description="Last update timestamp")
    sortOrder: CollectionSortOrder = Field(
        "MANUAL",
        description="Sort order",
    )
    image: Image | None = Field(None, description="Collection image")


# ============================================
# REVIEW TYPES
# ============================================

VALID_REVIEW_STATUSES = set(get_args(ReviewStatus))


class Review(BaseModel):
    """A product review."""

    id: NonEmptyString = Field(..., description="Globally-unique ID")
    productId: NonEmptyString = Field(..., description="Product this review is for")
    rating: int = Field(..., ge=1, le=5, description="Rating from 1 to 5")
    title: str = Field("", description="Review title")
    body: str = Field("", description="Review body text")
    author: NonEmptyString = Field(..., description="Author name")
    email: str | None = Field(None, description="Author email")
    status: ReviewStatus = Field("PUBLISHED", description="Review status")
    createdAt: ShopifyDateTime = Field(..., description="Creation timestamp")
    updatedAt: ShopifyDateTime = Field(..., description="Last update timestamp")


# ============================================
# SHIPPING METHOD TYPES
# ============================================


class ShippingMethod(BaseModel):
    """A store shipping method/rate."""

    id: NonEmptyString = Field(..., description="Unique ID (e.g., 'standard', 'express')")
    title: NonEmptyString = Field(..., description="Display name (e.g., 'Standard Shipping')")
    price: MoneyV2 = Field(..., description="Shipping cost")
    estimatedDays: str = Field("", description="Estimated delivery time (e.g., '5-7 business days')")
    active: bool = Field(True, description="Whether this method is currently available")


# ============================================
# DISCOUNT CODE TYPES
# ============================================

VALID_DISCOUNT_TYPES = set(get_args(DiscountType))


class DiscountCombinesWith(BaseModel):
    """Discount combinability flags matching Shopify's discount model."""

    orderDiscounts: bool = Field(False, description="Can combine with order-level discounts")
    productDiscounts: bool = Field(False, description="Can combine with product-level discounts")
    shippingDiscounts: bool = Field(False, description="Can combine with shipping discounts")


class DiscountCode(BaseModel):
    """A store discount code."""

    id: NonEmptyString = Field(..., description="Globally-unique ID")
    code: NonEmptyString = Field(..., description="The discount code string (e.g., 'SUMMER20')")
    discountType: DiscountType = Field("PERCENTAGE", description="Discount type")
    value: MoneyAmount = Field(..., description="Discount value (e.g., '20' for 20% or '10.00' for $10 off)")
    minimumPurchase: MoneyV2 | None = Field(None, description="Minimum purchase amount required")
    minimumTier: str | None = Field(
        None,
        description="Minimum loyalty tier required to use this code (null = no tier restriction)",
    )
    usageLimit: int | None = Field(None, ge=0, description="Max total uses (null = unlimited)")
    usageCount: int = Field(0, ge=0, description="Times used so far")
    combinesWith: DiscountCombinesWith = Field(
        default_factory=DiscountCombinesWith,
        description="Controls whether this code can combine with other discount classes",
    )
    active: bool = Field(True, description="Whether the code is currently active")
    createdAt: ShopifyDateTime = Field(..., description="Creation timestamp")
    updatedAt: ShopifyDateTime = Field(..., description="Last update timestamp")


# ============================================
# RETURN TYPES
# ============================================

VALID_RETURN_STATUSES = set(get_args(ReturnStatus))


class ReturnLineItem(BaseModel):
    """A line item in a return request."""

    orderLineItemId: NonEmptyString = Field(..., description="Reference to the order line item being returned")
    quantity: int = Field(..., ge=1, description="Quantity being returned")
    reason: str = Field("", description="Reason for return (e.g., 'defective', 'wrong_item', 'not_as_described')")


class Return(BaseModel):
    """A return/refund request linked to an order."""

    id: NonEmptyString = Field(..., description="Globally-unique ID")
    orderId: NonEmptyString = Field(..., description="Order this return is for")
    status: ReturnStatus = Field("REQUESTED", description="Return status")
    lineItems: list[ReturnLineItem] = Field(default_factory=list, description="Items being returned")
    refundAmount: MoneyV2 | None = Field(None, description="Amount to refund")
    reason: str = Field("", description="Overall return reason")
    note: str | None = Field(None, description="Internal note")
    createdAt: ShopifyDateTime = Field(..., description="Creation timestamp")
    updatedAt: ShopifyDateTime = Field(..., description="Last update timestamp")


# ============================================
# CART INPUT TYPES (for update_cart)
# ============================================


class CartLineInput(BaseModel):
    """Input for adding an item to cart."""

    merchandiseId: NonEmptyString = Field(..., description="Product variant ID")
    quantity: int = Field(1, ge=1, description="Quantity to add")
    attributes: list[Attribute] = Field(default_factory=list)


class CartLineUpdateInput(BaseModel):
    """Input for updating a cart line."""

    id: NonEmptyString = Field(..., description="Cart line ID")
    quantity: int = Field(..., ge=0, description="New quantity (0 removes item)")
    merchandiseId: str | None = None
    attributes: list[Attribute] | None = None


class CartSelectableAddressInput(BaseModel):
    """Delivery address input with selection flag."""

    selected: bool | None = Field(None, description="Should this address be selected")
    address: MailingAddress


class CartSelectedDeliveryOptionInput(BaseModel):
    """Input for selecting a delivery option."""

    deliveryGroupId: NonEmptyString = Field(..., description="The ID of the delivery group")
    deliveryOptionHandle: NonEmptyString = Field(..., description="The handle of the delivery option")


class CartBuyerIdentityInput(BaseModel):
    """Input for buyer identity."""

    email: str | None = None
    phone: str | None = None
    countryCode: str | None = Field(None, pattern=r"^[A-Z]{2}$", description="ISO country code")
    deliveryAddressPreferences: list[dict[str, Any]] | None = None


# ============================================
# POLICY/FAQ TYPES
# ============================================


class ShopPolicy(BaseModel):
    """A shop policy (Shopify ShopPolicy type)."""

    id: str
    title: str = Field(..., description="Policy title")
    body: str = Field(..., description="Policy content (HTML)")
    url: str = Field(..., description="URL to the policy")


class PolicySearchResult(BaseModel):
    """Search result for policies and FAQs."""

    results: list[ShopPolicy] = Field(default_factory=list)
    answer: str | None = Field(None, description="AI-generated answer to the query")


# ============================================
# TOOL INPUT ARGUMENT TYPES
# ============================================


class SearchShopCatalogArgs(BaseModel):
    """Arguments for search_shop_catalog tool."""

    model_config = ConfigDict(extra="allow")

    query: str = Field(..., description="A natural language query")
    context: str = Field(..., description="Additional context about the request")
    filters: list[SearchFilter] | None = Field(None, description="Filters to apply")
    country: str | None = Field(None, description="ISO 3166-1 alpha-2 country code")
    language: str | None = Field(None, description="ISO 639-1 language code")
    limit: int = Field(10, ge=0, description="Maximum products to return (max 250)")
    after: str | None = Field(None, description="Pagination cursor")


class GetCartArgs(BaseModel):
    """Arguments for get_cart tool."""

    cart_id: str = Field(..., description="Shopify cart id")


class UpdateCartArgs(BaseModel):
    """Arguments for update_cart tool."""

    cart_id: str | None = Field(None, description="Cart ID. If not provided, creates new cart")
    add_items: list[CartLineInput] | None = Field(None, description="Items to add")
    update_items: list[CartLineUpdateInput] | None = Field(None, description="Items to update")
    remove_line_ids: list[str] | None = Field(None, description="Line item IDs to remove")
    buyer_identity: CartBuyerIdentityInput | None = None
    delivery_addresses_to_add: list[CartSelectableAddressInput] | None = None
    delivery_addresses_to_replace: list[CartSelectableAddressInput] | None = None
    selected_delivery_options: list[CartSelectedDeliveryOptionInput] | None = None
    discount_codes: list[str] | None = None
    gift_card_codes: list[str] | None = None
    note: str | None = None


class SearchShopPoliciesAndFaqsArgs(BaseModel):
    """Arguments for search_shop_policies_and_faqs tool."""

    query: str = Field(..., description="A natural language query")
    context: str | None = Field(None, description="Additional context")


class GetProductDetailsArgs(BaseModel):
    """Arguments for get_product_details tool."""

    product_id: str = Field(..., description="The product ID, e.g. gid://shopify/Product/123")
    options: dict[str, str] | None = Field(None, description="Variant options to select")
    country: str | None = Field(None, description="ISO 3166-1 alpha-2 country code")
    language: str | None = Field(None, description="ISO 639-1 language code")


class CreateProductVariantInput(BaseModel):
    """Input for creating a product variant."""

    title: NonEmptyString = Field("Default", description="Variant title")
    price: MoneyAmount = Field("0.00", description="Variant price")
    sku: str | None = Field(None, description="SKU")
    quantityAvailable: int = Field(0, ge=0, description="Initial available inventory quantity")
    currencyCode: CurrencyCode = Field("USD", description="Price currency code")


class CreateProductArgs(BaseModel):
    """Arguments for create_product tool."""

    title: str = Field(..., description="Product title")
    description: str = Field("", description="Product description")
    product_type: str = Field("", description="Product type (e.g., 'Electronics')")
    vendor: str = Field("", description="Vendor/brand name")
    tags: list[str] | None = Field(None, description="Searchable tags")
    variants: list[CreateProductVariantInput] | None = Field(
        None, description="Variants with title, price, sku, quantityAvailable"
    )


class UpdateProductArgs(BaseModel):
    """Arguments for update_product tool."""

    product_id: str = Field(..., description="Product ID to update")
    title: str | None = Field(None, description="New title")
    description: str | None = Field(None, description="New description")
    product_type: str | None = Field(None, description="New product type")
    vendor: str | None = Field(None, description="New vendor")
    tags: list[str] | None = Field(None, description="New tags")


class DeleteProductArgs(BaseModel):
    """Arguments for delete_product tool."""

    product_id: str = Field(..., description="Product ID to delete")


class CreateDiscountCodeArgs(BaseModel):
    """Arguments for create_discount_code tool."""

    code: str = Field(..., description="The discount code string (e.g., 'SUMMER20')")
    discount_type: DiscountType = Field("PERCENTAGE", description="PERCENTAGE, FIXED_AMOUNT, or FREE_SHIPPING")
    value: MoneyAmount = Field(..., description="Discount value (e.g., '20' for 20% or '10.00' for $10 off)")
    minimum_purchase: float | None = Field(None, ge=0, description="Minimum purchase amount")
    usage_limit: int | None = Field(None, ge=0, description="Max total uses (null = unlimited)")
    product_ids: list[str] | None = Field(
        None, description="Product IDs this discount applies to (null = all products)"
    )
    minimum_tier: str | None = Field(
        None, description="Loyalty tier name required to use this code (null = no tier restriction)"
    )


class GetDiscountCodeArgs(BaseModel):
    """Arguments for get_discount_code tool."""

    code: str = Field(..., description="The discount code string")


class ListDiscountCodesArgs(BaseModel):
    """Arguments for list_discount_codes tool."""

    active_only: bool = Field(False, description="Only return active codes")


class UpdateDiscountCodeArgs(BaseModel):
    """Arguments for update_discount_code tool."""

    code: str = Field(..., description="The discount code to update")
    active: bool | None = Field(None, description="Enable or disable the code")
    value: MoneyAmount | None = Field(None, description="New discount value")
    usage_limit: int | None = Field(None, ge=0, description="New usage limit")
    minimum_purchase: float | None = Field(None, ge=0, description="New minimum purchase")
    product_ids: list[str] | None = Field(
        None, description="Product IDs this discount applies to (null = all, [] = clear restriction)"
    )
    minimum_tier: str | None = Field(
        None, description="New minimum tier name (null = keep existing, '' = clear restriction)"
    )


class DeleteDiscountCodeArgs(BaseModel):
    """Arguments for delete_discount_code tool."""

    code: str = Field(..., description="The discount code to delete")


class CreateShippingMethodArgs(BaseModel):
    """Arguments for create_shipping_method tool."""

    title: str = Field(..., description="Display name (e.g., 'Standard Shipping')")
    price: MoneyAmount = Field(..., description="Shipping cost as string (e.g., '5.99')")
    estimated_days: str = Field("", description="Estimated delivery time (e.g., '5-7 business days')")


class UpdateShippingMethodArgs(BaseModel):
    """Arguments for update_shipping_method tool."""

    shipping_method_id: str = Field(..., description="Shipping method ID to update")
    title: str | None = Field(None, description="New display name")
    price: MoneyAmount | None = Field(None, description="New price")
    estimated_days: str | None = Field(None, description="New estimated days")
    active: bool | None = Field(None, description="Enable or disable")


class DeleteShippingMethodArgs(BaseModel):
    """Arguments for delete_shipping_method tool."""

    shipping_method_id: str = Field(..., description="Shipping method ID to delete")


class ListShippingMethodsArgs(BaseModel):
    """Arguments for list_shipping_methods tool."""

    active_only: bool = Field(False, description="Only return active methods")


class CreatePolicyArgs(BaseModel):
    """Arguments for create_policy tool."""

    title: str = Field(..., description="Policy title (e.g., 'Return Policy')")
    body: str = Field(..., description="Policy content (HTML)")


class UpdatePolicyArgs(BaseModel):
    """Arguments for update_policy tool."""

    policy_id: str = Field(..., description="Policy ID to update")
    title: str | None = Field(None, description="New title")
    body: str | None = Field(None, description="New body content (HTML)")


class DeletePolicyArgs(BaseModel):
    """Arguments for delete_policy tool."""

    policy_id: str = Field(..., description="Policy ID to delete")


class ListPoliciesArgs(BaseModel):
    """Arguments for list_policies tool."""


VALID_PAYMENT_TYPES = set(get_args(PaymentType))


class CreditCardPaymentMethod(BaseModel):
    """Credit card payment input."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["credit_card"]
    card_number: NonEmptyString = Field(..., description="Card number, optionally containing spaces or dashes")
    cvv: NonEmptyString = Field(..., description="Card security code")
    expiry: NonEmptyString = Field(..., description="Expiry in MM/YY format")


class DigitalWalletPaymentMethod(BaseModel):
    """Digital wallet payment input."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["google_pay", "apple_pay", "paypal"]
    email: EmailStr = Field(..., description="Email address associated with the wallet account")


PaymentMethodInput = Annotated[
    CreditCardPaymentMethod | DigitalWalletPaymentMethod,
    Field(discriminator="type"),
]


class CreateOrderArgs(BaseModel):
    """Arguments for create_order tool."""

    cart_id: str = Field(..., description="Cart ID to convert into an order")
    payment_method: PaymentMethodInput = Field(
        ...,
        description="Payment method. For credit_card: {type, card_number, cvv, expiry}. For google_pay/apple_pay/paypal: {type, email}",
    )
    shipping_address: MailingAddress = Field(
        ...,
        description="Shipping address with at least address1, city, countryCode",
    )
    billing_address: MailingAddress = Field(
        ...,
        description="Billing address with at least address1, city, countryCode",
    )
    shipping_method_id: str | None = Field(
        None,
        description=(
            "Shipping method ID (e.g., 'standard', 'express'). If omitted, checkout uses the cart's selected "
            "delivery option."
        ),
    )
    discount_code: str | None = Field(None, description="Discount code to apply (optional)")
    email: str | None = Field(None, description="Customer email (overrides cart buyer identity)")
    phone: str | None = Field(None, description="Customer phone (overrides cart buyer identity)")
    note: str | None = Field(None, description="Order note")
    tags: list[str] | None = Field(None, description="Order tags")
    redeem_points: int | None = Field(
        None, description="Loyalty points to redeem on this order (requires customer lookup via email)"
    )
    apply_tier_discount: bool = Field(
        True, description="Whether to auto-apply the customer's loyalty tier discount (default: true)"
    )


class GetOrderArgs(BaseModel):
    """Arguments for get_order tool."""

    order_id: str = Field(..., description="Order ID")


class ListOrdersArgs(BaseModel):
    """Arguments for list_orders tool."""

    status: OrderStatusFilter | None = Field(None, description="Filter by financial or fulfillment status")
    limit: int = Field(20, ge=0, description="Maximum orders to return")
    after: str | None = Field(None, description="Pagination cursor")


class UpdateOrderArgs(BaseModel):
    """Arguments for update_order tool."""

    order_id: str = Field(..., description="Order ID")
    financial_status: FinancialStatus | None = Field(
        None,
        description=(
            "New financial status. Use cancel_order or the return workflow for VOIDED, REFUNDED, "
            "or PARTIALLY_REFUNDED so side effects stay consistent."
        ),
    )
    fulfillment_status: FulfillmentStatus | None = Field(None, description="New fulfillment status")
    note: str | None = Field(None, description="Order note")
    tags: list[str] | None = Field(None, description="Order tags")
    email: str | None = Field(None, description="Customer email. Existing order ownership cannot be reassigned.")
    phone: str | None = Field(None, description="Customer phone")
    shipping_address: MailingAddress | None = Field(None, description="Shipping address")


class CancelOrderArgs(BaseModel):
    """Arguments for cancel_order tool."""

    order_id: str = Field(..., description="Order ID")
    reason: str | None = Field(None, description="Cancellation reason")


class CreateCustomerArgs(BaseModel):
    """Arguments for create_customer tool."""

    email: str = Field(..., description="Customer email address")
    first_name: str | None = Field(None, description="First name")
    last_name: str | None = Field(None, description="Last name")
    phone: str | None = Field(None, description="Phone number")
    address: MailingAddress | None = Field(None, description="Default address")
    tags: list[str] | None = Field(None, description="Customer tags")
    note: str | None = Field(None, description="Internal note")
    accepts_marketing: bool = Field(False, description="Accepts marketing emails")


class GetCustomerArgs(BaseModel):
    """Arguments for get_customer tool."""

    customer_id: str = Field(..., description="Customer ID")


class ListCustomersArgs(BaseModel):
    """Arguments for list_customers tool."""

    query: str | None = Field(None, description="Search by name or email")
    tag: str | None = Field(None, description="Filter by tag")
    limit: int = Field(20, ge=0, description="Maximum customers to return")
    after: str | None = Field(None, description="Pagination cursor")


class UpdateCustomerArgs(BaseModel):
    """Arguments for update_customer tool."""

    customer_id: str = Field(..., description="Customer ID")
    first_name: str | None = Field(None, description="First name")
    last_name: str | None = Field(None, description="Last name")
    email: str | None = Field(None, description="Email address")
    phone: str | None = Field(None, description="Phone number")
    address: MailingAddress | None = Field(None, description="Add or update default address")
    tags: list[str] | None = Field(None, description="Customer tags")
    note: str | None = Field(None, description="Internal note")
    accepts_marketing: bool | None = Field(None, description="Accepts marketing emails")


class SearchCustomersArgs(BaseModel):
    """Arguments for search_customers tool."""

    query: str = Field(..., description="Search by name, email, or phone")
    limit: int = Field(20, ge=0, description="Maximum results")


class GetInventoryArgs(BaseModel):
    """Arguments for get_inventory tool."""

    product_id: str | None = Field(None, description="Filter to a specific product")
    low_stock_threshold: int | None = Field(None, ge=0, description="Only show variants at or below this quantity")


class UpdateInventoryArgs(BaseModel):
    """Arguments for update_inventory tool."""

    variant_id: str = Field(..., description="Product variant ID")
    quantity: int = Field(..., ge=0, description="New quantity available")


class CreateCollectionArgs(BaseModel):
    """Arguments for create_collection tool."""

    title: str = Field(..., description="Collection title")
    description: str = Field("", description="Collection description")
    product_ids: list[str] | None = Field(None, description="Initial product IDs to include")
    sort_order: CollectionSortOrder = Field("MANUAL", description="Sort order")


class GetCollectionArgs(BaseModel):
    """Arguments for get_collection tool."""

    collection_id: str = Field(..., description="Collection ID")


class ListCollectionsArgs(BaseModel):
    """Arguments for list_collections tool."""

    limit: int = Field(20, ge=0, description="Maximum collections to return")
    after: str | None = Field(None, description="Pagination cursor")


class AddToCollectionArgs(BaseModel):
    """Arguments for add_to_collection tool."""

    collection_id: str = Field(..., description="Collection ID")
    product_ids: list[str] = Field(..., description="Product IDs to add")


class RemoveFromCollectionArgs(BaseModel):
    """Arguments for remove_from_collection tool."""

    collection_id: str = Field(..., description="Collection ID")
    product_ids: list[str] = Field(..., description="Product IDs to remove")


class CreateReviewArgs(BaseModel):
    """Arguments for create_review tool."""

    product_id: str = Field(..., description="Product ID to review")
    rating: int = Field(..., ge=1, le=5, description="Rating from 1 to 5")
    title: str = Field("", description="Review title")
    body: str = Field("", description="Review body text")
    author: str = Field(..., description="Author name")
    email: str | None = Field(None, description="Author email")


class GetProductReviewsArgs(BaseModel):
    """Arguments for get_product_reviews tool."""

    product_id: str = Field(..., description="Product ID")
    status: ReviewStatus | None = Field(None, description="Filter by status (PENDING, PUBLISHED, HIDDEN)")
    limit: int = Field(20, ge=0, description="Maximum reviews to return")
    after: str | None = Field(None, description="Pagination cursor")


class DeleteReviewArgs(BaseModel):
    """Arguments for delete_review tool."""

    review_id: str = Field(..., description="Review ID to delete")


class UpdateReviewArgs(BaseModel):
    """Arguments for update_review tool."""

    review_id: str = Field(..., description="Review ID")
    status: ReviewStatus | None = Field(None, description="New status (PENDING, PUBLISHED, HIDDEN)")
    title: str | None = Field(None, description="New title")
    body: str | None = Field(None, description="New body")
    rating: int | None = Field(None, ge=1, le=5, description="New rating")


class CreateReturnLineItemInput(BaseModel):
    """Input line item for creating a return."""

    orderLineItemId: NonEmptyString = Field(..., description="Order line item ID being returned")
    quantity: int = Field(1, ge=1, description="Quantity to return")
    reason: str = Field("", description="Line-level return reason")


class CreateReturnArgs(BaseModel):
    """Arguments for create_return tool."""

    order_id: str = Field(..., description="Order ID to return against")
    line_items: list[CreateReturnLineItemInput] = Field(
        ..., description="Items to return: [{orderLineItemId, quantity, reason}]"
    )
    reason: str = Field("", description="Overall return reason")
    note: str | None = Field(None, description="Internal note")


class GetReturnArgs(BaseModel):
    """Arguments for get_return tool."""

    return_id: str = Field(..., description="Return ID")


class ListReturnsArgs(BaseModel):
    """Arguments for list_returns tool."""

    order_id: str | None = Field(None, description="Filter by order ID")
    status: ReturnStatus | None = Field(None, description="Filter by return status")
    limit: int = Field(20, ge=0, description="Maximum returns to return")
    after: str | None = Field(None, description="Pagination cursor")


class UpdateReturnArgs(BaseModel):
    """Arguments for update_return tool."""

    return_id: str = Field(..., description="Return ID")
    status: ReturnStatus | None = Field(
        None, description="New status (REQUESTED, APPROVED, RECEIVED, REFUNDED, REJECTED)"
    )
    note: str | None = Field(None, description="Internal note")


# ============================================
# LOYALTY PROGRAM TYPES
# ============================================


class LoyaltyTier(BaseModel):
    """A tier within the loyalty program."""

    name: NonEmptyString = Field(..., description="Tier name, e.g. 'Bronze', 'Silver', 'Gold'")
    min_lifetime_points: int = Field(..., ge=0, description="Minimum lifetime points to reach this tier")
    discount_percent: float = Field(
        0, ge=0, le=100, description="Percent discount off subtotal for members of this tier"
    )


class ConfigureLoyaltyProgramArgs(BaseModel):
    """Arguments for configure_loyalty_program tool."""

    enabled: bool | None = Field(None, description="Whether the program is active")
    earn_rate: float | None = Field(None, description="Points earned per $1 of order subtotal")
    redemption_rate: int | None = Field(None, description="Points needed to equal $1 when redeeming")
    max_redemption_percent: float | None = Field(
        None, description="Cap on what percent of subtotal can be paid with points (0-100)"
    )
    tiers: list[LoyaltyTier] | None = Field(None, description="Tier thresholds and discount rates")


class GetLoyaltyBalanceArgs(BaseModel):
    """Arguments for get_loyalty_balance tool."""

    customer_id: str = Field(..., description="Customer ID")


class GetLoyaltyTierArgs(BaseModel):
    """Arguments for get_loyalty_tier tool."""

    customer_id: str = Field(..., description="Customer ID")


class ListLoyaltyTiersArgs(BaseModel):
    """Arguments for list_loyalty_tiers tool (no fields)."""


class GetLoyaltyProgramArgs(BaseModel):
    """Arguments for get_loyalty_program tool (no fields)."""


class AwardPointsArgs(BaseModel):
    """Arguments for award_points tool."""

    customer_id: str = Field(..., description="Customer ID")
    points: int = Field(..., description="Points to award (positive integer)")
    reason: str | None = Field(None, description="Reason for awarding")


class RedeemPointsArgs(BaseModel):
    """Arguments for redeem_points tool."""

    customer_id: str = Field(..., description="Customer ID")
    points: int = Field(..., description="Points to redeem (positive integer)")


# ============================================
# STATE MODELS
# ============================================


class ShopifyStateCounters(BaseModel):
    """Monotonic id counters used to generate Shopify entity IDs.

    Loose (`extra="allow"`) so worlds that carry our extended counters
    round-trip cleanly, but every counter used by current tools is modeled.
    """

    model_config = ConfigDict(extra="allow")

    cart_id: int = Field(1000, ge=0)
    line_id: int = Field(1000, ge=0)
    product_id: int = Field(8000, ge=0)
    variant_id: int = Field(9000, ge=0)
    order_id: int = Field(2000, ge=0)
    line_item_id: int = Field(3000, ge=0)
    customer_id: int = Field(4000, ge=0)
    collection_id: int = Field(5000, ge=0)
    review_id: int = Field(6000, ge=0)
    return_id: int = Field(7000, ge=0)
    discount_id: int = Field(10000, ge=0)
    policy_id: int = Field(11000, ge=0)


class LooseProductOption(ProductOption):
    """ProductOption whose `id` is optional — fixture data often omits it."""

    model_config = ConfigDict(extra="allow")

    id: NonEmptyString | None = None


class LooseProduct(Product):
    """Product relaxed for synthetic/legacy snapshot shapes.

    Seeded fixtures and synthetic data often include only the minimum product
    fields (id, title, variants), so we drop the strict ``handle`` /
    ``priceRange`` requirements here and relax ``options`` to the fixture
    shape ({name, values}).
    """

    model_config = ConfigDict(extra="allow")

    handle: NonEmptyString | None = None
    priceRange: ProductPriceRange | None = None
    availableForSale: bool | None = None
    options: list[LooseProductOption] = Field(default_factory=list)
    category: dict[str, Any] | str | None = None
    categoryId: str | None = None
    productCategory: dict[str, Any] | str | None = None


class LooseCart(BaseModel):
    """Cart state that accepts compact synthetic cart fixtures."""

    model_config = ConfigDict(extra="allow")

    id: NonEmptyString
    checkoutUrl: str | None = None
    createdAt: ShopifyDateTime | None = None
    updatedAt: ShopifyDateTime | None = None
    lines: list[CartLine] = Field(default_factory=list)
    cost: CartCost | None = None
    buyerIdentity: CartBuyerIdentity = Field(default_factory=CartBuyerIdentity)
    attributes: list[Attribute] = Field(default_factory=list)
    discountCodes: list[CartDiscountCode] = Field(default_factory=list)
    discountAllocations: list[dict[str, Any]] = Field(default_factory=list)
    appliedGiftCards: list[AppliedGiftCard] = Field(default_factory=list)
    deliveryGroups: list[CartDeliveryGroup] = Field(default_factory=list)
    totalQuantity: int = Field(0, ge=0)
    note: str | None = None


class LoosePolicy(BaseModel):
    """Shop policy as stored in fixtures: no guaranteed `id` or `url`.

    Distinct from ``ShopPolicy`` (the API-shaped model) because seeded policies
    carry a category ``type`` (e.g. REFUND_POLICY) but rarely an id or url.
    """

    model_config = ConfigDict(extra="allow")

    id: NonEmptyString | None = None
    type: str | None = Field(default=None, description="Policy category (REFUND_POLICY, SHIPPING_POLICY, …)")
    title: NonEmptyString
    body: str
    url: str | None = None


class LooseOrder(Order):
    """Order state with mock-specific extensions preserved."""

    model_config = ConfigDict(extra="allow")


class LooseCustomer(Customer):
    """Customer state with loyalty extensions/backfilled defaults preserved."""

    model_config = ConfigDict(extra="allow")


class LooseCollection(Collection):
    """Collection state with synthetic metadata preserved."""

    model_config = ConfigDict(extra="allow")

    handle: NonEmptyString | None = None
    createdAt: ShopifyDateTime | None = None
    updatedAt: ShopifyDateTime | None = None
    products: list[NonEmptyString] = Field(
        default_factory=list,
        description="Legacy collection membership field accepted by older fixtures",
    )


class LooseReview(Review):
    """Review state with synthetic metadata preserved."""

    model_config = ConfigDict(extra="allow")

    createdAt: ShopifyDateTime | None = None
    updatedAt: ShopifyDateTime | None = None


class LooseReturn(Return):
    """Return state with synthetic metadata preserved."""

    model_config = ConfigDict(extra="allow")


class LooseDiscountCode(DiscountCode):
    """Discount code state with product restrictions preserved."""

    model_config = ConfigDict(extra="allow")

    productIds: list[NonEmptyString] | None = None


class LooseGiftCard(GiftCard):
    """Gift card state with mock-specific metadata preserved."""

    model_config = ConfigDict(extra="allow")


class LooseShippingMethod(ShippingMethod):
    """Shipping method state with synthetic metadata preserved."""

    model_config = ConfigDict(extra="allow")


class LoyaltyProgram(BaseModel):
    """Store loyalty program configuration."""

    model_config = ConfigDict(extra="allow")

    enabled: bool = False
    earn_rate: int | float = Field(1, ge=0)
    redemption_rate: int | float = Field(100, gt=0)
    max_redemption_percent: int | float = Field(50, ge=0, le=100)
    tiers: list[LoyaltyTier] = Field(default_factory=list)


class LooseFAQ(BaseModel):
    """Searchable FAQ entry."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    question: NonEmptyString
    answer: NonEmptyString


class ShopifyStateModel(BaseModel):
    """Full shopify state — round-trips with ShopifyState.to_dict().

    State entities are loaded into Pydantic models while exposing temporary
    dict-style access for legacy handlers. ``extra="allow"`` preserves
    mock-specific metadata that tools may carry but the core Shopify-shaped
    models do not yet describe.
    """

    model_config = ConfigDict(extra="allow")

    products: dict[str, LooseProduct] = Field(default_factory=dict, description="Products keyed by product gid")
    carts: dict[str, LooseCart] = Field(default_factory=dict, description="Carts keyed by cart gid")
    orders: dict[str, LooseOrder] = Field(default_factory=dict, description="Orders keyed by order gid")
    customers: dict[str, LooseCustomer] = Field(default_factory=dict, description="Customers keyed by customer gid")
    collections: dict[str, LooseCollection] = Field(default_factory=dict, description="Collections keyed by gid")
    reviews: dict[str, LooseReview] = Field(default_factory=dict, description="Reviews keyed by review id")
    returns: dict[str, LooseReturn] = Field(default_factory=dict, description="Returns keyed by return id")
    discount_codes: dict[str, LooseDiscountCode] = Field(
        default_factory=dict, description="Discount codes keyed by code"
    )
    gift_cards: dict[str, LooseGiftCard] = Field(default_factory=dict, description="Gift cards keyed by code or id")
    shipping_methods: dict[str, LooseShippingMethod] = Field(
        default_factory=dict, description="Shipping methods keyed by method id"
    )
    loyalty_program: LoyaltyProgram = Field(default_factory=LoyaltyProgram, description="Loyalty program configuration")
    current_customer_email: str | None = Field(
        default=None,
        description="Identifies which customer the agent is acting as in customer-mode worlds",
    )
    policies: list[LoosePolicy] = Field(default_factory=list, description="Shop policies and FAQs")
    faqs: list[LooseFAQ] = Field(default_factory=list, description="Store FAQ entries")
    counters: ShopifyStateCounters = Field(default_factory=ShopifyStateCounters)

    @model_validator(mode="after")
    def validate_keys_and_references(self) -> "ShopifyStateModel":
        product_refs = set(self.products) | {product.id for product in self.products.values()}
        normalized_product_refs = {product_ref.lower() for product_ref in product_refs}
        for key, product in self.products.items():
            if key != product.id:
                raise ValueError(f"products key {key!r} does not match product.id {product.id!r}")
        for key, cart in self.carts.items():
            if key != cart.id:
                raise ValueError(f"carts key {key!r} does not match cart.id {cart.id!r}")
        for key, order in self.orders.items():
            if key != order.id:
                raise ValueError(f"orders key {key!r} does not match order.id {order.id!r}")
        for key, customer in self.customers.items():
            if key != customer.id:
                raise ValueError(f"customers key {key!r} does not match customer.id {customer.id!r}")
        for key, collection in self.collections.items():
            if key != collection.id:
                raise ValueError(f"collections key {key!r} does not match collection.id {collection.id!r}")
            for product_id in collection.productIds + collection.products:
                if product_id.lower() not in normalized_product_refs:
                    raise ValueError(f"collection {key!r} references missing product {product_id!r}")
        for key, review in self.reviews.items():
            if key != review.id:
                raise ValueError(f"reviews key {key!r} does not match review.id {review.id!r}")
            if review.productId not in self.products:
                raise ValueError(f"review {key!r} references missing product {review.productId!r}")
        for key, return_obj in self.returns.items():
            if key != return_obj.id:
                raise ValueError(f"returns key {key!r} does not match return.id {return_obj.id!r}")
            order = self.orders.get(return_obj.orderId)
            if order is None:
                raise ValueError(f"return {key!r} references missing order {return_obj.orderId!r}")
            order_line_ids = {line.id for line in order.lineItems}
            for line_item in return_obj.lineItems:
                if line_item.orderLineItemId not in order_line_ids:
                    raise ValueError(f"return {key!r} references missing order line item {line_item.orderLineItemId!r}")
        for key, discount_code in self.discount_codes.items():
            if key not in {discount_code.id, discount_code.code}:
                raise ValueError(
                    f"discount_codes key {key!r} does not match discount id/code "
                    f"{discount_code.id!r}/{discount_code.code!r}"
                )
            for product_id in discount_code.productIds or []:
                if product_id.lower() not in normalized_product_refs:
                    raise ValueError(f"discount code {key!r} references missing product {product_id!r}")
        discount_codes_by_code: dict[str, str] = {}
        for key, discount_code in self.discount_codes.items():
            normalized_code = discount_code.code.upper()
            existing_key = discount_codes_by_code.get(normalized_code)
            if existing_key is not None:
                raise ValueError(
                    f"discount code {discount_code.code!r} is duplicated by "
                    f"discount_codes keys {existing_key!r} and {key!r}"
                )
            discount_codes_by_code[normalized_code] = key
        for key, gift_card in self.gift_cards.items():
            if key not in {gift_card.id, gift_card.code}:
                raise ValueError(
                    f"gift_cards key {key!r} does not match gift card id/code {gift_card.id!r}/{gift_card.code!r}"
                )
        gift_cards_by_code: dict[str, str] = {}
        for key, gift_card in self.gift_cards.items():
            normalized_code = gift_card.code.upper()
            existing_key = gift_cards_by_code.get(normalized_code)
            if existing_key is not None:
                raise ValueError(
                    f"gift card {gift_card.code!r} is duplicated by gift_cards keys {existing_key!r} and {key!r}"
                )
            gift_cards_by_code[normalized_code] = key
        for key, shipping_method in self.shipping_methods.items():
            if key != shipping_method.id:
                raise ValueError(
                    f"shipping_methods key {key!r} does not match shipping method id {shipping_method.id!r}"
                )
        return self


# ============================================
# LEGACY ALIASES (for backward compatibility in tool handlers)
# ============================================

# These match the original simplified names used in tool handlers
AddItemInput = CartLineInput
UpdateItemInput = CartLineUpdateInput
BuyerIdentity = CartBuyerIdentityInput
DeliveryAddress = MailingAddress
DeliveryAddressInput = CartSelectableAddressInput
SelectedDeliveryOption = CartSelectedDeliveryOptionInput
SearchResult = SearchResultItemConnection
AvailableFilter = Filter
