#!/usr/bin/env python3
"""Load the task test set for one authenticated Supabase user.

This script intentionally stops after the two data-loading stages:

1. Load the simple-task CSV test set.
2. Load the recurrent/composite task CSV test set.

Scheduler invocation and stale-marking were split into a separate helper so the
seed-loading workflow stays focused on creating test data only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Reuse the existing helpers instead of duplicating authentication, CSV
# parsing, and status-update logic in yet another test utility.
from app.application.use_cases import task_test_seeder
import load_recurrent_testset
import load_simple_tasks_testset


DEFAULT_LIST_NAME = "my list"


def parse_args() -> argparse.Namespace:
    """Parse command-line options for the combined test-data workflow."""

    parser = argparse.ArgumentParser(
        description=(
            "Load the task dataset for one Supabase user: simple tasks first, "
            "then recurrent/composite tasks."
        )
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
        help="Supabase user password. If omitted, the script prompts for it once.",
    )
    parser.add_argument(
        "--simple-csv",
        type=Path,
        default=task_test_seeder.SEED_TASKS_CSV_PATH,
        help="Path to the semicolon-delimited simple tasks CSV.",
    )
    parser.add_argument(
        "--recurrent-csv",
        type=Path,
        default=load_recurrent_testset.resolve_default_csv_path(),
        help="Path to the recurrent/composite tasks CSV.",
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
        type=load_simple_tasks_testset.parse_bool,
        help=(
            "Delete the authenticated user's existing non-recurring tasks before "
            "loading the simple-task CSV. Default: true."
        ),
    )
    parser.add_argument(
        "--no-from_scratch",
        "--no-from-scratch",
        dest="from_scratch",
        action="store_false",
        help="Keep existing non-recurring tasks before loading the simple-task CSV.",
    )
    parser.add_argument(
        "--rrule-instances",
        type=int,
        default=None,
        help=(
            "Override number_of_instances for recurrent root tasks with RRULE "
            "(compound parents and standalone simple tasks)."
        ),
    )
    parser.add_argument(
        "--recurrent-dry-run",
        action="store_true",
        help="Validate and print recurrent inserts without writing them.",
    )

    args = parser.parse_args()
    if args.rrule_instances is not None and args.rrule_instances < 1:
        parser.error("--rrule-instances must be at least 1.")
    return args


def print_stage_header(stage_number: int, label: str) -> None:
    """Print a compact visual separator before each stage."""

    print()
    print(f"=== Stage {stage_number}: {label} ===")


def load_simple_stage(client, *, list_id: str, args: argparse.Namespace) -> dict[str, int]:
    """Run the simple-task loader stage.

    This stage mirrors `load_simple_tasks_testset.py`. Its writes are complete
    and durable before the next stage begins, which is the checkpoint the user
    requested when describing "commit transaction" after the simple load.
    """

    result = task_test_seeder.seed_current_user_test_tasks(
        client,
        list_id=list_id,
        size_options=load_simple_tasks_testset.fetch_dimension_options(client, "dim_task_sizes"),
        consequence_options=load_simple_tasks_testset.fetch_dimension_options(
            client,
            "dim_task_consequences",
        ),
        friction_options=load_simple_tasks_testset.fetch_dimension_options(client, "dim_task_frictions"),
        csv_path=args.simple_csv,
        from_scratch=args.from_scratch,
    )
    print(
        "Simple stage committed: "
        f"deleted={result['deleted_count']}, inserted={result['inserted_count']}."
    )
    return result


def load_recurrent_stage(client, *, list_id: str, args: argparse.Namespace) -> dict[str, int]:
    """Run the recurrent/composite-task loader stage.

    This stage reuses the existing recurrent loader so parent/child task
    creation, instance generation, and RRULE expansion stay aligned with the
    rest of the project.
    """

    rows = load_recurrent_testset.read_seed_rows(args.recurrent_csv)
    result = load_recurrent_testset.load_test_data(
        client,
        rows,
        list_id=list_id,
        dry_run=args.recurrent_dry_run,
        rrule_instances=args.rrule_instances,
    )
    if args.recurrent_dry_run:
        print(
            "Recurrent stage dry run complete: "
            f"parents={result['parents_inserted']}, "
            f"simple={result['simple_tasks_inserted']}, "
            f"children={result['children_inserted']}, "
            f"tasks={result['tasks_inserted']}, "
            f"instances={result['instances_inserted']}."
        )
    else:
        print(
            "Recurrent stage committed: "
            f"parents={result['parents_inserted']}, "
            f"simple={result['simple_tasks_inserted']}, "
            f"children={result['children_inserted']}, "
            f"tasks={result['tasks_inserted']}, "
            f"instances={result['instances_inserted']}."
        )
    return result


def main() -> None:
    """Run the two-stage test-data loading workflow."""

    args = parse_args()
    client = load_simple_tasks_testset.build_client()
    user_id = load_simple_tasks_testset.authenticate(client, args.email, args.password)
    target_list = load_simple_tasks_testset.get_or_create_list(client, args.list_name)

    print(f"Authenticated user: {user_id}")
    print(f"List: {target_list['name']} ({target_list['id']})")
    print(f"Simple CSV: {args.simple_csv}")
    print(f"Recurrent CSV: {args.recurrent_csv}")
    print(f"From scratch: {args.from_scratch}")
    print(f"Recurrent dry run: {args.recurrent_dry_run}")

    print_stage_header(1, "Load simple task test set")
    load_simple_stage(client, list_id=target_list["id"], args=args)

    print_stage_header(2, "Load recurrent task test set")
    load_recurrent_stage(client, list_id=target_list["id"], args=args)

    print()
    print("Test-data loading workflow complete.")


if __name__ == "__main__":
    main()
