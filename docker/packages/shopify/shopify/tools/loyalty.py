"""Loyalty program tools — points, tiers, redemption."""

from datetime import UTC, datetime

from shopify.models import (
    AwardPointsArgs,
    ConfigureLoyaltyProgramArgs,
    GetLoyaltyBalanceArgs,
    GetLoyaltyProgramArgs,
    GetLoyaltyTierArgs,
    ListLoyaltyTiersArgs,
    LoyaltyTier,
    RedeemPointsArgs,
)
from shopify.state import (
    LooseCustomer,
    LoyaltyProgram,
    get_customer_by_id,
    get_state,
    save_state,
)


def compute_tier(lifetime_points: int, tiers: list[LoyaltyTier]) -> LoyaltyTier | None:
    """Return the highest tier whose threshold is met, or None if no tier qualifies."""
    eligible = [tier for tier in tiers if lifetime_points >= tier.min_lifetime_points]
    if not eligible:
        return None
    return max(eligible, key=lambda tier: tier.min_lifetime_points)


def _ensure_loyalty_fields(customer: LooseCustomer, now: str) -> None:
    """Backfill loyalty fields on customers created before the program existed."""
    if customer.loyaltyJoinedAt is None:
        customer.loyaltyJoinedAt = now


def _sync_tier(customer: LooseCustomer, program: LoyaltyProgram) -> None:
    """Recompute and store the customer's tier based on current lifetime points."""
    tier_obj = compute_tier(customer.lifetimePoints, program.tiers)
    customer.tier = tier_obj.name if tier_obj else None


def handle_configure_loyalty_program(args: ConfigureLoyaltyProgramArgs) -> dict:
    """Configure the store's loyalty program. Passes any non-None fields through."""
    state = get_state()
    program = state.loyalty_program

    if args.earn_rate is not None and args.earn_rate < 0:
        return {"program": None, "userErrors": [{"field": "earn_rate", "message": "earn_rate must be >= 0"}]}
    if args.redemption_rate is not None and args.redemption_rate <= 0:
        return {
            "program": None,
            "userErrors": [{"field": "redemption_rate", "message": "redemption_rate must be > 0"}],
        }
    if args.max_redemption_percent is not None and not (0 <= args.max_redemption_percent <= 100):
        return {
            "program": None,
            "userErrors": [
                {"field": "max_redemption_percent", "message": "max_redemption_percent must be between 0 and 100"}
            ],
        }

    if args.enabled is not None:
        program.enabled = args.enabled
    if args.earn_rate is not None:
        program.earn_rate = args.earn_rate
    if args.redemption_rate is not None:
        program.redemption_rate = args.redemption_rate
    if args.max_redemption_percent is not None:
        program.max_redemption_percent = args.max_redemption_percent
    if args.tiers is not None:
        program.tiers = args.tiers

    # Any customer's tier may have shifted if tier config changed
    for customer in state.customers.values():
        if customer.loyaltyJoinedAt:
            _sync_tier(customer, program)

    save_state()
    return {"program": program, "userErrors": []}


def handle_get_loyalty_program(args: GetLoyaltyProgramArgs) -> dict:
    """Read the current loyalty program configuration."""
    state = get_state()
    return {"program": state.loyalty_program, "userErrors": []}


def handle_list_loyalty_tiers(args: ListLoyaltyTiersArgs) -> dict:
    """List all loyalty tiers sorted by threshold ascending."""
    state = get_state()
    tiers = sorted(state.loyalty_program.tiers, key=lambda tier: tier.min_lifetime_points)
    return {"tiers": tiers, "totalCount": len(tiers)}


def handle_get_loyalty_balance(args: GetLoyaltyBalanceArgs) -> dict:
    """Get a customer's loyalty balance, lifetime points, and current tier."""
    customer = get_customer_by_id(args.customer_id)
    if customer is None:
        return {
            "balance": None,
            "userErrors": [{"field": "customer_id", "message": f"Customer not found: {args.customer_id}"}],
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


def handle_get_loyalty_tier(args: GetLoyaltyTierArgs) -> dict:
    """Get the full tier object for a customer's current tier (or null)."""
    customer = get_customer_by_id(args.customer_id)
    if customer is None:
        return {
            "tier": None,
            "userErrors": [{"field": "customer_id", "message": f"Customer not found: {args.customer_id}"}],
        }
    state = get_state()
    tier_obj = compute_tier(customer.lifetimePoints, state.loyalty_program.tiers)
    return {"tier": tier_obj, "userErrors": []}


def handle_award_points(args: AwardPointsArgs) -> dict:
    """Award loyalty points to a customer. Grows both balance and lifetime points."""
    if args.points <= 0:
        return {"balance": None, "userErrors": [{"field": "points", "message": "points must be positive"}]}

    customer = get_customer_by_id(args.customer_id)
    if customer is None:
        return {
            "balance": None,
            "userErrors": [{"field": "customer_id", "message": f"Customer not found: {args.customer_id}"}],
        }

    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    _ensure_loyalty_fields(customer, now)
    customer.pointsBalance += args.points
    customer.lifetimePoints += args.points
    customer.updatedAt = now

    state = get_state()
    _sync_tier(customer, state.loyalty_program)
    save_state()

    return {
        "balance": {
            "customerId": customer.id,
            "pointsBalance": customer.pointsBalance,
            "lifetimePoints": customer.lifetimePoints,
            "tier": customer.tier,
            "pointsAwarded": args.points,
            "reason": args.reason,
        },
        "userErrors": [],
    }


def handle_redeem_points(args: RedeemPointsArgs) -> dict:
    """Redeem loyalty points for dollar value. Deducts from balance; lifetime points unchanged."""
    if args.points <= 0:
        return {
            "redemption": None,
            "userErrors": [{"field": "points", "message": "points must be positive"}],
        }

    customer = get_customer_by_id(args.customer_id)
    if customer is None:
        return {
            "redemption": None,
            "userErrors": [{"field": "customer_id", "message": f"Customer not found: {args.customer_id}"}],
        }

    balance = customer.pointsBalance
    if args.points > balance:
        return {
            "redemption": None,
            "userErrors": [
                {
                    "field": "points",
                    "message": f"Insufficient balance: requested {args.points}, available {balance}",
                }
            ],
        }

    state = get_state()
    redemption_rate = state.loyalty_program.redemption_rate
    if redemption_rate <= 0:
        return {
            "redemption": None,
            "userErrors": [{"field": "redemption_rate", "message": "Loyalty program misconfigured"}],
        }

    dollar_value = args.points / redemption_rate
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    customer.pointsBalance = balance - args.points
    customer.updatedAt = now
    save_state()

    return {
        "redemption": {
            "customerId": customer.id,
            "pointsRedeemed": args.points,
            "dollarValue": {"amount": f"{dollar_value:.2f}", "currencyCode": "USD"},
            "pointsBalance": customer.pointsBalance,
        },
        "userErrors": [],
    }
