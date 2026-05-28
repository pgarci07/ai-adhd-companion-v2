"""Chunk focus-flow helpers, state, and review dialog rendering.

Chunk is the non-Pomodoro focus mode. Instead of using a fixed sprint length,
it recalculates the next work block from the task's remaining estimated size,
the elapsed session time, persona/state modifiers, and the user's continuous
work limit. This module owns that calculation and the review dialog that
appears after each Chunk cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import ceil
from typing import Any, Callable

import pandas as pd
import pytz
import streamlit as st

from app.application.adaptive import task_adaptation
from app.config import (
    CHUNK_PERSONA_STATE_MODIFIERS,
    CHUNK_TIMER_DEFAULT_SECONDS,
    DEFAULT_CHUNK_MIN_FLOOR_MINUTES,
    DEFAULT_MAX_CONTINUOUS_WORK_MINUTES,
    DEFAULT_SESSION_EXTENSION_TOLERANCE,
)


CHUNK_CONTINUOUS_WORK_SECONDS_KEY = "chunk_continuous_work_seconds"
CHUNK_REVIEW_PENDING_KEY = "chunk_review_pending"
CHUNK_SESSION_EXTENSION_PROMPT_CONTEXT_KEY = "chunk_session_extension_prompt_context"
CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY = "chunk_remaining_minutes_by_instance"


@dataclass(frozen=True)
class ChunkServices:
    """Runtime dependencies injected from the main Streamlit module.

    Keep Chunk decoupled from `main.py` by adding integration points here
    instead of importing main-level helpers directly. This makes the planning
    logic easier to unit test and lets Streamlit orchestration stay in one
    place.
    """

    get_user_preferences: Callable[[], dict[str, Any]]
    get_enriched_task_row_by_instance_id: Callable[[str | None], dict[str, Any] | None]
    get_open_task_row: Callable[[], dict[str, Any] | None]
    get_resumable_session_elapsed_minutes: Callable[[], float]
    get_effective_session_work_time: Callable[[], float]
    get_current_persona_name: Callable[[], str | None]
    get_effective_current_state_name: Callable[[], str | None]
    start_focus_cycle_tracker: Callable[[dict[str, Any], str], dict[str, Any]]
    set_focus_overlay_state: Callable[[dict[str, Any]], None]
    get_focus_overlay_state: Callable[[], dict[str, Any] | None]
    clear_focus_overlay_state: Callable[[], None]
    format_cycle_minutes_label: Callable[[float], str]
    schedule_work_timer: Callable[[float, Callable[..., None], str], None]
    disable_work_timer: Callable[[], None]
    get_work_timer_snapshot: Callable[[], Any]
    expire_chunk_cycle: Callable[..., None]
    get_current_task_adaptation: Callable[[Any], tuple[Any, Any]]
    get_tasks_dataframe: Callable[[], Any]
    begin_rest_break: Callable[..., None]
    clear_task_completion_feedback_request: Callable[[], None]
    request_task_completion_feedback: Callable[..., None]
    get_post_work_incomplete_task_status: Callable[[dict[str, Any]], str]
    update_task_status: Callable[[dict[str, Any], str], Any]
    notify_work_ended: Callable[[], None]
    handle_api_exception: Callable[[Exception, str], Any]


def ensure_chunk_session_state() -> None:
    """Initialise Chunk-owned session containers if Streamlit has not yet done it."""

    if CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY not in st.session_state:
        st.session_state[CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY] = {}


def start_chunk_overlay(
    task_row: dict[str, Any],
    duration_seconds: int | float,
    services: ChunkServices,
) -> None:
    """Create the shared focus-overlay state for one Chunk work cycle."""

    # Chunk reuses the Pomodoro overlay renderer in `main.py`. The only
    # difference is the `cycle_type`, the flexible duration label, and the fact
    # that the duration is calculated from the Chunk planning algorithm.
    tracker = services.start_focus_cycle_tracker(task_row, "chunk")
    iterations = int(tracker.get("iterations", 1) or 1)
    enriched_task_row = (
        services.get_enriched_task_row_by_instance_id(task_row.get("instance_id"))
        if task_row and task_row.get("instance_id")
        else None
    )
    task_duration_minutes = (
        (enriched_task_row or {}).get("size_minutes")
        if enriched_task_row
        else task_row.get("size_minutes")
    )
    services.set_focus_overlay_state(
        {
            "instance_id": task_row.get("instance_id"),
            "task_id": task_row.get("task_id"),
            "title": task_row.get("title"),
            "description": task_row.get("description"),
            "task_duration_minutes": task_duration_minutes,
            "duration_seconds": int(duration_seconds),
            "duration_minutes_label": services.format_cycle_minutes_label(duration_seconds),
            "iterations": iterations,
            "started_at": datetime.now(pytz.UTC).timestamp(),
            "mode": "work",
            "cycle_type": "chunk",
        }
    )


def get_chunk_min_floor_minutes(services: ChunkServices) -> int:
    """Return the preferred minimum useful size for one Chunk work block."""

    return int(
        services.get_user_preferences().get(
            "chunk_min_floor_minutes",
            DEFAULT_CHUNK_MIN_FLOOR_MINUTES,
        )
    )


def get_chunk_session_extension_tolerance() -> float:
    """Return the tolerated overrun beyond the expected session length.

    Expected session time is a planning estimate, not a hard law. Chunk allows
    a small configured overshoot before asking the user to extend the session
    explicitly.
    """

    return float(DEFAULT_SESSION_EXTENSION_TOLERANCE)


def get_chunk_continuous_work_seconds() -> int:
    """Return accumulated Chunk work seconds for the current authenticated session."""

    return int(st.session_state.get(CHUNK_CONTINUOUS_WORK_SECONDS_KEY, 0) or 0)


def add_chunk_continuous_work_seconds(additional_seconds: int | float) -> None:
    """Accumulate elapsed Chunk work across tasks until a forced rest resets it."""

    additional_seconds = max(0, int(additional_seconds or 0))
    st.session_state[CHUNK_CONTINUOUS_WORK_SECONDS_KEY] = (
        get_chunk_continuous_work_seconds() + additional_seconds
    )


def reset_chunk_continuous_work_seconds() -> None:
    """Clear the Chunk continuous-work accumulator after a forced rest starts."""

    st.session_state[CHUNK_CONTINUOUS_WORK_SECONDS_KEY] = 0


def get_max_continuous_work_minutes(services: ChunkServices) -> int:
    """Return the session-level cap before Chunk mode forces a rest break."""

    return int(
        services.get_user_preferences().get(
            "max_continuous_work_minutes",
            DEFAULT_MAX_CONTINUOUS_WORK_MINUTES,
        )
    )


def get_time_to_max_continuous_work_minutes(services: ChunkServices) -> float:
    """Return remaining minutes before Chunk mode must force a rest break."""

    max_continuous_minutes = get_max_continuous_work_minutes(services)
    elapsed_chunk_minutes = get_chunk_continuous_work_seconds() / 60.0
    return max(0.0, float(max_continuous_minutes) - float(elapsed_chunk_minutes))


def get_chunk_remaining_minutes(task_row: dict[str, Any] | None, services: ChunkServices) -> float:
    """Return the remaining Chunk size for one task instance in this resumable session.

    The first Chunk cycle uses the task's estimated size. Each completed or
    interrupted cycle subtracts the real elapsed work time, so repeated cycles
    shrink toward the remaining effort rather than restarting from the original
    estimate.
    """

    ensure_chunk_session_state()
    if not task_row:
        return CHUNK_TIMER_DEFAULT_SECONDS / 60.0

    resolved_task_row = (
        services.get_enriched_task_row_by_instance_id(task_row.get("instance_id"))
        or task_row
    )
    size_minutes = resolved_task_row.get("size_minutes")
    if pd.isna(size_minutes) if size_minutes is not None else True:
        custom_sizes = services.get_user_preferences().get(
            "custom_sizes",
            [15, 30, 60, 180, 720],
        )
        size_id = resolved_task_row.get("size_id")
        if size_id and 0 < int(size_id) <= len(custom_sizes):
            size_minutes = int(custom_sizes[int(size_id) - 1])
        else:
            size_minutes = CHUNK_TIMER_DEFAULT_SECONDS / 60.0

    remaining_by_instance = dict(
        st.session_state.get(CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY) or {}
    )
    instance_id = resolved_task_row.get("instance_id")
    if not instance_id:
        return float(size_minutes)

    if instance_id not in remaining_by_instance:
        remaining_by_instance[instance_id] = float(size_minutes)
        st.session_state[CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY] = remaining_by_instance
        return float(size_minutes)

    remaining_minutes = float(remaining_by_instance.get(instance_id) or 0.0)
    if remaining_minutes <= 0:
        # If the user asks for another cycle after exhausting the original
        # estimate, treat the task as under-estimated and offer a conservative
        # half-size continuation instead of returning a zero-length cycle.
        return max(1.0, float(size_minutes) / 2.0)

    return remaining_minutes


def register_chunk_work_elapsed(
    task_row: dict[str, Any] | None,
    elapsed_seconds: int | float,
    services: ChunkServices,
) -> None:
    """Subtract real worked time from the remaining Chunk size of one instance."""

    if not task_row:
        return

    resolved_task_row = (
        services.get_enriched_task_row_by_instance_id(task_row.get("instance_id"))
        or task_row
    )
    instance_id = resolved_task_row.get("instance_id")
    if not instance_id:
        return

    remaining_by_instance = dict(
        st.session_state.get(CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY) or {}
    )
    remaining_minutes = get_chunk_remaining_minutes(resolved_task_row, services)
    worked_minutes = max(0.0, float(elapsed_seconds or 0) / 60.0)
    remaining_by_instance[instance_id] = max(0.0, remaining_minutes - worked_minutes)
    st.session_state[CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY] = remaining_by_instance


def clear_chunk_remaining_minutes(task_row: dict[str, Any] | None) -> None:
    """Forget Chunk remaining-size state for one instance when it is no longer useful."""

    if not task_row:
        return

    instance_id = task_row.get("instance_id")
    if not instance_id:
        return

    remaining_by_instance = dict(
        st.session_state.get(CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY) or {}
    )
    remaining_by_instance.pop(instance_id, None)
    st.session_state[CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY] = remaining_by_instance


def calculate_next_chunk_plan(
    task_row: dict[str, Any] | None,
    services: ChunkServices,
) -> dict[str, Any]:
    """Calculate the next Chunk work block and whether session extension is needed.

    The algorithm balances four constraints:
    1. remaining task size;
    2. session stamina, modelled as elapsed/expected session time;
    3. persona/state modifiers from the adaptive matrix;
    4. maximum continuous work before a protected rest break.
    """

    resolved_task_row = task_row or services.get_open_task_row()
    size_minutes = float(get_chunk_remaining_minutes(resolved_task_row, services))
    elapsed_session_minutes = float(services.get_resumable_session_elapsed_minutes())
    expected_session_minutes = max(1.0, float(services.get_effective_session_work_time()))
    remaining_continuous_minutes = float(get_time_to_max_continuous_work_minutes(services))
    chunk_floor_minutes = float(get_chunk_min_floor_minutes(services))
    session_tolerance = get_chunk_session_extension_tolerance()

    stamina_factor = max(
        0.0,
        1.0 - (elapsed_session_minutes / expected_session_minutes),
    )
    work_base = size_minutes * stamina_factor

    # The adaptive matrix uses canonical aliases. Normalising here keeps the
    # rest of the calculation independent from display labels or local casing.
    raw_persona_name = str(services.get_current_persona_name() or "").strip().lower()
    raw_state_name = str(services.get_effective_current_state_name() or "").strip().lower()
    persona_name = task_adaptation.PERSONA_NAME_ALIASES.get(raw_persona_name, raw_persona_name)
    state_name = task_adaptation.STATE_NAME_ALIASES.get(raw_state_name, raw_state_name)
    modifier = (
        CHUNK_PERSONA_STATE_MODIFIERS.get(persona_name, {}).get(state_name)
        if persona_name and state_name
        else None
    )
    if modifier is None:
        modifier = 1.0

    work_target = work_base * float(modifier)
    hard_cap_minutes = max(1.0, remaining_continuous_minutes)
    suggested_minutes = min(work_target, hard_cap_minutes)

    if suggested_minutes >= chunk_floor_minutes:
        return {
            "status": "ok",
            "duration_minutes": max(1, int(round(suggested_minutes))),
        }

    # Very small calculated cycles are usually disruptive. Prefer the user's
    # minimum floor when it still fits within the soft session limit.
    floor_candidate_minutes = min(chunk_floor_minutes, hard_cap_minutes)
    soft_session_limit = expected_session_minutes * (1.0 + session_tolerance)
    remaining_soft_session_minutes = max(
        0.0,
        soft_session_limit - elapsed_session_minutes,
    )

    if floor_candidate_minutes <= remaining_soft_session_minutes:
        return {
            "status": "ok",
            "duration_minutes": max(1, int(round(floor_candidate_minutes))),
        }

    # If the floor would exceed the tolerated session overrun, pause the flow
    # and ask the user whether they want to extend the expected session.
    suggested_extension_minutes = max(
        1,
        int(ceil(floor_candidate_minutes - remaining_soft_session_minutes)),
    )
    return {
        "status": "needs_session_extension",
        "duration_minutes": max(1, int(round(max(suggested_minutes, 1.0)))),
        "suggested_floor_minutes": max(1, int(round(floor_candidate_minutes))),
        "remaining_soft_session_minutes": max(0, int(round(remaining_soft_session_minutes))),
        "suggested_extension_minutes": suggested_extension_minutes,
        "task_row": dict(resolved_task_row or {}),
    }


def get_next_chunk_work_seconds(
    task_row: dict[str, Any] | None,
    services: ChunkServices,
) -> int:
    """Return the next Chunk work-block duration in seconds."""

    chunk_plan = calculate_next_chunk_plan(task_row, services)
    return max(60, int(round(float(chunk_plan["duration_minutes"]) * 60)))


def clear_chunk_session_extension_prompt() -> None:
    """Clear any pending Chunk session-extension confirmation dialog."""

    st.session_state.pop(CHUNK_SESSION_EXTENSION_PROMPT_CONTEXT_KEY, None)


def queue_chunk_session_extension_prompt(chunk_plan: dict[str, Any], *, source_label: str) -> None:
    """Persist a pending Chunk extension prompt until the dialog consumes it."""

    st.session_state[CHUNK_SESSION_EXTENSION_PROMPT_CONTEXT_KEY] = {
        **dict(chunk_plan or {}),
        "source_label": source_label,
    }


def request_next_chunk_cycle(
    task_row: dict[str, Any] | None,
    *,
    source_label: str,
    services: ChunkServices,
) -> bool:
    """Start the next Chunk cycle or queue a session-extension confirmation."""

    resolved_task_row = task_row or services.get_open_task_row()
    if not resolved_task_row:
        return False

    clear_chunk_session_extension_prompt()
    chunk_plan = calculate_next_chunk_plan(resolved_task_row, services)
    if chunk_plan["status"] == "needs_session_extension":
        # The caller will render the extension prompt from session state. We do
        # not start a timer until the user accepts the longer session.
        queue_chunk_session_extension_prompt(chunk_plan, source_label=source_label)
        return False

    duration_minutes = float(chunk_plan["duration_minutes"])
    duration_seconds = int(duration_minutes * 60)
    services.schedule_work_timer(
        duration_minutes,
        services.expire_chunk_cycle,
        source_label,
    )
    start_chunk_overlay(resolved_task_row, duration_seconds, services)
    return True


def expire_chunk_cycle(timer=None, *, services: ChunkServices) -> None:
    """Finish the current Chunk cycle and queue its review dialog."""

    overlay_state = services.get_focus_overlay_state() or {}
    timer_snapshot = services.get_work_timer_snapshot()
    total_seconds = int(
        timer_snapshot.duration_seconds
        or overlay_state.get("duration_seconds")
        or get_next_chunk_work_seconds(None, services)
    )
    now_timestamp = datetime.now(pytz.UTC).timestamp()
    remaining_seconds = 0
    if timer_snapshot.running and timer_snapshot.expires_at is not None:
        if now_timestamp > float(timer_snapshot.expires_at):
            elapsed_seconds = min(
                total_seconds,
                max(0, int(now_timestamp - float(timer_snapshot.expires_at))),
            )
        else:
            remaining_seconds = max(
                0,
                int(float(timer_snapshot.expires_at) - now_timestamp),
            )
            elapsed_seconds = max(0, total_seconds - remaining_seconds)
    else:
        elapsed_seconds = total_seconds
    worked_seconds = elapsed_seconds or total_seconds
    # Interrupted cycles and normal expiries both count as work already done.
    # This is what lets the next Chunk plan shrink the remaining task estimate.
    add_chunk_continuous_work_seconds(worked_seconds)
    register_chunk_work_elapsed(
        overlay_state.get("task_row") or services.get_open_task_row(),
        worked_seconds,
        services,
    )
    services.clear_focus_overlay_state()
    services.disable_work_timer()
    st.session_state[CHUNK_REVIEW_PENDING_KEY] = True
    st.rerun()


def clear_chunk_review_state() -> None:
    """Clear pending Chunk-review dialog state."""

    st.session_state.pop(CHUNK_REVIEW_PENDING_KEY, None)


@st.dialog("Chunk review", on_dismiss=clear_chunk_review_state)
def chunk_review_dialog(services: ChunkServices) -> None:
    """Render the review dialog shown after a generic work chunk finishes."""

    st.markdown(
        '<div class="review-status-note">Work cycle is over.</div>',
        unsafe_allow_html=True,
    )
    current_adaptation, _ = services.get_current_task_adaptation(
        services.get_tasks_dataframe()
    )
    accumulated_chunk_seconds = get_chunk_continuous_work_seconds()
    max_continuous_work_seconds = get_max_continuous_work_minutes(services) * 60
    forced_rest_required = bool(
        current_adaptation
        and current_adaptation.protect_rest_breaks_with_messages
        and accumulated_chunk_seconds >= max_continuous_work_seconds
    )

    if forced_rest_required:
        # Some adaptive states protect rest more strongly. In that mode, the
        # user can complete the task, but starting another Chunk must route
        # through the rest flow first.
        st.info(
            "A rest break is required now because your continuous Chunk work "
            "has reached the configured limit."
        )

    task_complete_col, new_cycle_col, finish_col = st.columns(3, gap="small")

    try:
        open_task = services.get_open_task_row()

        with task_complete_col:
            if st.button(
                "Task complete",
                key="chunk_review_task_complete",
                type="primary",
                use_container_width=True,
            ):
                if not open_task:
                    st.warning("There is no open task to mark as completed.")
                    return
                services.request_task_completion_feedback(
                    open_task,
                    "chunk_review",
                    continue_choice="No",
                    forced_rest_required=forced_rest_required,
                )
                clear_chunk_review_state()
                st.rerun()
                return

        with new_cycle_col:
            if st.button(
                "New cycle",
                key="chunk_review_new_cycle",
                use_container_width=True,
            ):
                if forced_rest_required:
                    reset_chunk_continuous_work_seconds()
                    services.begin_rest_break(
                        previous_work_outcome="incomplete",
                        resume_cycle_type="chunk",
                    )
                elif open_task:
                    duration_seconds = get_next_chunk_work_seconds(open_task, services)
                    services.schedule_work_timer(
                        duration_seconds / 60.0,
                        services.expire_chunk_cycle,
                        "chunk_review_restart_work_cycle",
                    )
                    start_chunk_overlay(open_task, duration_seconds, services)
                else:
                    services.disable_work_timer()
                    services.clear_focus_overlay_state()
                clear_chunk_review_state()
                st.rerun()
                return

        with finish_col:
            if st.button(
                "Finish",
                key="chunk_review_finish",
                use_container_width=True,
            ):
                services.clear_task_completion_feedback_request()
                services.disable_work_timer()
                services.clear_focus_overlay_state()
                if open_task:
                    next_status = services.get_post_work_incomplete_task_status(open_task)
                    if next_status != "open":
                        services.update_task_status(open_task, next_status)
                services.notify_work_ended()
                clear_chunk_review_state()
                st.rerun()
                return
    except Exception as error:
        services.handle_api_exception(error, f"Could not finish chunk review: {error}")


def render_chunk_review_dialog(services: ChunkServices) -> None:
    """Show the Chunk review dialog when its session flag is enabled."""

    if st.session_state.get(CHUNK_REVIEW_PENDING_KEY):
        chunk_review_dialog(services)
