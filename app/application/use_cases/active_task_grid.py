"""Business helpers for the active task grid used by guided task opening.

This module keeps the "which grid is active right now?" decision outside the
Streamlit page functions. The UI still renders dataframes, but guided open
flows can ask one small business object for:

- the currently visible rows,
- the subset that is really workable,
- and the next candidate to propose for opening.

The key design choice is that proposals always come from the *active grid* that
the user is currently working with, not from an unrelated dataframe rebuilt
elsewhere with different filters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd

from app.application.adaptive import task_adaptation


WORKABLE_STATUSES = ("ready", "asleep", "debt")


@dataclass(frozen=True)
class ActiveTaskGrid:
    """Describe the task grid that currently owns guided-open decisions.

    ``visible_df`` is exactly the ordered set of rows the user is conceptually
    working with on the current page.

    ``workable_df`` is a stricter subset used by task-opening proposals. It
    intentionally excludes rows that may be visible for informational reasons
    but should never be opened for work, such as completed or stale tasks.
    """

    grid_kind: str
    page_name: str
    visible_df: pd.DataFrame
    workable_df: pd.DataFrame
    root_df: pd.DataFrame
    subtasks_df: pd.DataFrame
    adaptation: task_adaptation.TaskAdaptation | None


def build_subtasks_active_grid(
    visible_subtasks_df: pd.DataFrame,
    adaptation: task_adaptation.TaskAdaptation | None,
) -> ActiveTaskGrid:
    """Build the active grid for a visible subtasks secondary grid.

    Once the user is explicitly working inside a parent's child grid, guided
    open proposals should come from that visible ordered child list rather than
    jumping back to the root grid above it.
    """

    visible_df = task_adaptation.sort_tasks_for_intervention(
        visible_subtasks_df.reset_index(drop=True),
        adaptation,
    )
    return ActiveTaskGrid(
        grid_kind="subtasks",
        page_name="tasks",
        visible_df=visible_df,
        workable_df=filter_workable_rows(visible_df),
        root_df=visible_df,
        subtasks_df=visible_df.iloc[0:0].copy(),
        adaptation=adaptation,
    )


def split_root_tasks_and_subtasks(tasks_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split one enriched task dataframe into root tasks and child tasks.

    Parent rows and child rows have different UI behaviours and different
    candidate-selection semantics. Keeping the split explicit in the business
    layer avoids re-deriving it ad hoc in every caller.
    """

    if tasks_df.empty:
        return tasks_df.copy(), tasks_df.copy()

    root_tasks_df = tasks_df[tasks_df["parent_task_id"].isna()].reset_index(drop=True)
    subtasks_df = tasks_df[tasks_df["parent_task_id"].notna()].reset_index(drop=True)
    return root_tasks_df, subtasks_df


def filter_workable_rows(tasks_df: pd.DataFrame) -> pd.DataFrame:
    """Return only rows that may be proposed for real work.

    The active grid can contain rows that are valid to display but not valid to
    open. Guided open flows therefore always work from this filtered subset.
    Compound rows remain visible in ``visible_df`` but are excluded here because
    the open candidate must be an executable task instance.
    """

    if tasks_df.empty:
        return tasks_df.copy()

    workable_df = tasks_df[
        (~tasks_df["has_subtasks"])
        & (tasks_df["status"].isin(WORKABLE_STATUSES))
    ].reset_index(drop=True)
    return workable_df


def build_my_tasks_active_grid(
    tasks_df: pd.DataFrame,
    adaptation: task_adaptation.TaskAdaptation | None,
    *,
    show_routines: bool,
    show_completed_tasks: bool,
    completed_instance_ids: Iterable[str] | None,
    never_visible_statuses: Iterable[str],
    active_statuses: Iterable[str],
) -> ActiveTaskGrid:
    """Build the active grid for the My Tasks page.

    This builder mirrors the page filters, then applies the relevant adaptation
    ordering. The same ordered result is reused both for rendering and for
    guided-open proposals, which keeps the visible ranking and the candidate
    ranking aligned.
    """

    visible_df = tasks_df[
        (tasks_df["is_routine"] == show_routines)
        & (~tasks_df["status"].isin(tuple(never_visible_statuses)))
    ].reset_index(drop=True)

    if show_completed_tasks:
        completed_instance_ids = set(completed_instance_ids or [])
        visible_df = visible_df[
            (visible_df["status"] == "completed")
            & (visible_df["instance_id"].isin(completed_instance_ids))
        ].reset_index(drop=True)
    else:
        visible_df = visible_df[
            visible_df["status"].isin(tuple(active_statuses))
        ].reset_index(drop=True)

    visible_df = task_adaptation.sort_tasks_for_intervention(
        visible_df,
        adaptation,
    )
    root_df, subtasks_df = split_root_tasks_and_subtasks(visible_df)

    return ActiveTaskGrid(
        grid_kind="periodic" if show_routines else "tasks",
        page_name="tasks",
        visible_df=visible_df,
        workable_df=filter_workable_rows(visible_df),
        root_df=root_df,
        subtasks_df=subtasks_df,
        adaptation=adaptation,
    )


