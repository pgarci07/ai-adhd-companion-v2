#!/usr/bin/env python3
"""Load the simple task CSV test set for one Supabase user.

This script replaces the old Streamlit development menu action. It signs in as
the requested user, reads `infra/supabase/seeders/tasks.csv`, and inserts those
simple task rows through the same database RPC used by the UI.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.application.use_cases import task_test_seeder


DEFAULT_LIST_NAME = "my list"


def load_env_file() -> None:
    """Load .env key/value pairs without requiring python-dotenv."""

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
    """Create a Supabase client from .env/current environment."""

    load_env_file()
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")
    if not supabase_url or not supabase_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set in .env or the environment.")

    from supabase import create_client

    return create_client(supabase_url, supabase_key)


def authenticate(client: Any, email: str, password: str | None) -> str:
    """Authenticate the Supabase client as the requested user."""

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


def fetch_dimension_options(client: Any, table_name: str) -> list[dict[str, Any]]:
    """Fetch lookup values sorted by id."""

    response = client.table(table_name).select("id, weight, label").order("id").execute()
    options = response.data or []
    if not options:
        raise RuntimeError(f"No lookup values found in {table_name}.")
    return options


def get_or_create_list(client: Any, list_name: str) -> dict[str, Any]:
    """Return the target list row for the authenticated user, creating it if needed."""

    response = (
        client.table("lists")
        .select("id, name")
        .eq("name", list_name)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    if rows:
        return rows[0]

    insert_response = (
        client.table("lists")
        .insert(
            {
                "name": list_name,
                "description": "Created by scripts/load_simple_tasks_testset.py",
            }
        )
        .execute()
    )
    inserted_rows = insert_response.data or []
    if not inserted_rows:
        raise RuntimeError(f"Could not create list {list_name!r}.")
    return inserted_rows[0]


def parse_bool(value: str) -> bool:
    """Parse explicit command-line boolean values."""

    cleaned = value.strip().lower()
    if cleaned in {"1", "true", "t", "yes", "y"}:
        return True
    if cleaned in {"0", "false", "f", "no", "n"}:
        return False
    raise argparse.ArgumentTypeError(f"Invalid boolean value: {value!r}")


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""

    parser = argparse.ArgumentParser(
        description="Load infra/supabase/seeders/tasks.csv simple tasks into Supabase."
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
        "--csv",
        type=Path,
        default=task_test_seeder.SEED_TASKS_CSV_PATH,
        help="Path to the semicolon-delimited simple tasks CSV.",
    )
    parser.add_argument(
        "--list-name",
        default=DEFAULT_LIST_NAME,
        help=f"List name to use/create. Default: {DEFAULT_LIST_NAME!r}.",
    )
    parser.add_argument(
        "--from_scratch",
        nargs="?",
        const=True,
        default=True,
        type=parse_bool,
        help=(
            "Delete the authenticated user's existing non-recurring tasks before loading. "
            "Default: true."
        ),
    )
    parser.add_argument(
        "--no-from_scratch",
        "--no-from-scratch",
        dest="from_scratch",
        action="store_false",
        help="Load CSV rows without deleting existing non-recurring tasks first.",
    )
    return parser.parse_args()


def main() -> None:
    """Run the simple task test-set loader."""

    args = parse_args()
    records = task_test_seeder.read_task_seed_records(args.csv)
    client = build_client()
    user_id = authenticate(client, args.email, args.password)
    target_list = get_or_create_list(client, args.list_name)

    print(f"Authenticated user: {user_id}")
    print(f"CSV: {args.csv}")
    print(f"List: {target_list['name']} ({target_list['id']})")
    print(f"From scratch: {args.from_scratch}")
    print(f"Rows to load: {len(records)}")

    result = task_test_seeder.seed_current_user_test_tasks(
        client,
        list_id=target_list["id"],
        size_options=fetch_dimension_options(client, "dim_task_sizes"),
        consequence_options=fetch_dimension_options(client, "dim_task_consequences"),
        friction_options=fetch_dimension_options(client, "dim_task_frictions"),
        csv_path=args.csv,
        from_scratch=args.from_scratch,
    )
    print(
        "Load complete: "
        f"deleted={result['deleted_count']}, "
        f"inserted={result['inserted_count']}."
    )


if __name__ == "__main__":
    main()
