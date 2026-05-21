"""Development utility for replacing the current user's non-recurring tasks.

The seeder reads a CSV file with relative date offsets and inserts tasks through
the normal `create_task_and_instances` RPC so the database side effects remain
consistent with the rest of the application.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import csv


def _resolve_seed_tasks_csv_path() -> Path:
    """Locate the seed CSV by walking up the filesystem until the repo layout matches.

    This avoids hard-coding a fixed `parents[n]` depth, which can differ between
    the local workspace and the Docker image layout.
    """

    module_path = Path(__file__).resolve()
    relative_seed_path = Path("infra") / "supabase" / "seeders" / "tasks.csv"

    for base_path in module_path.parents:
        candidate_path = base_path / relative_seed_path
        if candidate_path.exists():
            return candidate_path

    # Fall back to the most likely repository-root shape to keep the error message useful.
    return module_path.parents[3] / relative_seed_path

# CSV file that defines the development tasks to be loaded into the database.
SEED_TASKS_CSV_PATH = _resolve_seed_tasks_csv_path()


@dataclass(frozen=True)
class TaskSeedRecord:
    """One seed task parsed from the CSV file."""

    title: str
    description: str | None
    start_offset_days: int
    due_offset_days: int
    size_position: int
    consequence_position: int
    friction_position: int


def _parse_seed_int(raw_value: str, field_name: str, row_number: int) -> int:
    """Parse an integer field from the CSV and raise a descriptive error if invalid."""

    try:
        return int(str(raw_value).strip())
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"Invalid integer value for '{field_name}' on CSV row {row_number}: {raw_value!r}"
        ) from error


def read_task_seed_records(csv_path: Path = SEED_TASKS_CSV_PATH) -> list[TaskSeedRecord]:
    """Load task seed records from the semicolon-delimited CSV file.

    Negative date offsets are supported intentionally so development seeds can
    create historical tasks in the past as well as future-dated ones.
    """

    if not csv_path.exists():
        raise FileNotFoundError(f"Seed file not found: {csv_path}")

    records: list[TaskSeedRecord] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.reader(csv_file, delimiter=";")
        next(reader, None)
        for row_number, row in enumerate(reader, start=2):
            if not row or not any(str(cell).strip() for cell in row):
                continue
            if len(row) < 7:
                raise ValueError(
                    f"CSV row {row_number} must contain 7 fields, found {len(row)}."
                )

            title = str(row[0]).strip()
            if not title:
                raise ValueError(f"CSV row {row_number} must include a task title.")

            records.append(
                TaskSeedRecord(
                    title=title,
                    description=str(row[1]).strip() or None,
                    start_offset_days=_parse_seed_int(row[2], "start_date", row_number),
                    due_offset_days=_parse_seed_int(row[3], "due_date", row_number),
                    size_position=_parse_seed_int(row[4], "size_weight", row_number),
                    consequence_position=_parse_seed_int(row[5], "consequence_weight", row_number),
                    friction_position=_parse_seed_int(row[6], "friction_weight", row_number),
                )
            )
    return records


def _resolve_dimension_id(options: list[dict], ordinal_position: int, dimension_name: str) -> int:
    """Map the CSV 1-based ordinal into the real lookup-table id sorted by id."""

    if ordinal_position < 1:
        raise ValueError(f"{dimension_name} positions must start at 1.")
    if ordinal_position > len(options):
        raise ValueError(
            f"{dimension_name} position {ordinal_position} exceeds the available lookup values ({len(options)})."
        )
    return int(options[ordinal_position - 1]["id"])


def _build_seed_datetime(base_date: date, offset_days: int, hour_value: int) -> str:
    """Build the UTC ISO timestamp used by the task-creation RPC.

    `offset_days` may be negative when a seed row is meant to land in the past.
    """

    target_date = base_date + timedelta(days=offset_days)
    target_datetime = datetime.combine(target_date, time(hour_value, 0)).replace(tzinfo=timezone.utc)
    return target_datetime.isoformat()


def delete_current_user_non_recurring_tasks(supabase_client) -> int:
    """Delete the current user's tasks whose `rrule` is null.

    The deletion is done task by task so the function remains compatible with
    RLS. We remove dependent rows explicitly because the development database
    currently keeps foreign keys from `task_instances` and status logs.
    """

    response = (
        supabase_client.table("tasks")
        .select("id")
        .is_("rrule", "null")
        .execute()
    )
    task_ids = [row["id"] for row in response.data or [] if row.get("id")]

    if not task_ids:
        return 0

    instance_response = (
        supabase_client.table("task_instances")
        .select("id, task_id")
        .in_("task_id", task_ids)
        .execute()
    )
    instance_ids = [row["id"] for row in instance_response.data or [] if row.get("id")]

    if instance_ids:
        # Status log rows must go first because they reference task instances directly.
        supabase_client.table("task_instance_status_log").delete().in_(
            "instance_changed_id",
            instance_ids,
        ).execute()

        # Remove instances before deleting their parent tasks.
        supabase_client.table("task_instances").delete().in_("id", instance_ids).execute()

    # Parent tasks can be removed once all dependent instances are gone.
    supabase_client.table("tasks").delete().in_("id", task_ids).execute()

    return len(task_ids)


def seed_current_user_test_tasks(
    supabase_client,
    *,
    list_id: str,
    size_options: list[dict],
    consequence_options: list[dict],
    friction_options: list[dict],
    csv_path: Path = SEED_TASKS_CSV_PATH,
    today_date: date | None = None,
    from_scratch: bool = True,
) -> dict[str, int]:
    """Load the CSV seed set for the current user.

    When `from_scratch` is true, existing non-recurring tasks for the
    authenticated user are deleted before the CSV rows are inserted. Recurring
    tasks are intentionally left untouched.
    """

    records = read_task_seed_records(csv_path)
    base_date = today_date or datetime.now(timezone.utc).date()
    deleted_count = (
        delete_current_user_non_recurring_tasks(supabase_client)
        if from_scratch
        else 0
    )
    inserted_count = 0

    for record in records:
        size_id = _resolve_dimension_id(size_options, record.size_position, "Size")
        consequence_id = _resolve_dimension_id(
            consequence_options,
            record.consequence_position,
            "Consequence",
        )
        friction_id = _resolve_dimension_id(
            friction_options,
            record.friction_position,
            "Friction",
        )

        supabase_client.rpc(
            "create_task_and_instances",
            {
                "p_list_id": list_id,
                "p_title": record.title,
                "p_description": record.description,
                "p_start_date": _build_seed_datetime(base_date, record.start_offset_days, 8),
                "p_due_date": _build_seed_datetime(base_date, record.due_offset_days, 18),
                "p_parent_task_id": None,
                "p_parent_instance_number": 1,
                "p_rrule": None,
                "p_size_id": size_id,
                "p_consequence_id": consequence_id,
                "p_friction_id": friction_id,
            },
        ).execute()
        inserted_count += 1

    return {
        "deleted_count": deleted_count,
        "inserted_count": inserted_count,
    }
