#!/usr/bin/env python3
"""Load composite recurring task test data from a semicolon CSV.

The loader authenticates as a Supabase user, inserts task templates into
`tasks`, and creates the requested parent and child rows in `task_instances`.
CSV `id` values are local identifiers only; generated database ids are resolved
while loading and used for parent/subtask relationships.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_LIST_NAME = "Test data set"
UTC = timezone.utc
WEEKDAY_BY_CODE = {
    "MO": 0,
    "TU": 1,
    "WE": 2,
    "TH": 3,
    "FR": 4,
    "SA": 5,
    "SU": 6,
}


@dataclass(frozen=True)
class SeedRow:
    """One row from `test_data_set.csv`."""

    local_id: str
    title: str
    is_parent: bool
    parent_id: str | None
    start_at: datetime
    due_at: datetime
    size_position: int
    consequence_position: int
    friction_position: int
    rrule: str | None
    number_of_instances: int
    row_number: int


def resolve_default_csv_path() -> Path:
    """Find the requested CSV in the repo's seeder/seeders locations."""

    script_path = Path(__file__).resolve()
    candidates = [
        Path("infra") / "supabase" / "seeders" / "test_data_set.csv",
        Path("infra") / "supabase" / "seeder" / "test_data_set.csv",
        Path("seeder") / "test_data_set.csv",
        Path("seeders") / "test_data_set.csv",
    ]

    for base_path in script_path.parents:
        for relative_path in candidates:
            candidate = base_path / relative_path
            if candidate.exists():
                return candidate

    return script_path.parents[1] / candidates[0]


def clean_text(value: Any) -> str:
    """Strip whitespace and optional single/double quotes from CSV values."""

    cleaned = str(value or "").strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
        cleaned = cleaned[1:-1].strip()
    return cleaned


def clean_optional_text(value: Any) -> str | None:
    """Return None for empty/CSV None-like values."""

    cleaned = clean_text(value)
    if not cleaned or cleaned.lower() in {"none", "null", "nan"}:
        return None
    return cleaned


def parse_int(value: Any, field_name: str, row_number: int) -> int:
    """Parse an integer CSV value with row-aware errors."""

    try:
        return int(clean_text(value))
    except ValueError as error:
        raise ValueError(
            f"CSV row {row_number}: invalid integer for {field_name}: {value!r}"
        ) from error


def parse_bool(value: Any, field_name: str, row_number: int) -> bool:
    """Parse yes/no parent markers."""

    cleaned = clean_text(value).lower()
    if cleaned in {"yes", "y", "true", "1"}:
        return True
    if cleaned in {"no", "n", "false", "0"}:
        return False
    raise ValueError(f"CSV row {row_number}: invalid boolean for {field_name}: {value!r}")


def parse_seed_datetime(value: Any, hour: int, field_name: str, row_number: int) -> datetime:
    """Parse a UTC CSV date and apply the requested hour."""

    cleaned = clean_text(value)
    for date_format in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            parsed_date = datetime.strptime(cleaned, date_format).date()
            return datetime.combine(parsed_date, time(hour, 0), tzinfo=UTC)
        except ValueError:
            continue
    raise ValueError(
        f"CSV row {row_number}: invalid date for {field_name}: {value!r}. "
        "Expected YYYY/MM/DD or YYYY-MM-DD."
    )


def read_seed_rows(csv_path: Path) -> list[SeedRow]:
    """Read and validate the semicolon-delimited test data set."""

    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    rows: list[SeedRow] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file, delimiter=";", quotechar="'")
        required_fields = {
            "id",
            "title",
            "is_parent",
            "parent_id",
            "start_date",
            "due_date",
            "size_weight",
            "consequence_weight",
            "friction_weight",
            "rrule",
            "number_of_instances",
        }
        missing_fields = required_fields.difference(reader.fieldnames or [])
        if missing_fields:
            raise ValueError(f"CSV is missing required fields: {sorted(missing_fields)}")

        for row_number, row in enumerate(reader, start=2):
            if not any(clean_text(value) for value in row.values()):
                continue

            local_id = clean_text(row["id"])
            title = clean_text(row["title"])
            if not local_id:
                raise ValueError(f"CSV row {row_number}: id is required.")
            if not title:
                raise ValueError(f"CSV row {row_number}: title is required.")

            rows.append(
                SeedRow(
                    local_id=local_id,
                    title=title,
                    is_parent=parse_bool(row["is_parent"], "is_parent", row_number),
                    parent_id=clean_optional_text(row["parent_id"]),
                    start_at=parse_seed_datetime(row["start_date"], 8, "start_date", row_number),
                    due_at=parse_seed_datetime(row["due_date"], 18, "due_date", row_number),
                    size_position=parse_int(row["size_weight"], "size_weight", row_number),
                    consequence_position=parse_int(
                        row["consequence_weight"],
                        "consequence_weight",
                        row_number,
                    ),
                    friction_position=parse_int(row["friction_weight"], "friction_weight", row_number),
                    rrule=clean_optional_text(row["rrule"]),
                    number_of_instances=parse_int(
                        row["number_of_instances"],
                        "number_of_instances",
                        row_number,
                    ),
                    row_number=row_number,
                )
            )

    local_ids = [row.local_id for row in rows]
    duplicate_ids = sorted({local_id for local_id in local_ids if local_ids.count(local_id) > 1})
    if duplicate_ids:
        raise ValueError(f"CSV contains duplicate local ids: {duplicate_ids}")

    return rows


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


