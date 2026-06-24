"""Store policy CRUD tools."""

from shopify.models import (
    CreatePolicyArgs,
    DeletePolicyArgs,
    ListPoliciesArgs,
    LoosePolicy,
    UpdatePolicyArgs,
)
from shopify.state import get_next_policy_id, get_policy_by_id, get_state, save_state


def handle_create_policy(args: CreatePolicyArgs) -> dict:
    """Create a new store policy."""
    policy_id = get_next_policy_id()
    policy_num = policy_id.rsplit("/", 1)[-1]

    policy = {
        "id": policy_id,
        "title": args.title,
        "body": args.body,
        "url": f"https://shop.example.com/policies/{policy_num}",
    }

    state = get_state()
    policy_model = LoosePolicy.model_validate(policy)
    state.policies.append(policy_model)
    save_state()

    return {"policy": policy_model, "userErrors": []}


def handle_list_policies(_args: ListPoliciesArgs) -> dict:
    """List all store policies."""
    state = get_state()
    return {"policies": state.policies, "totalCount": len(state.policies)}


def handle_update_policy(args: UpdatePolicyArgs) -> dict:
    """Update an existing policy."""
    policy = get_policy_by_id(args.policy_id)
    if policy is None:
        return {
            "policy": None,
            "userErrors": [{"field": "policy_id", "message": f"Policy not found: {args.policy_id}"}],
        }

    if args.title is not None:
        policy.title = args.title
    if args.body is not None:
        policy.body = args.body

    save_state()
    return {"policy": policy, "userErrors": []}


def handle_delete_policy(args: DeletePolicyArgs) -> dict:
    """Delete a policy."""
    state = get_state()
    for i, p in enumerate(state.policies):
        if p.id == args.policy_id:
            del state.policies[i]
            save_state()
            return {"deletedPolicyId": args.policy_id, "userErrors": []}

    return {
        "deletedPolicyId": None,
        "userErrors": [{"field": "policy_id", "message": f"Policy not found: {args.policy_id}"}],
    }
