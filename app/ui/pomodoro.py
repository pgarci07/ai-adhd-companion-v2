"""Pomodoro focus-flow helpers, rest handling, and overlay rendering.

This module owns the classic Pomodoro path and the Pomodoro-style rest path
shared by both Pomodoro and Chunk. It deliberately receives application
callbacks through `PomodoroServices` instead of importing `main.py`, so the
large Streamlit orchestrator can stay thin while this flow remains testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
import html
import textwrap

import pytz
import streamlit as st
import streamlit.components.v1 as components

from app.config import (
    DEFAULT_REST_DURATION_MINUTES,
    POMODORO_SPRINT_TEST_MINUTES,
    REST_MESSAGE_MODAL_SECONDS,
)


POMODORO_OVERLAY_STATE_KEY = "pomodoro_overlay_state"
FOCUS_CYCLE_TRACKER_KEY = "focus_cycle_tracker"
SPRINT_REVIEW_PENDING_KEY = "sprint_review_pending"
REST_MESSAGE_KEY = "rest_message"
REST_MESSAGE_EXPIRES_AT_KEY = "rest_message_expires_at"
REST_RESUME_PROMPT_CONTEXT_KEY = "rest_resume_prompt_context"
REST_RESUME_PROMPT_PENDING_KEY = "rest_resume_prompt_pending"


@dataclass(frozen=True)
class PomodoroServices:
    """Runtime dependencies injected from the main Streamlit module."""

    get_user_preferences: Callable[[], dict[str, Any]]
    get_enriched_task_row_by_instance_id: Callable[[str | None], dict[str, Any] | None]
    get_current_task_adaptation: Callable[[Any], tuple[Any, Any]]
    get_tasks_dataframe: Callable[[], Any]
    get_work_timer_snapshot: Callable[[], Any]
    get_work_timer: Callable[..., Any]
    schedule_work_timer: Callable[[float, Callable[..., None], str], None]
    disable_work_timer: Callable[[], None]
    append_timer_log_line: Callable[[str], None]
    expire_sprint_callback: Callable[..., None]
    expire_rest_callback: Callable[..., None]
    get_open_task_row: Callable[[], dict[str, Any] | None]
    get_post_work_incomplete_task_status: Callable[[dict[str, Any]], str]
    update_task_status: Callable[[dict[str, Any], str], Any]
    notify_work_ended: Callable[[], None]
    request_task_completion_feedback: Callable[..., None]
    clear_task_completion_feedback_request: Callable[[], None]
    display_message: Callable[..., Any]
    get_adaptive_message_intensity: Callable[[], str]
    render_voice_message_button: Callable[..., Any]
    request_next_chunk_cycle: Callable[..., bool]
    body_doubling_session_active: Callable[[], bool]
    get_open_task_guidance_expires_at: Callable[[], Any]
    handle_api_exception: Callable[[Exception, str], Any]
    rerun: Callable[[], None]
    info: Callable[[str], Any]


def get_pomodoro_overlay_state() -> dict[str, Any] | None:
    """Read the current focus-overlay payload from session state."""

    return st.session_state.get(POMODORO_OVERLAY_STATE_KEY)


def set_pomodoro_overlay_state(overlay_state: dict[str, Any]) -> None:
    """Persist focus-overlay state for Pomodoro and compatible flows."""

    st.session_state[POMODORO_OVERLAY_STATE_KEY] = overlay_state


def clear_pomodoro_overlay_state() -> None:
    """Remove the active focus overlay from session state."""

    st.session_state.pop(POMODORO_OVERLAY_STATE_KEY, None)


def get_focus_cycle_tracker() -> dict[str, Any]:
    """Return the current per-task focus-cycle tracker."""

    return st.session_state.get(FOCUS_CYCLE_TRACKER_KEY, {})


def start_focus_cycle_tracker(task_row: dict[str, Any], cycle_type: str) -> dict[str, Any]:
    """Start or increment the per-task focus-cycle counter."""

    tracker = get_focus_cycle_tracker()
    previous_instance_id = tracker.get("instance_id")
    previous_cycle_type = tracker.get("cycle_type")
    previous_iterations = int(tracker.get("iterations", 0) or 0)
    iterations = (
        previous_iterations + 1
        if previous_instance_id == task_row.get("instance_id")
        and previous_cycle_type == cycle_type
        else 1
    )
    new_tracker = {
        "instance_id": task_row.get("instance_id"),
        "task_id": task_row.get("task_id"),
        "cycle_type": cycle_type,
        "iterations": iterations,
    }
    st.session_state[FOCUS_CYCLE_TRACKER_KEY] = new_tracker
    return new_tracker


def clear_focus_cycle_tracker() -> None:
    """Clear the active focus-cycle tracker."""

    st.session_state.pop(FOCUS_CYCLE_TRACKER_KEY, None)


def format_cycle_minutes_label(duration_seconds: int | float) -> str:
    """Format a duration in seconds as a compact minute label."""

    minutes = round(float(duration_seconds or 0) / 60.0, 1)
    if minutes.is_integer():
        return str(int(minutes))
    return str(minutes)


def get_pomodoro_overlay_opacity(services: PomodoroServices) -> float:
    """Choose overlay opacity, allowing adaptive rules to override it."""

    adaptation, _ = services.get_current_task_adaptation(services.get_tasks_dataframe())
    if adaptation and adaptation.opaque_guided_pomodoro_overlay:
        return 0.96
    return 0.76


def start_pomodoro_overlay(
    task_row: dict[str, Any],
    duration_minutes: int | float,
    services: PomodoroServices,
) -> None:
    """Create the Pomodoro focus overlay state for the opened task."""

    tracker = start_focus_cycle_tracker(task_row, "pomodoro")
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
    set_pomodoro_overlay_state(
        {
            "instance_id": task_row.get("instance_id"),
            "task_id": task_row.get("task_id"),
            "title": task_row.get("title"),
            "description": task_row.get("description"),
            "task_duration_minutes": task_duration_minutes,
            "duration_minutes": int(duration_minutes),
            "duration_seconds": int(duration_minutes) * 60,
            "work_duration_minutes": int(duration_minutes),
            "iterations": iterations,
            "started_at": datetime.now(pytz.UTC).timestamp(),
            "mode": "work",
            "cycle_type": "pomodoro",
        }
    )


def start_pomodoro_rest_overlay(duration_minutes: int | float) -> None:
    """Switch the active overlay into Pomodoro rest mode."""

    previous_state = get_pomodoro_overlay_state() or {}
    set_pomodoro_overlay_state(
        {
            **previous_state,
            "duration_minutes": int(duration_minutes),
            "duration_seconds": int(duration_minutes) * 60,
            "started_at": datetime.now(pytz.UTC).timestamp(),
            "mode": "rest",
            "cycle_type": "pomodoro",
        }
    )


def get_effective_pomodoro_sprint_minutes(services: PomodoroServices) -> int:
    """Return the Pomodoro focus length for the current user session."""

    return int(
        services.get_user_preferences().get(
            "sprint",
            POMODORO_SPRINT_TEST_MINUTES,
        )
    )


def get_effective_rest_duration_minutes(services: PomodoroServices) -> int:
    """Return the preferred duration for Pomodoro-style rest blocks."""

    return int(
        services.get_user_preferences().get(
            "rest_duration",
            DEFAULT_REST_DURATION_MINUTES,
        )
    )


def set_rest_message(message: str) -> None:
    """Store a short post-rest message that can render in its own dialog."""

    st.session_state[REST_MESSAGE_KEY] = message
    st.session_state[REST_MESSAGE_EXPIRES_AT_KEY] = (
        datetime.now(pytz.UTC).timestamp() + REST_MESSAGE_MODAL_SECONDS
    )


def clear_rest_message() -> None:
    """Clear the post-rest message dialog state."""

    st.session_state.pop(REST_MESSAGE_KEY, None)
    st.session_state.pop(REST_MESSAGE_EXPIRES_AT_KEY, None)


def set_rest_resume_prompt_context(
    *,
    previous_work_outcome: str,
    work_duration_minutes: int | float,
    resume_cycle_type: str = "pomodoro",
) -> None:
    """Remember how work should resume after the rest timer finishes."""

    st.session_state[REST_RESUME_PROMPT_CONTEXT_KEY] = {
        "previous_work_outcome": previous_work_outcome,
        "work_duration_minutes": work_duration_minutes,
        "resume_cycle_type": resume_cycle_type,
    }
    st.session_state[REST_RESUME_PROMPT_PENDING_KEY] = False


def get_rest_resume_prompt_context() -> dict[str, Any] | None:
    """Return the pending post-rest resume/finish context."""

    return st.session_state.get(REST_RESUME_PROMPT_CONTEXT_KEY)


def clear_rest_resume_prompt_context() -> None:
    """Forget any pending post-rest resume/finish decision."""

    st.session_state.pop(REST_RESUME_PROMPT_CONTEXT_KEY, None)
    st.session_state.pop(REST_RESUME_PROMPT_PENDING_KEY, None)


def clear_expired_rest_message(services: PomodoroServices) -> None:
    """Expire the transient rest message after its display window."""

    expires_at = st.session_state.get(REST_MESSAGE_EXPIRES_AT_KEY)
    if expires_at is None:
        return

    if datetime.now(pytz.UTC).timestamp() >= float(expires_at):
        clear_rest_message()
        services.rerun()


def begin_pomodoro_rest_break(
    *,
    previous_work_outcome: str,
    services: PomodoroServices,
    resume_cycle_type: str = "pomodoro",
) -> None:
    """Start a Pomodoro-style rest break and remember how work should resume."""

    work_duration_minutes = get_effective_pomodoro_sprint_minutes(services)
    set_rest_resume_prompt_context(
        previous_work_outcome=previous_work_outcome,
        work_duration_minutes=work_duration_minutes,
        resume_cycle_type=resume_cycle_type,
    )
    rest_duration_minutes = get_effective_rest_duration_minutes(services)
    services.append_timer_log_line(
        "request_reset | timer=work_timer source=sprint_review_rest "
        f"duration_minutes={rest_duration_minutes} callback=eoRest"
    )
    services.get_work_timer(st.session_state).reset(
        duration=rest_duration_minutes * 60,
        on_expiry=services.expire_rest_callback,
    )
    start_pomodoro_rest_overlay(rest_duration_minutes)


def finalize_post_rest_finish(
    prompt_context: dict[str, Any] | None,
    services: PomodoroServices,
) -> None:
    """Apply post-work semantics when the user finishes after resting."""

    previous_work_outcome = (prompt_context or {}).get("previous_work_outcome")
    services.disable_work_timer()
    clear_pomodoro_overlay_state()
    clear_rest_resume_prompt_context()
    clear_rest_message()
    if previous_work_outcome == "incomplete":
        open_task = services.get_open_task_row()
        if open_task:
            next_status = services.get_post_work_incomplete_task_status(open_task)
            if next_status != "open":
                services.update_task_status(open_task, next_status)
    services.notify_work_ended()


def expire_sprint(timer=None, *, services: PomodoroServices) -> None:
    """Finish a Pomodoro sprint and queue the sprint-review dialog."""

    clear_pomodoro_overlay_state()
    st.session_state[SPRINT_REVIEW_PENDING_KEY] = True
    services.disable_work_timer()
    services.rerun()


def expire_rest(timer=None, *, services: PomodoroServices | None = None) -> None:
    """Finish a rest block and either show a message or resume/finish prompt."""

    if services is None:
        return

    open_task = services.get_open_task_row()
    if not open_task:
        clear_pomodoro_overlay_state()
        services.disable_work_timer()
        clear_rest_resume_prompt_context()
        services.info("Rest is over.")
        return

    if not get_rest_resume_prompt_context():
        clear_pomodoro_overlay_state()
        services.disable_work_timer()
        set_rest_message("Rest is over.")
        services.rerun()
        return

    clear_pomodoro_overlay_state()
    services.disable_work_timer()
    st.session_state[REST_RESUME_PROMPT_PENDING_KEY] = True
    services.rerun()


def render_pomodoro_overlay(services: PomodoroServices) -> None:
    """Render the full-screen focus/rest overlay."""

    overlay_state = get_pomodoro_overlay_state()
    if not overlay_state or overlay_state.get("mode") not in {"work", "rest"}:
        return

    timer_snapshot = services.get_work_timer_snapshot()
    if not timer_snapshot.running or timer_snapshot.expires_at is None:
        return

    remaining_seconds = max(
        0,
        int(timer_snapshot.expires_at - datetime.now(pytz.UTC).timestamp()),
    )
    total_seconds = max(
        1,
        int(
            timer_snapshot.duration_seconds
            or overlay_state.get("duration_seconds")
            or (overlay_state.get("duration_minutes") or 1) * 60
        ),
    )
    elapsed_seconds = max(0, total_seconds - remaining_seconds)
    progress_percentage = min(100, max(0, int((elapsed_seconds / total_seconds) * 100)))
    minutes = remaining_seconds // 60
    seconds = remaining_seconds % 60
    opacity = get_pomodoro_overlay_opacity(services)
    safe_title = html.escape(str(overlay_state.get("title") or "Open task"))
    safe_description = html.escape(str(overlay_state.get("description") or ""))
    iterations = int(overlay_state.get("iterations", 1) or 1)
    overlay_mode = overlay_state.get("mode", "work")
    is_rest_mode = overlay_mode == "rest"
    cycle_type = overlay_state.get("cycle_type", "pomodoro")
    is_chunk_mode = cycle_type == "chunk"
    badge_label = (
        "Pomodoro rest"
        if is_rest_mode
        else ("Work cycle" if is_chunk_mode else "Pomodoro focus")
    )
    status_label = (
        "Take the break until the timer ends"
        if is_rest_mode
        else (
            "Stay with this task until the cycle ends"
            if is_chunk_mode
            else "Stay with this task until the timer ends"
        )
    )
    card_title = "Rest break" if is_rest_mode else safe_title
    card_description = (
        "Step away from the task for a moment. The app will bring you back "
        "when the rest timer ends."
        if is_rest_mode
        else safe_description
    )
    duration_label = (
        str(int(overlay_state.get("duration_minutes", 0) or 0))
        if not is_chunk_mode
        else str(
            overlay_state.get("duration_minutes_label")
            or format_cycle_minutes_label(total_seconds)
        )
    )
    iteration_pill = (
        f"Cycle iteration {iterations}"
        if is_chunk_mode
        else f"Pomodoro iteration {iterations}"
    )
    duration_pill = (
        f"{'Rest' if is_rest_mode else ('Cycle' if is_chunk_mode else 'Sprint')} "
        f"length {duration_label} min"
    )
    task_duration_minutes = overlay_state.get("task_duration_minutes")
    task_duration_pill = ""
    if not is_rest_mode and task_duration_minutes not in {None, ""}:
        task_duration_pill = (
            f'<span class="pomodoro-pill">Est. task duration {task_duration_minutes} min</span>'
        )

    if is_rest_mode:
        backdrop_rgba = f"rgba(86, 113, 109, {min(0.84, opacity)})"
        card_background = "rgba(238, 246, 243, 0.94)"
        timer_color = "#24544f"
        text_color = "#305a56"
        description_color = "#5c7b76"
        frame_line = "rgba(48, 90, 86, 0.14)"
        badge_background = "rgba(53, 114, 103, 0.10)"
        badge_color = "#2e6f65"
        progress_background = "rgba(53, 114, 103, 0.16)"
        progress_fill = "linear-gradient(90deg, #4f9a8d 0%, #9fd0c3 100%)"
        panel_tint = "rgba(247, 252, 250, 0.78)"
        role_label = "Breathing block"
        card_kicker = "Intentional pause"
    elif is_chunk_mode:
        backdrop_rgba = f"rgba(89, 96, 109, {min(0.9, opacity)})"
        card_background = "rgba(244, 246, 248, 0.95)"
        timer_color = "#1f252c"
        text_color = "#2b3138"
        description_color = "#636c76"
        frame_line = "rgba(39, 46, 56, 0.14)"
        badge_background = "rgba(61, 69, 81, 0.08)"
        badge_color = "#3d4550"
        progress_background = "rgba(61, 69, 81, 0.13)"
        progress_fill = "linear-gradient(90deg, #55606d 0%, #8792a0 100%)"
        panel_tint = "rgba(249, 250, 251, 0.76)"
        role_label = "Repetitive focus"
        card_kicker = "Adaptive work chunk"
    else:
        backdrop_rgba = f"rgba(86, 104, 126, {opacity})"
        card_background = "rgba(241, 246, 250, 0.95)"
        timer_color = "#1f2e3c"
        text_color = "#2b3a49"
        description_color = "#617286"
        frame_line = "rgba(43, 64, 86, 0.14)"
        badge_background = "rgba(64, 101, 135, 0.10)"
        badge_color = "#30597d"
        progress_background = "rgba(64, 101, 135, 0.14)"
        progress_fill = "linear-gradient(90deg, #4c7396 0%, #7ea4c7 100%)"
        panel_tint = "rgba(247, 250, 252, 0.78)"
        role_label = "Focus block"
        card_kicker = "Task in progress"

    show_control_slot = is_rest_mode or is_chunk_mode
    control_html = '<div class="pomodoro-control-slot"></div>' if show_control_slot else ""
    overlay_html = textwrap.dedent(f"""
    <style>
    .pomodoro-overlay {{
        position: fixed;
        inset: 0;
        z-index: 99990;
        padding: 2rem;
        isolation: isolate;
        pointer-events: auto;
        background: {backdrop_rgba};
    }}
    .pomodoro-overlay::before {{ content: none; }}
    .pomodoro-shell {{
        position: relative;
        z-index: 1;
        height: 100%;
        max-width: 980px;
        margin: 0 auto;
        display: grid;
        align-items: center;
    }}
    .pomodoro-card {{
        border-radius: 30px;
        overflow: hidden;
        background: {card_background};
        border: 1px solid {frame_line};
        box-shadow: 0 30px 90px rgba(24, 29, 35, 0.22);
        display: grid;
        grid-template-rows: auto 1fr;
    }}
    .pomodoro-card::after {{
        content: "";
        display: block;
        height: 6px;
        background: {progress_fill};
        opacity: 0.92;
    }}
    .pomodoro-topbar {{
        padding: 1.1rem 1.35rem 1rem 1.35rem;
        border-bottom: 1px solid {frame_line};
        display: flex;
        align-items: center;
        gap: 0.9rem;
        justify-content: space-between;
    }}
    .pomodoro-topbar-left {{
        display: flex;
        align-items: center;
        gap: 0.75rem;
        min-width: 0;
    }}
    .pomodoro-badge {{
        display: inline-flex;
        align-items: center;
        gap: 0.45rem;
        padding: 0.5rem 0.8rem;
        border-radius: 999px;
        background: {badge_background};
        color: {badge_color};
        font-size: 0.8rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        white-space: nowrap;
    }}
    .pomodoro-role {{
        color: rgba(61, 68, 74, 0.7);
        font-size: 0.78rem;
        text-transform: uppercase;
        letter-spacing: 0.14em;
        font-weight: 700;
    }}
    .pomodoro-progress {{
        flex: 1;
        height: 10px;
        border-radius: 999px;
        background: {progress_background};
        overflow: hidden;
    }}
    .pomodoro-progress-fill {{
        width: {progress_percentage}%;
        height: 100%;
        background: {progress_fill};
    }}
    .pomodoro-body {{
        display: grid;
        grid-template-columns: minmax(250px, 0.95fr) minmax(320px, 1.15fr);
        min-height: 360px;
    }}
    .pomodoro-timer-panel,
    .pomodoro-task-panel {{ padding: 1.8rem 1.5rem; }}
    .pomodoro-timer-panel {{
        background: {panel_tint};
        border-right: 1px solid {frame_line};
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
        text-align: center;
    }}
    .pomodoro-kicker {{
        margin: 0 0 0.9rem 0;
        color: rgba(66, 72, 79, 0.7);
        font-size: 0.78rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.16em;
    }}
    .pomodoro-timer h1 {{
        margin: 0;
        font-size: clamp(4.4rem, 10vw, 7.2rem);
        line-height: 0.95;
        letter-spacing: -0.08em;
        color: {timer_color};
        font-variant-numeric: tabular-nums;
        font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
    }}
    .pomodoro-timer p {{
        margin: 0.9rem 0 0 0;
        color: #59616a;
        font-size: 0.92rem;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        max-width: 280px;
    }}
    .pomodoro-task-panel {{
        display: flex;
        flex-direction: column;
        justify-content: flex-start;
        text-align: left;
        color: {text_color};
        position: relative;
    }}
    .pomodoro-task-panel h2 {{
        margin: 0 0 0.45rem 0;
        font-size: clamp(1.55rem, 3vw, 2.45rem);
        line-height: 1.08;
    }}
    .pomodoro-task-panel p {{
        margin: 0;
        max-width: 540px;
        color: {description_color};
        font-size: 1rem;
        line-height: 1.5;
    }}
    .pomodoro-meta {{
        margin-top: 1.15rem;
        display: flex;
        flex-wrap: wrap;
        gap: 0.48rem;
        justify-content: flex-start;
    }}
    .pomodoro-pill {{
        padding: 0.52rem 0.72rem;
        border-radius: 14px;
        background: rgba(108, 115, 123, 0.07);
        border: 1px solid rgba(87, 95, 102, 0.08);
        color: #4b5259;
        font-size: 0.8rem;
        font-weight: 600;
        letter-spacing: 0.01em;
        white-space: nowrap;
    }}
    .pomodoro-control-slot {{
        margin-top: 1.15rem;
        width: 100%;
        min-height: 3.45rem;
    }}
    .pomodoro-gridline {{
        margin-top: 1rem;
        height: 1px;
        width: 100%;
        background: linear-gradient(90deg, rgba(70, 77, 84, 0.18) 0%, rgba(70, 77, 84, 0.02) 100%);
    }}
    @media (max-width: 720px) {{
        .pomodoro-overlay {{ padding: 1rem; }}
        .pomodoro-topbar {{
            flex-direction: column;
            align-items: stretch;
        }}
        .pomodoro-topbar-left {{ justify-content: space-between; }}
        .pomodoro-body {{ grid-template-columns: 1fr; }}
        .pomodoro-timer-panel {{
            border-right: 0;
            border-bottom: 1px solid {frame_line};
        }}
        .pomodoro-task-panel {{ text-align: center; }}
        .pomodoro-meta {{ justify-content: center; }}
    }}
    </style>
    <div class="pomodoro-overlay" aria-live="polite">
        <div class="pomodoro-shell">
            <div class="pomodoro-card">
                <div class="pomodoro-topbar">
                    <div class="pomodoro-topbar-left">
                        <div class="pomodoro-badge">{badge_label}</div>
                        <div class="pomodoro-role">{role_label}</div>
                    </div>
                    <div class="pomodoro-progress"><div class="pomodoro-progress-fill"></div></div>
                </div>
                <div class="pomodoro-body">
                    <div class="pomodoro-timer-panel">
                        <div class="pomodoro-kicker">{card_kicker}</div>
                        <div class="pomodoro-timer">
                            <h1>{minutes:02d}:{seconds:02d}</h1>
                            <p>{status_label}</p>
                        </div>
                    </div>
                    <div class="pomodoro-task-panel">
                        <h2>{card_title}</h2>
                        <p>{card_description}</p>
                        <div class="pomodoro-gridline"></div>
                        <div class="pomodoro-meta">
                            <span class="pomodoro-pill">{iteration_pill}</span>
                            <span class="pomodoro-pill">{duration_pill}</span>
                            {task_duration_pill}
                        </div>
                        {control_html}
                    </div>
                </div>
            </div>
        </div>
    </div>
    """).strip()
    if hasattr(st, "html"):
        st.html(overlay_html)
    else:
        components.html(overlay_html, height=900, scrolling=False)


def should_render_pomodoro_session_only(services: PomodoroServices) -> bool:
    """Return whether the app should render only the focus overlay."""

    overlay_state = get_pomodoro_overlay_state()
    if not overlay_state or overlay_state.get("mode") not in {"work", "rest"}:
        return False
    if services.body_doubling_session_active():
        return False
    if services.get_open_task_guidance_expires_at() is not None:
        return False
    if st.session_state.get(SPRINT_REVIEW_PENDING_KEY):
        return False
    if st.session_state.get(REST_RESUME_PROMPT_PENDING_KEY):
        return False
    if st.session_state.get(REST_MESSAGE_EXPIRES_AT_KEY) is not None:
        return False

    timer_snapshot = services.get_work_timer_snapshot()
    return bool(timer_snapshot.running and timer_snapshot.expires_at is not None)


def should_render_pomodoro_session_with_guidance_only(services: PomodoroServices) -> bool:
    """Return whether guidance should render on top of the focus overlay only."""

    overlay_state = get_pomodoro_overlay_state()
    if not overlay_state or overlay_state.get("mode") not in {"work", "rest"}:
        return False
    if services.body_doubling_session_active():
        return False
    if services.get_open_task_guidance_expires_at() is None:
        return False

    timer_snapshot = services.get_work_timer_snapshot()
    return bool(timer_snapshot.running and timer_snapshot.expires_at is not None)


def render_pomodoro_session_controls(
    services: PomodoroServices,
    *,
    expire_sprint_callback: Callable[..., None],
    expire_chunk_callback: Callable[..., None],
) -> None:
    """Render the floating stop/resume controls used by focus and rest overlays."""

    overlay_state = get_pomodoro_overlay_state() or {}
    if not overlay_state:
        return

    overlay_mode = overlay_state.get("mode")
    cycle_type = overlay_state.get("cycle_type", "pomodoro")
    if overlay_mode not in {"work", "rest"}:
        return

    if overlay_mode == "rest":
        current_adaptation, _ = services.get_current_task_adaptation(
            services.get_tasks_dataframe()
        )
        rest_protected = bool(
            current_adaptation
            and current_adaptation.protect_rest_breaks_with_messages
        )
        prompt_context = get_rest_resume_prompt_context() or {}

        st.markdown(
            """
            <style>
            div.element-container:has(.pomodoro-rest-resume-anchor)
            + div.element-container div[data-testid="stButton"] {
                position: fixed;
                right: max(calc(50vw - 490px + 1.5rem + 220px + 0.9rem), 2rem);
                top: 50%;
                transform: translateY(132px);
                z-index: 100001;
                width: 260px;
            }
            div.element-container:has(.pomodoro-rest-finish-anchor)
            + div.element-container div[data-testid="stButton"] {
                position: fixed;
                right: max(calc(50vw - 490px + 1.5rem), 2rem);
                top: 50%;
                transform: translateY(132px);
                z-index: 100001;
                width: 220px;
            }
            div.element-container:has(.pomodoro-rest-resume-anchor)
            + div.element-container div[data-testid="stButton"] > button,
            div.element-container:has(.pomodoro-rest-finish-anchor)
            + div.element-container div[data-testid="stButton"] > button {
                width: 100%;
                min-height: 3.1rem;
                border-radius: 16px;
                font-weight: 600;
                font-size: 1rem;
                padding: 0.7rem 1rem;
                transition: none !important;
                animation: none !important;
                will-change: auto !important;
            }
            div.element-container:has(.pomodoro-rest-resume-anchor)
            + div.element-container div[data-testid="stButton"] > button:hover,
            div.element-container:has(.pomodoro-rest-resume-anchor)
            + div.element-container div[data-testid="stButton"] > button:focus,
            div.element-container:has(.pomodoro-rest-resume-anchor)
            + div.element-container div[data-testid="stButton"] > button:focus-visible,
            div.element-container:has(.pomodoro-rest-finish-anchor)
            + div.element-container div[data-testid="stButton"] > button:hover,
            div.element-container:has(.pomodoro-rest-finish-anchor)
            + div.element-container div[data-testid="stButton"] > button:focus,
            div.element-container:has(.pomodoro-rest-finish-anchor)
            + div.element-container div[data-testid="stButton"] > button:focus-visible {
                transform: none !important;
                box-shadow: none !important;
                filter: none !important;
                transition: none !important;
                animation: none !important;
            }
            @media (max-width: 720px) {
                div.element-container:has(.pomodoro-rest-resume-anchor)
                + div.element-container div[data-testid="stButton"] {
                    left: 50%;
                    top: auto;
                    bottom: 5.9rem;
                    transform: translateX(-50%);
                    width: min(420px, calc(100vw - 2rem));
                }
                div.element-container:has(.pomodoro-rest-finish-anchor)
                + div.element-container div[data-testid="stButton"] {
                    left: 50%;
                    top: auto;
                    bottom: 2rem;
                    transform: translateX(-50%);
                    width: min(420px, calc(100vw - 2rem));
                }
            }
            </style>
            """,
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="pomodoro-rest-resume-anchor"></div>',
            unsafe_allow_html=True,
        )
        if st.button(
            "End rest and resume work",
            key="pomodoro_end_rest_and_resume_work",
            use_container_width=True,
            disabled=rest_protected,
        ):
            resume_work_after_rest(prompt_context, services)
            return
        st.markdown(
            '<div class="pomodoro-rest-finish-anchor"></div>',
            unsafe_allow_html=True,
        )
        if st.button(
            "End rest and finish",
            key="pomodoro_finish_after_rest_early",
            use_container_width=True,
            disabled=rest_protected,
        ):
            finalize_post_rest_finish(prompt_context, services)
            services.rerun()
            return
        return

    if cycle_type == "pomodoro":
        button_label = "Interrupt sprint"
        button_key = "pomodoro_interrupt_sprint"
        border_color = "rgba(52, 77, 112, 0.22)"
        background = "rgba(247, 249, 252, 0.98)"
        text_color = "#344d70"
        shadow_color = "rgba(26, 41, 58, 0.16)"
        on_click = expire_sprint_callback
    elif cycle_type == "chunk":
        button_label = "Interrupt cycle"
        button_key = "chunk_interrupt_cycle"
        border_color = "rgba(59, 68, 80, 0.22)"
        background = "rgba(247, 249, 251, 0.98)"
        text_color = "#3b4450"
        shadow_color = "rgba(25, 31, 38, 0.16)"
        on_click = expire_chunk_callback
    else:
        return

    st.markdown(
        f"""
        <style>
        div.element-container:has(.pomodoro-control-anchor)
        + div.element-container div[data-testid="stButton"] {{
            position: fixed;
            left: 50%;
            top: 50%;
            transform: translate(96px, 132px);
            z-index: 100001;
            width: 300px;
            max-width: calc(100vw - 5rem);
        }}
        div.element-container:has(.pomodoro-control-anchor)
        + div.element-container div[data-testid="stButton"] > button {{
            width: 100%;
            min-height: 3.45rem;
            border-radius: 18px;
            border: 1px solid {border_color};
            background: {background};
            color: {text_color};
            box-shadow: 0 14px 36px {shadow_color};
            font-weight: 700;
        }}
        @media (max-width: 720px) {{
            div.element-container:has(.pomodoro-control-anchor)
            + div.element-container div[data-testid="stButton"] {{
                left: 50%;
                top: auto;
                bottom: 2rem;
                transform: translateX(-50%);
                width: min(360px, calc(100vw - 2rem));
            }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown('<div class="pomodoro-control-anchor"></div>', unsafe_allow_html=True)
    if st.button(button_label, key=button_key, use_container_width=True):
        on_click()


def clear_sprint_review_state() -> None:
    """Clear pending sprint-review dialog state."""

    st.session_state.pop(SPRINT_REVIEW_PENDING_KEY, None)
    st.session_state.pop("adaptive_rest_skip_confirmed", None)


@st.dialog("Sprint review", on_dismiss=clear_sprint_review_state)
def sprint_review_dialog(services: PomodoroServices) -> None:
    """Render the review dialog shown after a Pomodoro sprint finishes."""

    open_task = services.get_open_task_row()
    if open_task:
        st.subheader(open_task["title"])
    else:
        st.subheader("Pomodoro sprint finished")
    task_complete_col, new_cycle_col, finish_col = st.columns(3, gap="small")

    try:
        with task_complete_col:
            if st.button(
                "Task complete",
                key="sprint_review_task_complete",
                type="primary",
                use_container_width=True,
            ):
                if not open_task:
                    st.warning("There is no open task to mark as completed.")
                    return
                services.request_task_completion_feedback(
                    open_task,
                    "sprint_review",
                    rest_choice="No",
                )
                clear_sprint_review_state()
                services.rerun()
                return

        with new_cycle_col:
            if st.button(
                "Continue",
                key="sprint_review_new_cycle",
                use_container_width=True,
            ):
                begin_pomodoro_rest_break(
                    previous_work_outcome="incomplete",
                    services=services,
                )
                clear_sprint_review_state()
                services.rerun()
                return

        with finish_col:
            if st.button(
                "Finish",
                key="sprint_review_finish",
                use_container_width=True,
            ):
                services.clear_task_completion_feedback_request()
                services.disable_work_timer()
                clear_pomodoro_overlay_state()
                if open_task:
                    next_status = services.get_post_work_incomplete_task_status(open_task)
                    if next_status != "open":
                        services.update_task_status(open_task, next_status)
                services.notify_work_ended()
                clear_sprint_review_state()
                services.rerun()
                return
    except Exception as error:
        services.handle_api_exception(error, f"Could not finish sprint review: {error}")


def render_sprint_review_dialog(services: PomodoroServices) -> None:
    """Show the sprint review dialog when pending."""

    if st.session_state.get(SPRINT_REVIEW_PENDING_KEY):
        sprint_review_dialog(services)


@st.dialog("Rest")
def rest_resume_prompt_dialog(services: PomodoroServices) -> None:
    """Ask whether the user wants to resume work after a rest break."""

    prompt_context = get_rest_resume_prompt_context() or {}
    if not prompt_context:
        return

    services.display_message(
        "POMODORO_REST_OVER_RESUME_WORK",
        services.get_adaptive_message_intensity(),
        renderer="info",
    )

    resume_col, finish_col = st.columns(2, gap="medium")
    with resume_col:
        if st.button(
            "Resume work",
            key="rest_resume_work_button",
            type="primary",
            use_container_width=True,
        ):
            resume_work_after_rest(prompt_context, services)
            return
    with finish_col:
        if st.button("Finish", key="rest_finish_button", use_container_width=True):
            finalize_post_rest_finish(prompt_context, services)
            services.rerun()


def render_rest_resume_prompt_dialog(services: PomodoroServices) -> None:
    """Render the post-rest resume/finish decision dialog when pending."""

    if st.session_state.get(REST_RESUME_PROMPT_PENDING_KEY):
        rest_resume_prompt_dialog(services)


def resume_work_after_rest(
    prompt_context: dict[str, Any] | None,
    services: PomodoroServices,
) -> None:
    """Resume work immediately after a Pomodoro-style rest break."""

    open_task = services.get_open_task_row()
    resume_cycle_type = (prompt_context or {}).get("resume_cycle_type") or "pomodoro"
    work_duration_minutes = int(
        (prompt_context or {}).get("work_duration_minutes")
        or get_effective_pomodoro_sprint_minutes(services)
    )
    clear_rest_resume_prompt_context()
    clear_rest_message()
    if not open_task:
        services.notify_work_ended()
        services.rerun()
        return
    if resume_cycle_type == "chunk":
        services.request_next_chunk_cycle(
            open_task,
            source_label="chunk_rest_resume_work",
        )
    else:
        services.schedule_work_timer(
            work_duration_minutes,
            services.expire_sprint_callback,
            "pomodoro_rest_resume_work",
        )
        start_pomodoro_overlay(open_task, work_duration_minutes, services)
    services.rerun()


@st.dialog("Rest")
def rest_message_dialog(services: PomodoroServices) -> None:
    """Render the short informational message shown after rest without context."""

    message = st.session_state.get(REST_MESSAGE_KEY)
    if not message:
        return

    st.write(message)
    services.render_voice_message_button(
        message,
        "rest_message",
        modal_expiry_key=REST_MESSAGE_EXPIRES_AT_KEY,
    )
    st.caption("This message closes automatically, but voice playback keeps it open a bit longer.")


def render_rest_message_dialog(services: PomodoroServices) -> None:
    """Render the rest message dialog while its expiry window is active."""

    expires_at = st.session_state.get(REST_MESSAGE_EXPIRES_AT_KEY)
    if expires_at is None:
        return

    if datetime.now(pytz.UTC).timestamp() >= float(expires_at):
        clear_rest_message()
        return

    rest_message_dialog(services)