def authenticate(client: Any, email: str | None = None) -> str:
    """Prompt for credentials and authenticate the Supabase client."""

    resolved_email = email or input("Supabase user email: ").strip()
    password = getpass.getpass("Supabase password: ")
    response = client.auth.sign_in_with_password(
        {
            "email": resolved_email,
            "password": password,
        }
    )
    user = getattr(response, "user", None)
    if user is None:
        raise RuntimeError("Supabase did not return an authenticated user.")
    return str(user.id)


def get_or_create_list(client: Any, list_name: str) -> str:
    """Return the current user's target list id, creating it if missing."""

    response = (
        client.table("lists")
        .select("id")
        .eq("name", list_name)
        .limit(1)
        .execute()
    )
    existing_rows = response.data or []
    if existing_rows:
        return existing_rows[0]["id"]

    insert_response = (
        client.table("lists")
        .insert(
            {
                "name": list_name,
                "description": "Created by scripts/load_test_data_set.py",
            }
        )
        .execute()
    )
    inserted_rows = insert_response.data or []
    if not inserted_rows:
        raise RuntimeError(f"Could not create list {list_name!r}.")
    return inserted_rows[0]["id"]


def fetch_dimension_options(client: Any, table_name: str) -> list[dict[str, Any]]:
    """Fetch lookup values sorted by id."""

    response = client.table(table_name).select("id, weight, label").order("id").execute()
    options = response.data or []
    if not options:
        raise RuntimeError(f"No lookup values found in {table_name}.")
    return options


def resolve_dimension_id(options: list[dict[str, Any]], position: int, dimension_name: str) -> int:
    """Map the CSV 1-based position/weight to the real lookup id."""

    if position < 1 or position > len(options):
        raise ValueError(
            f"{dimension_name} position {position} is outside available values 1..{len(options)}."
        )
    return int(options[position - 1]["id"])


def iso(value: datetime) -> str:
    """Return an ISO timestamp with UTC offset."""

    return value.astimezone(UTC).isoformat()


def build_parent_occurrences(parent: SeedRow) -> list[tuple[int, datetime, datetime]]:
    """Build parent instance windows from the first CSV window and the RRULE."""

    if parent.number_of_instances < 1:
        raise ValueError(
            f"CSV row {parent.row_number}: number_of_instances must be at least 1."
        )
    if parent.due_at < parent.start_at:
        raise ValueError(f"CSV row {parent.row_number}: due_date is before start_date.")
    if parent.number_of_instances > 1 and not parent.rrule:
        raise ValueError(
            f"CSV row {parent.row_number}: rrule is required when number_of_instances > 1."
        )

    duration = parent.due_at - parent.start_at
    starts = [parent.start_at]

    if parent.number_of_instances > 1:
        cursor = parent.start_at
        while len(starts) < parent.number_of_instances:
            next_start = next_rrule_start_after(parent.rrule or "", parent.start_at, cursor)
            starts.append(next_start)
            cursor = next_start

    return [
        (index, start_at, start_at + duration)
        for index, start_at in enumerate(starts, start=1)
    ]


def parse_rrule(rule: str) -> dict[str, str]:
    """Parse the simple RRULE form used by the application."""

    parts: dict[str, str] = {}
    for raw_part in rule.split(";"):
        if not raw_part.strip():
            continue
        if "=" not in raw_part:
            raise ValueError(f"Invalid RRULE part: {raw_part!r}")
        key, value = raw_part.split("=", 1)
        parts[key.strip().upper()] = value.strip().upper()
    if "FREQ" not in parts:
        raise ValueError(f"RRULE must include FREQ: {rule!r}")
    return parts


