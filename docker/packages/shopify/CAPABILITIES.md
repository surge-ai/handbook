# Shopify Capabilities

A mock Shopify store covering the full e-commerce lifecycle: product management, cart management, checkout, orders, customers, inventory, collections, reviews, returns, discounts, shipping, policies, and a loyalty program. Supports multi-store worlds (agent switches between stores via `set_active_store`) and customer-mode worlds (agent acts as a specific end customer rather than a merchant).

## What the agent can do

**Manage products.** Create new products with title, description, vendor, type, tags, and variants (each with price, SKU, and inventory). Update product details. Delete products (cleans up associated collection memberships and reviews). Search the catalog by keyword with filters (vendor, product type, price range, tags, availability). View detailed product information including variants, pricing, images, and options.

Catalog search uses case-insensitive word-AND matching. Empty queries intentionally search all products before filters apply, and an unset `available` filter returns both available and unavailable products. Multiple filters are ANDed; OR, negation, and exclude filters are not supported. The mock `category` filter is intentionally broad: it matches either product category fields (`category`, `categoryId`, `productCategory`) or collection membership by collection id, title, or handle. `country` and `language` inputs are accepted as compatibility hints but do not localize the mock catalog.

**Shopping cart.** Create carts, add items (by product variant), update quantities, remove items, set buyer identity (email, phone), add delivery addresses, select shipping options, apply discount codes and gift cards, and add order notes.

**Place and manage orders.** Convert a cart into an order (capturing line items, buyer info, addresses). View individual orders or list all orders with filtering by financial status (pending, paid, refunded) or fulfillment status. Update order status and details. Cancel orders with an optional reason.

**Customer management.** Create customer profiles with name, email, phone, addresses, and tags. Search customers by name, email, or phone. Update profiles and manage marketing consent. List customers with filtering by tag.

**Inventory.** View stock levels across all product variants, with optional filtering by product or low-stock threshold. Update quantities — product-level availability and total inventory recalculate automatically.

**Collections.** Create curated collections, add and remove products from collections, list all collections with product counts, and view collection details with full product data.

**Reviews.** Create reviews with ratings (1-5), title, body, and author. View reviews for a product with average rating. Moderate reviews by changing status (published, hidden, pending). Delete reviews.

**Returns and refunds.** Create return requests linked to orders with specific line items and quantities. The system validates line items and calculates refund amounts. Track returns through their lifecycle (requested → approved → received → refunded/rejected). When a return is marked as refunded, the linked order's financial status automatically updates to "refunded" or "partially refunded."

**Discount codes.** Create, update, get, list, and delete discount codes. Codes can be percentage-based or fixed-amount. When applied to a cart, they reduce the order total accordingly.

**Shipping methods.** Create, update, list, and delete shipping methods. Each method has a name, carrier, price, and estimated delivery window. Carts select from available methods.

**Policies.** Create, update, list, and delete shop policies (refund, shipping, terms, etc.). Agents can also search policies and FAQs by keyword. Policy/FAQ search result objects reserve `type` for the normalized result kind (`policy` or `faq`), regardless of any fixture-level policy category field.

**Loyalty program.** Configure a points-based loyalty program with earn/redemption rates and tiers. Award points to customers, check balances and tier status, redeem points for discounts, and list available tiers.

**Customer-mode operations (self).** When the agent represents a specific customer (set via `current_customer_email` in state), a set of `_my_` tools operate on the current customer's data: `get_my_customer`, `get_my_order`, `list_my_orders`, `create_my_return`, `create_my_review`, `update_my_customer`, `get_my_loyalty_balance`, `get_my_loyalty_tier`, `redeem_my_points`. These bypass the need to look up the customer ID each call.

**Multi-store.** When the world defines multiple stores under a `stores` key, the agent can list stores (`list_stores`), switch the active store, and perform all operations scoped to the active store. State is partitioned per store.

## Coverage gaps

- No payment processing or payment method management
- No carrier integration or shipping label generation
- No tax calculation
- No multi-currency support
- No order fulfillment tracking (shipping labels, tracking numbers)
- No webhook or notification system

## Toolsets

66 tools total. Toolsets map to `WORLDBENCH_TOOL_SETS` values (prefixed form — e.g., `shopify_cart`).

| Toolset | Tools | Description |
|---------|-------|-------------|
| `all` / `shopify_all` | 66 | Everything |
| `read` / `shopify_read` | 31 | All read-only tools |
| `write` / `shopify_write` | 35 | All write tools |
| `shopify_catalog` | 6 | Product + FAQ browsing: create/delete/update product, get details, search catalog, search policies |
| `shopify_cart` | 3 | Cart: get, list, update |
| `shopify_orders` | 5 | Order lifecycle: create, get, list, update, cancel |
| `shopify_customers` | 5 | Customer profiles: create, get, list, search, update |
| `shopify_inventory_collections` | 7 | Stock + collections: get/update inventory, create/get/list collections, add/remove products |
| `shopify_reviews_returns` | 8 | Reviews + returns: create/update/delete reviews, get product reviews, create/get/list/update returns |
| `shopify_discounts` | 5 | Discount codes: create, update, get, list, delete |
| `shopify_shipping` | 4 | Shipping methods: create, update, list, delete |
| `shopify_policies` | 5 | Shop policies: create, update, list, delete, search policies & FAQs |
| `shopify_loyalty` | 7 | Loyalty program: configure, award/redeem points, get balance/tier/program, list tiers |
| `shopify_self` | 9 | Customer-mode ops: all `_my_` tools (acts on `current_customer_email`) |
| `shopify_customer` | 17 | Full customer-mode toolset: self ops + catalog browsing + cart/order creation |
| `shopify_business` | 48 | Full merchant-mode toolset: everything except customer-mode `_my_` tools |
| `shopify_core` | 10 | Baseline order flow plus legacy Toolathlon catalog/policy search |
| `shopify_toolathlon_legacy` | 6 | Legacy Toolathlon tool subset (pre-integration) |
| `shopify_state` | 2 | `export_state`, `import_state` for fixture seeding and grading |

**Permission-mode toolsets** (`customer` / `business`) are mutually exclusive — use one when setting up a world that scopes the agent to a specific role. `customer` mode pairs with a state file that sets `current_customer_email`.

**Multi-store** worlds round-trip through `export_state` / `import_state` under a `{"stores": {store_id: {...}}}` wrapper; single-store worlds use the flat shape.
