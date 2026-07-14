"""Status display for --status flag."""

from datetime import datetime, timezone
from typing import Optional

import codeplain_REST_api as codeplain_api
from plain2code_console import console


def _create_progress_bar(remaining: int, total: int, width: int = 30) -> str:
    """Create a Unicode progress bar."""
    if total == 0:
        filled = 0
    else:
        filled = int((remaining / total) * width)

    empty = width - filled
    return "█" * filled + "░" * empty


def _display_credit_line(plan_credits: dict) -> None:
    """Display a plan credit line with progress bar."""
    plan_type = plan_credits["type"]
    remaining = plan_credits["remaining"]
    total = plan_credits["total"]
    period_end = plan_credits["period_end"]

    # Parse ISO-8601 timestamp and format as "Jun 1, 2026"
    # Handle both with and without timezone info
    dt_str = period_end.replace("Z", "+00:00")
    dt = datetime.fromisoformat(dt_str)
    # If naive datetime, assume UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    formatted_date = f"{dt.strftime('%b')} {dt.day}, {dt.year}"

    # Check if expired
    now = datetime.now(timezone.utc)
    is_expired = dt < now

    # Create progress bar (30 chars wide)
    bar = _create_progress_bar(remaining, total, width=30)

    # Format plan type label
    plan_label = "Free trial" if plan_type == "free" else plan_type.upper() + " plan"

    if is_expired:
        console.print(f"    {plan_label:10} {bar}   expired {formatted_date}")
    else:
        console.print(f"    {plan_label:10} {bar}   {remaining:2} of {total} remaining    expires {formatted_date}")


def _display_bucket_credit_line(bucket: dict, label: str) -> None:
    """Display a credit bucket line (purchased or promo) with progress bar."""
    remaining = bucket["remaining"]
    total = bucket["total"]
    expiry_date = bucket["expiry_date"]

    # Parse ISO-8601 timestamp and handle both with and without timezone info
    dt_str = expiry_date.replace("Z", "+00:00")
    dt = datetime.fromisoformat(dt_str)
    # If naive datetime, assume UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    formatted_date = f"{dt.strftime('%b')} {dt.day}, {dt.year}"

    # Check if expired
    now = datetime.now(timezone.utc)
    is_expired = dt < now

    bar = _create_progress_bar(remaining, total, width=30)

    if is_expired:
        console.print(f"    {label:10} {bar}   expired {formatted_date}")
    else:
        console.print(f"    {label:10} {bar}   {remaining:2} of {total} remaining    expires {formatted_date}")


def _display_status_message(plan_credits: Optional[dict], purchased_credits: list, promo_credits: list) -> None:
    """Display appropriate status message based on credit state."""
    has_remaining = False

    # Check if plan credits have remaining balance and are not expired
    if plan_credits:
        remaining = plan_credits["remaining"]
        dt_str = plan_credits["period_end"].replace("Z", "+00:00")
        period_end = datetime.fromisoformat(dt_str)
        # If naive datetime, assume UTC
        if period_end.tzinfo is None:
            period_end = period_end.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)

        if remaining > 0 and period_end > now:
            has_remaining = True

    # Check if any purchased or promo credits have remaining balance and not expired
    for bucket in [*purchased_credits, *promo_credits]:
        dt_str = bucket["expiry_date"].replace("Z", "+00:00")
        expiry_date = datetime.fromisoformat(dt_str)
        # If naive datetime, assume UTC
        if expiry_date.tzinfo is None:
            expiry_date = expiry_date.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)

        if bucket["remaining"] > 0 and expiry_date > now:
            has_remaining = True
            break

    if not has_remaining:
        console.print("\nNo rendering credits remaining. Upgrade to continue rendering.")


def print_status(api_key: str, api_url: str, client_version: str) -> None:
    """Display account status including user info and rendering credits."""
    codeplainAPI = codeplain_api.CodeplainAPI(api_key, console)
    codeplainAPI.api_url = api_url

    # First check client version
    version_check = codeplainAPI.connection_check(client_version)
    client_version_valid = version_check.get("client_version_valid", False)
    min_version = version_check.get("min_client_version", "unknown")

    response = codeplainAPI.status()

    user = response["user"]
    api_key_label = response["api_key_label"]
    org_owner = response.get("organization_owner_email")
    plan_credits = response.get("plan_credits")
    purchased_credits = response.get("purchased_credits", [])
    promo_credits = response.get("promo_credits", [])

    # Display header information
    if client_version_valid:
        console.print(f"Version: {client_version}")
    else:
        from plain2code_console import Plain2CodeConsole

        console.print(
            f"Version: {client_version} (outdated — minimum required: {min_version})",
            style=Plain2CodeConsole.ERROR_STYLE,
        )
        console.print("To update, run: uv tool upgrade codeplain\n", style=Plain2CodeConsole.ERROR_STYLE)
    console.print(f"Name: {user['first_name']} {user['last_name']}")
    console.print(f"User email: {user['email']}")
    console.print(f"Organization owner: {org_owner or 'N/A'}")
    console.print(f"API key label: {api_key_label}\n")

    # Display rendering credits section
    console.print("Rendering credits:")

    # Display plan credits (free trial or subscription)
    if plan_credits:
        _display_credit_line(plan_credits)

    # Display purchased credits
    for bucket in purchased_credits:
        _display_bucket_credit_line(bucket, "Purchased")

    # Display promo credits
    for bucket in promo_credits:
        _display_bucket_credit_line(bucket, "Promo")

    # Display status messages and management link
    _display_status_message(plan_credits, purchased_credits, promo_credits)
    console.print("\nTo manage your plan navigate to https://platform.codeplain.ai/plans")