def next_rrule_start_after(rule: str, dtstart: datetime, after: datetime) -> datetime:
    """Return the next occurrence after `after` for a small RRULE subset."""

    parts = parse_rrule(rule)
    frequency = parts["FREQ"]
    interval = int(parts.get("INTERVAL", "1"))
    if interval < 1:
        raise ValueError(f"RRULE INTERVAL must be >= 1: {rule!r}")

    if frequency == "DAILY":
        candidate = after + timedelta(days=1)
        while True:
            days_since_start = (candidate.date() - dtstart.date()).days
            if days_since_start >= 0 and days_since_start % interval == 0:
                return candidate
            candidate += timedelta(days=1)

    if frequency == "WEEKLY":
        weekday_codes = [
            code.strip()
            for code in parts.get("BYDAY", "").split(",")
            if code.strip()
        ]
        allowed_weekdays = (
            {WEEKDAY_BY_CODE[code] for code in weekday_codes}
            if weekday_codes
            else {dtstart.weekday()}
        )
        candidate = after + timedelta(days=1)
        while True:
            weeks_since_start = (candidate.date() - dtstart.date()).days // 7
            if (
                weeks_since_start >= 0
                and weeks_since_start % interval == 0
                and candidate.weekday() in allowed_weekdays
            ):
                return candidate
            candidate += timedelta(days=1)

    if frequency == "MONTHLY":
        candidate = after + timedelta(days=1)
        while True:
            months_since_start = (
                (candidate.year - dtstart.year) * 12
                + candidate.month
                - dtstart.month
            )
            if (
                months_since_start >= 0
                and months_since_start % interval == 0
                and candidate.day == dtstart.day
            ):
                return candidate
            candidate += timedelta(days=1)

    raise ValueError(f"Unsupported RRULE FREQ={frequency!r}; expected DAILY, WEEKLY, or MONTHLY.")


def insert_task(
    client: Any,
    *,
    list_id: str,
    row: SeedRow,
    parent_task_id: str | None,
    size_id: int,
    consequence_id: int,
    friction_id: int,
) -> str:
    """Insert one task template and return its generated id."""

    payload = {
        "list_id": list_id,
        "title": row.title,
        "description": row.title,
        "parent_task_id": parent_task_id,
        "rrule": row.rrule if parent_task_id is None else None,
        "is_active": True,
        "size_id": size_id,
        "consequence_id": consequence_id,
        "friction_id": friction_id,
        "is_adaptive": True,
    }
    response = client.table("tasks").insert(payload).execute()
    inserted_rows = response.data or []
    if not inserted_rows:
        raise RuntimeError(f"Could not insert task from CSV row {row.row_number}.")
    return inserted_rows[0]["id"]


def insert_instances(client: Any, payloads: list[dict[str, Any]]) -> int:
    """Insert task instance payloads in one request."""

    if not payloads:
        return 0
    client.table("task_instances").insert(payloads).execute()
    return len(payloads)


