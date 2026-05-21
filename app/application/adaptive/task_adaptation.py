"""Adaptive task-ranking rules driven by persona and user state.

This module keeps the adaptation matrix separate from the Streamlit UI so the
behaviour can evolve without pushing more decision logic into ``main.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil
from typing import Any


# Canonical persona names expected from the personas catalogue and profile data.
PROCRASTINATOR_PERSONA = "procrastinator"
HYPER_FOCUSED_PERSONA = "hyper-focused"
OVERWHELMED_PLANNER_PERSONA = "overwhelmed planner"

# Canonical user-state names used by the adaptive matrix.
FROZEN_STATE = "Frozen"
ENGAGED_STATE = "Engaged"
PLANNER_STATE = "Planner"
RECOVERY_STATE = "Recovery"

# Map recognised persona labels to the canonical names used by the adaptive matrix. 
PERSONA_NAME_ALIASES = {
    "procrastinator": PROCRASTINATOR_PERSONA,
    "hyper-focused": HYPER_FOCUSED_PERSONA,
    "overwhelmed planner": OVERWHELMED_PLANNER_PERSONA,
}

# State names are already stable, but normalising them keeps matching resilient
# to accidental whitespace differences.
STATE_NAME_ALIASES = {
    "frozen": FROZEN_STATE,
    "engaged": ENGAGED_STATE,
    "planner": PLANNER_STATE,
    "recovery": RECOVERY_STATE,
}


@dataclass(frozen=True)
class TaskSortRule:
    """Describe how the task grid should be sorted for one adaptation rule."""

    fields: tuple[str, ...]
    ascending: tuple[bool, ...]
    label: str


@dataclass(frozen=True)
class TaskAdaptation:
    """Bundle the UI and behavioural adjustments for a persona/state pair."""

    persona_name: str | None
    state_name: str | None
    max_visible_tasks: int | None = None
    sort_rule: TaskSortRule | None = None
    message_style: str | None = None
    message_intensity: str | None = None
    guidance_message_id: str | None = None
    parameter_settings: dict[str, int] = field(default_factory=dict)
    auto_open_first_task: bool = False
    default_body_doubling: bool | None = None
    default_pomodoro: bool | None = None
    force_body_doubling_pomodoro_timing: bool = False
    opaque_dialog_overlay: bool = False
    opaque_guided_pomodoro_overlay: bool = False
    cancel_needs_confirmation: bool = False
    protect_rest_breaks_with_messages: bool = False
    warn_if_open_outside_top_twenty_percent: bool = False
    planner_open_target_state: str | None = None
    planner_timeout_message_id: str | None = None


# Reusable numeric ordering strategies referenced by the adaptation matrix.
SORT_RULES = {
    "wsub_asc": TaskSortRule(
        fields=("WSUB", "due_date"),
        ascending=(True, True),
        label="WSUB ascending",
    ),
    "wobj_desc": TaskSortRule(
        fields=("WOBJ", "due_date"),
        ascending=(False, True),
        label="WOBJ descending",
    ),
    "wobj_wsub_desc": TaskSortRule(
        fields=("WOBJ", "WSUB", "due_date"),
        ascending=(False, False, True),
        label="WOBJ / WSUB descending",
    ),
}


def choose_intervention(user_profile: str | None, user_state: str | None, context: dict[str, Any] | None = None) -> TaskAdaptation | None:
    """Return the adaptation rule that matches the current persona/state pair."""

    context = context or {}
    persona_name = PERSONA_NAME_ALIASES.get(str(user_profile or "").strip().lower(), user_profile)
    state_name = STATE_NAME_ALIASES.get(str(user_state or "").strip().lower(), user_state)
    memory_state = context.get("memory_state")
    recent_state_sequence = tuple(context.get("recent_state_sequence") or ())
    pending_due_today_count = int(context.get("pending_due_today_count", 0) or 0)

    if persona_name == PROCRASTINATOR_PERSONA and state_name == FROZEN_STATE:
        return TaskAdaptation(
            persona_name=persona_name,
            state_name=state_name,
            max_visible_tasks=3,
            sort_rule=SORT_RULES["wsub_asc"],
            message_style="very_soft",
            message_intensity="medium",
            guidance_message_id="ADAPTIVE_PROCRASTINATOR_FROZEN_GUIDANCE",
            auto_open_first_task=True,
            default_body_doubling=True,
            default_pomodoro=False,
        )

    if persona_name == PROCRASTINATOR_PERSONA and state_name == ENGAGED_STATE:
        return TaskAdaptation(
            persona_name=persona_name,
            state_name=state_name,
            max_visible_tasks=5,
            sort_rule=SORT_RULES["wobj_wsub_desc"],
            message_style="neutral_positive",
            message_intensity="low",
            guidance_message_id="ADAPTIVE_PROCRASTINATOR_ENGAGED_GUIDANCE",
            auto_open_first_task=True,
            default_body_doubling=False,
            default_pomodoro=True,
        )

    if persona_name == PROCRASTINATOR_PERSONA and state_name == PLANNER_STATE:
        planner_open_target_state = ENGAGED_STATE if memory_state == ENGAGED_STATE else FROZEN_STATE
        return TaskAdaptation(
            persona_name=persona_name,
            state_name=state_name,
            max_visible_tasks=1,
            sort_rule=SORT_RULES["wsub_asc"],
            message_style="direct_kind",
            message_intensity="low",
            guidance_message_id="ADAPTIVE_PROCRASTINATOR_PLANNER_GUIDANCE",
            parameter_settings={"Y": 1},
            planner_open_target_state=planner_open_target_state,
            planner_timeout_message_id="PLANNER_TIMEOUT_PROCRASTINATOR",
        )

    if persona_name == HYPER_FOCUSED_PERSONA and state_name == FROZEN_STATE:
        return TaskAdaptation(
            persona_name=persona_name,
            state_name=state_name,
            max_visible_tasks=5,
            sort_rule=SORT_RULES["wobj_wsub_desc"],
            message_style="clear_functional",
            message_intensity="low",
            guidance_message_id="ADAPTIVE_HYPER_FOCUSED_FROZEN_GUIDANCE",
            parameter_settings={"Z": 2},
            auto_open_first_task=True,
            default_body_doubling=False,
            default_pomodoro=True,
        )

    if persona_name == HYPER_FOCUSED_PERSONA and state_name == ENGAGED_STATE:
        return TaskAdaptation(
            persona_name=persona_name,
            state_name=state_name,
            max_visible_tasks=5,
            sort_rule=SORT_RULES["wobj_desc"],
            message_style="direct",
            message_intensity="medium",
            guidance_message_id="ADAPTIVE_HYPER_FOCUSED_ENGAGED_GUIDANCE",
            auto_open_first_task=True,
            default_body_doubling=False,
            default_pomodoro=False,
            protect_rest_breaks_with_messages=True,
            force_body_doubling_pomodoro_timing=True,
        )

    if persona_name == HYPER_FOCUSED_PERSONA and state_name == PLANNER_STATE:
        return TaskAdaptation(
            persona_name=persona_name,
            state_name=state_name,
            max_visible_tasks=5,
            sort_rule=SORT_RULES["wobj_desc"],
            message_style="calm_corrective",
            message_intensity="low",
            guidance_message_id="ADAPTIVE_HYPER_FOCUSED_PLANNER_GUIDANCE",
            warn_if_open_outside_top_twenty_percent=True,
        )

    if persona_name == OVERWHELMED_PLANNER_PERSONA and state_name == FROZEN_STATE:
        return TaskAdaptation(
            persona_name=persona_name,
            state_name=state_name,
            max_visible_tasks=2,
            sort_rule=SORT_RULES["wsub_asc"],
            message_style="firm_kind",
            message_intensity="medium",
            guidance_message_id="ADAPTIVE_OVERWHELMED_PLANNER_FROZEN_GUIDANCE",
            auto_open_first_task=True,
            opaque_dialog_overlay=True,
            opaque_guided_pomodoro_overlay=True,
            cancel_needs_confirmation=pending_due_today_count > 0,
        )

    if persona_name == OVERWHELMED_PLANNER_PERSONA and state_name == ENGAGED_STATE:
        if recent_state_sequence[:3] == (ENGAGED_STATE, PLANNER_STATE, ENGAGED_STATE):
            return choose_intervention(PROCRASTINATOR_PERSONA, ENGAGED_STATE, context)
        return TaskAdaptation(
            persona_name=persona_name,
            state_name=state_name,
            max_visible_tasks=2,
            sort_rule=SORT_RULES["wobj_wsub_desc"],
            message_style="structured",
            message_intensity="low",
            guidance_message_id="ADAPTIVE_OVERWHELMED_PLANNER_ENGAGED_GUIDANCE",
            auto_open_first_task=True,
        )

    if persona_name == OVERWHELMED_PLANNER_PERSONA and state_name == PLANNER_STATE:
        planner_open_target_state = ENGAGED_STATE if memory_state == ENGAGED_STATE else FROZEN_STATE
        return TaskAdaptation(
            persona_name=persona_name,
            state_name=state_name,
            max_visible_tasks=2,
            sort_rule=SORT_RULES["wsub_asc"],
            message_style="calm_corrective",
            message_intensity="high",
            guidance_message_id="ADAPTIVE_OVERWHELMED_PLANNER_PLANNER_GUIDANCE",
            parameter_settings={"Y": 1},
            planner_open_target_state=planner_open_target_state,
            planner_timeout_message_id="PLANNER_TIMEOUT_OVERWHELMED_PLANNER",
        )

    return None


def sort_tasks_for_intervention(tasks_df, adaptation: TaskAdaptation | None):
    """Apply the configured task ordering for the active intervention."""

    if adaptation is None or adaptation.sort_rule is None or tasks_df.empty:
        return tasks_df
    return tasks_df.sort_values(
        by=list(adaptation.sort_rule.fields),
        ascending=list(adaptation.sort_rule.ascending),
        na_position="last",
    ).reset_index(drop=True)


def choose_primary_task(tasks_df, adaptation: TaskAdaptation | None):
    """Pick the first eligible task when an intervention wants auto-open."""

    if adaptation is None or not adaptation.auto_open_first_task or tasks_df.empty:
        return None
    openable_df = tasks_df[
        (~tasks_df["is_routine"])
        & (~tasks_df["has_subtasks"])
        & (tasks_df["status"].isin(["ready", "asleep", "debt"]))
    ]
    if openable_df.empty:
        return None
    return openable_df.iloc[0].to_dict()


def get_top_twenty_percent_instance_ids(tasks_df, adaptation: TaskAdaptation | None) -> set[str]:
    """Return the instance ids that belong to the top-ranked 20 percent."""

    if adaptation is None or not adaptation.warn_if_open_outside_top_twenty_percent or tasks_df.empty:
        return set()
    eligible_df = tasks_df[
        (~tasks_df["is_routine"])
        & (~tasks_df["has_subtasks"])
        & (tasks_df["status"].isin(["ready", "open", "asleep", "debt"]))
    ].reset_index(drop=True)
    if eligible_df.empty:
        return set()
    limit = max(1, ceil(len(eligible_df) * 0.2))
    return set(eligible_df.head(limit)["instance_id"].tolist())
