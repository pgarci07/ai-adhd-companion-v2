from __future__ import annotations

import pandas as pd

from app.application.adaptive import task_adaptation
from app.application.use_cases import active_task_grid


def make_task_row(
    *,
    instance_id: str,
    task_id: str,
    title: str,
    description: str = "",
    status: str = "ready",
    is_routine: bool = False,
    has_subtasks: bool = False,
    parent_task_id: str | None = None,
    parent_instance_id: str | None = None,
    wobj: int = 1,
    wsub: int = 1,
    due_date: str = "2026-05-20T09:00:00+00:00",
) -> dict:
    """Create one compact enriched-task row for active-grid tests."""

    return {
        "instance_id": instance_id,
        "task_id": task_id,
        "title": title,
        "description": description,
        "status": status,
        "is_routine": is_routine,
        "has_subtasks": has_subtasks,
        "parent_task_id": parent_task_id,
        "parent_instance_id": parent_instance_id,
        "WOBJ": wobj,
        "WSUB": wsub,
        "due_date": due_date,
    }


def make_auto_open_adaptation(sort_rule_name: str = "wobj_desc") -> task_adaptation.TaskAdaptation:
    """Create a minimal adaptation that enables guided-open behaviour."""

    return task_adaptation.TaskAdaptation(
        persona_name=task_adaptation.HYPER_FOCUSED_PERSONA,
        state_name=task_adaptation.ENGAGED_STATE,
        sort_rule=task_adaptation.SORT_RULES[sort_rule_name],
        auto_open_first_task=True,
    )


def test_build_my_tasks_active_grid_for_tasks_excludes_routines_and_orders_by_adaptation():
    tasks_df = pd.DataFrame(
        [
            make_task_row(instance_id="i1", task_id="t1", title="Low", wobj=2),
            make_task_row(instance_id="i2", task_id="t2", title="High", wobj=8),
            make_task_row(instance_id="i3", task_id="t3", title="Routine", is_routine=True, wobj=10),
        ]
    )

    active_grid = active_task_grid.build_my_tasks_active_grid(
        tasks_df,
        make_auto_open_adaptation("wobj_desc"),
        show_routines=False,
        show_completed_tasks=False,
        completed_instance_ids=set(),
        never_visible_statuses={"stale"},
        active_statuses={"ready", "asleep", "debt", "open"},
    )

    assert active_grid.grid_kind == "tasks"
    assert active_grid.visible_df["title"].tolist() == ["High", "Low"]
    assert active_grid.workable_df["title"].tolist() == ["High", "Low"]


def test_build_my_tasks_active_grid_for_periodic_uses_routine_rows_only():
    tasks_df = pd.DataFrame(
        [
            make_task_row(instance_id="i1", task_id="t1", title="Action", is_routine=False, wsub=5),
            make_task_row(instance_id="i2", task_id="t2", title="Routine A", is_routine=True, wsub=3),
            make_task_row(instance_id="i3", task_id="t3", title="Routine B", is_routine=True, wsub=1),
        ]
    )

    active_grid = active_task_grid.build_my_tasks_active_grid(
        tasks_df,
        make_auto_open_adaptation("wsub_asc"),
        show_routines=True,
        show_completed_tasks=False,
        completed_instance_ids=set(),
        never_visible_statuses={"stale"},
        active_statuses={"ready", "asleep", "debt", "open"},
    )

    assert active_grid.grid_kind == "periodic"
    assert active_grid.visible_df["title"].tolist() == ["Routine B", "Routine A"]


def test_build_task_search_active_grid_filters_search_and_respects_include_stale():
    tasks_df = pd.DataFrame(
        [
            make_task_row(instance_id="i1", task_id="t1", title="Mars notes"),
            make_task_row(instance_id="i2", task_id="t2", title="Mars archive", status="stale"),
            make_task_row(instance_id="i3", task_id="t3", title="Bath tap"),
        ]
    )

    active_grid = active_task_grid.build_task_search_active_grid(
        tasks_df,
        make_auto_open_adaptation("wobj_desc"),
        search_query="mars",
        include_routines=True,
        include_stale=False,
        never_visible_statuses={"stale"},
    )

    assert active_grid.grid_kind == "search_results"
    assert active_grid.visible_df["title"].tolist() == ["Mars notes"]