def load_test_data(
    client: Any,
    rows: list[SeedRow],
    *,
    list_id: str,
    dry_run: bool,
) -> dict[str, int]:
    """Load parent and child tasks plus their instances."""

    size_options = fetch_dimension_options(client, "dim_task_sizes")
    consequence_options = fetch_dimension_options(client, "dim_task_consequences")
    friction_options = fetch_dimension_options(client, "dim_task_frictions")

    rows_by_id = {row.local_id: row for row in rows}
    parent_rows = [row for row in rows if row.is_parent]
    child_rows = [row for row in rows if not row.is_parent]

    for child in child_rows:
        if not child.parent_id or child.parent_id not in rows_by_id:
            raise ValueError(
                f"CSV row {child.row_number}: parent_id {child.parent_id!r} does not exist."
            )
        if not rows_by_id[child.parent_id].is_parent:
            raise ValueError(
                f"CSV row {child.row_number}: parent_id {child.parent_id!r} is not a parent row."
            )

    created_task_ids: dict[str, str] = {}
    parent_instances_by_local_id: dict[str, list[dict[str, Any]]] = {}
    tasks_inserted = 0
    instances_inserted = 0

    for parent in parent_rows:
        size_id = resolve_dimension_id(size_options, parent.size_position, "Size")
        consequence_id = resolve_dimension_id(
            consequence_options,
            parent.consequence_position,
            "Consequence",
        )
        friction_id = resolve_dimension_id(friction_options, parent.friction_position, "Friction")
        occurrences = build_parent_occurrences(parent)

        if dry_run:
            print(f"[dry-run] parent {parent.local_id}: {parent.title} -> {len(occurrences)} instances")
            created_task_ids[parent.local_id] = f"dry-run-task-{parent.local_id}"
            parent_instances_by_local_id[parent.local_id] = [
                {
                    "id": f"dry-run-parent-instance-{parent.local_id}-{instance_number}",
                    "instance_number": instance_number,
                    "start_date": start_at,
                    "due_date": due_at,
                }
                for instance_number, start_at, due_at in occurrences
            ]
            tasks_inserted += 1
            instances_inserted += len(occurrences)
            continue

        parent_task_id = insert_task(
            client,
            list_id=list_id,
            row=parent,
            parent_task_id=None,
            size_id=size_id,
            consequence_id=consequence_id,
            friction_id=friction_id,
        )
        created_task_ids[parent.local_id] = parent_task_id
        tasks_inserted += 1

        parent_instance_payloads = [
            {
                "task_id": parent_task_id,
                "instance_number": instance_number,
                "parent_instance_id": None,
                "start_date": iso(start_at),
                "due_date": iso(due_at),
                "original_start_date": iso(start_at),
                "original_due_date": iso(due_at),
            }
            for instance_number, start_at, due_at in occurrences
        ]
        response = client.table("task_instances").insert(parent_instance_payloads).execute()
        parent_instances_by_local_id[parent.local_id] = response.data or []
        instances_inserted += len(parent_instance_payloads)

    for child in child_rows:
        parent_row = rows_by_id[child.parent_id or ""]
        parent_task_id = created_task_ids[parent_row.local_id]
        parent_instances = parent_instances_by_local_id[parent_row.local_id]
        if not parent_instances:
            raise RuntimeError(f"Parent {parent_row.local_id} has no generated instances.")

        size_id = resolve_dimension_id(size_options, child.size_position, "Size")
        consequence_id = resolve_dimension_id(
            consequence_options,
            child.consequence_position,
            "Consequence",
        )
        friction_id = resolve_dimension_id(friction_options, child.friction_position, "Friction")
        offset = child.start_at - parent_row.start_at
        duration = child.due_at - child.start_at
        if duration.total_seconds() < 0:
            raise ValueError(f"CSV row {child.row_number}: child due_date is before start_date.")

        if dry_run:
            print(
                f"[dry-run] child {child.local_id}: {child.title} -> "
                f"{len(parent_instances)} instances under parent {parent_row.local_id}"
            )
            tasks_inserted += 1
            instances_inserted += len(parent_instances)
            continue

        child_task_id = insert_task(
            client,
            list_id=list_id,
            row=child,
            parent_task_id=parent_task_id,
            size_id=size_id,
            consequence_id=consequence_id,
            friction_id=friction_id,
        )
        created_task_ids[child.local_id] = child_task_id
        tasks_inserted += 1

        child_payloads = []
        for parent_instance in parent_instances:
            parent_start = datetime.fromisoformat(
                str(parent_instance["start_date"]).replace("Z", "+00:00")
            )
            if parent_start.tzinfo is None:
                parent_start = parent_start.replace(tzinfo=UTC)
            child_start = parent_start.astimezone(UTC) + offset
            child_due = child_start + duration
            child_payloads.append(
                {
                    "task_id": child_task_id,
                    "parent_instance_id": parent_instance["id"],
                    "instance_number": int(parent_instance["instance_number"]),
                    "start_date": iso(child_start),
                    "due_date": iso(child_due),
                    "original_start_date": iso(child_start),
                    "original_due_date": iso(child_due),
                }
            )
        instances_inserted += insert_instances(client, child_payloads)

    return {
        "tasks_inserted": tasks_inserted,
        "instances_inserted": instances_inserted,
        "parents_inserted": len(parent_rows),
        "children_inserted": len(child_rows),
    }


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""

    parser = argparse.ArgumentParser(
        description="Load infra/supabase/seeders/test_data_set.csv into Supabase."
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=resolve_default_csv_path(),
        help="Path to test_data_set.csv.",
    )
    parser.add_argument(
        "--email",
        help="Supabase user email. If omitted, the script prompts for it.",
    )
    parser.add_argument(
        "--list-name",
        default=DEFAULT_LIST_NAME,
        help=f"List name to use/create. Default: {DEFAULT_LIST_NAME!r}.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print what would be inserted without writing rows.",
    )
    return parser.parse_args()


def main() -> None:
    """Run the loader."""

    args = parse_args()
    rows = read_seed_rows(args.csv)
    client = build_client()
    user_id = authenticate(client, args.email)
    list_id = "(dry-run)" if args.dry_run else get_or_create_list(client, args.list_name)

    print(f"Authenticated user: {user_id}")
    print(f"CSV: {args.csv}")
    print(f"List: {args.list_name} ({list_id})")

    result = load_test_data(client, rows, list_id=list_id, dry_run=args.dry_run)
    print(
        "Load complete: "
        f"parents={result['parents_inserted']}, "
        f"children={result['children_inserted']}, "
        f"tasks={result['tasks_inserted']}, "
        f"instances={result['instances_inserted']}."
    )


if __name__ == "__main__":
    main()
