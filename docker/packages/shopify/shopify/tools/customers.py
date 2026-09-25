"""Customer tool handlers."""

import contextlib
from datetime import UTC, datetime

from shopify.models import (
    CreateCustomerArgs,
    GetCustomerArgs,
    ListCustomersArgs,
    MailingAddress,
    SearchCustomersArgs,
    UpdateCustomerArgs,
)
from shopify.state import (
    LooseCustomer,
    _parse_query_tokens,
    get_all_customers,
    get_customer_by_email,
    get_customer_by_id,
    get_next_customer_id,
    get_state,
    save_state,
)


def handle_create_customer(args: CreateCustomerArgs) -> dict:
    """Create a new customer account."""
    # Check for duplicate email
    existing = get_customer_by_email(args.email)
    if existing is not None:
        return {
            "customer": None,
            "userErrors": [{"field": "email", "message": f"Customer with email {args.email} already exists"}],
        }

    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    customer_id = get_next_customer_id()

    address = args.address
    addresses = [address] if address else []

    customer = {
        "id": customer_id,
        "firstName": args.first_name,
        "lastName": args.last_name,
        "email": args.email,
        "phone": args.phone,
        "createdAt": now,
        "updatedAt": now,
        "defaultAddress": address,
        "addresses": addresses,
        "ordersCount": 0,
        "totalSpent": {"amount": "0.00", "currencyCode": "USD"},
        "tags": args.tags or [],
        "note": args.note,
        "acceptsMarketing": args.accepts_marketing,
        "state": "ENABLED",
    }

    state = get_state()
    state.customers[customer_id] = LooseCustomer.model_validate(customer)
    save_state()

    return {"customer": customer, "userErrors": []}


def handle_get_customer(args: GetCustomerArgs) -> dict:
    """Retrieve a customer by their ID."""
    customer = get_customer_by_id(args.customer_id)
    if customer is None:
        return {
            "customer": None,
            "userErrors": [{"field": "customer_id", "message": f"Customer not found: {args.customer_id}"}],
        }
    return {"customer": customer, "userErrors": []}


def handle_list_customers(args: ListCustomersArgs) -> dict:
    """List customers with optional query and tag filtering."""
    customers = get_all_customers()

    # Filter by search query (name or email)
    if args.query:
        query_lower = args.query.lower()
        customers = [
            customer
            for customer in customers
            if query_lower in (customer.firstName or "").lower()
            or query_lower in (customer.lastName or "").lower()
            or query_lower in customer.email.lower()
            or query_lower in f"{customer.firstName or ''} {customer.lastName or ''}".lower()
        ]

    # Filter by tag
    if args.tag:
        tag_lower = args.tag.lower()
        customers = [customer for customer in customers if tag_lower in [tag.lower() for tag in customer.tags]]

    # Sort by creation date descending
    customers.sort(key=lambda customer: customer.createdAt, reverse=True)

    total_count = len(customers)

    # Pagination
    start_idx = 0
    if args.after:
        with contextlib.suppress(ValueError):
            start_idx = int(args.after) + 1

    end_idx = start_idx + args.limit
    paginated = customers[start_idx:end_idx]

    has_next = end_idx < total_count
    end_cursor = str(end_idx - 1) if paginated else None

    return {
        "customers": paginated,
        "pageInfo": {
            "hasNextPage": has_next,
            "hasPreviousPage": start_idx > 0,
            "startCursor": str(start_idx) if paginated else None,
            "endCursor": end_cursor,
        },
        "totalCount": total_count,
    }


def handle_search_customers(args: SearchCustomersArgs) -> dict:
    """Search customers by name, email, or phone.

    Whitespace-separated tokens are ANDed against a haystack of
    first/last/email/phone; double-quoted segments must appear contiguously.
    Structured fields (email, phone) stay in the haystack so single-token
    substring matches (e.g. "@example.com") continue to work.
    """
    customers = get_all_customers()
    tokens = _parse_query_tokens(args.query)
    # Preserve original for exact-email relevance bump below.
    query_lower = args.query.lower().strip()

    matches = []
    for customer in customers:
        first = customer.firstName or ""
        last = customer.lastName or ""
        email = customer.email
        phone = customer.phone or ""
        # Group naturally-composite fields (full name) with spaces so phrases
        # like "alice smith" still match, and separate other field groups with
        # \n so phrases don't accidentally span into an unrelated field like
        # an email address.
        full_name = f"{first} {last}".strip()
        haystack = f"{full_name}\n{email}\n{phone}".lower()

        if tokens and all(t in haystack for t in tokens):
            matches.append(customer)

    # Sort by relevance (exact email match first, then by name)
    matches.sort(
        key=lambda c: (
            0 if c.email.lower() == query_lower else 1,
            (c.lastName or "").lower(),
            (c.firstName or "").lower(),
        )
    )

    return {
        "customers": matches[: args.limit],
        "totalCount": len(matches),
    }


def _set_default_address(customer: LooseCustomer, address: MailingAddress) -> None:
    """Set default address and keep one copy of that address in the address book."""
    customer.defaultAddress = address
    customer.addresses = [existing for existing in customer.addresses if existing != address] + [address]


def handle_update_customer(args: UpdateCustomerArgs) -> dict:
    """Update fields on an existing customer."""
    customer = get_customer_by_id(args.customer_id)
    if customer is None:
        return {
            "customer": None,
            "userErrors": [{"field": "customer_id", "message": f"Customer not found: {args.customer_id}"}],
        }

    if args.email is not None:
        existing = get_customer_by_email(args.email)
        if existing is not None and existing.id != customer.id:
            return {
                "customer": None,
                "userErrors": [{"field": "email", "message": f"Customer with email {args.email} already exists"}],
            }
    address = MailingAddress.model_validate(args.address) if args.address is not None else None

    if args.first_name is not None:
        customer.firstName = args.first_name
    if args.last_name is not None:
        customer.lastName = args.last_name
    if args.email is not None:
        customer.email = args.email
    if args.phone is not None:
        customer.phone = args.phone
    if args.tags is not None:
        customer.tags = args.tags
    if args.note is not None:
        customer.note = args.note
    if args.accepts_marketing is not None:
        customer.acceptsMarketing = args.accepts_marketing

    if address is not None:
        _set_default_address(customer, address)

    customer.updatedAt = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    save_state()

    return {"customer": customer, "userErrors": []}