def test_filter_workable_rows_excludes_completed_and_compound_rows():
    tasks_df = pd.DataFrame(
        [
            make_task_row(instance_id="i1", task_id="t1", title="Ready", status="ready"),
            make_task_row(instance_id="i2", task_id="t2", title="Completed", status="completed"),
            make_task_row(instance_id="i3", task_id="t3", title="Compound", has_subtasks=True, status="ready"),
        ]
    )

    workable_df = active_task_grid.filter_workable_rows(tasks_df)

    assert workable_df["title"].tolist() == ["Ready"]


def test_get_next_open_candidate_returns_first_root_candidate_in_sorted_order():
    visible_df = pd.DataFrame(
        [
            make_task_row(instance_id="i1", task_id="t1", title="Lower", wobj=3),
            make_task_row(instance_id="i2", task_id="t2", title="Higher", wobj=9),
        ]
    )
    visible_df = task_adaptation.sort_tasks_for_intervention(
        visible_df,
        make_auto_open_adaptation("wobj_desc"),
    )
    root_df, subtasks_df = active_task_grid.split_root_tasks_and_subtasks(visible_df)
    grid = active_task_grid.ActiveTaskGrid(
        grid_kind="tasks",
        page_name="tasks",
        visible_df=visible_df,
        workable_df=active_task_grid.filter_workable_rows(visible_df),
        root_df=root_df,
        subtasks_df=subtasks_df,
        adaptation=make_auto_open_adaptation("wobj_desc"),
    )

    candidate = active_task_grid.get_next_open_candidate(grid)

    assert candidate["title"] == "Higher"
    assert candidate["guided_parent_task_id"] is None


def test_get_next_open_candidate_skips_already_offered_instances():
    visible_df = pd.DataFrame(
        [
            make_task_row(instance_id="i1", task_id="t1", title="First", wobj=9),
            make_task_row(instance_id="i2", task_id="t2", title="Second", wobj=4),
        ]
    )
    visible_df = task_adaptation.sort_tasks_for_intervention(
        visible_df,
        make_auto_open_adaptation("wobj_desc"),
    )
    root_df, subtasks_df = active_task_grid.split_root_tasks_and_subtasks(visible_df)
    grid = active_task_grid.ActiveTaskGrid(
        grid_kind="tasks",
        page_name="tasks",
        visible_df=visible_df,
        workable_df=active_task_grid.filter_workable_rows(visible_df),
        root_df=root_df,
        subtasks_df=subtasks_df,
        adaptation=make_auto_open_adaptation("wobj_desc"),
    )

    candidate = active_task_grid.get_next_open_candidate(
        grid,
        offered_instance_ids={"i1"},
    )

    assert candidate["title"] == "Second"


def test_get_next_open_candidate_enters_compound_parent_and_returns_first_child():
    visible_df = pd.DataFrame(
        [
            make_task_row(
                instance_id="p1",
                task_id="parent-1",
                title="Parent",
                has_subtasks=True,
                wobj=10,
            ),
            make_task_row(
                instance_id="c1",
                task_id="child-1",
                title="Child low",
                parent_task_id="parent-1",
                parent_instance_id="p1",
                wobj=2,
            ),
            make_task_row(
                instance_id="c2",
                task_id="child-2",
                title="Child high",
                parent_task_id="parent-1",
                parent_instance_id="p1",
                wobj=9,
            ),
        ]
    )
    visible_df = task_adaptation.sort_tasks_for_intervention(
        visible_df,
        make_auto_open_adaptation("wobj_desc"),
    )
    root_df, subtasks_df = active_task_grid.split_root_tasks_and_subtasks(visible_df)
    grid = active_task_grid.ActiveTaskGrid(
        grid_kind="tasks",
        page_name="tasks",
        visible_df=visible_df,
        workable_df=active_task_grid.filter_workable_rows(visible_df),
        root_df=root_df,
        subtasks_df=subtasks_df,
        adaptation=make_auto_open_adaptation("wobj_desc"),
    )

    candidate = active_task_grid.get_next_open_candidate(grid)

    assert candidate["title"] == "Child high"
    assert candidate["guided_parent_task_id"] == "parent-1"


def test_get_next_open_candidate_returns_none_when_search_results_only_show_final_statuses():
    active_grid = active_task_grid.build_task_search_active_grid(
        pd.DataFrame(
            [
                make_task_row(instance_id="i1", task_id="t1", title="Done task", status="completed"),
                make_task_row(instance_id="i2", task_id="t2", title="Old stale", status="stale"),
            ]
        ),
        make_auto_open_adaptation("wobj_desc"),
        search_query="task",
        include_routines=True,
        include_stale=True,
        never_visible_statuses={"stale"},
    )

    assert active_task_grid.get_next_open_candidate(active_grid) is None
