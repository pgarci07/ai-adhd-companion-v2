#!/usr/bin/env python3
"""Move old task instances to stale for one authenticated Supabase user.

This helper is intentionally simple and test-oriented. It authenticates as the
requested user, inspects the current task-instance rows exposed by the same RPC
the UI uses, and moves matching instances to `stale` through the status-change
RPC so the task status log stays consistent.
"""

from __future__ import annotations

import argparse
import getpass
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


UTC = timezone.utc
DEFAULT_STALE_DAYS = 60
ELIGIBLE_CURRENT_STATUSES = {"ready", "open", "asleep", "debt"}


def load_env_file() -> None:
    """Load `.env` values without requiring python-dotenv."""

    env_path = None
    for base_path in Path(__file__).resolve().parents:
        candidate = base_path / ".env"
        if candidate.exists():
            env_path = candidate
            break
    if env_path is None:
        return

    with env_path.open("r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            cleaned_key = key.strip()
            cleaned_value = value.strip().strip('"').strip("'")
            os.environ.setdefault(cleaned_key, cleaned_value)


def build_client() -> Any:
    """Create a Supabase client from `.env` or process environment variables."""

    load_env_file()
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")
    if not supabase_url or not supabase_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set in .env or the environment.")

    from supabase import create_client

    return create_client(supabase_url, supabase_key)


def authenticate(client: Any, email: str, password: str | None) -> str:
    """Authenticate the client as the requested user."""

    resolved_password = password or getpass.getpass("Supabase password: ")
    response = client.auth.sign_in_with_password(
        {
            "email": email,
            "password": resolved_password,
        }
    )
    user = getattr(response, "user", None)
    if user is None:
        raise RuntimeError("Supabase did not return an authenticated user.")
    return str(user.id)


def parse_iso_datetime(value: Any) -> datetime | None:
    """Parse RPC timestamps while tolerating nulls and trailing `Z`."""

    if value in {None, ""}:
        return None

    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def fetch_current_task_rows(client: Any) -> list[dict[str, Any]]:
    """Load the authenticated user's current task rows through the UI RPC."""

    response = client.rpc("get_user_task_rows").execute()
    return list(response.data or [])


def get_stale_candidates(client: Any, stale_days: int) -> list[dict[str, Any]]:
    """Return the current rows whose due date is older than the requested cutoff.

    This script intentionally uses the current task rows instead of rebuilding
    task history from the status log. For test purposes we only need the active
    per-instance view plus the current status, and we still persist the status
    change through the canonical status-log RPC.
    """

    cutoff = datetime.now(UTC) - timedelta(days=stale_days)
    candidates: list[dict[str, Any]] = []

    for row in fetch_current_task_rows(client):
        current_status = str(row.get("status") or "").lower()
        if current_status not in ELIGIBLE_CURRENT_STATUSES:
            continue

        due_at = parse_iso_datetime(row.get("due_date"))
        if due_at is None or due_at >= cutoff:
            continue

        candidates.append(
            {
                "instance_id": row.get("instance_id"),
                "task_id": row.get("task_id"),
                "title": row.get("title") or "Untitled",
                "status": current_status,
                "due_date": due_at,
            }
        )

    candidates.sort(key=lambda row: (row["due_date"], str(row["instance_id"])))
    return candidates


def mark_instances_stale(client: Any, candidates: list[dict[str, Any]], dry_run: bool) -> int:
    """Move the selected instances to stale through the status-log RPC."""

    if dry_run:
        return 0

    for row in candidates:
        client.rpc(
            "set_task_instance_status",
            {
                "p_instance_id": row["instance_id"],
                "p_new_status": "stale",
            },
        ).execute()

    return len(candidates)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Mark authenticated-user task instances as stale when due_date is older than N days."
    )
    parser.add_argument(
        "--user",
        "--email",
        dest="email",
        required=True,
        help="Supabase user email.",
    )
    parser.add_argument(
        "--password",
        help="Supabase user password. If omitted, the script prompts for it.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_STALE_DAYS,
        help=f"Move tasks whose due_date is older than this many days. Default: {DEFAULT_STALE_DAYS}.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the matching instances without writing status changes.",
    )
    args = parser.parse_args()
    if args.days < 1:
        parser.error("--days must be at least 1.")
    return args


def main() -> None:
    """Run the stale-marking helper."""

    args = parse_args()
    client = build_client()
    user_id = authenticate(client, args.email, args.password)
    candidates = get_stale_candidates(client, args.days)

    print(f"Authenticated user: {user_id}")
    print(f"Stale threshold (days): {args.days}")
    print(f"Dry run: {args.dry_run}")
    print(f"Matching instances: {len(candidates)}")

    for row in candidates:
        print(
            f"- {row['title']} | instance={row['instance_id']} | "
            f"status={row['status']} | due_date={row['due_date'].isoformat()}"
        )

    updated_count = mark_instances_stale(client, candidates, args.dry_run)
    if not args.dry_run:
        print(f"Moved to stale: {updated_count}")


if __name__ == "__main__":
    main()