def build_task_search_active_grid(
    tasks_df: pd.DataFrame,
    adaptation: task_adaptation.TaskAdaptation | None,
    *,
    search_query: str,
    include_routines: bool,
    include_stale: bool,
    never_visible_statuses: Iterable[str],
) -> ActiveTaskGrid:
    """Build the active grid for the Task Search page.

    Search results are still a real working surface. The user may be looking at
    a filtered slice of the data, but guided open flows should still propose
    the next workable task from that visible search result set.
    """

    visible_df = tasks_df.copy()
    if not include_stale:
        visible_df = visible_df[
            ~visible_df["status"].isin(tuple(never_visible_statuses))
        ].reset_index(drop=True)
    if not include_routines:
        visible_df = visible_df[
            ~visible_df["is_routine"]
        ].reset_index(drop=True)

    search_text = str(search_query or "").strip()
    if search_text:
        search_mask = (
            visible_df["title"].fillna("").str.contains(search_text, case=False, regex=False)
            | visible_df["description"].fillna("").str.contains(search_text, case=False, regex=False)
        )
        visible_df = visible_df[search_mask].reset_index(drop=True)

    visible_df = task_adaptation.sort_tasks_for_intervention(
        visible_df,
        adaptation,
    )
    root_df, subtasks_df = split_root_tasks_and_subtasks(visible_df)

    return ActiveTaskGrid(
        grid_kind="search_results",
        page_name="task_search",
        visible_df=visible_df,
        workable_df=filter_workable_rows(visible_df),
        root_df=root_df,
        subtasks_df=subtasks_df,
        adaptation=adaptation,
    )


def get_child_tasks_for_parent_instance(subtasks_df: pd.DataFrame, parent_row: dict) -> pd.DataFrame:
    """Return child-task rows that belong to one concrete parent instance.

    Guided open must not merge children from different recurrences of the same
    parent task. Matching both ``parent_task_id`` and ``parent_instance_id``
    keeps the child candidate list tied to the exact visible parent instance.
    """

    parent_task_id = parent_row.get("task_id")
    parent_instance_id = parent_row.get("instance_id")
    if not parent_task_id or not parent_instance_id:
        return subtasks_df.iloc[0:0].copy()

    child_tasks_df = subtasks_df[
        (subtasks_df["parent_task_id"] == parent_task_id)
        & (subtasks_df["parent_instance_id"] == parent_instance_id)
    ].reset_index(drop=True)
    return child_tasks_df


def get_next_open_candidate(
    active_grid: ActiveTaskGrid,
    *,
    offered_instance_ids: Iterable[str] | None = None,
) -> dict | None:
    """Return the next guided-open candidate from the active grid.

    The search walks the *visible* root ordering first so parent containers
    remain part of the ranking. When a visible root row is a compound task, the
    candidate becomes the first workable child in that parent instance.
    """

    if active_grid.adaptation is None or not active_grid.adaptation.auto_open_first_task:
        return None
    if active_grid.grid_kind == "subtasks":
        if active_grid.workable_df.empty:
            return None
        workable_df = active_grid.workable_df
        offered_instance_ids = {
            str(instance_id)
            for instance_id in (offered_instance_ids or [])
            if instance_id
        }
        if offered_instance_ids:
            workable_df = workable_df[
                ~workable_df["instance_id"].astype(str).isin(offered_instance_ids)
            ].reset_index(drop=True)
        if workable_df.empty:
            return None
        target_task = workable_df.iloc[0].to_dict()
        target_task["guided_parent_task_id"] = target_task.get("parent_task_id")
        return target_task

    if active_grid.root_df.empty:
        return None

    offered_instance_ids = {
        str(instance_id)
        for instance_id in (offered_instance_ids or [])
        if instance_id
    }

    for _, root_row in active_grid.root_df.iterrows():
        if bool(root_row.get("has_subtasks")):
            child_tasks_df = get_child_tasks_for_parent_instance(
                active_grid.subtasks_df,
                root_row.to_dict(),
            )
            child_tasks_df = task_adaptation.sort_tasks_for_intervention(
                child_tasks_df,
                active_grid.adaptation,
            )
            child_tasks_df = filter_workable_rows(child_tasks_df)
            if offered_instance_ids:
                child_tasks_df = child_tasks_df[
                    ~child_tasks_df["instance_id"].astype(str).isin(offered_instance_ids)
                ].reset_index(drop=True)
            if child_tasks_df.empty:
                continue

            target_task = child_tasks_df.iloc[0].to_dict()
            target_task["guided_parent_task_id"] = root_row["task_id"]
            return target_task

        if root_row.get("status") not in WORKABLE_STATUSES:
            continue
        if str(root_row.get("instance_id")) in offered_instance_ids:
            continue
        target_task = root_row.to_dict()
        target_task["guided_parent_task_id"] = None
        return target_task

    return None
