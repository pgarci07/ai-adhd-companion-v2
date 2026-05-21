# Main Streamlit UI orchestrator for authentication, tasks, timers, and adaptive flows.
import sys
import os
import json
import base64
import logging
import warnings
import hashlib
import inspect
import html
import textwrap
import re
from math import ceil
from pathlib import Path

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

# Some third-party Streamlit components still rely on the legacy `st.cache`
# decorator internally. We don't use it in this app anymore, but we silence
# that specific deprecation warning to keep the UI launch clean.
warnings.filterwarnings(
    "ignore",
    message=r".*`st\.cache` is deprecated.*",
)

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from datetime import datetime, time, timedelta, date
from dateutil.rrule import rrulestr
import pytz # Recomendado para manejo de zonas horarias
from st_aggrid import AgGrid, GridOptionsBuilder
from app.config import (
    CHUNK_TIMER_DEFAULT_SECONDS,
    CHUNK_PERSONA_STATE_MODIFIERS,
    DEFAULT_COMPLETED_TASK_LOOKBACK_DAYS,
    DEFAULT_CHUNK_MIN_FLOOR_MINUTES,
    DEFAULT_MAX_CONTINUOUS_WORK_MINUTES,
    DEFAULT_PLANNER_TIMEOUT_MINUTES,
    DEFAULT_REST_DURATION_MINUTES,
    DEFAULT_SESSION_EXTENSION_TOLERANCE,
    EDIT_TASK_MAX_DATE,
    EDIT_TASK_MIN_DATE,
    GRID_ACTIVE_STATUSES,
    GRID_NEVER_VISIBLE_STATUSES,
    LOGOUT_FAREWELL_PROMPT_PATH,
    OPEN_TASK_GUIDANCE_MODAL_SECONDS,
    OPENAI_LOG_PATH,
    OPENAI_MODEL,
    PLANNER_TIMER_SOURCE_LABEL,
    POMODORO_REST_MINUTES,
    POMODORO_SPRINT_TEST_MINUTES,
    REST_MESSAGE_MODAL_SECONDS,
    STATE_TIME_RECENT_SESSIONS_LIMIT,
    SUPPORTED_LANGUAGES,
    SUPPORTED_TIME_MANAGEMENT_METHODS,
    TIMER_LOG_PATH,
    VALID_FIRST_DAY_OF_WEEK_VALUES,
    WELCOME_PROMPT_PATH,
    WORK_SESSION_RESUME_GRACE_HOURS,
    WORK_SESSION_STATE_REPROMPT_HOURS,
    WORK_TIMER_EXPIRY_STATE_NAME,
)
from app.ui import body_doubling, audio_support
from app.ui.state.timers import (
    INACTIVITY_TIMER_KEY,
    PLANNER_TIMER_KEY,
    WORK_TIMER_KEY,
    get_planner_timer,
    get_work_timer,
)
from app.application.adaptive import (
    display_message,
    get_delete_dialog_copy,
    normalize_message_intensity,
    should_display_message,
    task_adaptation,
)
from app.application.prompts.fallback_messages import (
    LOGOUT_FAREWELL_PROMPT_FALLBACK,
    REGISTRATION_WELCOME_PROMPT_FALLBACK,
    build_logout_farewell_fallback,
    build_open_task_guidance_fallback,
    build_registration_welcome_fallback,
)
from app.application.use_cases import (
    active_task_grid,
    personas_catalog,
    user_state_machine,
    task_state_machine,
)

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

try:
    from streamlit_cookies_manager import EncryptedCookieManager
except ImportError:
    EncryptedCookieManager = None

# Compatibility fallbacks for deployments that may still import an older
# `body_doubling` module while `main.py` expects newer session keys.
BODY_DOUBLING_RESULT_DIALOG_KEY = getattr(
    body_doubling,
    "BODY_DOUBLING_RESULT_DIALOG_KEY",
    "body_doubling_result_dialog",
)
BODY_DOUBLING_RESULT_NOTICE_KEY = getattr(
    body_doubling,
    "BODY_DOUBLING_RESULT_NOTICE_KEY",
    "body_doubling_result_notice",
)

# Import the personas module itself rather than binding `PERSONAS` directly at
# import time. Streamlit hot-reload can briefly expose partially initialised
# modules during a cold container restart, and module-level attribute imports
# are more brittle in that window than calling the cached accessors lazily.
supabase = personas_catalog.get_supabase_client()


def get_personas():
    """Return the cached personas catalogue on demand.

    Keeping this behind a function avoids tying the main UI import path to a
    module-level `PERSONAS` attribute that may not be ready during the very
    first hot-reload cycle after a container restart.
    """

    return personas_catalog.get_personas_catalog()

# Lookup tables used to populate task-dimension widgets and scoring metadata.
LOOKUP_TABLES = (
    "dim_task_sizes",
    "dim_task_consequences",
    "dim_task_frictions",
)
# User states that can initialise a new focused session.
INITIAL_SESSION_STATE_NAMES = {"Frozen", "Engaged"}
# State names that the user may select manually from the UI.
USER_SELECTABLE_STATE_NAMES = {"Frozen", "Engaged", "Recovery"}
# Cookie and session keys used for Supabase authentication persistence.
AUTH_COOKIE_KEY = "supabase_auth_session"
AUTH_SESSION_STATE_KEY = "supabase_auth_session_payload"
WORK_SESSION_SUSPENSION_COOKIE_KEY = "work_session_suspension"
AUTH_REFRESH_MARGIN_SECONDS = 60
AUTH_RESTORED_FROM_COOKIE_KEY = "auth_restored_from_cookie"
# Session keys for transient welcome and task-guidance messages.
REGISTRATION_WELCOME_MESSAGE_KEY = "registration_welcome_message"
OPEN_TASK_GUIDANCE_MESSAGE_KEY = "open_task_guidance_message"
OPEN_TASK_GUIDANCE_EXPIRES_AT_KEY = "open_task_guidance_expires_at"
# Session keys for task-opening and new-task dialog state.
OPEN_TASK_DIALOG_TASK_KEY = "open_task_dialog_task"
OPEN_TASK_DIALOG_SOURCE_KEY = "open_task_dialog_source"
OPEN_TASK_DIALOG_EXECUTION_STATE_KEY = "open_task_dialog_execution_state"
OPEN_TASK_DIALOG_GRID_CONTEXT_KEY = "open_task_dialog_grid_context"
OPEN_TASK_PENDING_CONTEXT_KEY = "open_task_pending_context"
OPEN_TASK_START_CONTEXT_KEY = "open_task_start_context"
# Legacy name kept for compatibility in session state. The value represents a
# latent execution state waiting for the next real task opening, whether it was
# requested from login or from the quick state buttons.
LOGIN_AUTO_OPEN_STATE_KEY = "login_auto_open_state"
# ``GUIDED_OPEN_REQUEST_PENDING_KEY`` models a one-shot request to try a guided
# opening immediately from the current visible grid. It is used for explicit
# entrypoints such as login/welcome or the quick "Set Frozen/Engaged" buttons.
# If no candidate exists in that current surface, the request should expire
# instead of silently re-triggering later after unrelated filter changes.
GUIDED_OPEN_REQUEST_PENDING_KEY = "guided_open_request_pending"
# ``GUIDED_AUTO_OPEN_CHAIN_ACTIVE_KEY`` models a broader adaptive workflow:
# once an auto-open proposal has actually been launched, the app keeps offering
# new candidates after accepted work cycles until the FSM stops the chain
# (for example because rejections reached Z, candidates were exhausted, or the
# user session moved to Recovery). This separates "please try now" from "we are
# currently inside a guided working chain".
GUIDED_AUTO_OPEN_CHAIN_ACTIVE_KEY = "guided_auto_open_chain_active"
ACTIVE_TASK_GRID_KIND_KEY = "active_task_grid_kind"
ACTIVE_SUBTASK_PARENT_TASK_ID_KEY = "active_subtask_parent_task_id"
ACTIVE_SUBTASK_PARENT_INSTANCE_ID_KEY = "active_subtask_parent_instance_id"
MY_TASKS_FILTER_SETTINGS_KEY = "my_tasks_filter_settings"
TASK_SEARCH_FILTER_SETTINGS_KEY = "task_search_filter_settings"
TRANSIENT_CONNECTION_RECOVERY_COUNT_KEY = "transient_connection_recovery_count"
NEW_TASK_DIALOG_PARENT_KEY = "new_task_dialog_parent"
NEW_TASK_PARENT_INSTANCE_KEY = "new_task_parent_instance_id"
NEW_TASK_RESET_PENDING_KEY = "new_task_reset_pending"
# Session key used to defer task completion until optional feedback is collected.
TASK_COMPLETION_FEEDBACK_REQUEST_KEY = "task_completion_feedback_request"
# Session keys for focus overlays and active focus-cycle metadata.
POMODORO_OVERLAY_STATE_KEY = "pomodoro_overlay_state"
FOCUS_CYCLE_TRACKER_KEY = "focus_cycle_tracker"
CHUNK_CONTINUOUS_WORK_SECONDS_KEY = "chunk_continuous_work_seconds"
OVERLAY_ACTION_QUERY_KEY = "_overlay_action"
SPRINT_REVIEW_PENDING_KEY = "sprint_review_pending"
CHUNK_REVIEW_PENDING_KEY = "chunk_review_pending"
CHUNK_SESSION_EXTENSION_PROMPT_CONTEXT_KEY = "chunk_session_extension_prompt_context"
REST_MESSAGE_KEY = "rest_message"
REST_MESSAGE_EXPIRES_AT_KEY = "rest_message_expires_at"
REST_RESUME_PROMPT_CONTEXT_KEY = "rest_resume_prompt_context"
REST_RESUME_PROMPT_PENDING_KEY = "rest_resume_prompt_pending"
RESUMABLE_SESSION_ELAPSED_SECONDS_KEY = "resumable_session_elapsed_seconds"
CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY = "chunk_remaining_minutes_by_instance"
# Session keys for persistent page notices and logout confirmation.
ADAPTIVE_NOTICE_QUEUE_KEY = "adaptive_notice_message"
TIMING_NOTICE_QUEUE_KEY = "timing_notice_message"
PLANNER_TIMER_RESTART_AFTER_NOTICE_KEY = "planner_timer_restart_after_notice"
SESSION_SUMMARY_MESSAGE_KEY = "session_summary_message"
LOGOUT_CONFIRM_DIALOG_KEY = "logout_confirm_dialog"
LOGOUT_FAREWELL_MESSAGE_KEY = "logout_farewell_message"
# Session keys used by manual and automatic voice playback.
VOICE_MESSAGE_CACHE_KEY = "voice_message_audio_cache"
AUTO_VOICE_MESSAGES_ENABLED_KEY = "auto_voice_messages_enabled"
AUTO_VOICE_REQUESTED_KEY = "auto_voice_messages_requested"
VOICE_AUTOPLAY_PENDING_KEY = "voice_autoplay_pending"
VOICE_AUTOPLAY_RENDERED_KEY = "voice_autoplay_rendered"
# Session keys that prevent duplicate minute-chime playback across reruns.
MINUTE_CHIME_STATE_KEY = "minute_chime_state"
MINUTE_CHIME_PENDING_TOKEN_KEY = "minute_chime_pending_token"
MINUTE_CHIME_RENDERED_TOKEN_KEY = "minute_chime_rendered_token"
# Session keys for the short celebratory cue played when the user moves from
# Frozen to Engaged. This is separate from the minute chime because it is tied
# to a specific FSM transition rather than elapsed timer milestones.
ENGAGED_CHEER_PENDING_TOKEN_KEY = "engaged_cheer_pending_token"
ENGAGED_CHEER_RENDERED_TOKEN_KEY = "engaged_cheer_rendered_token"
# Preference and session keys for adaptive guidance and auto-open behaviour.
ADAPTIVE_NOTICE_DISMISS_PREFERENCE_KEY = "hide_adaptive_guidance_notice"
ADAPTIVE_AUTO_OPEN_SIGNATURE_KEY = "adaptive_auto_open_signature"
# Session key that remembers which task instances were already offered during
# the current adaptive auto-open chain, so the next rerun can offer a different
# task instead of reopening one the user just tried without completing.
ADAPTIVE_AUTO_OPEN_OFFERED_INSTANCES_KEY = "adaptive_auto_open_offered_instances"
ADAPTIVE_AUTO_OPEN_REJECTED_INSTANCES_KEY = "adaptive_auto_open_rejected_instances"
# When adaptive guidance reaches a parent/container task, the UI keeps this
# parent id so the child grid can stay visible while the app offers subtasks
# sequentially instead of silently skipping the whole container.
ADAPTIVE_ACTIVE_PARENT_TASK_ID_KEY = "adaptive_active_parent_task_id"
# Preference key that preserves completed tasks with meaningful feedback during
# delete flows that support keeping worthy historical records.
KEEP_WORTHY_PREFERENCE_KEY = "keep_worthy"

# Bootstrap expected session keys so reruns always start from a known shape.
if "user_id" not in st.session_state:
    st.session_state["user_id"] = None
if "show_welcome_dialog" not in st.session_state:
    st.session_state["show_welcome_dialog"] = False
if "session_expected_work_time" not in st.session_state:
    st.session_state["session_expected_work_time"] = None
if RESUMABLE_SESSION_ELAPSED_SECONDS_KEY not in st.session_state:
    st.session_state[RESUMABLE_SESSION_ELAPSED_SECONDS_KEY] = 0
if CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY not in st.session_state:
    st.session_state[CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY] = {}
if "current_page" not in st.session_state:
    st.session_state["current_page"] = "tasks"
if "tasks_grid_version" not in st.session_state:
    st.session_state["tasks_grid_version"] = 0
if AUTO_VOICE_MESSAGES_ENABLED_KEY not in st.session_state:
    st.session_state[AUTO_VOICE_MESSAGES_ENABLED_KEY] = True
if AUTH_RESTORED_FROM_COOKIE_KEY not in st.session_state:
    st.session_state[AUTH_RESTORED_FROM_COOKIE_KEY] = False

# --- CONFIGURACIÓN DE PÁGINA ---
# Configure the single-page Streamlit application shell.
st.set_page_config(page_title="AI-ADHD", layout="wide", initial_sidebar_state="collapsed")


def load_app_css():
    """Load the shared Streamlit CSS overrides once at app startup."""

    css_path = Path(__file__).resolve().parent / "styles" / "app.css"
    if css_path.exists():
        st.markdown(
            f"<style>{css_path.read_text(encoding='utf-8')}</style>",
            unsafe_allow_html=True,
        )


load_app_css()

if EncryptedCookieManager:
    # Shared encrypted cookie store used to restore Supabase auth sessions.
    cookies = EncryptedCookieManager(
        prefix="ai-adhd-companion/",
        password=os.environ.get("COOKIES_PASSWORD", os.environ.get("SUPABASE_KEY", "ai-adhd-dev-cookie")),
    )
    if not cookies.ready():
        st.stop()
else:
    # Explicit fallback when encrypted cookies are unavailable in the environment.
    cookies = None

def to_supabase_date(date_value):
    """Convert a Python date object into the ISO date format expected by Supabase."""

    if not date_value:
        return None
    return date_value.isoformat()


def combine_date_and_time(selected_date, selected_time):
    """Combine date and time widgets into a UTC ISO timestamp string."""

    combined = datetime.combine(selected_date, selected_time)
    return combined.replace(tzinfo=pytz.UTC).isoformat()


def combine_date_and_time_value(selected_date, selected_time):
    """Combine date and time widgets into a timezone-aware UTC datetime."""

    combined = datetime.combine(selected_date, selected_time)
    return combined.replace(tzinfo=pytz.UTC)


def get_next_available_time(base_datetime=None):
    """Round a reference time up to the next full hour for task defaults."""

    current_dt = base_datetime.astimezone(pytz.UTC) if base_datetime else datetime.now(pytz.UTC)
    next_hour = current_dt.replace(minute=0, second=0, microsecond=0)
    if current_dt.minute > 0 or current_dt.second > 0 or current_dt.microsecond > 0:
        next_hour += timedelta(hours=1)
    return next_hour.time().replace(tzinfo=None)


def get_new_task_schedule_defaults(selected_date, all_day, base_datetime=None):
    """Return the default schedule values for the compact new-task dialog."""

    current_dt = base_datetime.astimezone(pytz.UTC) if base_datetime else datetime.now(pytz.UTC)
    today = current_dt.date()

    if selected_date > today:
        if all_day:
            return {
                "start_time": time(8, 0),
                "due_date": selected_date,
                "due_time": time(18, 0),
            }
        return {
            "start_time": time(8, 0),
            "due_date": selected_date,
            "due_time": time(9, 0),
        }

    rounded_start_time = get_next_available_time(current_dt)
    rounded_start_dt = datetime.combine(selected_date, rounded_start_time)

    if all_day:
        if rounded_start_time > time(18, 0):
            due_time = (rounded_start_dt + timedelta(hours=1)).time()
        else:
            due_time = time(18, 0)
        return {
            "start_time": rounded_start_time,
            "due_date": selected_date,
            "due_time": due_time,
        }

    return {
        "start_time": rounded_start_time,
        "due_date": selected_date,
        "due_time": (rounded_start_dt + timedelta(hours=1)).time(),
    }


def build_rrule(
    frequency,
    interval_value,
    byweekday_values=None,
    until_value=None,
):
    """Build a recurrence-rule string from the recurrence form fields."""

    parts = [f"FREQ={frequency}", f"INTERVAL={interval_value}"]

    if byweekday_values:
        parts.append(f"BYDAY={','.join(byweekday_values)}")

    if until_value:
        parts.append(f"UNTIL={until_value.strftime('%Y%m%dT%H%M%SZ')}")

    return ";".join(parts)


def format_task_datetime(value):
    """Format a task timestamp for detailed display in the UI."""

    parsed = parse_task_datetime(value)
    if parsed is None:
        return "-"
    return parsed.strftime("%Y-%m-%d %H:%M UTC")


def format_task_grid_date(value, *, now_utc=None):
    """Format task dates with short relative labels for the next few days."""

    if value is None:
        return "-"

    parsed = parse_task_datetime(value)
    if parsed is None:
        return "-"

    reference_now = now_utc or datetime.now(pytz.UTC)
    day_delta = (parsed.date() - reference_now.date()).days
    if day_delta == -3:
        return "Three days ago"
    if day_delta == -2:
        return "Two days ago"
    if day_delta == -1:
        return "Yesterday"
    if day_delta == 0:
        return "Today"
    if day_delta == 1:
        return "Tomorrow"
    if day_delta == 2:
        return "In two days"
    if day_delta == 3:
        return "In three days"
    return parsed.strftime("%Y-%m-%d %H:%M UTC")


def get_week_period_bounds(reference_utc):
    """Return the current week bounds using the preferred first day setting."""

    first_day = get_first_day_of_week()
    weekday_map = {
        "MO": 0,
        "TU": 1,
        "WE": 2,
        "TH": 3,
        "FR": 4,
        "SA": 5,
        "SU": 6,
    }
    target_weekday = weekday_map.get(first_day, 6)
    days_since_start = (reference_utc.weekday() - target_weekday) % 7
    week_start = (reference_utc - timedelta(days=days_since_start)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return week_start, week_start + timedelta(days=7)


def get_month_period_bounds(reference_utc):
    """Return the current calendar-month bounds in UTC."""

    month_start = reference_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if month_start.month == 12:
        next_month_start = month_start.replace(year=month_start.year + 1, month=1)
    else:
        next_month_start = month_start.replace(month=month_start.month + 1)
    return month_start, next_month_start


def get_user_status_log_entries(statuses, date_from=None, date_to=None):
    """Fetch task-status log rows for the current user in an optional period."""

    task_rows = get_task_rows()
    if not task_rows:
        return []

    instance_ids = [row["instance_id"] for row in task_rows if row.get("instance_id")]
    if not instance_ids:
        return []

    task_metadata = {row["instance_id"]: row for row in task_rows if row.get("instance_id")}

    query = (
        supabase.table("task_instance_status_log")
        .select("id, instance_changed_id, new_status_id, changed_at")
        .in_("instance_changed_id", instance_ids)
        .in_("new_status_id", list(statuses))
        .order("changed_at", desc=True)
    )

    if date_from is not None:
        query = query.gte("changed_at", date_from.isoformat())
    if date_to is not None:
        query = query.lt("changed_at", date_to.isoformat())

    response = query.execute()
    entries = []
    for row in response.data or []:
        metadata = task_metadata.get(row.get("instance_changed_id"))
        if not metadata:
            continue
        entries.append(
            {
                **metadata,
                "log_id": row.get("id"),
                "log_status": row.get("new_status_id"),
                "log_changed_at": row.get("changed_at"),
            }
        )
    return entries


def get_task_status_change_counts(instance_ids):
    """Return the number of status-log entries recorded for each task instance.

    The main task dataframe uses this to expose a lightweight "how many times
    did this instance change state" signal when the user asks for all fields.
    """

    resolved_instance_ids = [instance_id for instance_id in (instance_ids or []) if instance_id]
    if not resolved_instance_ids:
        return {}

    response = (
        supabase.table("task_instance_status_log")
        .select("instance_changed_id")
        .in_("instance_changed_id", resolved_instance_ids)
        .execute()
    )

    counts_by_instance_id: dict[str, int] = {}
    for row in response.data or []:
        instance_id = row.get("instance_changed_id")
        if not instance_id:
            continue
        counts_by_instance_id[instance_id] = counts_by_instance_id.get(instance_id, 0) + 1
    return counts_by_instance_id


def get_logged_task_model():
    """Build the task-domain model wrapper for the shared Supabase client."""

    return task_state_machine.LoggedTaskModel(supabase_client=supabase)


def sync_parent_task_from_latest_child_instances(parent_task_id, parent_instance_id=None):
    """Recompute a parent task from child tasks and child instances.

    When a parent instance is known, only that occurrence window is refreshed.
    Otherwise the RPC falls back to the latest child occurrences.
    """

    if not parent_task_id:
        return

    payload = {"p_task_id": parent_task_id}
    if parent_instance_id:
        payload["p_parent_instance_id"] = parent_instance_id

    supabase.rpc("sync_parent_task_from_latest_child_instances", payload).execute()


def set_user_state_runtime_parameters(**overrides):
    """Apply temporary P/T/M/Z/Y overrides to the user-state machine session."""

    get_logged_user_model().set_runtime_parameter_overrides(overrides)


def reset_user_state_runtime_parameters():
    """Clear temporary user-state-machine runtime overrides from the session."""

    get_logged_user_model().reset_runtime_parameter_overrides()


def build_task_adaptation_context(tasks_df):
    """Assemble the contextual signals needed by the adaptive task matrix."""

    user_fsm_context = st.session_state.get(user_state_machine.USER_FSM_CONTEXT_KEY) or {}
    now_utc = datetime.now(pytz.UTC)
    pending_due_today_count = 0
    if not tasks_df.empty:
        due_dates = tasks_df["due_date"].apply(parse_task_datetime)
        pending_due_today_mask = (
            tasks_df["status"].isin(["ready", "open", "asleep", "debt"])
            & due_dates.apply(lambda value: value is not None and value.date() == now_utc.date())
        )
        pending_due_today_count = int(pending_due_today_mask.sum())

    return {
        "memory_state": user_fsm_context.get("memory_state"),
        "recent_state_sequence": get_recent_user_state_names(limit=3),
        "pending_due_today_count": pending_due_today_count,
    }


def get_current_task_adaptation(tasks_df):
    """Resolve the active adaptive rule for the current persona and state."""

    persona_name = get_current_persona_name()
    state_name = get_current_state_name()
    context = build_task_adaptation_context(tasks_df)
    adaptation = task_adaptation.choose_intervention(persona_name, state_name, context)
    return adaptation, context


def get_current_adaptation_without_tasks():
    """Resolve the current adaptive rule when timer callbacks need lightweight access."""

    persona_name = get_current_persona_name()
    state_name = get_current_state_name()
    empty_tasks_df = pd.DataFrame()
    context = build_task_adaptation_context(empty_tasks_df)
    return task_adaptation.choose_intervention(persona_name, state_name, context)


def maybe_apply_task_adaptation_parameters(adaptation):
    """Push temporary FSM parameter overrides when an adaptation requests them."""

    if adaptation and adaptation.parameter_settings:
        set_user_state_runtime_parameters(**adaptation.parameter_settings)
    else:
        reset_user_state_runtime_parameters()


def maybe_render_adaptive_notice(adaptation):
    """Show the adaptive-guidance catalog message unless the user has hidden it."""

    if not adaptation or not adaptation.guidance_message_id:
        return
    if get_user_preferences().get(ADAPTIVE_NOTICE_DISMISS_PREFERENCE_KEY, False):
        return

    display_message(
        adaptation.guidance_message_id,
        get_adaptive_message_intensity(adaptation),
        renderer="info",
    )
    if st.checkbox("Do not show this adaptive guidance message anymore"):
        save_user_profile_updates(
            preferences_updates={ADAPTIVE_NOTICE_DISMISS_PREFERENCE_KEY: True}
        )
        st.rerun()


def maybe_queue_adaptive_auto_open(
    adaptation,
    primary_task,
    *,
    source="adaptive_auto_open",
    execution_state_name=None,
):
    """Auto-open the top-ranked task once when the adaptation requests it."""

    if not adaptation or not adaptation.auto_open_first_task or not primary_task:
        return
    if st.session_state.get("show_welcome_dialog"):
        return
    if has_active_flow_dialog():
        return
    if st.session_state.get(OPEN_TASK_DIALOG_TASK_KEY):
        return
    if st.session_state.get(OPEN_TASK_PENDING_CONTEXT_KEY):
        return

    guided_parent_task_id = primary_task.get("guided_parent_task_id")
    signature = (
        f"{adaptation.persona_name}:{adaptation.state_name}:"
        f"{primary_task['instance_id']}:{guided_parent_task_id}:{st.session_state.get('tasks_grid_version', 0)}"
    )
    if st.session_state.get(ADAPTIVE_AUTO_OPEN_SIGNATURE_KEY) == signature:
        return

    st.session_state[ADAPTIVE_AUTO_OPEN_SIGNATURE_KEY] = signature
    if guided_parent_task_id:
        st.session_state[ADAPTIVE_ACTIVE_PARENT_TASK_ID_KEY] = guided_parent_task_id
    else:
        st.session_state.pop(ADAPTIVE_ACTIVE_PARENT_TASK_ID_KEY, None)
    # Once a real proposal is launched, the app enters a guided auto-open
    # chain. Future reruns may therefore keep proposing tasks after accepted
    # work cycles until the FSM explicitly stops the chain.
    st.session_state[GUIDED_AUTO_OPEN_CHAIN_ACTIVE_KEY] = True
    mark_adaptive_task_offered(primary_task.get("instance_id"))
    st.session_state[OPEN_TASK_DIALOG_TASK_KEY] = primary_task
    st.session_state[OPEN_TASK_DIALOG_SOURCE_KEY] = source
    st.session_state[OPEN_TASK_DIALOG_GRID_CONTEXT_KEY] = build_open_task_grid_context()
    if execution_state_name:
        st.session_state[OPEN_TASK_DIALOG_EXECUTION_STATE_KEY] = execution_state_name
    else:
        st.session_state.pop(OPEN_TASK_DIALOG_EXECUTION_STATE_KEY, None)
    if source == "login_auto_open":
        st.session_state.pop(LOGIN_AUTO_OPEN_STATE_KEY, None)
        st.session_state.pop(GUIDED_OPEN_REQUEST_PENDING_KEY, None)
    st.rerun()


def get_open_task_execution_adaptation(tasks_df):
    """Resolve the adaptation that should drive the current open-task dialog.

    Login-triggered guided opens are a special case: the user stays in Planner
    until the task FSM actually moves a task instance to ``open``, but the
    dialog defaults should still come from the declared execution state that is
    waiting in memory.
    """

    execution_state_name = st.session_state.get(OPEN_TASK_DIALOG_EXECUTION_STATE_KEY)
    if not execution_state_name:
        return get_current_task_adaptation(tasks_df)

    persona_name = get_current_persona_name()
    context = build_task_adaptation_context(tasks_df)
    context["memory_state"] = execution_state_name
    adaptation = task_adaptation.choose_intervention(
        persona_name,
        execution_state_name,
        context,
    )
    return adaptation, context


def get_guided_auto_open_adaptation(tasks_df):
    """Return the adaptation that currently owns guided task opening.

    There are two related but different modes:

    1. A pending one-shot request created by welcome/login or the quick
       Set Frozen/Set Engaged buttons. That request should only try to open
       from the surface that is visible *right now*.
    2. An active guided chain. Once the app has actually launched an auto-open
       proposal, the chain may continue proposing tasks after each accepted
       work cycle until the FSM ends that chain explicitly.

    When the user is back in Planner during such a chain, the ranking should
    still be driven by the remembered execution state rather than by Planner
    itself.
    """

    execution_state_name = None
    if st.session_state.get(GUIDED_OPEN_REQUEST_PENDING_KEY):
        execution_state_name = st.session_state.get(LOGIN_AUTO_OPEN_STATE_KEY)
    elif (
        st.session_state.get(GUIDED_AUTO_OPEN_CHAIN_ACTIVE_KEY)
        and get_effective_current_state_name() == user_state_machine.PLANNER_STATE
    ):
        execution_state_name = (
            (st.session_state.get(user_state_machine.USER_FSM_CONTEXT_KEY) or {})
            .get("memory_state")
        )

    if execution_state_name not in {
        user_state_machine.FROZEN_STATE,
        user_state_machine.ENGAGED_STATE,
    }:
        return None, None

    persona_name = get_current_persona_name()
    context = build_task_adaptation_context(tasks_df)
    context["memory_state"] = execution_state_name
    adaptation = task_adaptation.choose_intervention(
        persona_name,
        execution_state_name,
        context,
    )
    if not adaptation or not adaptation.auto_open_first_task:
        return None, None
    return adaptation, context


def queue_guided_open_for_state(state_name):
    """Queue a guided task-opening proposal for the requested user state.

    This keeps the user in their current list-management state until the task
    FSM really opens a task instance and emits the corresponding user-state
    event. The requested state only influences the next guided open proposal.
    """

    clear_adaptive_offered_tasks()
    clear_open_task_dialog_state()
    # Manual/login-driven guided-open requests are intentional fresh asks from
    # the user. Clearing the previous signature here allows the same top-ranked
    # task to be proposed again after a prior cancellation.
    st.session_state.pop(ADAPTIVE_AUTO_OPEN_SIGNATURE_KEY, None)
    st.session_state.pop(GUIDED_AUTO_OPEN_CHAIN_ACTIVE_KEY, None)
    st.session_state[LOGIN_AUTO_OPEN_STATE_KEY] = state_name
    st.session_state[GUIDED_OPEN_REQUEST_PENDING_KEY] = True
    current_page = st.session_state.get("current_page")
    if current_page not in {"tasks", "task_search"}:
        current_page = "tasks"
    st.session_state["current_page"] = current_page


def has_active_flow_dialog(*, include_completion_feedback=True):
    """Return whether a non-page dialog is already scheduled for this run."""

    flow_dialog_keys = [
        LOGOUT_CONFIRM_DIALOG_KEY,
        BODY_DOUBLING_RESULT_DIALOG_KEY,
        body_doubling.BODY_DOUBLING_EXTRA_STEP_DIALOG_KEY,
        body_doubling.BODY_DOUBLING_REVIEW_DIALOG_KEY,
        body_doubling.BODY_DOUBLING_SCOPE_DIALOG_KEY,
        SPRINT_REVIEW_PENDING_KEY,
        CHUNK_REVIEW_PENDING_KEY,
    ]
    if include_completion_feedback:
        flow_dialog_keys.append(TASK_COMPLETION_FEEDBACK_REQUEST_KEY)

    return (
        any(bool(st.session_state.get(key)) for key in flow_dialog_keys)
        or bool(st.session_state.get(REST_RESUME_PROMPT_PENDING_KEY))
        or st.session_state.get(REST_MESSAGE_EXPIRES_AT_KEY) is not None
        or st.session_state.get(OPEN_TASK_GUIDANCE_EXPIRES_AT_KEY) is not None
    )


def get_adaptive_offered_instance_ids() -> set[str]:
    """Return task instances already offered in the current adaptive chain."""

    offered_ids = set(st.session_state.get(ADAPTIVE_AUTO_OPEN_OFFERED_INSTANCES_KEY) or [])
    offered_ids.update(st.session_state.get(ADAPTIVE_AUTO_OPEN_REJECTED_INSTANCES_KEY) or [])
    return {
        str(instance_id)
        for instance_id in offered_ids
        if instance_id
    }


def mark_adaptive_task_offered(instance_id: str | None) -> None:
    """Remember one task instance already offered during adaptive auto-open."""

    if not instance_id:
        return

    offered_ids = get_adaptive_offered_instance_ids()
    offered_ids.add(str(instance_id))
    st.session_state[ADAPTIVE_AUTO_OPEN_OFFERED_INSTANCES_KEY] = sorted(offered_ids)


def clear_adaptive_offered_tasks() -> None:
    """Clear the adaptive auto-open offered-task chain once progress resumes."""

    st.session_state.pop(ADAPTIVE_AUTO_OPEN_OFFERED_INSTANCES_KEY, None)
    st.session_state.pop(ADAPTIVE_AUTO_OPEN_REJECTED_INSTANCES_KEY, None)
    st.session_state.pop(ADAPTIVE_ACTIVE_PARENT_TASK_ID_KEY, None)


def get_my_tasks_filter_settings(filter_settings_override=None):
    """Return the current My Tasks visibility settings stored in session state."""

    persisted_settings = dict(
        filter_settings_override
        or st.session_state.get(MY_TASKS_FILTER_SETTINGS_KEY)
        or {}
    )
    active_grid_kind = st.session_state.get(
        ACTIVE_TASK_GRID_KIND_KEY,
        persisted_settings.get("active_grid_kind"),
    )
    show_routines = bool(
        st.session_state.get(
            "tasks_grid_show_routines",
            persisted_settings.get("show_routines", False),
        )
    )
    if active_grid_kind == "periodic":
        show_routines = True
    elif active_grid_kind == "tasks":
        show_routines = False

    return {
        "show_routines": show_routines,
        "show_completed_tasks": bool(
            st.session_state.get(
                "tasks_grid_show_completed_tasks",
                persisted_settings.get("show_completed_tasks", False),
            )
        ),
        "completed_days": int(
            st.session_state.get(
                "tasks_grid_completed_days",
                persisted_settings.get(
                    "completed_days",
                    DEFAULT_COMPLETED_TASK_LOOKBACK_DAYS,
                ),
            )
        ),
        "show_all_columns": bool(
            st.session_state.get(
                "tasks_grid_filter_show_all_fields",
                persisted_settings.get("show_all_columns", False),
            )
        ),
    }


def build_my_tasks_active_grid(tasks_df, adaptation, *, filter_settings_override=None):
    """Build the business-level active grid for the current My Tasks view.

    The builder reuses the page's current filter state and applies the supplied
    adaptation ordering before candidate selection happens. This is the critical
    bridge between "what the user sees" and "what guided open proposes next".
    """

    filter_settings = get_my_tasks_filter_settings(filter_settings_override)
    requested_grid_kind = (
        (filter_settings_override or {}).get("active_grid_kind")
        or st.session_state.get(ACTIVE_TASK_GRID_KIND_KEY)
    )
    requested_parent_task_id = (
        (filter_settings_override or {}).get("active_parent_task_id")
        or st.session_state.get(ACTIVE_SUBTASK_PARENT_TASK_ID_KEY)
    )
    requested_parent_instance_id = (
        (filter_settings_override or {}).get("active_parent_instance_id")
        or st.session_state.get(ACTIVE_SUBTASK_PARENT_INSTANCE_ID_KEY)
    )
    completed_instance_ids: set[str] = set()
    if filter_settings["show_completed_tasks"]:
        completed_cutoff = datetime.now(pytz.UTC) - timedelta(
            days=int(filter_settings["completed_days"])
        )
        recent_completed_entries = get_user_status_log_entries(
            {"completed"},
            date_from=completed_cutoff,
        )
        completed_instance_ids = {
            entry["instance_id"]
            for entry in recent_completed_entries
            if entry.get("status") == "completed"
        }

    if (
        requested_grid_kind == "subtasks"
        and requested_parent_task_id
        and requested_parent_instance_id
    ):
        # A visible secondary subtasks grid becomes a first-class working
        # surface. Guided-open proposals should keep using that ordered child
        # list until the user explicitly switches away, instead of silently
        # jumping back to the root task grid on the next rerun.
        base_visible_tasks_df = tasks_df[
            (tasks_df["is_routine"] == filter_settings["show_routines"])
            & (~tasks_df["status"].isin(GRID_NEVER_VISIBLE_STATUSES))
        ].reset_index(drop=True)
        _, visible_subtasks_df = active_task_grid.split_root_tasks_and_subtasks(
            base_visible_tasks_df
        )
        child_tasks_df = visible_subtasks_df[
            (visible_subtasks_df["parent_task_id"] == requested_parent_task_id)
            & (
                visible_subtasks_df["parent_instance_id"]
                == requested_parent_instance_id
            )
        ].reset_index(drop=True)
        if not child_tasks_df.empty:
            return active_task_grid.build_subtasks_active_grid(
                child_tasks_df,
                adaptation,
            )

    return active_task_grid.build_my_tasks_active_grid(
        tasks_df,
        adaptation,
        show_routines=filter_settings["show_routines"],
        show_completed_tasks=filter_settings["show_completed_tasks"],
        completed_instance_ids=completed_instance_ids,
        never_visible_statuses=GRID_NEVER_VISIBLE_STATUSES,
        active_statuses=GRID_ACTIVE_STATUSES,
    )


def get_task_search_filter_settings(filter_settings_override=None):
    """Return the current Task Search visibility settings stored in session state."""

    persisted_settings = dict(
        filter_settings_override
        or st.session_state.get(TASK_SEARCH_FILTER_SETTINGS_KEY)
        or {}
    )
    return {
        "search_query": str(
            st.session_state.get(
                "task_search_query",
                persisted_settings.get("search_query", ""),
            )
            or ""
        ).strip(),
        "include_routines": bool(
            st.session_state.get(
                "task_search_include_routines",
                persisted_settings.get("include_routines", True),
            )
        ),
        "include_stale": bool(
            st.session_state.get(
                "task_search_include_stale",
                persisted_settings.get("include_stale", False),
            )
        ),
    }


def build_task_search_active_grid(tasks_df, adaptation, *, filter_settings_override=None):
    """Build the business-level active grid for the current Task Search view.

    Search results can still drive guided-open flows, but only from rows that
    remain workable after the visible search filters are applied.
    """

    filter_settings = get_task_search_filter_settings(filter_settings_override)
    requested_grid_kind = (
        (filter_settings_override or {}).get("active_grid_kind")
        or st.session_state.get(ACTIVE_TASK_GRID_KIND_KEY)
    )
    requested_parent_task_id = (
        (filter_settings_override or {}).get("active_parent_task_id")
        or st.session_state.get(ACTIVE_SUBTASK_PARENT_TASK_ID_KEY)
    )
    requested_parent_instance_id = (
        (filter_settings_override or {}).get("active_parent_instance_id")
        or st.session_state.get(ACTIVE_SUBTASK_PARENT_INSTANCE_ID_KEY)
    )
    if (
        requested_grid_kind == "subtasks"
        and requested_parent_task_id
        and requested_parent_instance_id
    ):
        visible_df = tasks_df.copy()
        if not filter_settings["include_stale"]:
            visible_df = visible_df[
                ~visible_df["status"].isin(tuple(GRID_NEVER_VISIBLE_STATUSES))
            ].reset_index(drop=True)
        if not filter_settings["include_routines"]:
            visible_df = visible_df[
                ~visible_df["is_routine"]
            ].reset_index(drop=True)

        search_text = str(filter_settings["search_query"] or "").strip()
        if search_text:
            search_mask = (
                visible_df["title"].fillna("").str.contains(
                    search_text, case=False, regex=False
                )
                | visible_df["description"].fillna("").str.contains(
                    search_text, case=False, regex=False
                )
            )
            visible_df = visible_df[search_mask].reset_index(drop=True)

        _, visible_subtasks_df = active_task_grid.split_root_tasks_and_subtasks(
            visible_df
        )
        child_tasks_df = visible_subtasks_df[
            (visible_subtasks_df["parent_task_id"] == requested_parent_task_id)
            & (
                visible_subtasks_df["parent_instance_id"]
                == requested_parent_instance_id
            )
        ].reset_index(drop=True)
        if not child_tasks_df.empty:
            return active_task_grid.build_subtasks_active_grid(
                child_tasks_df,
                adaptation,
            )

    return active_task_grid.build_task_search_active_grid(
        tasks_df,
        adaptation,
        search_query=filter_settings["search_query"],
        include_routines=filter_settings["include_routines"],
        include_stale=filter_settings["include_stale"],
        never_visible_statuses=GRID_NEVER_VISIBLE_STATUSES,
    )


def build_open_task_grid_context():
    """Capture the current page/grid snapshot used to open the task dialog.

    The dialog can outlive one or more reruns while Streamlit repaints the page
    underneath it. Guided-open follow-up decisions therefore need a stable copy
    of the originating page and filter settings instead of reading whatever
    transient widget state happens to be available later.
    """

    current_page = st.session_state.get("current_page")
    return {
        "origin_page": current_page,
        "active_grid_kind": st.session_state.get(ACTIVE_TASK_GRID_KIND_KEY),
        "active_parent_task_id": st.session_state.get(
            ACTIVE_SUBTASK_PARENT_TASK_ID_KEY
        ),
        "active_parent_instance_id": st.session_state.get(
            ACTIVE_SUBTASK_PARENT_INSTANCE_ID_KEY
        ),
        "my_tasks_filter_settings": (
            get_my_tasks_filter_settings()
            if current_page == "tasks"
            else None
        ),
        "task_search_filter_settings": (
            get_task_search_filter_settings()
            if current_page == "task_search"
            else None
        ),
    }


def get_child_tasks_for_parent_instance(subtasks_df, parent_row):
    """Return only the child-task instances that belong to one parent instance.

    Parent rows in the root grid represent task instances, not abstract task
    definitions. The secondary subtasks grid therefore has to filter by both
    the parent task id and the selected parent instance id so different
    occurrences of the same parent task do not get merged together.
    """

    parent_task_id = parent_row.get("task_id")
    parent_instance_id = parent_row.get("instance_id")
    if not parent_task_id or not parent_instance_id:
        return subtasks_df.iloc[0:0].copy()

    return subtasks_df[
        (subtasks_df["parent_task_id"] == parent_task_id)
        & (subtasks_df["parent_instance_id"] == parent_instance_id)
    ].reset_index(drop=True)


def is_expired_jwt_error(error):
    """Detect the Supabase error patterns that mean the JWT is no longer valid."""

    error_text = str(error)
    return "JWT expired" in error_text or "PGRST303" in error_text


def clear_user_cache_state():
    """Clear cached user/profile/lookup data that depends on authentication."""

    st.session_state.pop("user_profile", None)
    st.session_state.pop("lookup_cache", None)
    st.session_state.pop("states_cache", None)
    st.session_state.pop("all_states_cache", None)


def clear_flow_state():
    """Clear dialogs, timers, overlays, and adaptive state for the current app session."""

    st.session_state.pop(REGISTRATION_WELCOME_MESSAGE_KEY, None)
    st.session_state.pop(OPEN_TASK_GUIDANCE_MESSAGE_KEY, None)
    st.session_state.pop(OPEN_TASK_GUIDANCE_EXPIRES_AT_KEY, None)
    st.session_state.pop(OPEN_TASK_DIALOG_TASK_KEY, None)
    st.session_state.pop(OPEN_TASK_DIALOG_SOURCE_KEY, None)
    st.session_state.pop(OPEN_TASK_DIALOG_EXECUTION_STATE_KEY, None)
    st.session_state.pop(OPEN_TASK_DIALOG_GRID_CONTEXT_KEY, None)
    st.session_state.pop(OPEN_TASK_PENDING_CONTEXT_KEY, None)
    st.session_state.pop(OPEN_TASK_START_CONTEXT_KEY, None)
    st.session_state.pop(LOGIN_AUTO_OPEN_STATE_KEY, None)
    st.session_state.pop(GUIDED_OPEN_REQUEST_PENDING_KEY, None)
    st.session_state.pop(GUIDED_AUTO_OPEN_CHAIN_ACTIVE_KEY, None)
    st.session_state.pop(NEW_TASK_DIALOG_PARENT_KEY, None)
    st.session_state.pop(NEW_TASK_PARENT_INSTANCE_KEY, None)
    st.session_state.pop(NEW_TASK_RESET_PENDING_KEY, None)
    st.session_state.pop(POMODORO_OVERLAY_STATE_KEY, None)
    st.session_state.pop(FOCUS_CYCLE_TRACKER_KEY, None)
    st.session_state.pop(CHUNK_CONTINUOUS_WORK_SECONDS_KEY, None)
    st.session_state.pop(SPRINT_REVIEW_PENDING_KEY, None)
    st.session_state.pop(CHUNK_REVIEW_PENDING_KEY, None)
    st.session_state.pop(REST_MESSAGE_KEY, None)
    st.session_state.pop(REST_MESSAGE_EXPIRES_AT_KEY, None)
    st.session_state.pop(REST_RESUME_PROMPT_CONTEXT_KEY, None)
    st.session_state.pop(REST_RESUME_PROMPT_PENDING_KEY, None)
    st.session_state.pop(RESUMABLE_SESSION_ELAPSED_SECONDS_KEY, None)
    st.session_state.pop(CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY, None)
    st.session_state.pop(VOICE_MESSAGE_CACHE_KEY, None)
    st.session_state.pop(AUTO_VOICE_REQUESTED_KEY, None)
    st.session_state.pop(VOICE_AUTOPLAY_PENDING_KEY, None)
    st.session_state.pop(VOICE_AUTOPLAY_RENDERED_KEY, None)
    st.session_state.pop(MINUTE_CHIME_STATE_KEY, None)
    st.session_state.pop(MINUTE_CHIME_PENDING_TOKEN_KEY, None)
    st.session_state.pop(MINUTE_CHIME_RENDERED_TOKEN_KEY, None)
    st.session_state.pop(ENGAGED_CHEER_PENDING_TOKEN_KEY, None)
    st.session_state.pop(ENGAGED_CHEER_RENDERED_TOKEN_KEY, None)
    st.session_state.pop(ADAPTIVE_AUTO_OPEN_SIGNATURE_KEY, None)
    st.session_state.pop(ADAPTIVE_AUTO_OPEN_OFFERED_INSTANCES_KEY, None)
    st.session_state.pop(ADAPTIVE_AUTO_OPEN_REJECTED_INSTANCES_KEY, None)
    st.session_state.pop(ADAPTIVE_ACTIVE_PARENT_TASK_ID_KEY, None)
    st.session_state.pop(PLANNER_TIMER_RESTART_AFTER_NOTICE_KEY, None)
    clear_page_notices()
    st.session_state.pop(LOGOUT_CONFIRM_DIALOG_KEY, None)
    st.session_state.pop(LOGOUT_FAREWELL_MESSAGE_KEY, None)
    st.session_state.pop(body_doubling.BODY_DOUBLING_FLOW_KEY, None)
    st.session_state.pop(body_doubling.BODY_DOUBLING_SCOPE_DIALOG_KEY, None)
    st.session_state.pop(body_doubling.BODY_DOUBLING_REVIEW_DIALOG_KEY, None)
    st.session_state.pop(body_doubling.BODY_DOUBLING_EXTRA_STEP_DIALOG_KEY, None)
    st.session_state.pop(BODY_DOUBLING_RESULT_DIALOG_KEY, None)
    st.session_state.pop(BODY_DOUBLING_RESULT_NOTICE_KEY, None)
    st.session_state.pop(INACTIVITY_TIMER_KEY, None)
    st.session_state.pop(PLANNER_TIMER_KEY, None)
    st.session_state.pop(WORK_TIMER_KEY, None)
    st.session_state.pop(user_state_machine.USER_FSM_CONTEXT_KEY, None)
    st.session_state["session_expected_work_time"] = None
    st.session_state["show_welcome_dialog"] = False
    st.session_state["current_page"] = "tasks"
    st.session_state["tasks_grid_version"] = 0
    st.session_state[AUTO_VOICE_MESSAGES_ENABLED_KEY] = True
    st.session_state[AUTH_RESTORED_FROM_COOKIE_KEY] = False


def clear_auth_state(*, clear_cookie=True):
    """Clear local authentication identifiers and optionally the persisted cookie."""

    if clear_cookie:
        clear_auth_cookie()
        clear_work_session_suspension_cookie()
    st.session_state["user_id"] = None
    st.session_state.pop(AUTH_SESSION_STATE_KEY, None)


def reset_authenticated_app_state(*, clear_cookie=True):
    """Clear authenticated app state while keeping the cleanup responsibilities explicit."""

    clear_auth_state(clear_cookie=clear_cookie)
    clear_user_cache_state()
    clear_flow_state()


def expire_auth_state(reason="auth_expired"):
    """End the domain session before clearing local authentication state."""

    finalize_session_for_recovery(reason)
    reset_authenticated_app_state(clear_cookie=True)


def close_expired_suspended_session():
    """Firmly close a suspended session after the resume grace window expires.

    A recoverable work-session suspension is intentionally different from a
    real logout: it keeps the Supabase auth cookie around for a limited period
    so the same browser can resume without credentials. Once the grace window
    has elapsed, that distinction no longer applies and we remove both the auth
    cookie and the suspension marker. Supabase sign-out is best-effort because
    the app may not have an active in-memory session while rendering the
    unauthenticated landing page.
    """

    try:
        supabase.auth.sign_out()
    except Exception:
        pass
    reset_authenticated_app_state(clear_cookie=True)


def clear_auth_cookie():
    """Remove the persisted auth cookie without raising user-visible errors."""

    if cookies is None:
        return

    try:
        if AUTH_COOKIE_KEY in cookies:
            del cookies[AUTH_COOKIE_KEY]
            cookies.save()
    except Exception:
        pass


def clear_work_session_suspension_cookie():
    """Remove the recoverable work-session suspension marker."""

    if cookies is None:
        return

    try:
        if WORK_SESSION_SUSPENSION_COOKIE_KEY in cookies:
            del cookies[WORK_SESSION_SUSPENSION_COOKIE_KEY]
            cookies.save()
    except Exception:
        pass


def save_work_session_suspension_cookie(
    reason,
    resume_state,
    resumable_elapsed_seconds,
    chunk_remaining_minutes_by_instance,
):
    """Persist a short-lived marker that allows auth-preserving session resume.

    The marker is intentionally tiny and does not contain Supabase credentials.
    Authentication still comes from ``AUTH_COOKIE_KEY``. This cookie only says:
    "the user was pushed out of the work session for a recoverable reason, at
    this time, and the safest state to restore quickly is X".
    """

    if cookies is None:
        return

    payload = {
        "reason": reason,
        "resume_state": resume_state,
        "suspended_at": datetime.now(pytz.UTC).isoformat(),
        "resumable_elapsed_seconds": int(resumable_elapsed_seconds or 0),
        "chunk_remaining_minutes_by_instance": dict(chunk_remaining_minutes_by_instance or {}),
    }
    try:
        cookies[WORK_SESSION_SUSPENSION_COOKIE_KEY] = json.dumps(payload)
        cookies.save()
    except Exception:
        pass


def load_work_session_suspension_payload():
    """Load the recoverable work-session suspension marker, if any.

    Badly formatted values are treated as stale local state and deleted. That
    keeps the bootstrap path deterministic: either we have a trustworthy
    suspension marker or we behave like a normal unauthenticated landing page.
    """

    raw_payload = cookies.get(WORK_SESSION_SUSPENSION_COOKIE_KEY) if cookies is not None else None
    if not raw_payload:
        return None
    if isinstance(raw_payload, str):
        try:
            raw_payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            clear_work_session_suspension_cookie()
            return None
    if not isinstance(raw_payload, dict):
        clear_work_session_suspension_cookie()
        return None
    return raw_payload


def parse_iso_datetime(value):
    """Parse an ISO timestamp into a timezone-aware UTC datetime."""

    if not value:
        return None
    try:
        parsed_value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed_value.tzinfo is None:
        return parsed_value.replace(tzinfo=pytz.UTC)
    return parsed_value.astimezone(pytz.UTC)


def get_work_session_suspension_age(payload):
    """Return the age of a suspension marker, or None if it cannot be parsed."""

    suspended_at = parse_iso_datetime((payload or {}).get("suspended_at"))
    if not suspended_at:
        return None
    return datetime.now(pytz.UTC) - suspended_at


def get_valid_work_session_suspension_payload():
    """Return a non-expired work-session suspension marker.

    The eight-hour rule is enforced here so every caller shares the same hard
    boundary. Once expired, the session is closed firmly by deleting both the
    auth cookie and the suspension marker.
    """

    payload = load_work_session_suspension_payload()
    if not payload:
        return None
    age = get_work_session_suspension_age(payload)
    if age is None or age > timedelta(hours=WORK_SESSION_RESUME_GRACE_HOURS):
        close_expired_suspended_session()
        return None
    return payload


def decode_jwt_expiry(access_token):
    """Decode the expiry claim from a JWT access token when possible."""

    if not access_token or not isinstance(access_token, str):
        return None

    try:
        payload_segment = access_token.split(".")[1]
        payload_segment += "=" * (-len(payload_segment) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_segment))
        expires_at = payload.get("exp")
        return int(expires_at) if expires_at is not None else None
    except Exception:
        return None


def get_session_expires_at(session):
    """Resolve a Supabase session expiry timestamp from the available fields."""

    expires_at = getattr(session, "expires_at", None)
    if expires_at is not None:
        return int(expires_at)

    expires_in = getattr(session, "expires_in", None)
    if expires_in is not None:
        return int(datetime.now(pytz.UTC).timestamp()) + int(expires_in)

    return decode_jwt_expiry(getattr(session, "access_token", None))


def get_auth_payload_from_response(auth_response):
    """Extract the serialisable auth payload that we persist in session/cookies."""

    session = getattr(auth_response, "session", None)
    if not session:
        return None

    access_token = getattr(session, "access_token", None)
    refresh_token = getattr(session, "refresh_token", None)
    if not access_token or not refresh_token:
        return None

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": get_session_expires_at(session),
    }


def save_auth_cookie(auth_response):
    """Persist the latest auth payload in session state and encrypted cookies."""

    auth_payload = get_auth_payload_from_response(auth_response)
    if not auth_payload:
        return None

    st.session_state[AUTH_SESSION_STATE_KEY] = auth_payload

    if cookies is None:
        return auth_payload

    try:
        cookies[AUTH_COOKIE_KEY] = json.dumps(auth_payload)
        cookies.save()
    except Exception:
        # A cookie failure should not block a valid login.
        pass

    return auth_payload


def load_auth_cookie_payload():
    """Restore the cached auth payload from session state or encrypted cookies."""

    session_payload = st.session_state.get(AUTH_SESSION_STATE_KEY)
    if isinstance(session_payload, dict):
        return session_payload

    raw_session = cookies.get(AUTH_COOKIE_KEY) if cookies is not None else None
    if not raw_session:
        return None

    if isinstance(raw_session, str):
        try:
            raw_session = json.loads(raw_session)
        except json.JSONDecodeError:
            clear_auth_cookie()
            return None

    if not isinstance(raw_session, dict):
        clear_auth_cookie()
        return None

    st.session_state[AUTH_SESSION_STATE_KEY] = raw_session
    return raw_session


def should_refresh_access_token(expires_at):
    """Return whether the access token is near expiry and should be refreshed."""

    if expires_at is None:
        return True

    try:
        expires_at = int(expires_at)
    except (TypeError, ValueError):
        return True

    now_timestamp = int(datetime.now(pytz.UTC).timestamp())
    return expires_at <= now_timestamp + AUTH_REFRESH_MARGIN_SECONDS


def refresh_auth_session(refresh_token):
    """Refresh the current Supabase session using the stored refresh token."""

    if not refresh_token:
        return None

    try:
        auth_response = supabase.auth.refresh_session(refresh_token)
    except Exception:
        return None

    user = getattr(auth_response, "user", None)
    session = getattr(auth_response, "session", None)
    if not user or not session:
        return None

    save_auth_cookie(auth_response)
    st.session_state["user_id"] = user.id
    return auth_response


def try_refresh_cached_auth_session():
    """Refresh the stored Supabase auth session if a refresh token is available."""

    raw_session = load_auth_cookie_payload()
    if not raw_session:
        return None
    return refresh_auth_session(raw_session.get("refresh_token"))


def restore_auth_session_from_cookie(*, force=False):
    """Restore a remembered auth session during app bootstrap.

    Recoverable work-session suspensions deliberately block automatic restore.
    That keeps a Planner-timeout expulsion meaningful: the user lands outside
    the authenticated app shell, but the encrypted Supabase auth cookie remains
    available for the normal Sign in entry point to resume during the grace
    window. Passing ``force=True`` is the resume path and bypasses this guard.
    """

    if cookies is None or st.session_state.get("user_id"):
        return
    if not force and get_valid_work_session_suspension_payload():
        return

    raw_session = load_auth_cookie_payload()
    if not raw_session:
        return

    access_token = raw_session.get("access_token")
    refresh_token = raw_session.get("refresh_token")
    if not access_token or not refresh_token:
        expire_auth_state("invalid_cached_session")
        return

    expires_at = raw_session.get("expires_at") or decode_jwt_expiry(access_token)
    if should_refresh_access_token(expires_at):
        if refresh_auth_session(refresh_token) is None:
            expire_auth_state("refresh_failed")
        return

    try:
        auth_response = supabase.auth.set_session(access_token, refresh_token)
    except Exception:
        if refresh_auth_session(refresh_token) is None:
            expire_auth_state("restore_refresh_failed")
        return

    user = getattr(auth_response, "user", None)
    if not user:
        expire_auth_state("restore_returned_no_user")
        return

    # Cookie-based auth restore should re-enter the authenticated shell with a
    # clean guided-open bootstrap. The durable auth cookie proves identity, but
    # it should not resurrect a stale guided auto-open chain from an older
    # Streamlit run or browser refresh.
    st.session_state.pop(GUIDED_OPEN_REQUEST_PENDING_KEY, None)
    st.session_state.pop(GUIDED_AUTO_OPEN_CHAIN_ACTIVE_KEY, None)
    st.session_state.pop(LOGIN_AUTO_OPEN_STATE_KEY, None)
    save_auth_cookie(auth_response)
    st.session_state["user_id"] = user.id
    st.session_state[AUTH_RESTORED_FROM_COOKIE_KEY] = True


def ensure_fresh_auth_session():
    """Ensure the cached auth session is still valid or refreshed if needed."""

    raw_session = load_auth_cookie_payload()
    if not raw_session:
        return not st.session_state.get("user_id")

    refresh_token = raw_session.get("refresh_token")
    access_token = raw_session.get("access_token")
    expires_at = raw_session.get("expires_at") or decode_jwt_expiry(access_token)

    if should_refresh_access_token(expires_at):
        if refresh_auth_session(refresh_token) is None:
            expire_auth_state("refresh_failed")
            return False
        return True

    return True


def resume_suspended_work_session():
    """Try to resume a recoverable work-session suspension without credentials.

    The encrypted auth cookie is the source of identity; the suspension cookie
    only decides whether the Sign in button should bypass the login form and
    how the ADHD state should be recovered. If the suspension is older than the
    configured hard grace window, the auth cookie is removed and the user must
    sign in again. If the suspension is still fresh but older than the reprompt
    window, we restore authentication but ask the Welcome dialog to collect the
    current state because the old execution state is no longer trustworthy.

    The function returns ``True`` only when the session was resumed and the app
    should continue into the authenticated shell. Returning ``False`` means the
    caller should fall back to the normal sign-in dialog immediately.
    """

    payload = get_valid_work_session_suspension_payload()
    if not payload:
        return False

    restore_auth_session_from_cookie(force=True)
    if not st.session_state.get("user_id"):
        clear_work_session_suspension_cookie()
        return False
    st.session_state[AUTH_RESTORED_FROM_COOKIE_KEY] = False

    refresh_user_profile_cache()
    age = get_work_session_suspension_age(payload)
    should_ask_state = (
        age is None
        or age >= timedelta(hours=WORK_SESSION_STATE_REPROMPT_HOURS)
    )

    if should_ask_state:
        # Recovery has lasted long enough that we should not assume the old
        # Frozen/Engaged state is still accurate. The normal welcome dialog uses
        # LOGIN_DECLARED_EVENT to append the newly declared state to the log.
        st.session_state[RESUMABLE_SESSION_ELAPSED_SECONDS_KEY] = 0
        st.session_state[CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY] = {}
        st.session_state["show_welcome_dialog"] = True
    else:
        resume_state = payload.get("resume_state")
        if resume_state not in {user_state_machine.FROZEN_STATE, user_state_machine.ENGAGED_STATE}:
            resume_state = user_state_machine.FROZEN_STATE
        st.session_state[RESUMABLE_SESSION_ELAPSED_SECONDS_KEY] = int(
            payload.get("resumable_elapsed_seconds", 0) or 0
        )
        st.session_state[CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY] = dict(
            payload.get("chunk_remaining_minutes_by_instance") or {}
        )
        dispatch_user_state_event(
            user_state_machine.MANUAL_SET_STATE_EVENT,
            target_state=resume_state,
        )
        st.session_state["show_welcome_dialog"] = False

    clear_work_session_suspension_cookie()
    st.session_state["current_page"] = "tasks"
    return True


def apply_recent_suspension_state_after_login():
    """Restore a fresh resumable-session state after manual credential login.

    The primary happy path for quick resume is the landing-page Sign in button
    calling ``resume_suspended_work_session()`` directly. In practice, browser
    timing or cookie/plugin races can occasionally force the user through the
    credential dialog even though a fresh suspension marker still exists.

    This helper gives that second path the same <1 hour state restoration
    semantics: reuse the remembered execution state and resumable-session
    counters instead of asking the Welcome dialog for a state the app already
    knows.
    """

    payload = get_valid_work_session_suspension_payload()
    if not payload:
        return False

    age = get_work_session_suspension_age(payload)
    if age is None or age >= timedelta(hours=WORK_SESSION_STATE_REPROMPT_HOURS):
        return False

    resume_state = payload.get("resume_state")
    if resume_state not in {
        user_state_machine.FROZEN_STATE,
        user_state_machine.ENGAGED_STATE,
    }:
        resume_state = user_state_machine.FROZEN_STATE

    st.session_state[RESUMABLE_SESSION_ELAPSED_SECONDS_KEY] = int(
        payload.get("resumable_elapsed_seconds", 0) or 0
    )
    st.session_state[CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY] = dict(
        payload.get("chunk_remaining_minutes_by_instance") or {}
    )
    dispatch_user_state_event(
        user_state_machine.MANUAL_SET_STATE_EVENT,
        target_state=resume_state,
    )
    clear_work_session_suspension_cookie()
    return True


def handle_api_exception(error, fallback_message="Could not complete the request."):
    """Handle auth-expiry errors specially and show a generic fallback otherwise."""

    if is_expired_jwt_error(error):
        if try_refresh_cached_auth_session() is not None:
            st.rerun()
            return True

        expire_auth_state("auth_expired")
        st.error("Your session expired and could not be refreshed. Please sign in again.")
        st.rerun()
        return True

    st.error(fallback_message)
    return False


def get_state_id_name_map():
    """Return a cached mapping from state name to state id."""

    ensure_all_states_cache()
    return {
        state["name"]: state["id"]
        for state in st.session_state.get("all_states_cache", [])
        if state.get("name") and state.get("id") is not None
    }


def get_logged_user_model():
    """Build the user-domain model wrapper backed by session state and Supabase."""

    return user_state_machine.LoggedUserModel(
        supabase_client=supabase,
        session_store=st.session_state,
    )


def normalise_notice_payload(message, *, default_zone="adaptive"):
    """Return a session-storable catalog message payload."""

    if not message:
        return None

    if hasattr(message, "to_dict"):
        message = message.to_dict()
    if not isinstance(message, dict) or not message.get("message_id"):
        return None

    zone = message.get("zone") or default_zone
    if zone not in {"adaptive", "timing"}:
        zone = default_zone

    return {
        "message_id": message["message_id"],
        "zone": zone,
        "renderer": message.get("renderer", "info"),
        "intensity": message.get("intensity"),
        "params": dict(message.get("params") or {}),
    }


def store_page_notice(message, *, default_zone="adaptive", intensity=None):
    """Store the latest persistent My Tasks/Task Search notice for one zone."""

    payload = normalise_notice_payload(message, default_zone=default_zone)
    if not payload:
        return
    if intensity is not None and payload.get("intensity") is None:
        payload["intensity"] = intensity

    target_key = (
        TIMING_NOTICE_QUEUE_KEY
        if payload["zone"] == "timing"
        else ADAPTIVE_NOTICE_QUEUE_KEY
    )
    st.session_state[target_key] = payload


def store_transition_notices(messages, *, intensity=None):
    """Store structured transition messages emitted by the user-state machine."""

    for message in messages or []:
        store_page_notice(message, intensity=intensity)


def clear_page_notices(*, zone=None):
    """Clear persistent page notices for one zone or both zones."""

    if zone in {None, "adaptive"}:
        st.session_state.pop(ADAPTIVE_NOTICE_QUEUE_KEY, None)
    if zone in {None, "timing"}:
        st.session_state.pop(TIMING_NOTICE_QUEUE_KEY, None)


def parse_actual_duration_to_minutes(duration_text):
    """Parse flexible `D/H/M` duration tokens into integer minutes.

    The feedback dialog is hand-typed, so we accept compact human formats such
    as `3D`, `3D:2H`, `2H:3M`, `1D:3M`, and the original `00D:00H:00M`.
    """

    cleaned_text = (duration_text or "").strip()
    if not cleaned_text:
        return None

    total_minutes = 0
    seen_units = set()
    previous_unit_rank = -1
    unit_rank = {"D": 0, "H": 1, "M": 2}
    unit_minutes = {"D": 24 * 60, "H": 60, "M": 1}

    for token in cleaned_text.split(":"):
        token_match = re.fullmatch(r"(?i)\s*(\d+)\s*([DHM])\s*", token)
        if not token_match:
            raise ValueError(
                "Use formats such as 3D, 3D:2H, 2H:3M, or 1D:3M for actual duration."
            )

        amount = int(token_match.group(1))
        unit = token_match.group(2).upper()
        if unit in seen_units or unit_rank[unit] <= previous_unit_rank:
            raise ValueError(
                "Write actual duration from larger units to smaller ones, without repeating units."
            )

        seen_units.add(unit)
        previous_unit_rank = unit_rank[unit]
        total_minutes += amount * unit_minutes[unit]

    return total_minutes


def format_actual_duration_minutes(total_minutes):
    """Format persisted actual-duration minutes as `DD:HH:MM` for feedback forms."""

    if total_minutes is None or total_minutes == "":
        return ""

    total_minutes = int(total_minutes or 0)
    days_value, day_remainder = divmod(total_minutes, 24 * 60)
    hours_value, minutes_value = divmod(day_remainder, 60)
    return f"{days_value:02d}D:{hours_value:02d}H:{minutes_value:02d}M"


def save_task_completion_feedback(
    task_row,
    *,
    final_comments,
    actual_friction_id,
    actual_duration_minutes,
):
    """Persist optional completion feedback on the completed task instance."""

    if not task_row or not task_row.get("instance_id"):
        raise ValueError("Task instance id is required to save completion feedback.")

    (
        supabase.table("task_instances")
        .update(
            {
                "final_comments": final_comments,
                "actual_friction_id": actual_friction_id,
                "actual_duration": actual_duration_minutes,
            }
        )
        .eq("id", task_row["instance_id"])
        .execute()
    )


def request_task_completion_feedback(task_row, source, **payload):
    """Queue the completion-feedback dialog before marking one task done."""

    st.session_state[TASK_COMPLETION_FEEDBACK_REQUEST_KEY] = {
        "task_row": dict(task_row or {}),
        "source": source,
        "payload": payload,
    }


def clear_task_completion_feedback_request():
    """Remove any pending completion-feedback dialog request."""

    st.session_state.pop(TASK_COMPLETION_FEEDBACK_REQUEST_KEY, None)


def notify_work_ended():
    """Tell the FSM that the current work phase has ended and Planner may resume.

    This event is broader than task completion: it also covers cases where the
    user intentionally stops working on an unfinished task after a Chunk cycle
    or after the rest decision dialog. Planner can then decide whether to
    auto-open a new task or simply leave the user in list-selection mode.
    """

    current_state = get_effective_current_state_name()
    if current_state not in {user_state_machine.FROZEN_STATE, user_state_machine.ENGAGED_STATE}:
        return

    dispatch_user_state_event(user_state_machine.WORK_ENDED_EVENT)


def get_pomodoro_overlay_state():
    """Read the current focus-overlay payload from session state."""

    return st.session_state.get(POMODORO_OVERLAY_STATE_KEY)


def clear_pomodoro_overlay_state():
    """Remove the active focus overlay from session state."""

    st.session_state.pop(POMODORO_OVERLAY_STATE_KEY, None)


def get_focus_cycle_tracker():
    """Return the current per-task focus-cycle tracker."""

    return st.session_state.get(FOCUS_CYCLE_TRACKER_KEY, {})


def start_focus_cycle_tracker(task_row, cycle_type):
    """Start or increment the focus-cycle counter for one task and cycle type."""

    tracker = get_focus_cycle_tracker()
    previous_instance_id = tracker.get("instance_id")
    previous_cycle_type = tracker.get("cycle_type")
    previous_iterations = int(tracker.get("iterations", 0) or 0)
    iterations = (
        previous_iterations + 1
        if previous_instance_id == task_row.get("instance_id") and previous_cycle_type == cycle_type
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


def clear_focus_cycle_tracker():
    """Clear the active focus-cycle tracker."""

    st.session_state.pop(FOCUS_CYCLE_TRACKER_KEY, None)


def format_cycle_minutes_label(duration_seconds):
    """Format a duration in seconds as a compact minute label."""

    minutes = round(float(duration_seconds or 0) / 60.0, 1)
    if minutes.is_integer():
        return str(int(minutes))
    return str(minutes)


def get_pomodoro_overlay_opacity():
    """Choose the overlay opacity, allowing adaptive rules to override it."""

    adaptation, _ = get_current_task_adaptation(get_tasks_dataframe())
    if adaptation and adaptation.opaque_guided_pomodoro_overlay:
        return 0.96
    return 0.76


def start_pomodoro_overlay(task_row, duration_minutes):
    """Create the Pomodoro focus overlay state for the opened task."""

    tracker = start_focus_cycle_tracker(task_row, "pomodoro")
    iterations = int(tracker.get("iterations", 1) or 1)
    enriched_task_row = (
        get_enriched_task_row_by_instance_id(task_row.get("instance_id"))
        if task_row and task_row.get("instance_id")
        else None
    )
    task_duration_minutes = (
        (enriched_task_row or {}).get("size_minutes")
        if enriched_task_row
        else task_row.get("size_minutes")
    )
    st.session_state[POMODORO_OVERLAY_STATE_KEY] = {
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


def start_pomodoro_rest_overlay(duration_minutes):
    """Switch the active overlay into Pomodoro rest mode."""

    previous_state = get_pomodoro_overlay_state() or {}
    st.session_state[POMODORO_OVERLAY_STATE_KEY] = {
        **previous_state,
        "duration_minutes": int(duration_minutes),
        "duration_seconds": int(duration_minutes) * 60,
        "started_at": datetime.now(pytz.UTC).timestamp(),
        "mode": "rest",
        "cycle_type": "pomodoro",
    }


def start_chunk_overlay(task_row, duration_seconds):
    tracker = start_focus_cycle_tracker(task_row, "chunk")
    iterations = int(tracker.get("iterations", 1) or 1)
    enriched_task_row = (
        get_enriched_task_row_by_instance_id(task_row.get("instance_id"))
        if task_row and task_row.get("instance_id")
        else None
    )
    task_duration_minutes = (
        (enriched_task_row or {}).get("size_minutes")
        if enriched_task_row
        else task_row.get("size_minutes")
    )
    st.session_state[POMODORO_OVERLAY_STATE_KEY] = {
        "instance_id": task_row.get("instance_id"),
        "task_id": task_row.get("task_id"),
        "title": task_row.get("title"),
        "description": task_row.get("description"),
        "task_duration_minutes": task_duration_minutes,
        "duration_seconds": int(duration_seconds),
        "duration_minutes_label": format_cycle_minutes_label(duration_seconds),
        "iterations": iterations,
        "started_at": datetime.now(pytz.UTC).timestamp(),
        "mode": "work",
        "cycle_type": "chunk",
    }


def get_next_chunk_work_seconds(task_row=None):
    """Return the next Chunk work-block duration in seconds."""

    chunk_plan = calculate_next_chunk_plan(task_row)
    return max(60, int(round(float(chunk_plan["duration_minutes"]) * 60)))


def calculate_next_chunk_plan(task_row=None):
    """Calculate the next Chunk work block and whether session extension is needed.

    Chunk mode treats expected session length as a soft planning estimate. We
    honour it with a tolerance window, but we prefer a user-configured minimum
    useful block length over absurdly tiny chunks. If even that floor would
    exceed the tolerated session overrun, we pause and ask the user whether to
    extend the expected session time.
    """

    resolved_task_row = task_row or get_open_task_row()
    size_minutes = float(get_chunk_remaining_minutes(resolved_task_row))
    elapsed_session_minutes = float(get_resumable_session_elapsed_minutes())
    expected_session_minutes = max(1.0, float(get_effective_session_work_time()))
    remaining_continuous_minutes = float(get_time_to_max_continuous_work_minutes())
    chunk_floor_minutes = float(get_chunk_min_floor_minutes())
    session_tolerance = get_chunk_session_extension_tolerance()

    stamina_factor = max(
        0.0,
        1.0 - (elapsed_session_minutes / expected_session_minutes),
    )
    work_base = size_minutes * stamina_factor

    persona_name = task_adaptation.PERSONA_NAME_ALIASES.get(
        str(get_current_persona_name() or "").strip().lower(),
        str(get_current_persona_name() or "").strip().lower(),
    )
    state_name = task_adaptation.STATE_NAME_ALIASES.get(
        str(get_effective_current_state_name() or "").strip().lower(),
        str(get_effective_current_state_name() or "").strip().lower(),
    )
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


def clear_chunk_session_extension_prompt():
    """Clear any pending Chunk session-extension confirmation dialog."""

    st.session_state.pop(CHUNK_SESSION_EXTENSION_PROMPT_CONTEXT_KEY, None)


def queue_chunk_session_extension_prompt(chunk_plan, *, source_label):
    """Persist a pending Chunk extension prompt until the dialog consumes it."""

    st.session_state[CHUNK_SESSION_EXTENSION_PROMPT_CONTEXT_KEY] = {
        **dict(chunk_plan or {}),
        "source_label": source_label,
    }


def request_next_chunk_cycle(task_row=None, *, source_label):
    """Start the next Chunk cycle or queue a session-extension confirmation.

    This helper centralises every entry into Chunk mode so first cycles,
    repeated cycles, and post-rest resumptions all use the same duration logic
    and the same extension-confirmation behaviour.
    """

    resolved_task_row = task_row or get_open_task_row()
    if not resolved_task_row:
        return False

    clear_chunk_session_extension_prompt()
    chunk_plan = calculate_next_chunk_plan(resolved_task_row)
    if chunk_plan["status"] == "needs_session_extension":
        queue_chunk_session_extension_prompt(chunk_plan, source_label=source_label)
        return False

    duration_minutes = float(chunk_plan["duration_minutes"])
    duration_seconds = int(duration_minutes * 60)
    schedule_work_timer(
        duration_minutes,
        eoChunk,
        source_label,
    )
    start_chunk_overlay(resolved_task_row, duration_seconds)
    return True


def get_effective_pomodoro_sprint_minutes():
    """Return the Pomodoro focus length for the current user session.

    The working-preferences form persists this setting under the `sprint`
    preference key. The legacy test constant remains as a safe fallback for
    older profiles or any transient situation where preferences are not yet
    available.
    """

    return int(
        get_user_preferences().get(
            "sprint",
            POMODORO_SPRINT_TEST_MINUTES,
        )
    )


def get_max_continuous_work_minutes():
    """Return the session-level cap before Chunk mode forces a rest break."""

    return int(
        get_user_preferences().get(
            "max_continuous_work_minutes",
            DEFAULT_MAX_CONTINUOUS_WORK_MINUTES,
        )
    )


def get_effective_rest_duration_minutes():
    """Return the preferred duration for Pomodoro-style rest blocks.

    Rest time is intentionally shared across classic Pomodoro and any other
    flow that explicitly routes the user into a "take a break now" phase, so a
    single preference keeps those recovery moments consistent.
    """

    return int(
        get_user_preferences().get(
            "rest_duration",
            DEFAULT_REST_DURATION_MINUTES,
        )
    )


def get_chunk_min_floor_minutes():
    """Return the preferred minimum useful size for one Chunk work block."""

    return int(
        get_user_preferences().get(
            "chunk_min_floor_minutes",
            DEFAULT_CHUNK_MIN_FLOOR_MINUTES,
        )
    )


def get_chunk_session_extension_tolerance():
    """Return the tolerated overrun beyond the expected session length.

    The expected session time is a planning estimate, not a hard law. Chunk
    mode therefore allows a small configured overshoot before asking the user
    to extend the session explicitly.
    """

    return float(DEFAULT_SESSION_EXTENSION_TOLERANCE)


def get_chunk_continuous_work_seconds():
    """Return accumulated Chunk work seconds for the current authenticated session."""

    return int(st.session_state.get(CHUNK_CONTINUOUS_WORK_SECONDS_KEY, 0) or 0)


def add_chunk_continuous_work_seconds(additional_seconds):
    """Accumulate elapsed Chunk work across tasks until a forced rest resets it."""

    additional_seconds = max(0, int(additional_seconds or 0))
    st.session_state[CHUNK_CONTINUOUS_WORK_SECONDS_KEY] = (
        get_chunk_continuous_work_seconds() + additional_seconds
    )


def reset_chunk_continuous_work_seconds():
    """Clear the Chunk continuous-work accumulator after a forced rest starts."""

    st.session_state[CHUNK_CONTINUOUS_WORK_SECONDS_KEY] = 0


def get_chunk_remaining_minutes(task_row):
    """Return the remaining Chunk size for one task instance in this resumable session.

    The first Chunk cycle uses the original task size. Each completed or
    interrupted work cycle then subtracts the real elapsed work time so the next
    cycle uses the remaining effort instead of the original estimate.
    """

    if not task_row:
        return CHUNK_TIMER_DEFAULT_SECONDS / 60.0

    resolved_task_row = (
        get_enriched_task_row_by_instance_id(task_row.get("instance_id"))
        or task_row
    )
    size_minutes = resolved_task_row.get("size_minutes")
    if pd.isna(size_minutes) if size_minutes is not None else True:
        custom_sizes = get_user_preferences().get("custom_sizes", [15, 30, 60, 180, 720])
        size_id = resolved_task_row.get("size_id")
        if size_id and 0 < int(size_id) <= len(custom_sizes):
            size_minutes = int(custom_sizes[int(size_id) - 1])
        else:
            size_minutes = CHUNK_TIMER_DEFAULT_SECONDS / 60.0

    remaining_by_instance = dict(st.session_state.get(CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY) or {})
    instance_id = resolved_task_row.get("instance_id")
    if not instance_id:
        return float(size_minutes)

    if instance_id not in remaining_by_instance:
        remaining_by_instance[instance_id] = float(size_minutes)
        st.session_state[CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY] = remaining_by_instance
        return float(size_minutes)

    remaining_minutes = float(remaining_by_instance.get(instance_id) or 0.0)
    if remaining_minutes <= 0:
        # If the user asks for another Chunk cycle after exhausting the
        # original estimate, treat that as an under-estimated task and fall
        # back to half of the original task size.
        return max(1.0, float(size_minutes) / 2.0)

    return remaining_minutes


def register_chunk_work_elapsed(task_row, elapsed_seconds):
    """Subtract real worked time from the remaining Chunk size of one instance."""

    if not task_row:
        return

    resolved_task_row = (
        get_enriched_task_row_by_instance_id(task_row.get("instance_id"))
        or task_row
    )
    instance_id = resolved_task_row.get("instance_id")
    if not instance_id:
        return

    remaining_by_instance = dict(st.session_state.get(CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY) or {})
    remaining_minutes = get_chunk_remaining_minutes(resolved_task_row)
    worked_minutes = max(0.0, float(elapsed_seconds or 0) / 60.0)
    remaining_by_instance[instance_id] = max(0.0, remaining_minutes - worked_minutes)
    st.session_state[CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY] = remaining_by_instance


def clear_chunk_remaining_minutes(task_row):
    """Forget Chunk remaining-size state for one instance when it is no longer useful."""

    if not task_row:
        return

    instance_id = task_row.get("instance_id")
    if not instance_id:
        return

    remaining_by_instance = dict(st.session_state.get(CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY) or {})
    remaining_by_instance.pop(instance_id, None)
    st.session_state[CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY] = remaining_by_instance


def get_query_param_value(param_name):
    try:
        value = st.query_params.get(param_name)
        if isinstance(value, list):
            return value[0] if value else None
        return value
    except Exception:
        try:
            params = st.experimental_get_query_params()
        except Exception:
            return None
        values = params.get(param_name)
        return values[0] if values else None


def clear_query_param(param_name):
    try:
        if param_name in st.query_params:
            del st.query_params[param_name]
        return
    except Exception:
        pass

    try:
        params = st.experimental_get_query_params()
        params.pop(param_name, None)
        st.experimental_set_query_params(**params)
    except Exception:
        return


def handle_overlay_action_query_params():
    action = get_query_param_value(OVERLAY_ACTION_QUERY_KEY)
    if not action:
        return

    clear_query_param(OVERLAY_ACTION_QUERY_KEY)

    overlay_state = get_pomodoro_overlay_state() or {}
    overlay_mode = overlay_state.get("mode")
    cycle_type = overlay_state.get("cycle_type", "pomodoro")

    if action == "interrupt_chunk" and overlay_mode == "work" and cycle_type == "chunk":
        eoChunk()
    elif action == "end_rest_early" and overlay_mode == "rest":
        eoRest()


def render_pomodoro_overlay():
    overlay_state = get_pomodoro_overlay_state()
    if not overlay_state or overlay_state.get("mode") not in {"work", "rest"}:
        return

    timer_snapshot = get_work_timer_snapshot()
    if not timer_snapshot.running or timer_snapshot.expires_at is None:
        return

    remaining_seconds = max(0, int(timer_snapshot.expires_at - datetime.now(pytz.UTC).timestamp()))
    total_seconds = max(1, int(timer_snapshot.duration_seconds or overlay_state.get("duration_seconds") or (overlay_state.get("duration_minutes") or 1) * 60))
    elapsed_seconds = max(0, total_seconds - remaining_seconds)
    progress_percentage = min(100, max(0, int((elapsed_seconds / total_seconds) * 100)))
    minutes = remaining_seconds // 60
    seconds = remaining_seconds % 60
    opacity = get_pomodoro_overlay_opacity()
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
        "Step away from the task for a moment. The app will bring you back when the rest timer ends."
        if is_rest_mode
        else safe_description
    )
    duration_label = (
        str(int(overlay_state.get("duration_minutes", 0) or 0))
        if not is_chunk_mode
        else str(overlay_state.get("duration_minutes_label") or format_cycle_minutes_label(total_seconds))
    )
    iteration_pill = (
        f"Cycle iteration {iterations}"
        if is_chunk_mode
        else f"Pomodoro iteration {iterations}"
    )
    duration_pill = (
        f"{'Rest' if is_rest_mode else ('Cycle' if is_chunk_mode else 'Sprint')} length {duration_label} min"
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
    .pomodoro-overlay::before {{
        content: none;
    }}
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
    .pomodoro-task-panel {{
        padding: 1.8rem 1.5rem;
    }}
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
        .pomodoro-overlay {{
            padding: 1rem;
        }}
        .pomodoro-topbar {{
            flex-direction: column;
            align-items: stretch;
        }}
        .pomodoro-topbar-left {{
            justify-content: space-between;
        }}
        .pomodoro-body {{
            grid-template-columns: 1fr;
        }}
        .pomodoro-timer-panel {{
            border-right: 0;
            border-bottom: 1px solid {frame_line};
        }}
        .pomodoro-task-panel {{
            text-align: center;
        }}
        .pomodoro-meta {{
            justify-content: center;
        }}
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


def should_render_pomodoro_session_only():
    """Return whether the authenticated app should be replaced by the focus overlay."""

    overlay_state = get_pomodoro_overlay_state()
    if not overlay_state or overlay_state.get("mode") not in {"work", "rest"}:
        return False
    if body_doubling.should_render_body_doubling_session_only():
        return False
    if st.session_state.get(OPEN_TASK_GUIDANCE_EXPIRES_AT_KEY) is not None:
        return False
    if st.session_state.get(SPRINT_REVIEW_PENDING_KEY):
        return False
    if st.session_state.get(REST_RESUME_PROMPT_PENDING_KEY):
        return False
    if st.session_state.get(REST_MESSAGE_EXPIRES_AT_KEY) is not None:
        return False

    timer_snapshot = get_work_timer_snapshot()
    return bool(timer_snapshot.running and timer_snapshot.expires_at is not None)


def should_render_pomodoro_session_with_guidance_only():
    """Return whether guidance should render on top of the focus overlay only.

    Streamlit reruns the whole page while the task-start guidance modal stays
    open for voice playback. If we let the normal page render underneath during
    those reruns, the user briefly sees grids, sidebar, and other UI layers
    flashing below the overlay. This helper keeps the app in an overlay-only
    mode for that temporary guidance window.
    """

    overlay_state = get_pomodoro_overlay_state()
    if not overlay_state or overlay_state.get("mode") not in {"work", "rest"}:
        return False
    if body_doubling.should_render_body_doubling_session_only():
        return False
    if st.session_state.get(OPEN_TASK_GUIDANCE_EXPIRES_AT_KEY) is None:
        return False

    timer_snapshot = get_work_timer_snapshot()
    return bool(timer_snapshot.running and timer_snapshot.expires_at is not None)


def render_pomodoro_session_controls():
    """Render the stop/interrupt control used by rest and chunk overlays."""

    overlay_state = get_pomodoro_overlay_state() or {}
    if not overlay_state:
        return

    overlay_mode = overlay_state.get("mode")
    cycle_type = overlay_state.get("cycle_type", "pomodoro")
    if overlay_mode not in {"work", "rest"}:
        return

    if overlay_mode == "rest":
        current_adaptation, _ = get_current_task_adaptation(get_tasks_dataframe())
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
        st.markdown('<div class="pomodoro-rest-resume-anchor"></div>', unsafe_allow_html=True)
        if st.button(
            "End rest and resume work",
            key="pomodoro_end_rest_and_resume_work",
            use_container_width=True,
            disabled=rest_protected,
        ):
            resume_work_after_rest(prompt_context)
            return
        st.markdown('<div class="pomodoro-rest-finish-anchor"></div>', unsafe_allow_html=True)
        if st.button(
            "End rest and finish",
            key="pomodoro_finish_after_rest_early",
            use_container_width=True,
            disabled=rest_protected,
        ):
            finalize_post_rest_finish(prompt_context)
            st.rerun()
            return
        return

    if overlay_mode == "rest":
        return
    elif cycle_type == "pomodoro":
        button_label = "Interrupt sprint"
        button_key = "pomodoro_interrupt_sprint"
        border_color = "rgba(52, 77, 112, 0.22)"
        background = "rgba(247, 249, 252, 0.98)"
        text_color = "#344d70"
        shadow_color = "rgba(26, 41, 58, 0.16)"
        on_click = eoSprint
    elif cycle_type == "chunk":
        button_label = "Interrupt cycle"
        button_key = "chunk_interrupt_cycle"
        border_color = "rgba(59, 68, 80, 0.22)"
        background = "rgba(247, 249, 251, 0.98)"
        text_color = "#3b4450"
        shadow_color = "rgba(25, 31, 38, 0.16)"
        on_click = eoChunk
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
        return


if hasattr(st, "fragment"):
    @st.fragment
    def render_pomodoro_session_controls_fragment():
        render_pomodoro_session_controls()
else:
    def render_pomodoro_session_controls_fragment():
        render_pomodoro_session_controls()


def render_body_doubling_session_controls():
    """Render the manual end control for an active Body-Doubling micro-session."""

    if not body_doubling.should_render_body_doubling_session_only():
        return

    st.markdown(
        """
        <style>
        div[data-testid="stButton"]:has(button[kind="secondary"]) {
            position: fixed;
            left: 50%;
            bottom: clamp(2rem, 6vh, 4.25rem);
            transform: translateX(-50%);
            z-index: 100001;
            width: min(560px, calc(100vw - 5rem));
        }
        div[data-testid="stButton"]:has(button[kind="secondary"]) > button {
            width: 100%;
            min-height: 3.25rem;
            border-radius: 16px;
            border: 1px solid rgba(59, 68, 80, 0.22);
            background: rgba(247, 249, 251, 0.98);
            color: #3b4450;
            box-shadow: 0 14px 36px rgba(25, 31, 38, 0.16);
            font-weight: 700;
        }
        @media (max-width: 720px) {
            div[data-testid="stButton"]:has(button[kind="secondary"]) {
                left: 50%;
                bottom: 1.35rem;
                transform: translateX(-50%);
                width: min(360px, calc(100vw - 2rem));
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    if st.button("End micro-session", key="body_doubling_end_micro_session"):
        disable_work_timer()
        body_doubling.end_body_doubling_micro_session()


def render_page_notice_zone(session_key, *, fallback_intensity="high"):
    """Render one persistent page notice zone from the catalog."""

    message = st.session_state.get(session_key)
    if not message or not message.get("message_id"):
        return

    display_message(
        message["message_id"],
        message.get("intensity") or fallback_intensity,
        renderer=message.get("renderer", "info"),
        **(message.get("params") or {}),
    )
    if (
        session_key == TIMING_NOTICE_QUEUE_KEY
        and st.session_state.pop(PLANNER_TIMER_RESTART_AFTER_NOTICE_KEY, False)
        and get_effective_current_state_name() == user_state_machine.PLANNER_STATE
    ):
        reset_planner_timer()


def render_adaptive_page_notices():
    """Render persistent adaptive and timing notices for task list pages."""

    render_page_notice_zone(ADAPTIVE_NOTICE_QUEUE_KEY)
    render_page_notice_zone(TIMING_NOTICE_QUEUE_KEY)


def render_session_summary_message():
    """Render and clear the stored session summary message."""

    message = st.session_state.get(SESSION_SUMMARY_MESSAGE_KEY)
    if not message:
        return

    st.info(message)
    st.session_state.pop(SESSION_SUMMARY_MESSAGE_KEY, None)


@st.dialog("Completion feedback")
def task_completion_feedback_dialog():
    """Collect optional feedback before marking a task instance as completed."""

    request_payload = st.session_state.get(TASK_COMPLETION_FEEDBACK_REQUEST_KEY)
    if not request_payload:
        return

    task_row = request_payload.get("task_row") or {}
    source = request_payload.get("source")
    payload = request_payload.get("payload") or {}

    st.write(f"Before completing **{task_row.get('title', 'this task')}**, you can optionally record some feedback.")

    friction_options = [None] + get_lookup_options("dim_task_frictions")
    selected_actual_friction = st.selectbox(
        "Actual friction",
        options=friction_options,
        index=1 if len(friction_options) > 1 else 0,
        format_func=lambda item: "No value" if item is None else format_lookup_option(item),
        key=f"completion_feedback_actual_friction_{task_row.get('instance_id')}",
    )
    actual_duration_text = st.text_input(
        "Actual duration",
        placeholder="00D:00H:00M",
        key=f"completion_feedback_actual_duration_{task_row.get('instance_id')}",
    )
    final_comments = st.text_area(
        "Final comments",
        placeholder="Optional reflections about how the task actually went",
        key=f"completion_feedback_final_comments_{task_row.get('instance_id')}",
        height=120,
    )

    submit_column, skip_column = st.columns(2, gap="small")
    with submit_column:
        submit_clicked = st.button(
            "Save feedback and complete",
            type="primary",
            use_container_width=True,
            key=f"completion_feedback_submit_{task_row.get('instance_id')}",
        )
    with skip_column:
        skip_clicked = st.button(
            "Complete without feedback",
            use_container_width=True,
            key=f"completion_feedback_skip_{task_row.get('instance_id')}",
        )

    if not submit_clicked and not skip_clicked:
        return

    try:
        actual_duration_minutes = None
        if submit_clicked:
            actual_duration_minutes = parse_actual_duration_to_minutes(actual_duration_text)
            save_task_completion_feedback(
                task_row,
                final_comments=(final_comments.strip() or None),
                actual_friction_id=(
                    selected_actual_friction["id"] if selected_actual_friction else None
                ),
                actual_duration_minutes=actual_duration_minutes,
            )

        update_task_status(task_row, "completed")
        clear_task_completion_feedback_request()

        if source == "mark_done":
            st.success("Task marked as completed.")
            st.rerun()
            return

        if source == "existing_open_resolution":
            queue_open_task_start(payload["context"])
            st.rerun()
            return

        if source == "sprint_review":
            if payload.get("rest_choice") == "Yes":
                begin_pomodoro_rest_break(previous_work_outcome="completed")
            else:
                disable_work_timer()
                clear_pomodoro_overlay_state()
                notify_work_ended()

            clear_open_task_dialog_state()
            st.session_state.pop(SPRINT_REVIEW_PENDING_KEY, None)
            st.rerun()
            return

        if source == "chunk_review":
            if payload.get("forced_rest_required"):
                reset_chunk_continuous_work_seconds()
                begin_pomodoro_rest_break(
                    previous_work_outcome="completed",
                    resume_cycle_type="chunk",
                )
            else:
                disable_work_timer()
                clear_pomodoro_overlay_state()
                notify_work_ended()
            st.session_state.pop(CHUNK_REVIEW_PENDING_KEY, None)
            st.rerun()
            return

        if source == "body_doubling_simple_completed":
            body_doubling.clear_body_doubling_flow()
            notify_work_ended()
            st.success("Excellent. The task has been marked as completed.")
            st.rerun()
            return

        if source == "body_doubling_final_completed":
            flow_snapshot = payload.get("flow_snapshot") or {}
            body_doubling.maybe_open_body_doubling_result_dialog(
                flow_snapshot,
                get_body_doubling_services(),
                "completed",
            )
            body_doubling.clear_body_doubling_flow()
            notify_work_ended()
            st.rerun()
            return

        st.rerun()
    except Exception as error:
        handle_api_exception(error, f"Could not complete the task with feedback: {error}")


def render_task_completion_feedback_dialog():
    """Render the deferred completion-feedback dialog when requested."""

    if st.session_state.get(TASK_COMPLETION_FEEDBACK_REQUEST_KEY):
        task_completion_feedback_dialog()


def get_user_state_session_counters():
    """Return the session counters maintained by the user-state machine."""

    context_payload = st.session_state.get(user_state_machine.USER_FSM_CONTEXT_KEY) or {}
    return {
        "completed_tasks": int(context_payload.get("completed_tasks_in_session", 0) or 0),
        "consecutive_completed_tasks": int(context_payload.get("consecutive_completed_tasks", 0) or 0),
        "microsteps_completed": int(context_payload.get("completed_microsteps_in_session", 0) or 0),
        "consecutive_microsteps_completed": int(context_payload.get("consecutive_completed_microsteps", 0) or 0),
    }


def get_logout_farewell_profile_context():
    """Return user profile fields included in the logout farewell prompt."""

    user_profile = ensure_user_profile_cache()
    persona = get_personas().get(user_profile.get("persona_id")) or {}
    return {
        "full_name": user_profile.get("full_name"),
        "first_name": user_profile.get("first_name"),
        "age": user_profile.get("age"),
        "persona_name": persona.get("name") or get_persona_name_by_id(user_profile.get("persona_id")),
        "persona_description": persona.get("description") or persona.get("self_describing"),
    }


def load_logout_farewell_prompt():
    """Load the static OpenAI prompt instructions for logout farewell."""

    try:
        return LOGOUT_FAREWELL_PROMPT_PATH.read_text(encoding="utf-8").strip()
    except OSError as error:
        log_openai_event(
            logging.ERROR,
            "Could not read logout farewell prompt file.",
            prompt_path=LOGOUT_FAREWELL_PROMPT_PATH,
            error=repr(error),
        )
        return LOGOUT_FAREWELL_PROMPT_FALLBACK


def build_logout_farewell_prompt(counters, profile_context):
    """Build the OpenAI prompt for the voluntary logout farewell."""

    prompt_template = load_logout_farewell_prompt()
    return (
        f"{prompt_template}\n\n"
        "User profile:\n"
        f"- Full name: {profile_context.get('full_name') or 'not provided'}\n"
        f"- First name: {profile_context.get('first_name') or 'not provided'}\n"
        f"- Age: {profile_context.get('age') if profile_context.get('age') is not None else 'not provided'}\n"
        f"- Persona/profile: {profile_context.get('persona_name') or 'not provided'}\n"
        f"- Persona/profile description: {profile_context.get('persona_description') or 'not provided'}\n\n"
        "Session completion data:\n"
        f"- Completed tasks: {int(counters.get('completed_tasks', 0) or 0)}\n"
        f"- Consecutive completed tasks: {int(counters.get('consecutive_completed_tasks', 0) or 0)}\n"
        f"- Completed micro-steps: {int(counters.get('microsteps_completed', 0) or 0)}\n"
        f"- Consecutive completed micro-steps: {int(counters.get('consecutive_microsteps_completed', 0) or 0)}"
    )


def generate_logout_farewell_message(counters):
    """Generate or fall back to a logout farewell message."""

    api_key = os.environ.get("OPENAI_API_KEY")
    profile_context = get_logout_farewell_profile_context()

    if OpenAI is None:
        log_openai_event(
            logging.ERROR,
            "OpenAI package is not installed; using fallback logout farewell.",
            model=OPENAI_MODEL,
            counters=counters,
            first_name=profile_context.get("first_name"),
            age=profile_context.get("age"),
            persona_description=profile_context.get("persona_description"),
        )
        return build_logout_farewell_fallback(counters)

    if not api_key:
        log_openai_event(
            logging.WARNING,
            "OPENAI_API_KEY is not configured; using fallback logout farewell.",
            model=OPENAI_MODEL,
            counters=counters,
            first_name=profile_context.get("first_name"),
            age=profile_context.get("age"),
            persona_description=profile_context.get("persona_description"),
        )
        return build_logout_farewell_fallback(counters)

    try:
        log_openai_event(
            logging.INFO,
            "Requesting logout farewell from OpenAI.",
            model=OPENAI_MODEL,
            counters=counters,
            first_name=profile_context.get("first_name"),
            age=profile_context.get("age"),
            persona_description=profile_context.get("persona_description"),
        )
        client = OpenAI(api_key=api_key)
        response = client.responses.create(
            model=OPENAI_MODEL,
            input=build_logout_farewell_prompt(counters, profile_context),
            max_output_tokens=160,
        )
        farewell_message = extract_openai_text(response)
        if not farewell_message:
            log_openai_event(
                logging.ERROR,
                "OpenAI response did not contain extractable text; using fallback logout farewell.",
                model=OPENAI_MODEL,
                counters=counters,
                first_name=profile_context.get("first_name"),
                age=profile_context.get("age"),
                persona_description=profile_context.get("persona_description"),
                response_type=type(response).__name__,
            )
            return build_logout_farewell_fallback(counters)

        log_openai_event(
            logging.INFO,
            "OpenAI logout farewell generated successfully.",
            model=OPENAI_MODEL,
            counters=counters,
            first_name=profile_context.get("first_name"),
            age=profile_context.get("age"),
            message_length=len(farewell_message),
        )
        return farewell_message
    except Exception as error:
        get_openai_logger().exception(
            "OpenAI logout farewell generation failed; using fallback. context=%s",
            json.dumps(
                {
                    "model": OPENAI_MODEL,
                    "counters": counters,
                    "first_name": profile_context.get("first_name"),
                    "age": profile_context.get("age"),
                    "persona_description": str(profile_context.get("persona_description") or "")[:300],
                    "error": repr(error),
                },
                ensure_ascii=False,
            ),
        )
        return build_logout_farewell_fallback(counters)


@st.dialog("Log out")
def logout_confirmation_dialog():
    """Render the voluntary logout confirmation dialog with current counters."""

    counters = get_user_state_session_counters()
    farewell_message = st.session_state.get(LOGOUT_FAREWELL_MESSAGE_KEY)
    if not farewell_message:
        with st.spinner("Preparing your sign-off..."):
            farewell_message = generate_logout_farewell_message(counters)
        st.session_state[LOGOUT_FAREWELL_MESSAGE_KEY] = farewell_message

    safe_farewell_message = html.escape(str(farewell_message or ""))
    st.markdown(
        f'<div class="task-support-message logout-farewell-message">{safe_farewell_message}</div>',
        unsafe_allow_html=True,
    )
    render_auto_voice_message(farewell_message, "logout_farewell")

    stay_column, logout_column = st.columns(2, gap="small")
    with stay_column:
        if st.button("Stay signed in", use_container_width=True):
            st.session_state.pop(LOGOUT_CONFIRM_DIALOG_KEY, None)
            st.session_state.pop(LOGOUT_FAREWELL_MESSAGE_KEY, None)
            st.rerun()
    with logout_column:
        if st.button("Log out now", type="primary", use_container_width=True):
            st.session_state.pop(LOGOUT_CONFIRM_DIALOG_KEY, None)
            logout(force=True)


def cleanup_open_task_for_recovery():
    """Move the currently open task to asleep or debt during Recovery cleanup."""

    open_task = get_open_task_row()
    if not open_task:
        return None

    next_status = get_incomplete_open_task_resolution_status(open_task)
    update_task_status(open_task, next_status)
    return next_status


def get_incomplete_open_task_resolution_status(task_row, *, now=None):
    """Return debt for overdue open tasks, otherwise asleep."""

    due_date = parse_task_datetime(task_row.get("due_date"))
    current_time = now or datetime.now(pytz.UTC)
    return "debt" if due_date and due_date < current_time else "asleep"


def get_post_work_incomplete_task_status(task_row, *, now=None):
    """Return the status an unfinished active task should keep after work stops.

    Once a task has been opened for active work, stopping the current chunk
    should leave it in ``open`` unless the due date has already passed. In that
    overdue case, ``debt`` takes precedence immediately.
    """

    due_date = parse_task_datetime(task_row.get("due_date"))
    current_time = now or datetime.now(pytz.UTC)
    return "debt" if due_date and due_date < current_time else "open"


def start_planner_timer():
    """Start the planner timer using the current planner-minutes preference."""

    planner_minutes = int(get_user_preferences().get("planner_minutes", DEFAULT_PLANNER_TIMEOUT_MINUTES))
    append_timer_log_line(
        "request_start | timer=planner_timer source=start_planner_timer "
        f"duration_minutes={planner_minutes}"
    )
    get_planner_timer(st.session_state).start(
        duration=planner_minutes * 60,
        on_expiry=expire_planner_timer,
    )


def reset_planner_timer():
    """Reset the planner timer using the current planner-minutes preference."""

    planner_minutes = int(get_user_preferences().get("planner_minutes", DEFAULT_PLANNER_TIMEOUT_MINUTES))
    append_timer_log_line(
        "request_reset | timer=planner_timer source=reset_planner_timer "
        f"duration_minutes={planner_minutes}"
    )
    get_planner_timer(st.session_state).reset(
        duration=planner_minutes * 60,
        on_expiry=expire_planner_timer,
    )


def disable_planner_timer():
    """Disable the planner timer and log the operation."""

    append_timer_log_line("request_disable | timer=planner_timer source=disable_planner_timer")
    get_planner_timer(st.session_state).disable()
    clear_page_notices(zone="timing")


def tick_planner_timer():
    """Advance the planner timer on each rerun for authenticated users."""

    if not st.session_state.get("user_id"):
        return

    timer = get_planner_timer(st.session_state)
    snapshot = timer.snapshot()
    now_timestamp = datetime.now().astimezone().timestamp()
    if (
        snapshot.enabled
        and snapshot.running
        and snapshot.expires_at is not None
        and now_timestamp >= snapshot.expires_at
    ):
        append_timer_log_line(
            "detected_expired | timer=planner_timer source=tick_planner_timer"
        )
        timer.stop()
        expire_planner_timer(timer)
        return

    timer.tick()


def ensure_planner_timer_matches_state():
    """Keep the Planner timer alive whenever the current user state is Planner."""

    if not st.session_state.get("user_id"):
        return
    if get_effective_current_state_name() != user_state_machine.PLANNER_STATE:
        return
    if (
        st.session_state.get(PLANNER_TIMER_RESTART_AFTER_NOTICE_KEY)
        and st.session_state.get(TIMING_NOTICE_QUEUE_KEY)
    ):
        return

    planner_snapshot = get_planner_timer(st.session_state).snapshot()
    if planner_snapshot.running and planner_snapshot.expires_at is not None:
        return

    append_timer_log_line(
        "request_start | timer=planner_timer source=ensure_planner_timer_matches_state"
    )
    start_planner_timer()


def apply_user_state_transition_result(result):
    """Apply timer, cleanup, and feedback side effects from a user-state transition."""

    if result.current_state_id is not None and "user_profile" in st.session_state:
        st.session_state["user_profile"] = {
            **st.session_state["user_profile"],
            "state_id": result.current_state_id,
        }

    last_event = (
        result.context.last_event
        if result and result.context
        else None
    )

    if result.changed and result.current_state == user_state_machine.RECOVERY_STATE:
        # Guided chains should never survive session closure or timeout
        # recovery. A new authenticated working session must opt in again.
        st.session_state.pop(GUIDED_AUTO_OPEN_CHAIN_ACTIVE_KEY, None)
        st.session_state.pop(GUIDED_OPEN_REQUEST_PENDING_KEY, None)
        st.session_state.pop(LOGIN_AUTO_OPEN_STATE_KEY, None)
    elif result.changed and result.current_state == user_state_machine.PLANNER_STATE:
        if last_event == user_state_machine.WORK_ENDED_EVENT:
            # Returning to Planner after an accepted work cycle is exactly the
            # point where a guided auto-open chain may continue with the next
            # candidate, so we intentionally preserve the chain flag here.
            pass
        else:
            # All other routes back to Planner should stop the current guided
            # chain. This includes rejection thresholds, exhausted candidates,
            # and any explicit state-management transitions that hand control
            # back to list planning rather than continued guided execution.
            st.session_state.pop(GUIDED_AUTO_OPEN_CHAIN_ACTIVE_KEY, None)
            st.session_state.pop(GUIDED_OPEN_REQUEST_PENDING_KEY, None)
            st.session_state.pop(LOGIN_AUTO_OPEN_STATE_KEY, None)

    # Guided auto-open rejection/exhaustion can be evaluated while the user is
    # already in Planner and only the latent execution state lives in
    # `memory_state`. In that case the FSM can emit the "returned to Planner"
    # message without a persisted state change, so we must still shut down the
    # current guided chain here.
    if any(
        message.message_id
        in {
            "STATE_RETURNED_TO_PLANNER_AFTER_REJECTIONS",
            "STATE_RETURNED_TO_PLANNER_NO_MORE_AUTO_OPEN_TASKS",
        }
        for message in (result.ui_messages or [])
    ):
        st.session_state.pop(GUIDED_AUTO_OPEN_CHAIN_ACTIVE_KEY, None)
        st.session_state.pop(GUIDED_OPEN_REQUEST_PENDING_KEY, None)
        st.session_state.pop(LOGIN_AUTO_OPEN_STATE_KEY, None)

    if result.changed and result.context and result.context.last_event != user_state_machine.TASK_OPENED_EVENT:
        # Offered-task chains only make sense within the current adaptive state.
        # Opening a suggested task can intentionally leave Planner, but it should
        # not make that same unfinished task eligible again on the next rerun.
        clear_adaptive_offered_tasks()

    if result.changed and result.current_state in {
        user_state_machine.PLANNER_STATE,
        user_state_machine.RECOVERY_STATE,
    }:
        disable_work_timer()

    if result.start_planner_timer:
        start_planner_timer()
    elif result.reset_planner_timer:
        if result.context and result.context.last_event == user_state_machine.PLANNER_TIMER_ELAPSED_EVENT:
            st.session_state[PLANNER_TIMER_RESTART_AFTER_NOTICE_KEY] = True
        else:
            reset_planner_timer()
    elif result.stop_planner_timer:
        disable_planner_timer()

    if result.requires_recovery_cleanup:
        cleanup_status = cleanup_open_task_for_recovery()
        if cleanup_status:
            store_page_notice(
                {
                    "message_id": "STATE_OPEN_TASK_RECOVERY_CLEANUP",
                    "zone": "adaptive",
                    "params": {"status": cleanup_status},
                }
            )

    if result.ui_messages:
        store_transition_notices(result.ui_messages, intensity=get_adaptive_message_intensity())

    if result.changed and result.current_state != user_state_machine.PLANNER_STATE:
        clear_page_notices(zone="timing")

    maybe_schedule_engaged_cheer(result)

    # Do not surface automatic session-end summaries here. The visible farewell
    # belongs only to the explicit user-initiated logout dialog, which is shown
    # before the auth session is destroyed. Timer/auth transitions should not
    # create a logout-style message on the landing page.


def get_resume_state_from_transition_result(result):
    """Return the execution state to restore after a recoverable timeout.

    Planner timeout moves the persisted user state to Recovery, but the FSM
    keeps the useful pre-Recovery execution state in ``memory_state``. That is
    the state we should restore when the user resumes quickly enough that we do
    not need to ask how they are arriving to the new session.
    """

    context = getattr(result, "context", None)
    memory_state = getattr(context, "memory_state", None)
    if memory_state in {user_state_machine.FROZEN_STATE, user_state_machine.ENGAGED_STATE}:
        return memory_state
    return user_state_machine.FROZEN_STATE


def suspend_authenticated_work_session(reason, result):
    """Leave the app shell after a timeout while preserving auth for resume.

    This is the key distinction from real logout. A real logout signs out of
    Supabase and removes the encrypted auth cookie. A recoverable work-session
    suspension only clears in-memory Streamlit state and stores a short-lived
    marker with the state that should be restored. The auth cookie is left in
    place so the same browser can resume without credentials for the configured
    grace window.
    """

    save_work_session_suspension_cookie(
        reason,
        get_resume_state_from_transition_result(result),
        int(get_resumable_session_elapsed_minutes() * 60),
        st.session_state.get(CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY) or {},
    )
    clear_auth_state(clear_cookie=False)
    clear_user_cache_state()
    clear_flow_state()
    st.rerun()


def dispatch_user_state_event(
    event,
    *,
    declared_state=None,
    start_in_planner=False,
    target_state=None,
    event_payload=None,
    session_end_reason=None,
):
    """Dispatch one event into the user-state machine and apply the result."""

    user_id = st.session_state.get("user_id")
    if not user_id:
        return None

    user_profile = ensure_user_profile_cache()
    current_state_name = get_state_name_by_id(user_profile.get("state_id"))
    result = get_logged_user_model().transition(
        user_id=user_id,
        current_state_name=current_state_name,
        state_id_by_name=get_state_id_name_map(),
        event=event,
        preferences=user_profile.get("preferences"),
        declared_state=declared_state,
        start_in_planner=start_in_planner,
        target_state=target_state,
        event_payload=event_payload,
        session_end_reason=session_end_reason,
    )
    apply_user_state_transition_result(result)
    return result


def finalize_session_for_recovery(reason):
    """Best-effort terminal cleanup before auth/session state is cleared."""

    if not st.session_state.get("user_id"):
        return None

    result = None
    try:
        result = dispatch_user_state_event(
            user_state_machine.LOGOUT_EVENT,
            session_end_reason=reason,
        )
    except Exception:
        # The cleanup below is still worth trying if the FSM transition could
        # not be persisted, for example during an auth-expiry edge case.
        result = None

    if not result or not result.requires_recovery_cleanup:
        try:
            cleanup_status = cleanup_open_task_for_recovery()
            if cleanup_status:
                store_page_notice(
                    {
                        "message_id": "STATE_OPEN_TASK_RECOVERY_CLEANUP",
                        "zone": "adaptive",
                        "params": {"status": cleanup_status},
                    }
                )
        except Exception:
            pass

    return result


def expire_work_timer(timer=None):
    """Handle generic work-timer expiry when no Pomodoro/chunk flow overrides it."""

    if not st.session_state.get("user_id"):
        return

    try:
        dispatch_user_state_event(
            user_state_machine.MANUAL_SET_STATE_EVENT,
            target_state=WORK_TIMER_EXPIRY_STATE_NAME,
        )
        store_page_notice(
            {"message_id": "WORK_TIMER_FINISHED_PLANNER", "zone": "timing"},
            intensity=get_adaptive_message_intensity(),
        )
        st.rerun()
    except Exception as error:
        handle_api_exception(error, f"Could not update state after work timer expiry: {error}")


def expire_planner_timer(timer=None):
    """Handle planner-timer expiry and end the session when required."""

    if not st.session_state.get("user_id"):
        return

    try:
        planner_adaptation = get_current_adaptation_without_tasks()
        result = dispatch_user_state_event(
            user_state_machine.PLANNER_TIMER_ELAPSED_EVENT
        )
        if result and result.should_end_session:
            suspend_authenticated_work_session("planner_limit", result)
            return
        if (
            result
            and not result.should_end_session
            and planner_adaptation
            and planner_adaptation.planner_timeout_message_id
        ):
            # Some Planner adaptations require a more didactic timeout reminder
            # than the generic FSM message shown for every persona/state pair.
            timeout_intensity = get_adaptive_message_intensity(planner_adaptation)
            if should_display_message(
                planner_adaptation.planner_timeout_message_id,
                timeout_intensity,
            ):
                store_page_notice(
                    {
                        "message_id": planner_adaptation.planner_timeout_message_id,
                        "zone": "timing",
                        "intensity": timeout_intensity,
                        "renderer": "info",
                        "params": {},
                    }
                )
        rerun_app()
    except Exception as error:
        handle_api_exception(error, f"Could not update state after planner timer expiry: {error}")


def start_work_timer():
    """Start the generic work timer with its default expiry callback."""

    append_timer_log_line("request_start | timer=work_timer source=start_work_timer")
    timer = get_work_timer(st.session_state)
    timer.start(
        duration=CHUNK_TIMER_DEFAULT_SECONDS,
        on_expiry=expire_work_timer,
    )


def disable_work_timer():
    append_timer_log_line("request_disable | timer=work_timer source=disable_work_timer")
    get_work_timer(st.session_state).disable()


def set_rest_message(message):
    st.session_state[REST_MESSAGE_KEY] = message
    st.session_state[REST_MESSAGE_EXPIRES_AT_KEY] = (
        datetime.now(pytz.UTC).timestamp() + REST_MESSAGE_MODAL_SECONDS
    )


def clear_rest_message():
    st.session_state.pop(REST_MESSAGE_KEY, None)
    st.session_state.pop(REST_MESSAGE_EXPIRES_AT_KEY, None)


def set_rest_resume_prompt_context(
    *,
    previous_work_outcome,
    work_duration_minutes,
    resume_cycle_type="pomodoro",
):
    """Persist the post-rest prompt context until the user chooses how to continue.

    The rest timer finishes outside the original sprint-review dialog, so the UI
    needs a small amount of durable context to know whether finishing after the
    break should behave like the work cycle ended with the task completed or
    incomplete.
    """

    st.session_state[REST_RESUME_PROMPT_CONTEXT_KEY] = {
        "previous_work_outcome": previous_work_outcome,
        "work_duration_minutes": int(work_duration_minutes),
        "resume_cycle_type": resume_cycle_type,
    }
    st.session_state[REST_RESUME_PROMPT_PENDING_KEY] = False


def get_rest_resume_prompt_context():
    """Return the stored post-rest decision context, if any."""

    return st.session_state.get(REST_RESUME_PROMPT_CONTEXT_KEY)


def clear_rest_resume_prompt_context():
    """Forget any pending post-rest resume/finish decision."""

    st.session_state.pop(REST_RESUME_PROMPT_CONTEXT_KEY, None)
    st.session_state.pop(REST_RESUME_PROMPT_PENDING_KEY, None)


def clear_expired_rest_message():
    expires_at = st.session_state.get(REST_MESSAGE_EXPIRES_AT_KEY)
    if expires_at is None:
        return

    if datetime.now(pytz.UTC).timestamp() >= float(expires_at):
        clear_rest_message()
        st.rerun()


def begin_pomodoro_rest_break(
    *,
    previous_work_outcome,
    resume_cycle_type="pomodoro",
):
    """Start a Pomodoro-style rest break and remember how work should resume.

    ``resume_cycle_type`` distinguishes a classic Pomodoro sprint restart from a
    Chunk-mode restart, where the next work block should be recalculated rather
    than blindly reusing the previous duration.
    """

    work_duration_minutes = get_effective_pomodoro_sprint_minutes()
    set_rest_resume_prompt_context(
        previous_work_outcome=previous_work_outcome,
        work_duration_minutes=work_duration_minutes,
        resume_cycle_type=resume_cycle_type,
    )
    rest_duration_minutes = get_effective_rest_duration_minutes()
    append_timer_log_line(
        f"request_reset | timer=work_timer source=sprint_review_rest duration_minutes={rest_duration_minutes} callback=eoRest"
    )
    get_work_timer(st.session_state).reset(
        duration=rest_duration_minutes * 60,
        on_expiry=eoRest,
    )
    start_pomodoro_rest_overlay(rest_duration_minutes)


def finalize_post_rest_finish(prompt_context):
    """Apply the same post-work semantics when the user finishes after resting.

    For a completed task, finishing after the break should behave like ending
    the sprint immediately after the work phase: overlays stop and the FSM can
    move back to Planner if no task remains open. For Chunk mode, finishing
    after a forced rest should preserve the task as ``open`` unless it is
    already overdue, in which case it transitions to ``debt`` immediately.
    """

    previous_work_outcome = (prompt_context or {}).get("previous_work_outcome")
    resume_cycle_type = (prompt_context or {}).get("resume_cycle_type")
    disable_work_timer()
    clear_pomodoro_overlay_state()
    clear_rest_resume_prompt_context()
    clear_rest_message()
    if previous_work_outcome == "incomplete":
        open_task = get_open_task_row()
        if open_task:
            next_status = get_post_work_incomplete_task_status(open_task)
            if next_status != "open":
                update_task_status(open_task, next_status)
    notify_work_ended()


def eoSprint(timer=None):
    clear_pomodoro_overlay_state()
    st.session_state[SPRINT_REVIEW_PENDING_KEY] = True
    disable_work_timer()
    st.rerun()


def eoChunk(timer=None):
    overlay_state = get_pomodoro_overlay_state() or {}
    timer_snapshot = get_work_timer_snapshot()
    total_seconds = int(
        timer_snapshot.duration_seconds
        or overlay_state.get("duration_seconds")
        or get_next_chunk_work_seconds()
    )
    remaining_seconds = 0
    if timer_snapshot.running and timer_snapshot.expires_at is not None:
        remaining_seconds = max(
            0,
            int(timer_snapshot.expires_at - datetime.now(pytz.UTC).timestamp()),
        )
    elapsed_seconds = max(0, total_seconds - remaining_seconds)
    add_chunk_continuous_work_seconds(elapsed_seconds or total_seconds)
    register_chunk_work_elapsed(
        overlay_state.get("task_row") or get_open_task_row(),
        elapsed_seconds or total_seconds,
    )
    clear_pomodoro_overlay_state()
    disable_work_timer()
    st.session_state[CHUNK_REVIEW_PENDING_KEY] = True
    st.rerun()


def eoRest(timer=None):
    open_task = get_open_task_row()
    if not open_task:
        clear_pomodoro_overlay_state()
        disable_work_timer()
        clear_rest_resume_prompt_context()
        st.info("Rest is over.")
        return

    if not get_rest_resume_prompt_context():
        clear_pomodoro_overlay_state()
        disable_work_timer()
        set_rest_message("Rest is over.")
        st.rerun()
        return

    clear_pomodoro_overlay_state()
    disable_work_timer()
    st.session_state[REST_RESUME_PROMPT_PENDING_KEY] = True
    st.rerun()


def reset_work_timer_for_open_task(use_pomodoro_sprints):
    if use_pomodoro_sprints:
        duration_minutes = get_effective_pomodoro_sprint_minutes()
        schedule_work_timer(
            duration_minutes,
            eoSprint,
            "reset_work_timer_for_open_task use_pomodoro_sprints=True",
        )
        open_task = get_open_task_row()
        if open_task:
            start_pomodoro_overlay(open_task, duration_minutes)
    else:
        duration_seconds = get_next_chunk_work_seconds()
        duration_minutes = duration_seconds / 60.0
        schedule_work_timer(
            duration_minutes,
            eoChunk,
            "reset_work_timer_for_open_task use_pomodoro_sprints=False",
        )
        open_task = get_open_task_row()
        if open_task:
            start_chunk_overlay(open_task, duration_seconds)


def clear_sprint_review_state():
    """Clear pending sprint-review dialog state."""

    st.session_state.pop(SPRINT_REVIEW_PENDING_KEY, None)
    st.session_state.pop("adaptive_rest_skip_confirmed", None)


def clear_chunk_review_state():
    """Clear pending chunk-review dialog state."""

    st.session_state.pop(CHUNK_REVIEW_PENDING_KEY, None)


def set_open_task_guidance_message(message):
    st.session_state[OPEN_TASK_GUIDANCE_MESSAGE_KEY] = message
    st.session_state[OPEN_TASK_GUIDANCE_EXPIRES_AT_KEY] = (
        datetime.now(pytz.UTC).timestamp() + OPEN_TASK_GUIDANCE_MODAL_SECONDS
    )


def clear_open_task_guidance_message():
    st.session_state.pop(OPEN_TASK_GUIDANCE_MESSAGE_KEY, None)
    st.session_state.pop(OPEN_TASK_GUIDANCE_EXPIRES_AT_KEY, None)


def clear_expired_open_task_guidance_message():
    expires_at = st.session_state.get(OPEN_TASK_GUIDANCE_EXPIRES_AT_KEY)
    if expires_at is None:
        return

    if datetime.now(pytz.UTC).timestamp() >= float(expires_at):
        clear_open_task_guidance_message()
        st.rerun()


def tick_work_timer():
    if not st.session_state.get("user_id"):
        return

    timer = get_work_timer(st.session_state)
    timer.tick()


def get_user_lists():
    response = (
        supabase.table("lists")
        .select("id, name")
        .order("name")
        .execute()
    )
    return response.data or []


def load_lookup_cache():
    """ Load all lookup tables from the database and cache them in session state."""
    lookup_cache = {}

    for table_name in LOOKUP_TABLES:
        response = (
            supabase.table(table_name)
            .select("id, label, self_describing, weight")
            .order("id")
            .execute()
        )
        rows = response.data or []
        lookup_cache[table_name] = {
            "options": [
                {
                    "id": row["id"],
                    "label": row["label"],
                    "self_describing": row["self_describing"],
                }
                for row in rows
            ],
            "weights": {row["id"]: row["weight"] for row in rows},
        }

    st.session_state["lookup_cache"] = lookup_cache


def ensure_lookup_cache():
    if "lookup_cache" not in st.session_state:
        load_lookup_cache()


def get_lookup_options(table_name):
    """ Return the mapping of ids to labels & self_describing for the given lookup table."""
    ensure_lookup_cache()
    table_cache = st.session_state["lookup_cache"].get(table_name, {})
    return table_cache.get("options", [])


def get_lookup_weights(table_name):
    """ Return the mapping of ids to weights for the given lookup table."""
    ensure_lookup_cache()
    table_cache = st.session_state["lookup_cache"].get(table_name, {})
    return table_cache.get("weights", {})

# silly but can be triggered from the debug console to refresh all lookup caches without a full page reload
def refresh_lookup_cache():
    load_lookup_cache()


def load_states_cache():
    """ Load user-selectable states from the database and cache them in session state."""
    response = (
        supabase.table("states")
        .select("id, name, self_describing")
        .order("id")
        .execute()
    )
    st.session_state["states_cache"] = [
        state
        for state in response.data or []
        if state.get("name") in USER_SELECTABLE_STATE_NAMES
    ]


def load_all_states_cache():
    """ Load the all states from the database and cache them in session state."""
    response = (
        supabase.table("states")
        .select("id, name, self_describing")
        .order("id")
        .execute()
    )
    st.session_state["all_states_cache"] = response.data or []


def ensure_states_cache():
    if "states_cache" not in st.session_state:
        load_states_cache()


def ensure_all_states_cache():
    if "all_states_cache" not in st.session_state:
        load_all_states_cache()


def get_states_options():
    """ Return the list of user-selectable states for dropdowns and other selectors."""
    ensure_states_cache()
    return st.session_state["states_cache"]


def get_all_states_options():
    """ Return the list of all states for finite-state-machine (FSM)."""
    ensure_all_states_cache()
    return st.session_state["all_states_cache"]


def get_initial_session_state_options():
    """ Return the list of states that can be used as initial states for new sessions at login."""

    return [
        state
        for state in get_states_options()
        if state.get("name") in INITIAL_SESSION_STATE_NAMES
    ]


def extract_first_name(full_name):
    if not full_name:
        return None
    # This will not work correctly for all cultures and name formats, 
    # but it's a simple heuristic for the project.
    parts = str(full_name).strip().split()
    if not parts:
        return None
    return parts[0]


def calculate_age(born_value):
    if not born_value:
        return None

    try:
        born_date = born_value if isinstance(born_value, date) else date.fromisoformat(str(born_value))
    except ValueError:
        return None

    today = datetime.now(pytz.UTC).date()
    age = today.year - born_date.year
    if (today.month, today.day) < (born_date.month, born_date.day):
        age -= 1
    return age


def get_openai_logger():
    logger = logging.getLogger("ai_adhd.openai")
    if logger.handlers:
        return logger

    OPENAI_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(OPENAI_LOG_PATH, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def log_openai_event(level, message, **context):
    safe_context = {}
    for key, value in context.items():
        if value is None:
            safe_context[key] = None
        elif key == "persona_description":
            safe_context[key] = str(value)[:300]
        else:
            safe_context[key] = str(value)[:120]

    get_openai_logger().log(
        level,
        "%s | context=%s",
        message,
        json.dumps(safe_context, ensure_ascii=False),
    )


def append_timer_log_line(message):
    TIMER_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    TIMER_LOG_PATH.touch(exist_ok=True)
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with TIMER_LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(f"{timestamp} INFO {message}\n")


def get_voice_message_cache():
    return st.session_state.setdefault(VOICE_MESSAGE_CACHE_KEY, {})


def get_auto_voice_requested_keys():
    return st.session_state.setdefault(AUTO_VOICE_REQUESTED_KEY, set())


def get_voice_message_cache_key(message_text, key_prefix):
    message_digest = hashlib.sha1(str(message_text).encode("utf-8")).hexdigest()[:12]
    return f"{key_prefix}:{message_digest}"


def extend_modal_expiry(expiry_key, seconds):
    if not expiry_key:
        return

    st.session_state[expiry_key] = (
        datetime.now(pytz.UTC).timestamp() + int(seconds)
    )


def estimate_voice_playback_seconds(message_text):
    estimator = getattr(audio_support, "estimate_voice_playback_seconds", None)
    if callable(estimator):
        return estimator(message_text)

    cleaned_message = (message_text or "").strip()
    if not cleaned_message:
        return 10

    word_count = len(cleaned_message.split())
    estimated_seconds = max(10, min(35, int((word_count / 155) * 60) + 6))
    # max 35s, min 10s
    # formula is valid for EN but may not work for all languages 
    return estimated_seconds


def is_auto_voice_enabled():
    return bool(st.session_state.get(AUTO_VOICE_MESSAGES_ENABLED_KEY, True))


def render_voice_message_button(
    message_text,
    key_prefix,
    *,
    label="Play voice message",
    modal_expiry_key=None,
    keep_open_seconds=None,
):
    """Render a button to play a voice message."""
    cleaned_message = (message_text or "").strip()
    if not cleaned_message:
        return

    cache_key = get_voice_message_cache_key(cleaned_message, key_prefix)
    voice_cache = get_voice_message_cache()
    cached_entry = voice_cache.get(cache_key, {})
    auto_voice_enabled = is_auto_voice_enabled()
    auto_requested_keys = get_auto_voice_requested_keys()

    if auto_voice_enabled and cache_key not in voice_cache and cache_key not in auto_requested_keys:
        audio_bytes, error_message = audio_support.convert_text_to_speech(cleaned_message)
        voice_cache[cache_key] = {
            "audio_bytes": audio_bytes,
            "error_message": error_message,
        }
        auto_requested_keys.add(cache_key)
        cached_entry = voice_cache[cache_key]
        if audio_bytes:
            st.session_state[VOICE_AUTOPLAY_PENDING_KEY] = cache_key
            extend_modal_expiry(
                modal_expiry_key,
                keep_open_seconds
                if keep_open_seconds is not None
                else estimate_voice_playback_seconds(cleaned_message),
            )

    if auto_voice_enabled:
        disable_auto_voice = st.checkbox(
            "Do not play voice messages automatically anymore",
            value=False,
            key=f"disable_auto_voice_{cache_key}",
        )
        if disable_auto_voice:
            st.session_state[AUTO_VOICE_MESSAGES_ENABLED_KEY] = False
            auto_voice_enabled = False

    if st.button(label, key=f"voice_button_{cache_key}", use_container_width=False):
        audio_bytes, error_message = audio_support.convert_text_to_speech(cleaned_message)
        voice_cache[cache_key] = {
            "audio_bytes": audio_bytes,
            "error_message": error_message,
        }
        cached_entry = voice_cache[cache_key]
        if audio_bytes:
            st.session_state[VOICE_AUTOPLAY_PENDING_KEY] = cache_key
            extend_modal_expiry(
                modal_expiry_key,
                keep_open_seconds
                if keep_open_seconds is not None
                else estimate_voice_playback_seconds(cleaned_message),
            )

    if cached_entry.get("error_message"):
        st.info(cached_entry["error_message"])
    elif cached_entry.get("audio_bytes"):
        if cache_key == st.session_state.get(VOICE_AUTOPLAY_PENDING_KEY) and cache_key != st.session_state.get(VOICE_AUTOPLAY_RENDERED_KEY):
            st.markdown(
                audio_support.build_hidden_autoplay_audio_html(
                    cached_entry["audio_bytes"],
                    "audio/mpeg",
                ),
                unsafe_allow_html=True,
            )
            st.session_state[VOICE_AUTOPLAY_RENDERED_KEY] = cache_key
        st.audio(cached_entry["audio_bytes"], format="audio/mp3")


def render_auto_voice_message(message_text, key_prefix):
    """Generate and autoplay one voice message without requiring a button click."""

    cleaned_message = (message_text or "").strip()
    if not cleaned_message:
        return

    cache_key = get_voice_message_cache_key(cleaned_message, key_prefix)
    voice_cache = get_voice_message_cache()
    cached_entry = voice_cache.get(cache_key)

    if cached_entry is None:
        with st.spinner("Preparing voice playback..."):
            audio_bytes, error_message = audio_support.convert_text_to_speech(cleaned_message)
        voice_cache[cache_key] = {
            "audio_bytes": audio_bytes,
            "error_message": error_message,
        }
        cached_entry = voice_cache[cache_key]
        if audio_bytes:
            st.session_state[VOICE_AUTOPLAY_PENDING_KEY] = cache_key

    if cached_entry.get("error_message"):
        st.info(cached_entry["error_message"])
        return

    audio_bytes = cached_entry.get("audio_bytes")
    if not audio_bytes:
        return

    if cache_key != st.session_state.get(VOICE_AUTOPLAY_RENDERED_KEY):
        st.markdown(
            audio_support.build_hidden_autoplay_audio_html(
                audio_bytes,
                "audio/mpeg",
            ),
            unsafe_allow_html=True,
        )
        st.session_state[VOICE_AUTOPLAY_RENDERED_KEY] = cache_key
    st.audio(audio_bytes, format="audio/mp3")


def get_enable_minute_chime_preference():
    return bool(get_user_preferences().get("enable_minute_chime", True))


def schedule_work_timer(duration_minutes, on_expiry, source_label):
    duration_seconds = max(1, int(round(float(duration_minutes) * 60)))
    append_timer_log_line(
        (
            "request_reset | timer=work_timer "
            f"source={source_label} duration_minutes={duration_minutes} "
            f"callback={getattr(on_expiry, '__name__', type(on_expiry).__name__)}"
        )
    )
    get_work_timer(st.session_state).reset(
        duration=duration_seconds,
        on_expiry=on_expiry,
    )


def get_work_timer_snapshot():
    return get_work_timer(st.session_state).snapshot()


def get_active_focus_timer_context():
    pomodoro_overlay_state = get_pomodoro_overlay_state()
    if pomodoro_overlay_state and pomodoro_overlay_state.get("mode") == "rest":
        return None

    body_doubling_flow = body_doubling.get_body_doubling_flow()
    if body_doubling_flow and body_doubling_flow.get("phase") == "session":
        duration_seconds = int((body_doubling_flow.get("session_duration_minutes") or 1) * 60)
        if body_doubling_flow.get("timer_source") == "work_timer":
            timer_snapshot = get_work_timer_snapshot()
            if not timer_snapshot.running or timer_snapshot.expires_at is None:
                return None
            duration_seconds = int(timer_snapshot.duration_seconds or duration_seconds)
            remaining_seconds = max(0, int(timer_snapshot.expires_at - datetime.now(pytz.UTC).timestamp()))
            started_at = (
                float(timer_snapshot.expires_at) - float(timer_snapshot.duration_seconds)
                if timer_snapshot.duration_seconds is not None
                else body_doubling_flow.get("session_started_at")
            )
            signature = (
                "body_doubling:work_timer:"
                f"{body_doubling_flow['task'].get('instance_id')}:{timer_snapshot.expires_at}"
            )
        else:
            session_ends_at = body_doubling_flow.get("session_ends_at")
            started_at = body_doubling_flow.get("session_started_at")
            if session_ends_at is None or started_at is None:
                return None
            remaining_seconds = max(0, int(float(session_ends_at) - datetime.now(pytz.UTC).timestamp()))
            signature = (
                "body_doubling:micro_session:"
                f"{body_doubling_flow['task'].get('instance_id')}:{started_at}"
            )

        elapsed_seconds = max(0, duration_seconds - remaining_seconds)
        return {
            "signature": signature,
            "elapsed_minutes": elapsed_seconds // 60,
            "remaining_seconds": remaining_seconds,
        }

    timer_snapshot = get_work_timer_snapshot()
    if not timer_snapshot.running or timer_snapshot.expires_at is None:
        return None

    duration_seconds = int(timer_snapshot.duration_seconds or 0)
    remaining_seconds = max(0, int(timer_snapshot.expires_at - datetime.now(pytz.UTC).timestamp()))
    elapsed_seconds = max(0, duration_seconds - remaining_seconds)
    return {
        "signature": f"work_timer:{timer_snapshot.expires_at}:{duration_seconds}",
        "elapsed_minutes": elapsed_seconds // 60,
        "remaining_seconds": remaining_seconds,
    }


def maybe_schedule_minute_chime():
    if not get_enable_minute_chime_preference():
        return

    timer_context = get_active_focus_timer_context()
    chime_state = st.session_state.setdefault(
        MINUTE_CHIME_STATE_KEY,
        {"signature": None, "last_elapsed_minute": 0},
    )

    if timer_context is None:
        chime_state["signature"] = None
        chime_state["last_elapsed_minute"] = 0
        return

    if chime_state.get("signature") != timer_context["signature"]:
        chime_state["signature"] = timer_context["signature"]
        chime_state["last_elapsed_minute"] = 0

    elapsed_minutes = int(timer_context["elapsed_minutes"])
    if elapsed_minutes < 1 or timer_context["remaining_seconds"] <= 0:
        return

    if elapsed_minutes == chime_state.get("last_elapsed_minute"):
        return

    chime_state["last_elapsed_minute"] = elapsed_minutes
    st.session_state[MINUTE_CHIME_PENDING_TOKEN_KEY] = (
        f"{timer_context['signature']}:{elapsed_minutes}"
    )


def render_pending_minute_chime():
    pending_token = st.session_state.get(MINUTE_CHIME_PENDING_TOKEN_KEY)
    if not pending_token:
        return

    if pending_token == st.session_state.get(MINUTE_CHIME_RENDERED_TOKEN_KEY):
        return

    st.markdown(
        audio_support.build_hidden_autoplay_audio_html(
            audio_support.build_minute_chime_wav_bytes(),
            "audio/wav",
        ),
        unsafe_allow_html=True,
    )
    st.session_state[MINUTE_CHIME_RENDERED_TOKEN_KEY] = pending_token


def maybe_schedule_engaged_cheer(result):
    """Queue a one-shot celebratory sound for Frozen -> Engaged transitions.

    The user asked for a clear audible cue when the FSM promotes them into
    Engaged. We key this off the transition messages rather than UI state so
    the sound only plays when the state-machine has really recognised that
    change.
    """

    if not result:
        return

    engaged_message_ids = {
        "STATE_FROZEN_TO_ENGAGED",
        "STATE_FROZEN_TO_ENGAGED_MOMENTUM",
        "STATE_FROZEN_TO_ENGAGED_MICROSTEPS",
    }
    if not any(message.message_id in engaged_message_ids for message in (result.ui_messages or [])):
        return

    context = result.context
    token = (
        "engaged:"
        f"{result.current_state or 'unknown'}:"
        f"{getattr(context, 'completed_tasks_in_session', 0)}:"
        f"{getattr(context, 'completed_microsteps_in_session', 0)}:"
        f"{datetime.now(pytz.UTC).timestamp()}"
    )
    st.session_state[ENGAGED_CHEER_PENDING_TOKEN_KEY] = token


def render_pending_engaged_cheer():
    """Autoplay the short celebratory cue once for each queued Engaged token."""

    pending_token = st.session_state.get(ENGAGED_CHEER_PENDING_TOKEN_KEY)
    if not pending_token:
        return

    if pending_token == st.session_state.get(ENGAGED_CHEER_RENDERED_TOKEN_KEY):
        return

    st.markdown(
        audio_support.build_hidden_autoplay_audio_html(
            audio_support.load_engaged_cheer_audio_bytes(),
            "audio/mpeg",
        ),
        unsafe_allow_html=True,
    )
    st.session_state[ENGAGED_CHEER_RENDERED_TOKEN_KEY] = pending_token


def get_body_doubling_services():
    service_kwargs = {
        "get_user_preferences": get_user_preferences,
        "save_user_preferences": lambda updates: save_user_profile_updates(
            preferences_updates=updates
        ),
        "update_task_status": update_task_status,
        "log_openai_event": log_openai_event,
        "get_openai_logger": get_openai_logger,
        "extract_openai_text": extract_openai_text,
        "openai_class": OpenAI,
        "openai_model": OPENAI_MODEL,
        "schedule_work_timer": schedule_work_timer,
        "disable_work_timer": disable_work_timer,
        "get_work_timer_snapshot": get_work_timer_snapshot,
        "get_effective_current_state_name": get_effective_current_state_name,
        "get_current_persona_profile_context": get_current_persona_profile_context,
        "get_persona_decompose_threshold": get_persona_decompose_threshold,
        "clear_task_completion_feedback_request": clear_task_completion_feedback_request,
        "notify_microstep_completed": lambda: dispatch_user_state_event(
            user_state_machine.MICROSTEP_COMPLETED_EVENT
        ),
        "notify_work_ended": notify_work_ended,
        "request_task_completion_feedback": request_task_completion_feedback,
    }
    accepted_parameters = inspect.signature(body_doubling.BodyDoublingServices).parameters
    compatible_kwargs = {
        key: value
        for key, value in service_kwargs.items()
        if key in accepted_parameters
    }
    return body_doubling.BodyDoublingServices(**compatible_kwargs)


def load_registration_welcome_prompt():
    try:
        return WELCOME_PROMPT_PATH.read_text(encoding="utf-8").strip()
    except OSError as error:
        log_openai_event(
            logging.ERROR,
            "Could not read registration welcome prompt file.",
            prompt_path=WELCOME_PROMPT_PATH,
            error=repr(error),
        )
        return REGISTRATION_WELCOME_PROMPT_FALLBACK


def build_registration_welcome_prompt(first_name, age, persona_description):
    prompt_template = load_registration_welcome_prompt()
    age_text = str(age) if age is not None else "not provided"
    return (
        f"{prompt_template}\n\n"
        "User context:\n"
        f"- First name: {first_name or 'not provided'}\n"
        f"- Age: {age_text}\n"
        f"- Persona description: {persona_description or 'not provided'}"
    )


def extract_openai_text(response):
    output_text = getattr(response, "output_text", None)
    if output_text:
        return output_text.strip()

    try:
        content = response.output[0].content[0]
        text = getattr(content, "text", None)
        return text.strip() if text else None
    except Exception:
        return None


def generate_registration_welcome_message(first_name, age, persona_description):
    api_key = os.environ.get("OPENAI_API_KEY")
    if OpenAI is None:
        log_openai_event(
            logging.ERROR,
            "OpenAI package is not installed; using fallback welcome message.",
            model=OPENAI_MODEL,
            first_name=first_name,
            age=age,
            persona_description=persona_description,
        )
        return build_registration_welcome_fallback(first_name)

    if not api_key:
        log_openai_event(
            logging.WARNING,
            "OPENAI_API_KEY is not configured; using fallback welcome message.",
            model=OPENAI_MODEL,
            first_name=first_name,
            age=age,
            persona_description=persona_description,
        )
        return build_registration_welcome_fallback(first_name)

    try:
        log_openai_event(
            logging.INFO,
            "Requesting registration welcome message from OpenAI.",
            model=OPENAI_MODEL,
            first_name=first_name,
            age=age,
            persona_description=persona_description,
        )
        client = OpenAI(api_key=api_key)
        response = client.responses.create(
            model=OPENAI_MODEL,
            input=build_registration_welcome_prompt(first_name, age, persona_description),
            max_output_tokens=180,
        )
        welcome_message = extract_openai_text(response)
        if not welcome_message:
            log_openai_event(
                logging.ERROR,
                "OpenAI response did not contain extractable text; using fallback welcome message.",
                model=OPENAI_MODEL,
                first_name=first_name,
                age=age,
                response_type=type(response).__name__,
            )
            return build_registration_welcome_fallback(first_name)

        log_openai_event(
            logging.INFO,
            "OpenAI registration welcome message generated successfully.",
            model=OPENAI_MODEL,
            first_name=first_name,
            age=age,
            message_length=len(welcome_message),
        )
        return welcome_message
    except Exception as error:
        get_openai_logger().exception(
            "OpenAI welcome message generation failed; using fallback. context=%s",
            json.dumps(
                {
                    "model": OPENAI_MODEL,
                    "first_name": first_name,
                    "age": age,
                    "persona_description": str(persona_description or "")[:300],
                    "error": repr(error),
                },
                ensure_ascii=False,
            ),
        )
        return build_registration_welcome_fallback(first_name)


def build_open_task_guidance_prompt(
    task_title,
    task_description,
    use_pomodoro_sprints,
    use_body_doubling,
    duration_minutes,
):
    return (
        "You are the supportive task-start voice of AI-ADHD.\n"
        "Write a concise British English message for a user who has just opened a task.\n"
        "Use a practical, warm, non-judgemental tone. Do not mention that you are an AI model.\n"
        "Keep it to 2 short paragraphs and focus on starting now.\n\n"
        "Task context:\n"
        f"- Title: {task_title or 'Untitled'}\n"
        f"- Description: {task_description or 'No description provided'}\n"
        f"- Uses Pomodoro sprint: {'yes' if use_pomodoro_sprints else 'no'}\n"
        f"- Uses Body-Doubling: {'yes' if use_body_doubling else 'no'}\n"
        f"- Timer duration in minutes: {duration_minutes}"
    )


def generate_open_task_guidance_message(
    task_row,
    use_pomodoro_sprints,
    use_body_doubling,
    duration_minutes,
):
    task_title = task_row.get("title", "Untitled")
    api_key = os.environ.get("OPENAI_API_KEY")

    if OpenAI is None:
        log_openai_event(
            logging.ERROR,
            "OpenAI package is not installed; using fallback open-task guidance.",
            model=OPENAI_MODEL,
            task_title=task_title,
            use_pomodoro_sprints=use_pomodoro_sprints,
            use_body_doubling=use_body_doubling,
        )
        return build_open_task_guidance_fallback(
            task_title,
            use_pomodoro_sprints,
            use_body_doubling,
        )

    if not api_key:
        log_openai_event(
            logging.WARNING,
            "OPENAI_API_KEY is not configured; using fallback open-task guidance.",
            model=OPENAI_MODEL,
            task_title=task_title,
            use_pomodoro_sprints=use_pomodoro_sprints,
            use_body_doubling=use_body_doubling,
        )
        return build_open_task_guidance_fallback(
            task_title,
            use_pomodoro_sprints,
            use_body_doubling,
        )

    try:
        log_openai_event(
            logging.INFO,
            "Requesting open-task guidance from OpenAI.",
            model=OPENAI_MODEL,
            task_title=task_title,
            use_pomodoro_sprints=use_pomodoro_sprints,
            use_body_doubling=use_body_doubling,
        )
        client = OpenAI(api_key=api_key)
        response = client.responses.create(
            model=OPENAI_MODEL,
            input=build_open_task_guidance_prompt(
                task_title=task_title,
                task_description=task_row.get("description"),
                use_pomodoro_sprints=use_pomodoro_sprints,
                use_body_doubling=use_body_doubling,
                duration_minutes=duration_minutes,
            ),
            max_output_tokens=180,
        )
        guidance_message = extract_openai_text(response)
        if not guidance_message:
            log_openai_event(
                logging.ERROR,
                "OpenAI response did not contain extractable text; using fallback open-task guidance.",
                model=OPENAI_MODEL,
                task_title=task_title,
                response_type=type(response).__name__,
            )
            return build_open_task_guidance_fallback(
                task_title,
                use_pomodoro_sprints,
                use_body_doubling,
            )

        log_openai_event(
            logging.INFO,
            "OpenAI open-task guidance generated successfully.",
            model=OPENAI_MODEL,
            task_title=task_title,
            message_length=len(guidance_message),
        )
        return guidance_message
    except Exception as error:
        get_openai_logger().exception(
            "OpenAI open-task guidance generation failed; using fallback. context=%s",
            json.dumps(
                {
                    "model": OPENAI_MODEL,
                    "task_title": task_title,
                    "use_pomodoro_sprints": use_pomodoro_sprints,
                    "use_body_doubling": use_body_doubling,
                    "error": repr(error),
                },
                ensure_ascii=False,
            ),
        )
        return build_open_task_guidance_fallback(
            task_title,
            use_pomodoro_sprints,
            use_body_doubling,
        )

#
#  Helper functions for user profile caching and preferences management
#
def load_user_profile_cache():
    # Start with a default profile structure to ensure all expected keys are present
    # This also serves as a fallback in case of any issues loading the actual profile
    default_profile = {
        "full_name": None,
        "first_name": None,
        "persona_id": None,
        "role": "user",
        "born": None,
        "age": None,
        "state_id": None,
        # Only include default values for preferences that are actually used
        "preferences": {
            "average_session_time": 120,
            "custom_sizes": [15, 30, 60, 180, 720],
            "chunk_min_floor_minutes": DEFAULT_CHUNK_MIN_FLOOR_MINUTES,
            "enable_minute_chime": True,
            "first_day_of_week": "SU",
            "keep_worthy": True,
            "rest_duration": DEFAULT_REST_DURATION_MINUTES,
            "max_continuous_work_minutes": DEFAULT_MAX_CONTINUOUS_WORK_MINUTES,
        },
    }
    user_id = st.session_state.get("user_id")

    if not user_id:
        st.session_state["user_profile"] = default_profile
        return default_profile
    
    try:
        profile = get_logged_user_model().load_profile(user_id)
    except Exception:
        profile = default_profile

    preferences = profile.get("preferences") or {}
    custom_sizes = preferences.get("custom_sizes") or default_profile["preferences"]["custom_sizes"]
    if len(custom_sizes) < 5:
        custom_sizes = default_profile["preferences"]["custom_sizes"]

    normalized_profile = {
        "full_name": profile.get("full_name"),
        "first_name": extract_first_name(profile.get("full_name")),
        "persona_id": profile.get("persona_id"),
        "role": profile.get("role") or default_profile["role"],
        "born": profile.get("born"),
        "age": calculate_age(profile.get("born")),
        "state_id": profile.get("state_id"),
        "preferences": {
            **preferences,
            "average_session_time": preferences.get(
                "average_session_time",
                default_profile["preferences"]["average_session_time"],
            ),
            "custom_sizes": custom_sizes,
            "chunk_min_floor_minutes": int(
                preferences.get(
                    "chunk_min_floor_minutes",
                    default_profile["preferences"]["chunk_min_floor_minutes"],
                )
            ),
            "enable_minute_chime": preferences.get(
                "enable_minute_chime",
                default_profile["preferences"]["enable_minute_chime"],
            ),
            "rest_duration": int(
                preferences.get(
                    "rest_duration",
                    default_profile["preferences"]["rest_duration"],
                )
            ),
            "keep_worthy": bool(
                preferences.get(
                    KEEP_WORTHY_PREFERENCE_KEY,
                    default_profile["preferences"][KEEP_WORTHY_PREFERENCE_KEY],
                )
            ),
            "max_continuous_work_minutes": int(
                preferences.get(
                    "max_continuous_work_minutes",
                    default_profile["preferences"]["max_continuous_work_minutes"],
                )
            ),
            "first_day_of_week": (
                preferences.get("first_day_of_week")
                if preferences.get("first_day_of_week") in VALID_FIRST_DAY_OF_WEEK_VALUES
                else default_profile["preferences"]["first_day_of_week"]
            ),
        },
    }
    st.session_state["user_profile"] = normalized_profile
    return normalized_profile


def ensure_user_profile_cache():
    if "user_profile" not in st.session_state:
        return load_user_profile_cache()
    return st.session_state["user_profile"]


def refresh_user_profile_cache():
    return load_user_profile_cache()


def get_user_preferences():
    profile = ensure_user_profile_cache()
    return profile["preferences"]


def get_recent_user_state_names(limit=3):
    user_id = st.session_state.get("user_id")
    if not user_id:
        return []

    response = (
        supabase.table("user_state_log")
        .select("state_id")
        .eq("user_id", user_id)
        .order("experienced_at", desc=True)
        .order("id", desc=True)
        .limit(limit)
        .execute()
    )
    return [
        get_state_name_by_id(row.get("state_id"))
        for row in (response.data or [])
        if row.get("state_id") is not None
    ]


def get_last_session_period_bounds():
    """Return the active-session bounds based on the user-state log.

    A session starts at the first non-Recovery state after the latest Recovery
    marker. Because voluntary and forced exits always push the user into
    Recovery, that split gives us the currently active session window.
    """

    user_id = st.session_state.get("user_id")
    if not user_id:
        return None

    response = (
        supabase.table("user_state_log")
        .select("state_id, experienced_at, id")
        .eq("user_id", user_id)
        .order("experienced_at", desc=False)
        .order("id", desc=False)
        .execute()
    )
    rows = response.data or []
    if not rows:
        return None

    last_recovery_index = -1
    for index, row in enumerate(rows):
        if get_state_name_by_id(row.get("state_id")) == user_state_machine.RECOVERY_STATE:
            last_recovery_index = index

    for row in rows[last_recovery_index + 1:]:
        if get_state_name_by_id(row.get("state_id")) != user_state_machine.RECOVERY_STATE:
            session_start = parse_task_datetime(row.get("experienced_at"))
            if session_start is None:
                return None
            return session_start, datetime.now(pytz.UTC)
    return None


def get_persona_name_by_id(persona_id):
    if not persona_id:
        return None

    persona = get_personas().get(persona_id)
    if persona:
        return persona.get("name")

    return None


def get_state_name_by_id(state_id):
    if not state_id:
        return None

    for state in get_all_states_options():
        if state.get("id") == state_id:
            return state.get("name")

    return None


def get_state_description_by_id(state_id):
    if not state_id:
        return None

    for state in get_all_states_options():
        if state.get("id") == state_id:
            return state.get("self_describing")

    return None


def is_recovery_state_id(state_id):
    """Return whether a persisted state id represents Recovery."""

    return get_state_name_by_id(state_id) == user_state_machine.RECOVERY_STATE


def get_persona_decompose_threshold():
    """Return the Body-Doubling decomposition threshold for the current persona.

    The threshold lives in the personas catalogue because it expresses how much
    decomposition support a user archetype tends to need across contexts. A
    null value means the persona should never auto-decompose through OpenAI.
    """

    user_profile = ensure_user_profile_cache()
    persona = get_personas().get(user_profile.get("persona_id")) or {}
    threshold = persona.get("decompose_threshold")
    if threshold in {None, ""}:
        return None

    try:
        return int(threshold)
    except (TypeError, ValueError):
        return None


def get_current_persona_name():
    user_profile = ensure_user_profile_cache()
    return get_persona_name_by_id(user_profile.get("persona_id"))


def get_current_persona_profile_context():
    """Return persona and age context for adaptive prompts.

    Body-Doubling prompt builders need a small, stable slice of profile data,
    not the whole cached profile. Keeping the extraction here avoids repeating
    the same persona lookup and fallback rules across multiple prompts.
    """

    user_profile = ensure_user_profile_cache()
    persona = get_personas().get(user_profile.get("persona_id")) or {}
    return {
        "persona_name": persona.get("name") or get_persona_name_by_id(user_profile.get("persona_id")),
        "persona_description": persona.get("description") or persona.get("self_describing"),
        "age": user_profile.get("age"),
    }


def get_current_state_name():
    user_profile = ensure_user_profile_cache()
    return get_state_name_by_id(user_profile.get("state_id"))


def get_effective_current_state_name():
    """Return the freshest known state, preferring the in-session FSM context."""

    fsm_context = st.session_state.get(user_state_machine.USER_FSM_CONTEXT_KEY) or {}
    context_state = fsm_context.get("current_state")
    if context_state:
        return context_state
    return get_current_state_name()


def rerun_app():
    """Request a full app rerun, even when called from a Streamlit fragment."""

    try:
        st.rerun(scope="app")
    except TypeError:
        st.rerun()


def get_adaptive_message_intensity(adaptation=None):
    """Resolve the canonical adaptive message-intensity name for this render."""

    resolved_adaptation = adaptation
    if resolved_adaptation is None:
        resolved_adaptation, _ = get_current_task_adaptation(get_tasks_dataframe())
    if not resolved_adaptation or not resolved_adaptation.message_intensity:
        return "high"
    return normalize_message_intensity(resolved_adaptation.message_intensity)


def get_effective_session_work_time():
    """Return the current expected work time, including temporary session overrides."""

    session_work_time = st.session_state.get("session_expected_work_time")
    if session_work_time is not None:
        return session_work_time

    return get_user_preferences().get("average_session_time", 120)


def get_resumable_session_elapsed_minutes():
    """Return active work-session minutes accumulated across resumable resumes.

    The resumable session spans canonical sessions only while the resume happens
    within the one-hour trust window. Closed canonical-session durations are
    persisted locally, while the currently active canonical segment is derived
    live from the user-state log so we do not count time spent outside the app.
    """

    elapsed_seconds = int(st.session_state.get(RESUMABLE_SESSION_ELAPSED_SECONDS_KEY, 0) or 0)
    current_bounds = get_last_session_period_bounds()
    if current_bounds:
        session_start, session_end = current_bounds
        if session_start and session_end and session_end > session_start:
            elapsed_seconds += int((session_end - session_start).total_seconds())
    return max(0.0, elapsed_seconds / 60.0)


def get_time_to_max_continuous_work_minutes():
    """Return remaining minutes before Chunk mode must force a rest break."""

    max_continuous_minutes = get_max_continuous_work_minutes()
    elapsed_chunk_minutes = get_chunk_continuous_work_seconds() / 60.0
    return max(0.0, float(max_continuous_minutes) - float(elapsed_chunk_minutes))


def get_first_day_of_week():
    """Return the validated first-day-of-week preference."""

    first_day = get_user_preferences().get("first_day_of_week", "SU")
    return first_day if first_day in VALID_FIRST_DAY_OF_WEEK_VALUES else "SU"


def get_week_period_bounds(reference_dt=None):
    current_dt = reference_dt or datetime.now(pytz.UTC)
    first_day = get_first_day_of_week()
    weekday_index_map = {
        "MO": 0,
        "TU": 1,
        "WE": 2,
        "TH": 3,
        "FR": 4,
        "SA": 5,
        "SU": 6,
    }
    start_weekday = weekday_index_map[first_day]
    days_back = (current_dt.weekday() - start_weekday) % 7
    week_start = (current_dt - timedelta(days=days_back)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return week_start, current_dt


def get_month_period_bounds(reference_dt=None):
    current_dt = reference_dt or datetime.now(pytz.UTC)
    month_start = current_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return month_start, current_dt


def get_user_state_time_summary(date_from, date_to=None):
    """Fetch aggregated time-per-state data for the current user."""

    resolved_to = date_to or datetime.now(pytz.UTC)
    response = supabase.rpc(
        "get_user_state_time_summary",
        {
            "p_user_id": st.session_state["user_id"],
            "p_date_from": date_from.isoformat(),
            "p_date_to": resolved_to.isoformat(),
        },
    ).execute()
    return response.data or []


def get_user_session_summaries(limit=STATE_TIME_RECENT_SESSIONS_LIMIT):
    """Fetch the most recent user sessions ordered from newest to oldest."""

    response = supabase.rpc(
        "get_user_session_summaries",
        {
            "p_user_id": st.session_state["user_id"],
            "p_limit": int(limit),
        },
    ).execute()
    return response.data or []


def format_duration_from_seconds(total_seconds):
    """Format a duration in seconds into a compact hours/minutes label."""

    total_seconds = int(total_seconds or 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def save_user_profile_updates(preferences_updates=None):
    """Persist preference changes for the user."""

    user_profile = ensure_user_profile_cache()
    current_preferences = user_profile.get("preferences", {})
    updated_preferences = {
        **current_preferences,
        **(preferences_updates or {}),
    }
    get_logged_user_model().update_preferences(
        st.session_state["user_id"],
        updated_preferences,
    )

    refreshed_profile = {
        **user_profile,
        "preferences": updated_preferences,
    }
    st.session_state["user_profile"] = refreshed_profile
    return refreshed_profile


def should_prompt_welcome_dialog():
    """Return whether the login flow should still ask for the initial state."""

    if not st.session_state.get("user_id"):
        return False

    if st.session_state.get("show_welcome_dialog"):
        return True

    user_profile = ensure_user_profile_cache()
    state_id = user_profile.get("state_id")
    if state_id is not None and not is_recovery_state_id(state_id):
        return False

    # Cookie-based auth restore should not trigger the Welcome dialog just
    # because the very first profile fetch after a container restart returned
    # incomplete data. Recovery is different: it is a terminal between-session
    # marker, not a valid active-session state, so the app must ask again.
    if is_recovery_state_id(state_id):
        return True

    # Explicit flows such as manual login or resumable-session reprompting
    # already set `show_welcome_dialog` themselves.
    return not bool(st.session_state.get(AUTH_RESTORED_FROM_COOKIE_KEY))


def parse_task_datetime(value):
    """Parse task timestamps coming from strings, pandas, dates, or datetimes."""

    # Be careful with pandas/NumPy null-like values here: some of them do not
    # behave well with plain truthiness checks and can silently blank dates in
    # the task grid if we bail out too early.
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
        if isinstance(value, pd.Timestamp):
            # Pandas can give us either timezone-aware or naive timestamps
            # depending on how the dataframe was built. Treat them like the
            # plain-datetime branch below so fresh task rows do not lose their
            # relative date labels in the grid.
            timestamp_value = value.to_pydatetime()
            if timestamp_value.tzinfo is None:
                return timestamp_value.replace(tzinfo=pytz.UTC)
            return timestamp_value.astimezone(pytz.UTC)
        if isinstance(value, date) and not isinstance(value, datetime):
            return datetime.combine(value, time(0, 0)).replace(tzinfo=pytz.UTC)
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=pytz.UTC)
            return value.astimezone(pytz.UTC)
        if isinstance(value, str):
            cleaned_value = value.strip()
            if not cleaned_value:
                return None
            return datetime.fromisoformat(cleaned_value.replace("Z", "+00:00")).astimezone(pytz.UTC)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(pytz.UTC)
    except (ValueError, TypeError, AttributeError):
        return None


def get_urgency_score(
    due_date_value,
    size_minutes,
    average_session_time,
    session_expected_work_time,
    now_utc,
):
    due_date = parse_task_datetime(due_date_value)
    if due_date is None or pd.isna(size_minutes):
        return pd.NA

    if due_date < now_utc:
        return 3.0

    expected_logoff = now_utc + timedelta(minutes=session_expected_work_time)
    slack_minutes = max((expected_logoff - now_utc).total_seconds() / 60, 1)

    if due_date.date() > now_utc.date():
        extra_days = (due_date.date() - now_utc.date()).days
        slack_minutes += extra_days * average_session_time

    urgency_ratio = size_minutes / max(slack_minutes, 1)

    if urgency_ratio > 0.95:
        return 5.0
    if urgency_ratio >= 0.8:
        return 4.0
    if urgency_ratio >= 0.6:
        return 3.0
    if urgency_ratio >= 0.3:
        return 2.0
    return 1.0


def get_task_rows():
    """Load task rows through the task-domain model."""

    return get_logged_task_model().list_user_task_rows()

def get_tasks_dataframe():
    """Build the enriched task dataframe used by the main grid and reports."""

    rows = get_task_rows()
    dataframe = pd.DataFrame(rows)
    if dataframe.empty:
        return dataframe

    # Recover the number of logged state changes per instance so advanced grid
    # views can expose a compact history signal without querying row by row.
    status_change_counts = get_task_status_change_counts(dataframe["instance_id"].tolist())
    dataframe["status_change_count"] = dataframe["instance_id"].map(status_change_counts).fillna(0).astype(int)

    # --- lookup weights ---
    size_weights = get_lookup_weights("dim_task_sizes")
    consequence_weights = get_lookup_weights("dim_task_consequences")
    friction_weights = get_lookup_weights("dim_task_frictions")
    user_preferences = get_user_preferences()
    size_to_time = {
        index + 1: minutes
        for index, minutes in enumerate(user_preferences["custom_sizes"])
    }
    average_session_time = user_preferences["average_session_time"]
    session_expected_work_time = get_effective_session_work_time()
    now_utc = datetime.now(pytz.UTC)
    consequence_factor = 1.5
    urgency_factor = 2.0
    size_factor = 1.0
    friction_factor = 2.0

    # Normalise datetime columns once at dataframe-build time so the grid,
    # urgency scoring, and relative-date labels all consume the same parsed
    # values instead of re-parsing mixed raw types independently.
    dataframe["start_date_parsed"] = dataframe["start_date"].apply(parse_task_datetime)
    dataframe["due_date_parsed"] = dataframe["due_date"].apply(parse_task_datetime)

    # --- map ids to weights ---
    dataframe["size_weight"] = dataframe["size_id"].map(size_weights)
    dataframe["consequence_weight"] = dataframe["consequence_id"].map(consequence_weights)
    dataframe["friction_weight"] = dataframe["friction_id"].map(friction_weights)
    if "actual_friction_id" in dataframe.columns:
        dataframe["actual_friction_weight"] = dataframe["actual_friction_id"].map(friction_weights)
    dataframe["size_minutes"] = dataframe["size_weight"].map(size_to_time)

    # --- priority scores ---
    dataframe["Urgency"] = dataframe.apply(
        lambda row: get_urgency_score(
            row["due_date_parsed"],
            row["size_minutes"],
            average_session_time,
            session_expected_work_time,
            now_utc,
        ),
        axis=1,
    )
    dataframe.loc[dataframe["status"].isin({"completed", "stale"}), "Urgency"] = 0

    task_title_map = dataframe.set_index("task_id")["title"].to_dict()
    task_routine_map = dataframe.set_index("task_id")["is_routine"].to_dict()
    dataframe["parent_title"] = dataframe["parent_task_id"].map(task_title_map)
    dataframe["parent_is_routine"] = dataframe["parent_task_id"].map(task_routine_map)
    direct_routine_flags = dataframe["is_routine"].astype("boolean")
    inherited_routine_flags = dataframe["parent_is_routine"].astype("boolean")
    dataframe["is_routine"] = (
        direct_routine_flags.fillna(False)
        | inherited_routine_flags.fillna(False)
    )
    dataframe["is_subtask"] = dataframe["parent_task_id"].notna()
    parent_task_ids = set(dataframe["parent_task_id"].dropna())
    dataframe["has_subtasks"] = dataframe["task_id"].isin(parent_task_ids)
    # Compound parent instances inherit the highest urgency of their direct
    # child instances. Parent rows are containers rather than executable work,
    # so their own due-date urgency is less meaningful than the most urgent
    # subtask they expose. Match children by parent_instance_id, not just
    # parent_task_id, so recurrent parent occurrences do not borrow urgency
    # from a different occurrence. This runs before WOBJ so adaptive ordering
    # consumes the inherited urgency.
    parent_rows = dataframe[
        dataframe["has_subtasks"]
        & (~dataframe["status"].isin({"completed", "stale"}))
    ]
    for parent_index, parent_row in parent_rows.iterrows():
        child_rows = dataframe[
            (dataframe["parent_task_id"] == parent_row["task_id"])
            & (dataframe["parent_instance_id"] == parent_row["instance_id"])
        ]
        if not child_rows.empty:
            dataframe.at[parent_index, "Urgency"] = child_rows["Urgency"].max()

    dataframe["WOBJ"] = (
        (dataframe["consequence_weight"] * consequence_factor)
        + (dataframe["Urgency"] * urgency_factor)
    ).round(2)

    dataframe["WSUB"] = (
        (dataframe["size_weight"] * size_factor)
        + (dataframe["friction_weight"] * friction_factor)
    ).round(2)

    # --- priority labels ---
    def get_priority_label(urgency):
        if urgency >= 5:
            return "🔴 High"
        elif urgency >= 3:
            return "🟡 Medium"
        return "🟢 Low"

    dataframe["priority_label"] = dataframe["Urgency"].apply(get_priority_label)

    # --- display columns ---
    dataframe["display_start_date"] = dataframe["start_date_parsed"].apply(
        lambda value: format_task_grid_date(value, now_utc=now_utc)
    )
    dataframe["display_due_date"] = dataframe["due_date_parsed"].apply(
        lambda value: format_task_grid_date(value, now_utc=now_utc)
    )
    dataframe["children_label"] = dataframe["has_subtasks"].apply(
        lambda has_children: "Yes" if bool(has_children) else ""
    )
    dataframe["display_title"] = dataframe["title"]

    return dataframe


def split_root_tasks_and_subtasks(tasks_df):
    """Split the enriched task dataframe into root tasks and child tasks.

    The main task page now renders one grid for root tasks and, when a parent is
    selected, a second grid for its subtasks. Keeping the two datasets separate
    avoids forcing one AgGrid sort order to preserve parent/child adjacency.
    """

    return active_task_grid.split_root_tasks_and_subtasks(tasks_df)


def format_lookup_option(item):
    """Format lookup-table items for select boxes."""

    return f"{item['label']} - {item['self_describing']}"


def format_state_option(item):
    """Format state options for human-readable select boxes."""

    return f"{item['name']} - {item['self_describing']}"


def parse_datetime_value(value):
    """Parse a datetime-like value and normalise it to UTC."""

    parsed = parse_task_datetime(value)
    if parsed is None:
        return None
    return parsed.astimezone(pytz.UTC)


def parse_rrule_components(rrule_value):
    """Split an RRULE string into its component key/value parts."""

    components = {}
    try:
        if rrule_value is None or pd.isna(rrule_value):
            return components
    except Exception:
        if rrule_value is None:
            return components

    if isinstance(rrule_value, float):
        return components

    if isinstance(rrule_value, str) and rrule_value.lower() == "nan":
        return components

    if not isinstance(rrule_value, str):
        rrule_value = str(rrule_value)

    if not rrule_value.strip():
        return components

    for part in rrule_value.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        components[key] = value

    return components


def get_option_index(options, selected_id):
    """Return the index of the selected lookup option id, if present."""

    for index, option in enumerate(options):
        if option["id"] == selected_id:
            return index
    return None


def get_nullable_option_index(options, selected_id):
    """Return an index for lookup options that may include a leading None."""

    for index, option in enumerate(options):
        if option is None and selected_id is None:
            return index
        if option is not None and option.get("id") == selected_id:
            return index
    return 0


def has_rrule_value(rrule_value):
    """Return whether a task row contains a meaningful recurrence rule."""

    return bool(parse_rrule_components(rrule_value))


def get_next_recurrence_start(start_at_value, rrule_value):
    """Return the next scheduled start for a recurrent task, if one exists."""

    if not start_at_value or not has_rrule_value(rrule_value):
        return None

    rule = rrulestr(f"RRULE:{rrule_value}", dtstart=start_at_value.astimezone(pytz.UTC))
    return rule.after(start_at_value.astimezone(pytz.UTC), inc=False)


def validate_recurrent_task_window(start_at_value, due_at_value, rrule_value):
    """Ensure one recurring occurrence finishes before the next one starts."""

    if not has_rrule_value(rrule_value):
        return True

    try:
        next_recurrence_start = get_next_recurrence_start(start_at_value, rrule_value)
    except Exception:
        st.error("Could not validate the recurrence schedule. Review the recurrence settings.")
        return False

    if next_recurrence_start is None:
        return True

    if due_at_value > next_recurrence_start:
        st.error(
            "For recurrent tasks, due date must not be later than the next occurrence start date "
            f"({next_recurrence_start.strftime('%Y-%m-%d %H:%M UTC')})."
        )
        return False

    return True


def get_task_row_by_instance_id(instance_id):
    """Return the current task row for one instance id."""

    rows = get_task_rows()
    for row in rows:
        if row.get("instance_id") == instance_id:
            return row
    return None


def get_enriched_task_row_by_instance_id(instance_id):
    """Return the current UI-enriched task row, including derived weights such as WSUB."""

    if not instance_id:
        return None

    tasks_df = get_tasks_dataframe()
    if tasks_df.empty or "instance_id" not in tasks_df.columns:
        return None

    matching_rows = tasks_df.loc[tasks_df["instance_id"] == instance_id]
    if matching_rows.empty:
        return None

    # Use the grid-ready row so guided flows receive the same derived values
    # the user saw in the UI, including WOBJ/WSUB and urgency calculations.
    return matching_rows.iloc[0].to_dict()


def get_task_row_by_task_id(task_id):
    """Return the current primary task row for one task id."""

    rows = get_task_rows()
    matching_rows = [row for row in rows if row.get("task_id") == task_id]
    if not matching_rows:
        return None
    return sorted(
        matching_rows,
        key=lambda row: int(row.get("instance_number") or 0),
    )[0]


def refresh_parent_task_for_subtask(parent_task):
    """Reload the parent task before creating a subtask under it."""

    if not parent_task:
        return None

    fresh_parent_task = get_task_row_by_instance_id(parent_task["instance_id"])
    if fresh_parent_task is None:
        st.error("Could not refresh the parent task before creating the subtask.")
        return None

    return {
        **parent_task,
        **fresh_parent_task,
    }


def get_valid_open_dialog_task():
    """Return a fresh open-task dialog target only if it is still actionable.

    Adaptive auto-open stores a task row in session state before the rerun.
    Between that moment and the next render the task may have changed state
    (for example, it may already be completed from a sprint review). Refreshing
    it here prevents the UI from reopening a stale task snapshot.
    """

    pending_task = st.session_state.get(OPEN_TASK_DIALOG_TASK_KEY)
    if not pending_task:
        return None

    instance_id = pending_task.get("instance_id")
    if not instance_id:
        clear_open_task_dialog_state()
        return None

    fresh_task = get_task_row_by_instance_id(instance_id)
    # Stale tasks remain actionable in the UI because opening them can mean
    # creating a cloned replacement instance with a new schedule.
    if not fresh_task:
        clear_open_task_dialog_state()
        return None

    st.session_state[OPEN_TASK_DIALOG_TASK_KEY] = fresh_task
    return fresh_task


def open_new_task_dialog(parent_task=None):
    """Open the new-task dialog, optionally in subtask mode."""

    st.session_state[NEW_TASK_DIALOG_PARENT_KEY] = parent_task


def close_new_task_dialog(*, reset_form=False):
    """Close the new-task dialog and optionally wipe its form state."""

    st.session_state.pop(NEW_TASK_DIALOG_PARENT_KEY, None)
    if reset_form:
        clear_new_task_form_state()


def clear_new_task_form_state():
    """Remove every session key owned by the new-task dialog."""

    for key in (
        NEW_TASK_PARENT_INSTANCE_KEY,
        "new_task_title",
        "new_task_description",
        "new_task_selected_list_name",
        "new_task_all_day",
        "new_task_schedule_signature",
        "new_task_start_date",
        "new_task_due_date",
        "new_task_last_start_date",
        "new_task_start_time",
        "new_task_due_time",
        "new_task_last_start_time",
        "new_task_selected_size",
        "new_task_selected_consequence",
        "new_task_selected_friction",
        "new_task_keep_open",
        "new_task_is_recurrent",
        "new_task_recurrence_frequency",
        "new_task_recurrence_interval",
        "new_task_has_end_date",
        "new_task_recurrence_end_date",
        "new_task_selected_weekdays",
    ):
        st.session_state.pop(key, None)


def reset_new_task_form_for_next_entry(parent_task=None):
    """Reset the new-task dialog to explicit defaults for the next entry.

    Streamlit can preserve widget values across reruns when the widget identity
    stays the same. Writing the next-entry defaults explicitly is more reliable
    than simply popping the keys when the dialog remains open after save.
    """

    st.session_state["new_task_title"] = ""
    st.session_state["new_task_description"] = ""
    st.session_state["new_task_selected_size"] = None
    st.session_state["new_task_selected_consequence"] = None
    st.session_state["new_task_selected_friction"] = None
    st.session_state["new_task_is_recurrent"] = False
    st.session_state["new_task_recurrence_frequency"] = None
    st.session_state["new_task_recurrence_interval"] = 1
    st.session_state["new_task_has_end_date"] = False
    st.session_state["new_task_recurrence_end_date"] = None
    st.session_state["new_task_selected_weekdays"] = []

    if parent_task:
        parent_start = parse_datetime_value(parent_task.get("start_date"))
        parent_due = parse_datetime_value(parent_task.get("due_date"))
        if parent_start and parent_due:
            # Follow-up subtasks reuse the current parent schedule by default.
            st.session_state["new_task_start_date"] = parent_start.date()
            st.session_state["new_task_due_date"] = parent_due.date()
            st.session_state["new_task_start_time"] = parent_start.time().replace(tzinfo=None)
            st.session_state["new_task_due_time"] = parent_due.time().replace(tzinfo=None)
            st.session_state["new_task_all_day"] = False
            st.session_state["new_task_schedule_signature"] = get_subtask_schedule_signature(parent_start)
            st.session_state[NEW_TASK_PARENT_INSTANCE_KEY] = parent_task.get("instance_id")
        if parent_task.get("list_id") is not None:
            parent_list_name = next(
                (
                    name
                    for name, list_id in {
                        item["name"]: item["id"] for item in get_user_lists()
                    }.items()
                    if list_id == parent_task["list_id"]
                ),
                None,
            )
            if parent_list_name:
                st.session_state["new_task_selected_list_name"] = parent_list_name
    else:
        default_start_date = datetime.now(pytz.UTC).date()
        default_schedule = get_new_task_schedule_defaults(default_start_date, True)
        st.session_state.pop(NEW_TASK_PARENT_INSTANCE_KEY, None)
        st.session_state["new_task_selected_list_name"] = None
        st.session_state["new_task_start_date"] = default_start_date
        st.session_state["new_task_all_day"] = True
        st.session_state["new_task_start_time"] = default_schedule["start_time"]
        st.session_state["new_task_due_date"] = default_schedule["due_date"]
        st.session_state["new_task_due_time"] = default_schedule["due_time"]
        st.session_state["new_task_schedule_signature"] = (
            f"{default_start_date.isoformat()}|1"
        )


def queue_new_task_form_reset(parent_task=None):
    """Store a deferred request to reset the new-task form on the next rerun.

    This avoids mutating widget-backed session keys after the widgets have
    already been instantiated in the current run, which Streamlit forbids.
    """

    if parent_task:
        st.session_state[NEW_TASK_RESET_PENDING_KEY] = {
            "task_id": parent_task.get("task_id"),
            "instance_id": parent_task.get("instance_id"),
        }
    else:
        st.session_state[NEW_TASK_RESET_PENDING_KEY] = {
            "task_id": None,
            "instance_id": None,
        }


def extract_created_task_id_from_rpc_response(raw_data):
    """Normalise the scalar task id returned by the task-creation RPC."""

    if isinstance(raw_data, list):
        return raw_data[0] if raw_data else None
    if isinstance(raw_data, dict):
        return raw_data.get("task_id") or raw_data.get("id")
    return raw_data


def get_subtask_schedule_signature(parent_start):
    """Return the schedule signature used by subtask prefill mode.

    The new-task dialog recalculates default times whenever the start-date/all-day
    signature changes. Subtask mode intentionally pins that signature to the
    copied parent schedule so the generic defaults do not overwrite it on the
    next rerun.
    """

    return f"{parent_start.date().isoformat()}|0"


def get_aggrid_selected_row(grid_response):
    """Normalise the selected row returned by AgGrid across response shapes."""

    selected_rows = None

    if hasattr(grid_response, "selected_rows"):
        selected_rows = grid_response.selected_rows
    elif isinstance(grid_response, dict):
        selected_rows = (
            grid_response.get("selected_rows")
            or grid_response.get("selectedRows")
            or grid_response.get("selected_data")
        )

    if selected_rows is None:
        return None

    if isinstance(selected_rows, pd.DataFrame):
        if selected_rows.empty:
            return None
        return selected_rows.iloc[0].to_dict()

    if isinstance(selected_rows, list):
        if not selected_rows:
            return None
        first_row = selected_rows[0]
        if isinstance(first_row, dict):
            return first_row
        if hasattr(first_row, "to_dict"):
            return first_row.to_dict()

    return None


def update_single_task_instance(task_row, start_at_value, due_at_value, mark_as_exception):
    """Update only one task instance, optionally marking it as an exception."""

    instance_payload = {
        "start_date": start_at_value.isoformat(),
        "due_date": due_at_value.isoformat(),
    }

    if mark_as_exception:
        instance_payload.update(
            {
                "is_exception": True,
                "original_start_date": task_row["start_date"],
                "original_due_date": task_row["due_date"],
            }
        )
    else:
        instance_payload.update(
            {
                "is_exception": False,
                "original_start_date": start_at_value.isoformat(),
                "original_due_date": due_at_value.isoformat(),
            }
        )

    (
        supabase.table("task_instances")
        .update(instance_payload)
        .eq("id", task_row["instance_id"])
        .execute()
    )


def get_edit_task_case(task_row):
    """Classify one selected task row according to the edit-policy scenarios."""

    if str(task_row.get("status") or "").lower() == "completed":
        return "completed"
    if str(task_row.get("status") or "").lower() == "stale":
        return "stale"
    if bool(task_row.get("parent_task_id")):
        parent_task = (
            get_task_row_by_instance_id(task_row.get("parent_instance_id"))
            if task_row.get("parent_instance_id")
            else None
        )
        return (
            "recurrent_subtask"
            if parent_task and has_rrule_value(parent_task.get("rrule"))
            else "non_recurrent_subtask"
        )
    if bool(task_row.get("has_subtasks")):
        return "recurrent_compound" if has_rrule_value(task_row.get("rrule")) else "non_recurrent_compound"
    return "recurrent_simple" if has_rrule_value(task_row.get("rrule")) else "non_recurrent_simple"


def has_pending_future_task_instances(task_row):
    """Return whether the selected task has later work instances already scheduled."""

    task_id = task_row.get("task_id")
    selected_instance_number = int(task_row.get("instance_number") or 0)
    if not task_id or not selected_instance_number:
        return False

    for row in get_task_rows():
        if row.get("task_id") != task_id:
            continue
        if int(row.get("instance_number") or 0) <= selected_instance_number:
            continue
        if str(row.get("status") or "").lower() in {"ready", "open", "asleep", "debt"}:
            return True
    return False


def render_edit_task_notice_if_needed(edit_case, task_row):
    """Show informational edit-policy dialogs described by the edit rationale."""

    message_id = None
    if edit_case == "non_recurrent_subtask":
        message_id = "E4"
    elif has_pending_future_task_instances(task_row):
        if edit_case == "recurrent_compound":
            message_id = "E6"
        elif edit_case == "recurrent_simple":
            message_id = "E7"
        elif edit_case == "recurrent_subtask":
            message_id = "E8"

    if not message_id:
        return

    display_message(
        message_id,
        get_adaptive_message_intensity(),
        renderer="warning",
        key_prefix=f"edit_task_{task_row.get('instance_id')}_{message_id}",
    )


@st.dialog("New task")
def new_task_form(parent_task=None):
    """Render the task-creation dialog with compact defaults and recurrence support."""

    pending_new_task_reset = st.session_state.pop(NEW_TASK_RESET_PENDING_KEY, None)
    if pending_new_task_reset is not None:
        resolved_parent_task = parent_task
        if pending_new_task_reset.get("instance_id"):
            resolved_parent_task = get_task_row_by_instance_id(
                pending_new_task_reset["instance_id"]
            ) or parent_task
        elif pending_new_task_reset.get("task_id"):
            resolved_parent_task = get_task_row_by_task_id(
                pending_new_task_reset["task_id"]
            ) or parent_task
        reset_new_task_form_for_next_entry(parent_task=resolved_parent_task)
        if resolved_parent_task:
            parent_task = resolved_parent_task

    user_lists = get_user_lists()
    list_options = {item["name"]: item["id"] for item in user_lists}
    list_names = list(list_options.keys())
    size_options = get_lookup_options("dim_task_sizes")
    consequence_options = get_lookup_options("dim_task_consequences")
    friction_options = get_lookup_options("dim_task_frictions")

    # When the dialog stays open across saves, explicitly normalise widget-backed
    # state to valid defaults before rendering the next form instance.
    if (
        "new_task_selected_size" not in st.session_state
        or st.session_state.get("new_task_selected_size") not in size_options
    ) and size_options:
        st.session_state["new_task_selected_size"] = size_options[0]
    if (
        "new_task_selected_consequence" not in st.session_state
        or st.session_state.get("new_task_selected_consequence") not in consequence_options
    ) and consequence_options:
        st.session_state["new_task_selected_consequence"] = consequence_options[0]
    if (
        "new_task_selected_friction" not in st.session_state
        or st.session_state.get("new_task_selected_friction") not in friction_options
    ) and friction_options:
        st.session_state["new_task_selected_friction"] = friction_options[0]

    if "new_task_start_date" not in st.session_state:
        st.session_state["new_task_start_date"] = datetime.now(pytz.UTC).date()
    if "new_task_all_day" not in st.session_state:
        st.session_state["new_task_all_day"] = True
    if "new_task_due_date" not in st.session_state:
        st.session_state["new_task_due_date"] = st.session_state["new_task_start_date"]
    if "new_task_start_time" not in st.session_state:
        st.session_state["new_task_start_time"] = get_next_available_time()
    if "new_task_due_time" not in st.session_state:
        st.session_state["new_task_due_time"] = time(18, 0)
    if "new_task_schedule_signature" not in st.session_state:
        st.session_state["new_task_schedule_signature"] = None

    parent_task_id = parent_task["task_id"] if parent_task else None
    parent_instance_number = parent_task["instance_number"] if parent_task else None
    parent_instance_id = parent_task["instance_id"] if parent_task else None

    if parent_task and st.session_state.get(NEW_TASK_PARENT_INSTANCE_KEY) != parent_instance_id:
        parent_start = parse_datetime_value(parent_task.get("start_date"))
        parent_due = parse_datetime_value(parent_task.get("due_date"))
        if parent_start and parent_due:
            # When entering subtask mode, prefill the current parent schedule.
            st.session_state["new_task_start_date"] = parent_start.date()
            st.session_state["new_task_due_date"] = parent_due.date()
            st.session_state["new_task_start_time"] = parent_start.time().replace(tzinfo=None)
            st.session_state["new_task_due_time"] = parent_due.time().replace(tzinfo=None)
            st.session_state["new_task_all_day"] = False
            st.session_state["new_task_schedule_signature"] = get_subtask_schedule_signature(parent_start)
        st.session_state[NEW_TASK_PARENT_INSTANCE_KEY] = parent_instance_id
    elif not parent_task:
        st.session_state.pop(NEW_TASK_PARENT_INSTANCE_KEY, None)

    st.markdown('<div class="new-task-form-shell">', unsafe_allow_html=True)
    if parent_task:
        st.markdown(
            (
                '<div class="new-task-card">'
                '<div class="new-task-kicker">Subtask</div>'
                '<div class="new-task-title">Create a subtask quickly and keep the timing aligned with its parent.</div>'
                '</div>'
            ),
            unsafe_allow_html=True,
        )
        st.caption(f"Creating a subtask for: {parent_task['title']}")

    title = st.text_input("Title", key="new_task_title", placeholder="Write a clear, concrete task title")
    description = st.text_area(
        "Description",
        key="new_task_description",
        placeholder="Optional context, notes, or the first step",
        height=110,
    )
    parent_list_name = None
    if parent_task:
        parent_list_name = next(
            (name for name, list_id in list_options.items() if list_id == parent_task["list_id"]),
            None,
        )

    if parent_task and parent_list_name:
        selected_list_name = parent_list_name
        st.text_input("List", value=parent_list_name, disabled=True)
    elif list_names:
        if (
            "new_task_selected_list_name" not in st.session_state
            or st.session_state.get("new_task_selected_list_name") not in list_names
        ):
            st.session_state["new_task_selected_list_name"] = list_names[0]
        default_list_index = 0
        current_list_name = st.session_state.get("new_task_selected_list_name")
        if current_list_name in list_names:
            default_list_index = list_names.index(current_list_name)
        selected_list_name = st.selectbox(
            "List",
            options=list_names,
            index=default_list_index,
            key="new_task_selected_list_name",
        )
    else:
        selected_list_name = None
        st.info("This user does not have any available list yet.")

    start_date = st.session_state["new_task_start_date"]
    all_day = st.session_state["new_task_all_day"]
    schedule_signature = f"{start_date.isoformat()}|{int(all_day)}"
    if st.session_state.get("new_task_schedule_signature") != schedule_signature:
        schedule_defaults = get_new_task_schedule_defaults(start_date, all_day)
        st.session_state["new_task_start_time"] = schedule_defaults["start_time"]
        st.session_state["new_task_due_date"] = schedule_defaults["due_date"]
        st.session_state["new_task_due_time"] = schedule_defaults["due_time"]
        st.session_state["new_task_schedule_signature"] = schedule_signature

    start_time = st.session_state["new_task_start_time"]
    due_date = st.session_state["new_task_due_date"]
    due_time = st.session_state["new_task_due_time"]

    compact_left, compact_right = st.columns([1.35, 0.75], gap="small")
    with compact_left:
        start_date = st.date_input(
            "Start date",
            key="new_task_start_date",
        )
    with compact_right:
        all_day = st.checkbox("All day", key="new_task_all_day")

    if not all_day:
        schedule_left, schedule_right = st.columns([1.2, 1.2], gap="small")
        with schedule_left:
            start_time = st.time_input("Start time", key="new_task_start_time")
        with schedule_right:
            due_date_col, due_time_col = st.columns([1.35, 1.1], gap="small")
            with due_date_col:
                due_date = st.date_input("Due date", key="new_task_due_date")
            with due_time_col:
                due_time = st.time_input("Due time", key="new_task_due_time")

    effort_left, effort_right, effort_third = st.columns([1.15, 1.15, 1.15], gap="small")
    with effort_left:
        selected_size = st.selectbox(
            "Task size",
            options=size_options,
            index=0 if size_options else None,
            format_func=format_lookup_option,
            key="new_task_selected_size",
        )
    with effort_right:
        selected_consequence = st.selectbox(
            "Consequence",
            options=consequence_options,
            index=0 if consequence_options else None,
            format_func=format_lookup_option,
            key="new_task_selected_consequence",
        )
    with effort_third:
        selected_friction = st.selectbox(
            "Friction",
            options=friction_options,
            index=0 if friction_options else None,
            format_func=format_lookup_option,
            key="new_task_selected_friction",
        )
    is_recurrent = False
    if parent_task_id:
        st.caption("Subtasks cannot be recurrent.")
    else:
        is_recurrent = st.checkbox("Recurrent task", key="new_task_is_recurrent")

    rrule_value = None
    recurrence_frequency = None
    recurrence_interval = 1
    recurrence_end_date = None
    selected_weekdays = []
    weekday_options = {
        "Monday": "MO",
        "Tuesday": "TU",
        "Wednesday": "WE",
        "Thursday": "TH",
        "Friday": "FR",
        "Saturday": "SA",
        "Sunday": "SU",
    }

    if is_recurrent:
        recurrence_frequency = st.selectbox(
            "Frequency",
            options=["DAILY", "WEEKLY", "MONTHLY"],
            key="new_task_recurrence_frequency",
        )
        recurrence_interval = st.number_input(
            "Repeat every",
            min_value=1,
            value=1,
            step=1,
            key="new_task_recurrence_interval",
        )

        if recurrence_frequency == "WEEKLY":
            selected_weekdays = st.multiselect(
                "Week days",
                options=list(weekday_options.keys()),
                default=[start_date.strftime("%A")],
                key="new_task_selected_weekdays",
            )

        has_end_date = st.checkbox("Set recurrence end date", key="new_task_has_end_date")
        if has_end_date:
            recurrence_end_date = st.date_input(
                "Recurrence end date",
                value=due_date,
                min_value=start_date,
                key="new_task_recurrence_end_date",
            )

    keep_open = st.checkbox(
        (
            "Keep this form open for entering new subtasks"
            if parent_task
            else "Keep this form open after creating the task"
        ),
        key="new_task_keep_open",
        value=bool(st.session_state.get("new_task_keep_open", False)),
    )

    if parent_task:
        create_column, cancel_column = st.columns(2, gap="small")
        create_subtask_clicked = False
    else:
        create_column, create_subtask_column, cancel_column = st.columns(3, gap="small")
    with create_column:
        create_clicked = st.button("Save", type="primary", use_container_width=True)
    if not parent_task:
        with create_subtask_column:
            create_subtask_clicked = st.button(
                "Save and create subtask",
                use_container_width=True,
            )
    with cancel_column:
        cancel_clicked = st.button("Cancel", use_container_width=True)

    if cancel_clicked:
        close_new_task_dialog(reset_form=False)
        st.rerun()

    if create_clicked or create_subtask_clicked:
        try:
            current_parent_task = refresh_parent_task_for_subtask(parent_task)
            if parent_task and current_parent_task is None:
                return

            list_id = list_options.get(selected_list_name)
            if not list_id:
                st.error("No list is available for this user yet.")
                return

            if not title.strip():
                st.error("Title is required.")
                return

            size_id = selected_size["id"] if selected_size else None
            consequence_id = selected_consequence["id"] if selected_consequence else None
            friction_id = selected_friction["id"] if selected_friction else None

            if not size_id or not consequence_id or not friction_id:
                st.error("Select a size, a consequence, and a friction level.")
                return

            if all_day:
                effective_schedule = get_new_task_schedule_defaults(start_date, True)
                start_time = effective_schedule["start_time"]
                due_date = effective_schedule["due_date"]
                due_time = effective_schedule["due_time"]

            start_at_value = combine_date_and_time_value(start_date, start_time)
            due_at_value = combine_date_and_time_value(due_date, due_time)
            if due_at_value < start_at_value:
                st.error("Due date and time must be later than or equal to the start date and time.")
                return

            if is_recurrent and recurrence_frequency == "WEEKLY" and not selected_weekdays:
                st.error("Select at least one week day for a weekly recurrent task.")
                return

            recurrence_until = None
            if recurrence_end_date:
                recurrence_until = combine_date_and_time_value(
                    recurrence_end_date,
                    due_time,
                )
                if recurrence_until < start_at_value:
                    st.error("Recurrence end date must be later than the start date.")
                    return

            if is_recurrent:
                rrule_value = build_rrule(
                    frequency=recurrence_frequency,
                    interval_value=int(recurrence_interval),
                    byweekday_values=[
                        weekday_options[day_name] for day_name in selected_weekdays
                    ],
                    until_value=recurrence_until,
                )
                if not validate_recurrent_task_window(start_at_value, due_at_value, rrule_value):
                    return

            start_at = start_at_value.isoformat()
            due_at = due_at_value.isoformat()

            create_response = supabase.rpc(
                "create_task_and_instances",
                {
                    "p_list_id": list_id,
                    "p_title": title.strip(),
                    "p_description": description.strip() or None,
                    "p_start_date": start_at,
                    "p_due_date": due_at,
                    "p_parent_task_id": parent_task_id,
                    "p_parent_instance_number": parent_instance_number,
                    "p_rrule": rrule_value,
                    "p_size_id": size_id,
                    "p_consequence_id": consequence_id,
                    "p_friction_id": friction_id,
                },
            ).execute()

            created_task_id = extract_created_task_id_from_rpc_response(create_response.data)
            synced_parent_task = None
            if parent_task_id:
                sync_parent_task_from_latest_child_instances(
                    parent_task_id,
                    parent_instance_id,
                )
                synced_parent_task = get_task_row_by_instance_id(parent_instance_id)
            st.session_state["tasks_grid_version"] += 1

            st.success("Task created successfully.")
            if keep_open:
                next_parent_task = (
                    (synced_parent_task or current_parent_task)
                    if parent_task
                    else None
                )
                queue_new_task_form_reset(parent_task=next_parent_task if parent_task else None)
                if parent_task:
                    open_new_task_dialog(next_parent_task)
                else:
                    created_parent_task = (
                        get_task_row_by_task_id(created_task_id)
                        if create_subtask_clicked and created_task_id
                        else None
                    )
                    open_new_task_dialog(created_parent_task)
            else:
                if create_subtask_clicked and created_task_id:
                    close_new_task_dialog(reset_form=True)
                    open_new_task_dialog(get_task_row_by_task_id(created_task_id))
                else:
                    close_new_task_dialog(reset_form=True)
            st.rerun()
        except Exception as e:
            st.error(f"Error creating task: {e}")
    st.markdown("</div>", unsafe_allow_html=True)


@st.dialog("Edit task")
def edit_task_form(task_row):
    edit_case = get_edit_task_case(task_row)

    if edit_case == "stale":
        st.error("Stale tasks cannot be edited.")
        return

    if edit_case == "completed":
        friction_options = [None] + get_lookup_options("dim_task_frictions")
        actual_friction_id = task_row.get("actual_friction_id")
        selected_actual_friction = st.selectbox(
            "Actual friction",
            options=friction_options,
            index=get_nullable_option_index(friction_options, actual_friction_id),
            format_func=lambda item: "No value" if item is None else format_lookup_option(item),
            key=f"edit_completed_actual_friction_{task_row.get('instance_id')}",
        )
        actual_duration_text = st.text_input(
            "Actual duration",
            value=format_actual_duration_minutes(task_row.get("actual_duration")),
            placeholder="00D:00H:00M",
            key=f"edit_completed_actual_duration_{task_row.get('instance_id')}",
        )
        final_comments = st.text_area(
            "Final comments",
            value=task_row.get("final_comments") or "",
            placeholder="Optional reflections about how the task actually went",
            key=f"edit_completed_final_comments_{task_row.get('instance_id')}",
            height=80,
        )
        save_completed_column, cancel_completed_column = st.columns(2, gap="small")
        with save_completed_column:
            save_completed_clicked = st.button(
                "Save",
                type="primary",
                use_container_width=True,
                key=f"edit_completed_save_{task_row.get('instance_id')}",
            )
        with cancel_completed_column:
            cancel_completed_clicked = st.button(
                "Cancel",
                use_container_width=True,
                key=f"edit_completed_cancel_{task_row.get('instance_id')}",
            )
        if cancel_completed_clicked:
            st.rerun()
            return
        if save_completed_clicked:
            try:
                save_task_completion_feedback(
                    task_row,
                    final_comments=(final_comments.strip() or None),
                    actual_friction_id=(
                        selected_actual_friction["id"] if selected_actual_friction else None
                    ),
                    actual_duration_minutes=parse_actual_duration_to_minutes(actual_duration_text),
                )
                st.success("Completion feedback updated successfully.")
                st.session_state["tasks_grid_version"] += 1
                st.rerun()
            except Exception as e:
                handle_api_exception(e, f"Could not update completion feedback: {e}")
        return

    render_edit_task_notice_if_needed(edit_case, task_row)

    user_lists = get_user_lists()
    list_options = {item["name"]: item["id"] for item in user_lists}
    list_names = list(list_options.keys())
    size_options = get_lookup_options("dim_task_sizes")
    consequence_options = get_lookup_options("dim_task_consequences")
    friction_options = get_lookup_options("dim_task_frictions")
    parsed_start = parse_datetime_value(task_row["start_date"]) or datetime.now(pytz.UTC)
    parsed_due = parse_datetime_value(task_row["due_date"]) or parsed_start
    initial_start_date = parsed_start.date()
    initial_due_date = parsed_due.date()
    rrule_raw_value = task_row.get("rrule")
    rrule_components = parse_rrule_components(rrule_raw_value)
    is_recurrent = has_rrule_value(rrule_raw_value)
    recurrence_frequency = rrule_components.get("FREQ", "DAILY")
    recurrence_interval = int(rrule_components.get("INTERVAL", "1"))
    selected_weekdays = []
    recurrence_end_date = None
    weekday_options = {
        "Monday": "MO",
        "Tuesday": "TU",
        "Wednesday": "WE",
        "Thursday": "TH",
        "Friday": "FR",
        "Saturday": "SA",
        "Sunday": "SU",
    }
    reverse_weekday_options = {value: key for key, value in weekday_options.items()}
    if rrule_components.get("BYDAY"):
        selected_weekdays = [
            reverse_weekday_options[day_code]
            for day_code in rrule_components["BYDAY"].split(",")
            if day_code in reverse_weekday_options
        ]
    if rrule_components.get("UNTIL"):
        try:
            recurrence_end_date = datetime.strptime(
                rrule_components["UNTIL"],
                "%Y%m%dT%H%M%SZ",
            ).date()
        except ValueError:
            recurrence_end_date = None

    initial_recurrence_end_date = (
        max(recurrence_end_date, initial_start_date)
        if recurrence_end_date
        else None
    )
    instance_id = task_row.get("instance_id")
    edit_widget_suffix = f"{instance_id}_{st.session_state.get('tasks_grid_version', 0)}"
    edit_start_date_key = f"edit_task_start_date_{edit_widget_suffix}"
    edit_start_time_key = f"edit_task_start_time_{edit_widget_suffix}"
    edit_due_date_key = f"edit_task_due_date_{edit_widget_suffix}"
    edit_due_time_key = f"edit_task_due_time_{edit_widget_suffix}"
    if edit_start_date_key not in st.session_state:
        st.session_state[edit_start_date_key] = initial_start_date
    if edit_start_time_key not in st.session_state:
        st.session_state[edit_start_time_key] = parsed_start.time().replace(tzinfo=None)
    if edit_due_date_key not in st.session_state:
        st.session_state[edit_due_date_key] = initial_due_date
    if edit_due_time_key not in st.session_state:
        st.session_state[edit_due_time_key] = parsed_due.time().replace(tzinfo=None)

    is_parent_container = bool(task_row.get("has_subtasks"))
    if is_parent_container:
        st.info(
            "Parent containers only allow title, description, and recurrence edits. "
            "Their dates and dimensions are derived from the subtasks they contain."
        )
    parent_derived_fields_disabled = is_parent_container

    current_list_name = next(
        (name for name, list_id in list_options.items() if list_id == task_row["list_id"]),
        None,
    )
    title = st.text_input("Title", value=task_row["title"])
    description = st.text_area(
        "Description",
        value=task_row["description"] or "",
        height=80,
    )

    if current_list_name and list_names:
        default_list_index = list_names.index(current_list_name)
        selected_list_name = st.selectbox(
            "List",
            options=list_names,
            index=default_list_index,
            disabled=parent_derived_fields_disabled,
        )
    elif current_list_name:
        selected_list_name = current_list_name
        st.text_input("List", value=current_list_name, disabled=True)
    else:
        selected_list_name = None
        st.info("This user does not have any available list yet.")

    with st.container(border=True):
        st.caption("Dates")
        start_date_column, start_time_column, due_date_column, due_time_column = st.columns(
            [1.35, 0.72, 1.35, 0.72],
            gap="medium",
        )
        with start_date_column:
            start_date = st.date_input(
                "Start date",
                min_value=EDIT_TASK_MIN_DATE,
                max_value=EDIT_TASK_MAX_DATE,
                disabled=parent_derived_fields_disabled,
                key=edit_start_date_key,
            )
        with start_time_column:
            start_time = st.time_input(
                "Start time",
                disabled=parent_derived_fields_disabled,
                key=edit_start_time_key,
            )
        with due_date_column:
            due_date = st.date_input(
                "Due date",
                min_value=EDIT_TASK_MIN_DATE,
                max_value=EDIT_TASK_MAX_DATE,
                disabled=parent_derived_fields_disabled,
                key=edit_due_date_key,
            )
        with due_time_column:
            due_time = st.time_input(
                "Due time",
                disabled=parent_derived_fields_disabled,
                key=edit_due_time_key,
            )

    with st.container(border=True):
        st.caption("Dimensions")
        size_column, consequence_column, friction_column = st.columns(3, gap="medium")
        with size_column:
            selected_size = st.selectbox(
                "Task size",
                options=size_options,
                index=get_option_index(size_options, task_row["size_id"]),
                format_func=format_lookup_option,
                disabled=parent_derived_fields_disabled,
            )
        with consequence_column:
            selected_consequence = st.selectbox(
                "Consequence",
                options=consequence_options,
                index=get_option_index(consequence_options, task_row["consequence_id"]),
                format_func=format_lookup_option,
                disabled=parent_derived_fields_disabled,
            )
        with friction_column:
            selected_friction = st.selectbox(
                "Friction",
                options=friction_options,
                index=get_option_index(friction_options, task_row["friction_id"]),
                format_func=format_lookup_option,
                disabled=parent_derived_fields_disabled,
            )

    if task_row["parent_task_id"]:
        st.caption("Subtasks cannot be recurrent.")
        is_recurrent = False
    else:
        recurrent_column, _, _ = st.columns([1, 1, 1], gap="small")
        with recurrent_column:
            is_recurrent = st.checkbox("Recurrent task", value=is_recurrent)

    if is_recurrent:
        with st.container(border=True):
            st.caption("Recurrence")
            frequency_options = ["DAILY", "WEEKLY", "MONTHLY"]
            recurrence_left, recurrence_middle = st.columns([1.35, 0.8], gap="medium")
            with recurrence_left:
                recurrence_frequency = st.selectbox(
                    "Frequency",
                    options=frequency_options,
                    index=frequency_options.index(recurrence_frequency) if recurrence_frequency in frequency_options else 0,
                )
            with recurrence_middle:
                recurrence_interval = st.number_input(
                    "Repeat every",
                    min_value=1,
                    value=int(recurrence_interval),
                    step=1,
                )

            if recurrence_frequency == "WEEKLY":
                default_weekdays = selected_weekdays or [start_date.strftime("%A")]
                selected_weekdays = st.multiselect(
                    "Week days",
                    options=list(weekday_options.keys()),
                    default=default_weekdays,
                )

            has_end_date = st.checkbox(
                "End of recurrence",
                value=recurrence_end_date is not None,
            )
            if has_end_date:
                recurrence_end_column, _, _ = st.columns([1.2, 1, 1], gap="small")
                with recurrence_end_column:
                    recurrence_end_date = st.date_input(
                        "Recurrence end date",
                        value=initial_recurrence_end_date or due_date,
                        min_value=start_date,
                    )
            else:
                recurrence_end_date = None

    save_column, cancel_column = st.columns(2, gap="small")
    with save_column:
        save_clicked = st.button(
            "Save",
            type="primary",
            use_container_width=True,
            key=f"edit_task_save_{task_row['instance_id']}",
        )
    with cancel_column:
        cancel_clicked = st.button(
            "Cancel",
            use_container_width=True,
            key=f"edit_task_cancel_{task_row['instance_id']}",
        )
    if cancel_clicked:
        st.rerun()
        return

    if save_clicked:
        try:
            start_at_value = combine_date_and_time_value(start_date, start_time)
            due_at_value = combine_date_and_time_value(due_date, due_time)
            if due_at_value < start_at_value:
                st.error("Due date must be later than or equal to start date.")
                return

            if is_parent_container:
                if not title.strip():
                    st.error("Title is required.")
                    return

                rrule_value = None
                if is_recurrent:
                    if recurrence_frequency == "WEEKLY" and not selected_weekdays:
                        st.error("Select at least one week day for a weekly recurrent task.")
                        return

                    recurrence_until = None
                    if recurrence_end_date:
                        recurrence_until = combine_date_and_time_value(recurrence_end_date, due_time)
                        if recurrence_until < start_at_value:
                            st.error("Recurrence end date must be later than the start date.")
                            return

                    rrule_value = build_rrule(
                        frequency=recurrence_frequency,
                        interval_value=int(recurrence_interval),
                        byweekday_values=[
                            weekday_options[day_name] for day_name in selected_weekdays
                        ],
                        until_value=recurrence_until,
                    )
                    if not validate_recurrent_task_window(start_at_value, due_at_value, rrule_value):
                        return

                (
                    supabase.table("tasks")
                    .update(
                        {
                            "title": title.strip(),
                            "description": description.strip() or None,
                            "rrule": rrule_value,
                        }
                    )
                    .eq("id", task_row["task_id"])
                    .execute()
                )
            else:
                list_id = list_options.get(selected_list_name)
                if not list_id:
                    st.error("No list is available for this user yet.")
                    return

                if not title.strip():
                    st.error("Title is required.")
                    return

                size_id = selected_size["id"] if selected_size else None
                consequence_id = selected_consequence["id"] if selected_consequence else None
                friction_id = selected_friction["id"] if selected_friction else None
                if not size_id or not consequence_id or not friction_id:
                    st.error("Select a size, a consequence, and a friction level.")
                    return

                if is_recurrent and recurrence_frequency == "WEEKLY" and not selected_weekdays:
                    st.error("Select at least one week day for a weekly recurrent task.")
                    return

                rrule_value = None
                if is_recurrent:
                    recurrence_until = None
                    if recurrence_end_date:
                        recurrence_until = combine_date_and_time_value(recurrence_end_date, due_time)
                        if recurrence_until < start_at_value:
                            st.error("Recurrence end date must be later than the start date.")
                            return

                    rrule_value = build_rrule(
                        frequency=recurrence_frequency,
                        interval_value=int(recurrence_interval),
                        byweekday_values=[
                            weekday_options[day_name] for day_name in selected_weekdays
                        ],
                        until_value=recurrence_until,
                    )
                    if not validate_recurrent_task_window(start_at_value, due_at_value, rrule_value):
                        return

                task_payload = {
                    "list_id": list_id,
                    "title": title.strip(),
                    "description": description.strip() or None,
                    "rrule": rrule_value,
                    "size_id": size_id,
                    "consequence_id": consequence_id,
                    "friction_id": friction_id,
                }

                (
                    supabase.table("tasks")
                    .update(task_payload)
                    .eq("id", task_row["task_id"])
                    .execute()
                )
                update_single_task_instance(
                    task_row=task_row,
                    start_at_value=start_at_value,
                    due_at_value=due_at_value,
                    mark_as_exception=False,
                )
                if task_row.get("parent_task_id"):
                    sync_parent_task_from_latest_child_instances(
                        task_row["parent_task_id"],
                        task_row.get("parent_instance_id"),
                    )

            st.success("Task updated successfully.")
            st.session_state["tasks_grid_version"] += 1
            st.rerun()
        except Exception as e:
            st.error(f"Error updating task: {e}")


@st.dialog("Task details")
def task_details_dialog(task_row):
    st.subheader(task_row["title"])
    details = {
        "Task ID": task_row["task_id"],
        "Instance ID": task_row["instance_id"],
        "List ID": task_row["list_id"],
        "Instance number": task_row["instance_number"],
        "Parent task ID": task_row["parent_task_id"],
        "Parent instance ID": task_row["parent_instance_id"],
        "Description": task_row["description"] or "-",
        "Start date": format_task_datetime(task_row["start_date"]),
        "Due date": format_task_datetime(task_row["due_date"]),
        "Status": task_row["status"],
        "RRULE": task_row["rrule"] or "-",
        "Size ID": task_row["size_id"],
        "Consequence ID": task_row["consequence_id"],
        "Friction ID": task_row["friction_id"],
        "Active": task_row["is_active"],
        "Routine": task_row.get("is_routine"),
        "Adaptive": task_row["is_adaptive"],
        "Priority": task_row.get("priority_label"),
        "WOBJ": task_row.get("WOBJ"),
        "WSUB": task_row.get("WSUB"),
        "Urgency": task_row.get("Urgency"),
    }

    for label, value in details.items():
        st.write(f"**{label}:** {value}")


def get_delete_task_context(task_row):
    response = supabase.rpc(
        "get_task_delete_context",
        {
            "p_task_id": task_row["task_id"],
            "p_instance_id": task_row["instance_id"],
        },
    ).execute()
    return response.data or {}


def classify_delete_task_case(context):
    """Map the selected row into the delete-policy scenario from the spec."""

    if context.get("is_subtask"):
        if context.get("parent_is_recurring"):
            return "recurrent_subtask"
        return "subtask_non_recurrent"

    if context.get("is_recurring"):
        if context.get("has_subtasks"):
            return "recurrent_compound"
        return "recurrent_simple"

    if context.get("has_subtasks"):
        return "non_recurrent_compound"

    return "non_recurrent_simple"


def remove_task_recurrency(task_row):
    """Clear the recurrence rule without deleting the task definition."""

    (
        supabase.table("tasks")
        .update({"rrule": None})
        .eq("id", task_row["task_id"])
        .execute()
    )
    st.session_state["tasks_grid_version"] += 1


def execute_delete_task_policy(task_row, *, delete_scope, keep_worthy=False):
    """Run the delete-policy RPC and refresh any affected parent summaries."""

    supabase.rpc(
        "delete_task_by_policy",
        {
            "p_task_id": task_row["task_id"],
            "p_instance_id": task_row["instance_id"],
            "p_scope": delete_scope,
            "p_keep_worthy": keep_worthy,
        },
    ).execute()

    if task_row.get("parent_task_id"):
        # Deleting one subtask can change parent dates and derived dimensions,
        # so refresh the parent summary immediately after the delete policy runs.
        sync_parent_task_from_latest_child_instances(
            task_row["parent_task_id"],
            task_row.get("parent_instance_id"),
        )

    st.session_state["tasks_grid_version"] += 1


def render_delete_policy_message(message_id, *, intensity, key_prefix, **params):
    """Render one delete-policy message and return the clicked button label."""

    result = display_message(
        message_id,
        intensity,
        renderer="warning",
        key_prefix=key_prefix,
        **params,
    )
    return result.button_clicked


def open_delete_task_dialog(task_row):
    """Dispatch to the Streamlit dialog wrapper with the correct static title."""

    if task_row.get("parent_task_id"):
        delete_subtask_dialog(task_row)
    elif bool(task_row.get("has_subtasks")):
        delete_compound_task_dialog(task_row)
    else:
        delete_task_dialog(task_row)


def render_delete_task_dialog_content(task_row, dialog_copy):
    """Render the shared delete-confirmation body for all task types."""

    try:
        context = get_delete_task_context(task_row)
    except Exception as e:
        handle_api_exception(e, f"Could not inspect delete impact: {e}")
        return

    st.write(f"{dialog_copy.target_label}: **{task_row['title']}**")
    st.caption(dialog_copy.subtitle)

    delete_case = classify_delete_task_case(context)
    intensity = get_adaptive_message_intensity()
    total_instance_count = int(context.get("total_instance_count", 0) or 0)
    has_future_instances = bool(context.get("has_future_instances"))
    warn_worthy = bool(context.get("warn_worthy"))
    warn_any_worthy = bool(context.get("warn_any_worthy"))
    current_worthy = bool(context.get("current_worthy"))
    has_multiple_instances = total_instance_count > 1

    message_button = None
    message_params = {}
    message_id = None

    if delete_case == "non_recurrent_simple":
        message_id = "D1" if warn_worthy else "D1.1"
    elif delete_case == "subtask_non_recurrent":
        message_id = "D1" if warn_worthy else "D1.2"
    elif delete_case == "non_recurrent_compound":
        message_id = "D2" if warn_any_worthy else "D2.1"
    elif delete_case == "recurrent_simple":
        if not has_multiple_instances:
            message_id = "D3" if warn_any_worthy else "D1.1"
        else:
            message_id = "D3.1"
            message_params = {
                "warn_worthy": warn_worthy,
                "show_selected_instance_button": not warn_worthy,
                "show_selected_and_future_button": not warn_worthy,
                "show_future_button": warn_worthy,
                "show_all_instances_button": True,
            }
    elif delete_case == "recurrent_subtask":
        if not has_multiple_instances:
            message_id = "D4"
            message_params = {
                "current_worthy": current_worthy,
            }
        else:
            message_id = "D4.1"
            message_params = {
                "warn_worthy": warn_worthy,
                "show_future_explanation": (not current_worthy) and has_future_instances,
                "show_selected_instance_button": not warn_worthy,
                "show_selected_and_future_button": not warn_worthy,
                "show_future_button": warn_worthy or has_future_instances,
                "show_all_instances_button": True,
            }
    elif delete_case == "recurrent_compound":
        if not has_multiple_instances:
            message_id = "D5" if warn_any_worthy else "D2.1"
        else:
            message_id = "D5.1"
            message_params = {
                "warn_worthy": warn_worthy,
                "show_selected_instance_button": not warn_worthy,
                "show_selected_and_future_button": not warn_worthy,
                "show_future_button": warn_worthy,
                "show_all_instances_button": True,
            }

    if message_id:
        message_button = render_delete_policy_message(
            message_id,
            intensity=intensity,
            key_prefix=f"delete_task_{task_row['instance_id']}_{message_id}",
            **message_params,
        )

    if not message_button:
        return

    try:
        if message_button in {"Ok", "Cancel"}:
            st.rerun()
            return

        if message_button == "Proceed":
            execute_delete_task_policy(task_row, delete_scope="current")
            st.rerun()
            return

        if message_button == "Do it!":
            execute_delete_task_policy(task_row, delete_scope="current")
            st.rerun()
            return

        if message_button == "Go!":
            execute_delete_task_policy(task_row, delete_scope="current")
            st.rerun()
            return

        if message_button == "Remove recurrency":
            remove_task_recurrency(task_row)
            st.rerun()
            return

        if message_button == "Selected instance":
            execute_delete_task_policy(task_row, delete_scope="current")
            st.rerun()
            return

        if message_button == "Selected and future instances":
            execute_delete_task_policy(task_row, delete_scope="selected_future")
            st.rerun()
            return

        if message_button == "Future instances":
            execute_delete_task_policy(task_row, delete_scope="future")
            st.rerun()
            return

        if message_button == "All instances":
            keep_worthy = False
            if delete_case in {"recurrent_simple", "recurrent_compound"}:
                keep_worthy = warn_any_worthy
            elif delete_case == "recurrent_subtask":
                keep_worthy = warn_any_worthy
            execute_delete_task_policy(
                task_row,
                delete_scope="all",
                keep_worthy=keep_worthy,
            )
            st.rerun()
            return
    except Exception as e:
        handle_api_exception(e, f"Could not delete task: {e}")


@st.dialog(get_delete_dialog_copy("simple_task").title)
def delete_task_dialog(task_row):
    render_delete_task_dialog_content(
        task_row,
        get_delete_dialog_copy("simple_task"),
    )


@st.dialog(get_delete_dialog_copy("compound_task").title)
def delete_compound_task_dialog(task_row):
    render_delete_task_dialog_content(
        task_row,
        get_delete_dialog_copy("compound_task"),
    )


@st.dialog(get_delete_dialog_copy("subtask").title)
def delete_subtask_dialog(task_row):
    render_delete_task_dialog_content(
        task_row,
        get_delete_dialog_copy("subtask"),
    )


def update_task_status(
    task_row,
    new_status,
    *,
    reopened_start_at=None,
    reopened_due_at=None,
    planner_open_target_state=None,
):
    """Apply a task-status transition through the task-domain model."""

    task_model = get_logged_task_model()
    transition_result = task_model.transition_to_status(
        task_row,
        new_status,
        task_rows=get_task_rows(),
        reopened_start_at=reopened_start_at,
        reopened_due_at=reopened_due_at,
    )
    st.session_state["tasks_grid_version"] += 1

    effective_task_row = transition_result.effective_task_row or task_row
    overlay_state = get_pomodoro_overlay_state()
    overlay_instance_id = overlay_state.get("instance_id") if overlay_state else None
    if overlay_state and overlay_instance_id in {
        task_row.get("instance_id"),
        effective_task_row.get("instance_id"),
    }:
        if new_status in {"completed", "asleep", "debt"}:
            clear_pomodoro_overlay_state()
            clear_focus_cycle_tracker()
    if new_status == "completed":
        clear_chunk_remaining_minutes(effective_task_row)
    for user_event in transition_result.user_state_events:
        dispatch_kwargs = {"event_payload": dict(user_event.payload or {})}
        if (
            user_event.event_name == user_state_machine.TASK_OPENED_EVENT
            and planner_open_target_state
        ):
            dispatch_kwargs["target_state"] = planner_open_target_state
            dispatch_kwargs["event_payload"]["planner_open_target_state"] = planner_open_target_state
        dispatch_user_state_event(user_event.event_name, **dispatch_kwargs)
    if transition_result.target_status == "completed":
        clear_adaptive_offered_tasks()
        if task_row.get("parent_task_id") and task_model.maybe_complete_parent_from_children(
            transition_result.effective_task_row or task_row
        ):
            st.info("The parent task was also completed because all its subtasks are complete.")
    return transition_result


def get_open_task_row(exclude_instance_id=None):
    for row in get_task_rows():
        if row.get("status") != "open":
            continue
        if exclude_instance_id and row.get("instance_id") == exclude_instance_id:
            continue
        return row
    return None


def is_guided_cycle_active_for_open_task():
    """Return whether an open task is currently inside a guided focus flow.

    Quick state switches should stay disabled while a guided cycle is active so
    the user cannot desynchronise the app state from Body-Doubling, Pomodoro,
    or chunk flows that are already in progress.
    """

    open_task = get_open_task_row()
    if not open_task:
        return False

    body_doubling_flow = body_doubling.get_body_doubling_flow()
    if body_doubling_flow:
        return True

    overlay_state = get_pomodoro_overlay_state() or {}
    if overlay_state.get("instance_id") == open_task.get("instance_id"):
        return True

    work_timer_snapshot = get_work_timer_snapshot()
    if work_timer_snapshot.running:
        return True

    return False


def clear_open_task_dialog_state():
    """Clear all session keys that belong to the open-task dialog flow."""

    st.session_state.pop(OPEN_TASK_DIALOG_TASK_KEY, None)
    st.session_state.pop(OPEN_TASK_DIALOG_SOURCE_KEY, None)
    st.session_state.pop(OPEN_TASK_DIALOG_EXECUTION_STATE_KEY, None)
    st.session_state.pop(OPEN_TASK_DIALOG_GRID_CONTEXT_KEY, None)
    st.session_state.pop(OPEN_TASK_PENDING_CONTEXT_KEY, None)
    st.session_state.pop(OPEN_TASK_START_CONTEXT_KEY, None)


def queue_open_task_start(context):
    """Defer opening a task until no Streamlit dialog is currently rendering."""

    st.session_state[OPEN_TASK_START_CONTEXT_KEY] = context
    st.session_state.pop(OPEN_TASK_DIALOG_TASK_KEY, None)
    st.session_state.pop(OPEN_TASK_DIALOG_SOURCE_KEY, None)
    st.session_state.pop(OPEN_TASK_DIALOG_EXECUTION_STATE_KEY, None)
    st.session_state.pop(OPEN_TASK_DIALOG_GRID_CONTEXT_KEY, None)
    st.session_state.pop(OPEN_TASK_PENDING_CONTEXT_KEY, None)


def process_pending_open_task_start():
    """Start a previously confirmed open-task flow outside dialog rendering."""

    context = st.session_state.pop(OPEN_TASK_START_CONTEXT_KEY, None)
    if context:
        complete_open_task_flow(context)


def build_open_task_context(
    task_row,
    pomodoro_choice,
    body_doubling_choice,
    *,
    reopened_start_at=None,
    reopened_due_at=None,
):
    """Build the execution context that drives the open-task flow."""

    current_adaptation, _ = get_open_task_execution_adaptation(get_tasks_dataframe())
    use_pomodoro_sprints = pomodoro_choice == "Yes"
    use_body_doubling = body_doubling_choice == "Yes"
    if (
        current_adaptation
        and current_adaptation.force_body_doubling_pomodoro_timing
        and use_body_doubling
    ):
        use_pomodoro_sprints = True
    duration_minutes = (
        get_effective_pomodoro_sprint_minutes()
        if use_pomodoro_sprints
        else (get_next_chunk_work_seconds(task_row) / 60.0)
    )
    current_page = st.session_state.get("current_page")
    return {
        "task_row": task_row,
        "use_pomodoro_sprints": use_pomodoro_sprints,
        "use_body_doubling": use_body_doubling,
        "duration_minutes": duration_minutes,
        "reopened_start_at": reopened_start_at,
        "reopened_due_at": reopened_due_at,
        "origin_page": current_page,
        "origin_my_tasks_filter_settings": (
            get_my_tasks_filter_settings()
            if current_page == "tasks"
            else None
        ),
        "origin_task_search_filter_settings": (
            get_task_search_filter_settings()
            if current_page == "task_search"
            else None
        ),
        # Some Planner adaptations need the opened task to land in a specific
        # execution state instead of relying only on the remembered state.
        "planner_open_target_state": (
            current_adaptation.planner_open_target_state if current_adaptation else None
        ),
    }


def complete_open_task_flow(context):
    """Execute the selected open-task flow and start the right timer/overlay."""

    task_row = dict(context["task_row"])
    use_pomodoro_sprints = context["use_pomodoro_sprints"]
    use_body_doubling = context["use_body_doubling"]
    duration_minutes = context["duration_minutes"]

    st.session_state["use_body_doubling"] = use_body_doubling
    transition_result = update_task_status(
        task_row,
        "open",
        reopened_start_at=context.get("reopened_start_at"),
        reopened_due_at=context.get("reopened_due_at"),
        planner_open_target_state=context.get("planner_open_target_state"),
    )
    active_task_row = transition_result.effective_task_row or task_row
    enriched_active_task_row = (
        get_enriched_task_row_by_instance_id(active_task_row.get("instance_id"))
        or active_task_row
    )
    clear_page_notices()
    if use_body_doubling:
        clear_pomodoro_overlay_state()
        with st.spinner("Preparing Body-Doubling support..."):
            body_doubling.start_body_doubling_flow(
                {
                    **enriched_active_task_row,
                    "use_pomodoro_sprints": use_pomodoro_sprints,
                },
                get_body_doubling_services(),
            )
    else:
        reset_work_timer_for_open_task(use_pomodoro_sprints)
        with st.spinner("Preparing task support..."):
            set_open_task_guidance_message(
                generate_open_task_guidance_message(
                    task_row=enriched_active_task_row,
                    use_pomodoro_sprints=use_pomodoro_sprints,
                    use_body_doubling=use_body_doubling,
                    duration_minutes=duration_minutes,
                )
            )
    clear_open_task_dialog_state()
    if use_body_doubling:
        st.success("Task opened with Body-Doubling.")
    else:
        st.success("Task opened.")
    st.rerun()


def render_existing_open_task_resolution(context):
    """Resolve conflicts when another task is already open before starting a new one."""

    requested_task = context.get("task_row") or {}
    existing_open_task = get_open_task_row(
        exclude_instance_id=requested_task.get("instance_id")
    )
    if not existing_open_task:
        # The conflict may have disappeared between the original dialog render
        # and this rerun (for example after a task was completed or another
        # branch cleared the open state). In that case the UI should not leave
        # a stale "another task is open" interstitial hanging around.
        st.session_state.pop(OPEN_TASK_PENDING_CONTEXT_KEY, None)
        queue_open_task_start(context)
        st.rerun()
        return

    context["existing_open_task"] = existing_open_task
    st.warning(
        f"You were working on **{existing_open_task['title']}**, "
        "and now we are suggesting something else. What do you really want to do?"
    )
    st.write(f"Did you complete **{existing_open_task['title']}**?")

    completed_column, asleep_column, cancel_column = st.columns(3)
    incomplete_status = get_incomplete_open_task_resolution_status(existing_open_task)
    incomplete_label = (
        "No, send to debt"
        if incomplete_status == "debt"
        else "No, send to sleep"
    )
    try:
        with completed_column:
            if st.button("Yes, completed", type="primary", use_container_width=True):
                request_task_completion_feedback(
                    existing_open_task,
                    "existing_open_resolution",
                    context=context,
                )
                st.rerun()

        with asleep_column:
            if st.button(incomplete_label, use_container_width=True):
                update_task_status(existing_open_task, incomplete_status)
                queue_open_task_start(context)
                st.rerun()

        with cancel_column:
            if st.button("Cancel", use_container_width=True):
                clear_open_task_dialog_state()
                st.rerun()
    except Exception as e:
        handle_api_exception(e, f"Could not resolve the previously open task: {e}")


@st.dialog("Open task")
def open_task_dialog(task_row):
    """Render the dialog used to choose Pomodoro and Body-Doubling options."""

    st.markdown('<span class="open-task-form-anchor"></span>', unsafe_allow_html=True)
    pending_context = st.session_state.get(OPEN_TASK_PENDING_CONTEXT_KEY)
    if pending_context:
        render_existing_open_task_resolution(pending_context)
        return

    current_tasks_df = get_tasks_dataframe()
    current_adaptation, adaptation_context = get_open_task_execution_adaptation(current_tasks_df)

    parent_title = (task_row.get("parent_title") or "").strip()
    safe_task_title = html.escape(str(task_row.get("title") or "Untitled"))
    st.markdown(
        f'<div class="open-task-title">{safe_task_title}</div>',
        unsafe_allow_html=True,
    )
    if parent_title:
        # Subtasks are easier to recognise when the execution dialog reminds
        # the user which parent container they belong to.
        st.caption(f"Parent task: {parent_title}")
    if current_adaptation and current_adaptation.guidance_message_id:
        display_message(
            current_adaptation.guidance_message_id,
            get_adaptive_message_intensity(current_adaptation),
            renderer="info",
        )
    if current_adaptation and current_adaptation.warn_if_open_outside_top_twenty_percent:
        top_twenty_instance_ids = task_adaptation.get_top_twenty_percent_instance_ids(
            task_adaptation.sort_tasks_for_intervention(current_tasks_df, current_adaptation),
            current_adaptation,
        )
        if task_row.get("instance_id") not in top_twenty_instance_ids:
            st.warning("This task is outside the top 20% of the current priority ranking.")

    # When the adaptive matrix is silent, default to plain focused work.
    # That means both toggles should land on "No" instead of implicitly opting
    # the user into Pomodoro or Body-Doubling.
    default_pomodoro_index = 1
    default_body_doubling_index = 1
    if current_adaptation and current_adaptation.default_pomodoro is not None:
        default_pomodoro_index = 0 if current_adaptation.default_pomodoro else 1
    if current_adaptation and current_adaptation.default_body_doubling is not None:
        default_body_doubling_index = 0 if current_adaptation.default_body_doubling else 1

    pomodoro_choice = st.selectbox(
        "Use Pomodoro sprints?",
        options=["Yes", "No"],
        index=default_pomodoro_index,
        placeholder="Choose yes or no",
        key=f"open_task_pomodoro_{task_row['instance_id']}",
    )
    body_doubling_choice = st.selectbox(
        "Use Body-Doubling?",
        options=["Yes", "No"],
        index=default_body_doubling_index,
        placeholder="Choose yes or no",
        key=f"open_task_body_doubling_{task_row['instance_id']}",
    )
    reopen_start_at = None
    reopen_due_at = None
    requires_historical_clone = task_row.get("status") in {"stale", "completed"}
    opening_blocked = False
    if task_row.get("status") == "stale" and task_row.get("rrule"):
        # Recurring stale tasks should wait for their recurrence rule instead
        # of spawning an ad-hoc cloned occurrence from the UI.
        st.error(
            "Recurring stale tasks cannot be reopened manually. Their next occurrences are generated from the recurrence rule."
        )
        opening_blocked = True
    elif requires_historical_clone:
        if task_row.get("status") == "stale":
            st.warning("This task is stale. To work on it again, the app will create a new instance with a new schedule.")
        else:
            st.info("This task is already completed. To work on it again, the app will create a new instance with a new schedule.")
        stale_defaults = get_new_task_schedule_defaults(datetime.now(pytz.UTC).date(), False)
        stale_start_date, stale_due_date = st.columns(2, gap="small")
        with stale_start_date:
            reopened_start_date = st.date_input(
                "New start date",
                value=datetime.now(pytz.UTC).date(),
                key=f"stale_reopen_start_date_{task_row['instance_id']}",
            )
        with stale_due_date:
            reopened_due_date = st.date_input(
                "New due date",
                value=stale_defaults["due_date"],
                min_value=reopened_start_date,
                key=f"stale_reopen_due_date_{task_row['instance_id']}",
            )
        stale_start_time, stale_due_time = st.columns(2, gap="small")
        with stale_start_time:
            reopened_start_time = st.time_input(
                "New start time",
                value=stale_defaults["start_time"],
                key=f"stale_reopen_start_time_{task_row['instance_id']}",
            )
        with stale_due_time:
            reopened_due_time = st.time_input(
                "New due time",
                value=stale_defaults["due_time"],
                key=f"stale_reopen_due_time_{task_row['instance_id']}",
            )
        reopened_start_at_value = combine_date_and_time_value(
            reopened_start_date,
            reopened_start_time,
        )
        reopened_due_at_value = combine_date_and_time_value(
            reopened_due_date,
            reopened_due_time,
        )
        if reopened_due_at_value < reopened_start_at_value:
            st.error("New due date and time must be later than or equal to the new start date and time.")
        else:
            reopen_start_at = reopened_start_at_value.isoformat()
            reopen_due_at = reopened_due_at_value.isoformat()
    timing_notice_placeholder = st.empty()
    if pomodoro_choice == "Yes" and body_doubling_choice == "Yes":
        timing_notice_placeholder.info(
            "Pomodoro will manage the timer. Body-Doubling will handle supportive guidance and review without starting a second countdown."
        )
    elif (
        current_adaptation
        and current_adaptation.force_body_doubling_pomodoro_timing
        and body_doubling_choice == "Yes"
    ):
        timing_notice_placeholder.info(
            "For this adaptive context, Body-Doubling will use Pomodoro timing automatically."
        )
    else:
        timing_notice_placeholder.empty()

    open_button_col, cancel_button_col = st.columns(2, gap="small")

    with open_button_col:
        if st.button(
            "OK",
            type="primary",
            use_container_width=True,
            key=f"open_task_ok_{task_row['instance_id']}",
        ):
            if pomodoro_choice is None or body_doubling_choice is None:
                st.error("Please answer both questions before opening the task.")
                return
            if opening_blocked:
                return

            try:
                context = build_open_task_context(
                    task_row,
                    pomodoro_choice,
                    body_doubling_choice,
                    reopened_start_at=reopen_start_at,
                    reopened_due_at=reopen_due_at,
                )
                if requires_historical_clone and (not reopen_start_at or not reopen_due_at):
                    return
                existing_open_task = get_open_task_row(
                    exclude_instance_id=task_row["instance_id"]
                )
                if existing_open_task:
                    context["existing_open_task"] = existing_open_task
                    st.session_state[OPEN_TASK_PENDING_CONTEXT_KEY] = context
                    st.rerun()

                queue_open_task_start(context)
                st.rerun()
            except Exception as e:
                handle_api_exception(e, f"Could not open task: {e}")

    with cancel_button_col:
        if st.button(
            "Cancel",
            use_container_width=True,
            key=f"open_task_cancel_{task_row['instance_id']}",
        ):
            if (
                current_adaptation
                and current_adaptation.cancel_needs_confirmation
                and adaptation_context.get("pending_due_today_count", 0) > 0
            ):
                st.warning(
                    "The prudent move is to keep completing a few due-today tasks before going back to list management. "
                    "Press Cancel again if you still want to close this dialog."
                )
                confirmation_key = f"open_task_cancel_confirmed_{task_row['instance_id']}"
                if not st.session_state.get(confirmation_key):
                    st.session_state[confirmation_key] = True
                    return
                st.session_state.pop(confirmation_key, None)
            if st.session_state.get(OPEN_TASK_DIALOG_SOURCE_KEY) in {
                "adaptive_auto_open",
                "login_auto_open",
            }:
                # In adaptive auto-open modes, cancelling the proposed task counts
                # as rejecting it. The FSM decides when the accumulated rejections
                # should move the user into Planner (for example Z=1 in
                # Hyper-focused + Frozen).
                mark_adaptive_task_offered(task_row.get("instance_id"))
                current_page = st.session_state.get("current_page")
                grid_context = dict(
                    st.session_state.get(OPEN_TASK_DIALOG_GRID_CONTEXT_KEY) or {}
                )
                origin_page = grid_context.get("origin_page") or current_page
                if origin_page == "task_search":
                    current_active_grid = build_task_search_active_grid(
                        current_tasks_df,
                        current_adaptation,
                        filter_settings_override={
                            **(
                                grid_context.get("task_search_filter_settings")
                                or {}
                            ),
                            "active_grid_kind": grid_context.get("active_grid_kind"),
                            "active_parent_task_id": grid_context.get(
                                "active_parent_task_id"
                            ),
                            "active_parent_instance_id": grid_context.get(
                                "active_parent_instance_id"
                            ),
                        },
                    )
                else:
                    current_active_grid = build_my_tasks_active_grid(
                        current_tasks_df,
                        current_adaptation,
                        filter_settings_override={
                            **(
                                grid_context.get("my_tasks_filter_settings")
                                or {}
                            ),
                            "active_grid_kind": grid_context.get("active_grid_kind"),
                            "active_parent_task_id": grid_context.get(
                                "active_parent_task_id"
                            ),
                            "active_parent_instance_id": grid_context.get(
                                "active_parent_instance_id"
                            ),
                        },
                    )
                # Recompute the next proposal from whichever grid is currently
                # active, so rejection handling stays aligned with the page the
                # user is actually navigating.
                next_auto_open_target = active_task_grid.get_next_open_candidate(
                    current_active_grid,
                    offered_instance_ids=get_adaptive_offered_instance_ids(),
                )
                if next_auto_open_target is None:
                    dispatch_user_state_event(
                        user_state_machine.AUTO_OPEN_CANDIDATES_EXHAUSTED_EVENT,
                    )
                else:
                    dispatch_user_state_event(user_state_machine.TASK_REJECTED_EVENT)
            clear_open_task_dialog_state()
            st.rerun()


@st.dialog("Task support")
def open_task_guidance_dialog():
    message = st.session_state.get(OPEN_TASK_GUIDANCE_MESSAGE_KEY)
    if not message:
        return

    safe_message = html.escape(str(message or ""))
    st.markdown(
        f'<div class="task-support-message">{safe_message}</div>',
        unsafe_allow_html=True,
    )
    render_voice_message_button(
        message,
        "open_task_guidance",
        modal_expiry_key=OPEN_TASK_GUIDANCE_EXPIRES_AT_KEY,
    )
    st.caption("This message closes automatically, but voice playback keeps it open a bit longer.")


def render_open_task_guidance_dialog():
    expires_at = st.session_state.get(OPEN_TASK_GUIDANCE_EXPIRES_AT_KEY)
    if expires_at is None:
        return

    if datetime.now(pytz.UTC).timestamp() >= float(expires_at):
        clear_open_task_guidance_message()
        return

    open_task_guidance_dialog()


@st.dialog("Sprint review", on_dismiss=clear_sprint_review_state)
def sprint_review_dialog():
    """Render the review dialog shown after a Pomodoro sprint finishes."""

    open_task = get_open_task_row()
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
                request_task_completion_feedback(
                    open_task,
                    "sprint_review",
                    rest_choice="No",
                )
                clear_sprint_review_state()
                st.rerun()
                return

        with new_cycle_col:
            if st.button(
                "Continue",
                key="sprint_review_new_cycle",
                use_container_width=True,
            ):
                begin_pomodoro_rest_break(previous_work_outcome="incomplete")
                clear_sprint_review_state()
                st.rerun()
                return

        with finish_col:
            if st.button(
                "Finish",
                key="sprint_review_finish",
                use_container_width=True,
            ):
                disable_work_timer()
                clear_pomodoro_overlay_state()
                if open_task:
                    next_status = get_post_work_incomplete_task_status(open_task)
                    if next_status != "open":
                        update_task_status(open_task, next_status)
                notify_work_ended()
                clear_sprint_review_state()
                st.rerun()
                return
    except Exception as e:
        handle_api_exception(e, f"Could not finish sprint review: {e}")


def render_sprint_review_dialog():
    if st.session_state.get(SPRINT_REVIEW_PENDING_KEY):
        sprint_review_dialog()


@st.dialog("Chunk review", on_dismiss=clear_chunk_review_state)
def chunk_review_dialog():
    """Render the review dialog shown after a generic work chunk finishes."""

    st.markdown(
        '<div class="review-status-note">Work cycle is over.</div>',
        unsafe_allow_html=True,
    )
    current_adaptation, _ = get_current_task_adaptation(get_tasks_dataframe())
    accumulated_chunk_seconds = get_chunk_continuous_work_seconds()
    max_continuous_work_seconds = get_max_continuous_work_minutes() * 60
    forced_rest_required = bool(
        current_adaptation
        and current_adaptation.protect_rest_breaks_with_messages
        and accumulated_chunk_seconds >= max_continuous_work_seconds
    )

    if forced_rest_required:
        st.info(
            "A rest break is required now because your continuous Chunk work has reached the configured limit."
        )

    task_complete_col, new_cycle_col, finish_col = st.columns(3, gap="small")

    try:
        open_task = get_open_task_row()

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
                request_task_completion_feedback(
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
                    begin_pomodoro_rest_break(
                        previous_work_outcome="incomplete",
                        resume_cycle_type="chunk",
                    )
                elif open_task:
                    duration_seconds = get_next_chunk_work_seconds()
                    schedule_work_timer(
                        duration_seconds / 60.0,
                        eoChunk,
                        "chunk_review_restart_work_cycle",
                    )
                    start_chunk_overlay(open_task, duration_seconds)
                else:
                    disable_work_timer()
                    clear_pomodoro_overlay_state()
                clear_chunk_review_state()
                st.rerun()
                return

        with finish_col:
            if st.button(
                "Finish",
                key="chunk_review_finish",
                use_container_width=True,
            ):
                clear_task_completion_feedback_request()
                disable_work_timer()
                clear_pomodoro_overlay_state()
                if open_task:
                    next_status = get_post_work_incomplete_task_status(open_task)
                    if next_status != "open":
                        update_task_status(open_task, next_status)
                notify_work_ended()
                clear_chunk_review_state()
                st.rerun()
                return
    except Exception as e:
        handle_api_exception(e, f"Could not finish chunk review: {e}")


def render_chunk_review_dialog():
    if st.session_state.get(CHUNK_REVIEW_PENDING_KEY):
        chunk_review_dialog()


@st.dialog("Rest")
def rest_resume_prompt_dialog():
    """Ask whether the user wants to resume work after a Pomodoro rest break."""

    prompt_context = get_rest_resume_prompt_context() or {}
    if not prompt_context:
        return

    display_message(
        "POMODORO_REST_OVER_RESUME_WORK",
        get_adaptive_message_intensity(),
        renderer="info",
    )

    resume_col, finish_col = st.columns(2, gap="medium")
    with resume_col:
        if st.button("Resume work", key="rest_resume_work_button", type="primary", use_container_width=True):
            resume_work_after_rest(prompt_context)
            return
    with finish_col:
        if st.button("Finish", key="rest_finish_button", use_container_width=True):
            finalize_post_rest_finish(prompt_context)
            st.rerun()


def render_rest_resume_prompt_dialog():
    """Render the post-rest resume/finish decision dialog when pending."""

    if st.session_state.get(REST_RESUME_PROMPT_PENDING_KEY):
        rest_resume_prompt_dialog()


def resume_work_after_rest(prompt_context):
    """Resume work immediately after a Pomodoro-style rest break."""

    open_task = get_open_task_row()
    resume_cycle_type = (prompt_context or {}).get("resume_cycle_type") or "pomodoro"
    work_duration_minutes = int(
        (prompt_context or {}).get("work_duration_minutes")
        or get_effective_pomodoro_sprint_minutes()
    )
    clear_rest_resume_prompt_context()
    clear_rest_message()
    if not open_task:
        notify_work_ended()
        st.rerun()
        return
    if resume_cycle_type == "chunk":
        request_next_chunk_cycle(
            open_task,
            source_label="chunk_rest_resume_work",
        )
    else:
        schedule_work_timer(
            work_duration_minutes,
            eoSprint,
            "pomodoro_rest_resume_work",
        )
        start_pomodoro_overlay(open_task, work_duration_minutes)
    st.rerun()


@st.dialog("Rest")
def rest_message_dialog():
    message = st.session_state.get(REST_MESSAGE_KEY)
    if not message:
        return

    st.write(message)
    render_voice_message_button(
        message,
        "rest_message",
        modal_expiry_key=REST_MESSAGE_EXPIRES_AT_KEY,
    )
    st.caption("This message closes automatically, but voice playback keeps it open a bit longer.")


def render_rest_message_dialog():
    expires_at = st.session_state.get(REST_MESSAGE_EXPIRES_AT_KEY)
    if expires_at is None:
        return

    if datetime.now(pytz.UTC).timestamp() >= float(expires_at):
        clear_rest_message()
        return

    rest_message_dialog()


def build_task_row_tooltip(row):
    """Build the plain-text tooltip shown when hovering a task-grid row."""

    def has_value(value):
        if value is None:
            return False
        try:
            if pd.isna(value):
                return False
        except (TypeError, ValueError):
            pass
        return str(value).strip() != ""

    def add_line(lines, label, value):
        if has_value(value):
            lines.append(f"{label}: {value}")

    tooltip_lines = []
    add_line(tooltip_lines, "Title", row.get("title") or row.get("display_title"))
    add_line(tooltip_lines, "Description", row.get("description"))
    if str(row.get("status") or "").lower() == "completed":
        add_line(tooltip_lines, "Due", row.get("display_due_date"))
        add_line(tooltip_lines, "Final comments", row.get("final_comments"))
        if has_value(row.get("actual_duration")):
            add_line(
                tooltip_lines,
                "Actual duration",
                format_actual_duration_minutes(row.get("actual_duration")),
            )
        add_line(tooltip_lines, "Actual friction", row.get("actual_friction_weight"))
        return "\n".join(tooltip_lines)

    add_line(tooltip_lines, "Start", row.get("display_start_date"))
    add_line(tooltip_lines, "Due", row.get("display_due_date"))
    add_line(tooltip_lines, "Status", row.get("status"))
    add_line(tooltip_lines, "WOBJ", row.get("WOBJ"))
    add_line(tooltip_lines, "WSUB", row.get("WSUB"))
    add_line(tooltip_lines, "Urgency", row.get("Urgency"))
    add_line(tooltip_lines, "Compound", row.get("children_label"))
    rrule_value = row.get("rrule")
    if has_value(rrule_value) and has_value(row.get("is_active")):
        active_label = "Active" if bool(row.get("is_active")) else "Inactive"
        rrule_value = f"{rrule_value}, {active_label}"
    add_line(tooltip_lines, "RRULE", rrule_value)

    dimension_parts = []
    for label, column_name in (
        ("size", "size_weight"),
        ("consequence", "consequence_weight"),
        ("friction", "friction_weight"),
    ):
        value = row.get(column_name)
        if has_value(value):
            dimension_parts.append(f"{label} {value}")
    if dimension_parts:
        tooltip_lines.append(f"Dimension weights: {', '.join(dimension_parts)}")

    return "\n".join(tooltip_lines)


def render_task_grid_table(
    filtered_tasks_df,
    *,
    grid_key_prefix,
    visible_row_limit,
    forced_show_all_columns=None,
    extra_visible_columns=None,
):
    """Render a task grid and return the currently selected row.

    Search results reuse the same column set and row-selection behaviour as the
    main task page so task actions stay consistent across views.
    """

    default_visible_columns = [
        "display_title",
        "display_due_date",
        "status",
        "WOBJ",
        "WSUB",
    ]
    all_fields_visible_columns = [
        "display_title",
        "display_due_date",
        "status",
        "status_change_count",
        "WOBJ",
        "Urgency",
        "WSUB",
        "size_minutes",
        "display_start_date",
        "rrule",
    ]
    if forced_show_all_columns is None:
        show_all_columns = st.toggle(
            "Show all task fields",
            value=False,
            key=f"{grid_key_prefix}_show_all_columns",
        )
    else:
        # Some pages want to place the "show all fields" toggle in a shared
        # filter strip above the grid while still reusing the same table
        # renderer underneath.
        show_all_columns = bool(forced_show_all_columns)
    visible_columns = (
        all_fields_visible_columns
        if show_all_columns
        else default_visible_columns
    )
    if extra_visible_columns:
        # Root-task grids may need one or two extra context columns without
        # changing the shared baseline used by other pages.
        visible_columns = visible_columns + [
            column_name
            for column_name in extra_visible_columns
            if column_name not in visible_columns
        ]
    grid_df = filtered_tasks_df.copy()
    grid_df["start_date_raw"] = grid_df["start_date"]
    grid_df["due_date_raw"] = grid_df["due_date"]
    # Recompute the display labels immediately before rendering the grid so
    # AgGrid always receives plain string values even if intermediate
    # dataframe operations changed dtypes earlier in the pipeline.
    grid_now_utc = datetime.now(pytz.UTC)
    grid_df["display_start_date"] = grid_df["start_date_raw"].apply(
        lambda value: format_task_grid_date(value, now_utc=grid_now_utc)
    )
    grid_df["display_due_date"] = grid_df["due_date_raw"].apply(
        lambda value: format_task_grid_date(value, now_utc=grid_now_utc)
    )
    grid_df["row_tooltip"] = grid_df.apply(build_task_row_tooltip, axis=1)
    # Keep parsed helper columns out of AgGrid. They are useful for local
    # calculations, but timezone-aware datetime helper fields can interfere
    # with grid serialisation and make otherwise valid display columns
    # disappear.
    grid_df = grid_df.drop(
        columns=["start_date_parsed", "due_date_parsed"],
        errors="ignore",
    )
    hidden_metadata_columns = [
        column_name
        for column_name in grid_df.columns
        if column_name not in visible_columns and column_name not in {"start_date", "due_date"}
    ]
    ordered_grid_columns = visible_columns + hidden_metadata_columns
    grid_df = grid_df[ordered_grid_columns].copy()
    grid_builder = GridOptionsBuilder.from_dataframe(grid_df)
    grid_builder.configure_selection(
        selection_mode="single",
        use_checkbox=False,
        suppressRowDeselection=False,
    )
    grid_builder.configure_grid_options(
        rowSelection="single",
        suppressRowClickSelection=False,
        rowMultiSelectWithClick=False,
        domLayout="normal",
        enableBrowserTooltips=True,
        tooltipShowDelay=250,
        tooltipHideDelay=10000,
    )
    for column_name in grid_df.columns:
        column_options = {"hide": column_name not in visible_columns}
        if column_name in visible_columns:
            column_options["tooltipField"] = "row_tooltip"
        grid_builder.configure_column(
            column_name,
            **column_options,
        )

    grid_builder.configure_column(
        "display_title",
        header_name="Title",
        width=360,
        minWidth=300,
        flex=2,
        cellStyle={"fontWeight": "bold"},
        tooltipField="row_tooltip",
    )
    grid_builder.configure_column(
        "display_due_date",
        header_name="Due date",
        width=190,
        minWidth=180,
        flex=1,
        type=["textColumn"],
        filter="agTextColumnFilter",
        cellDataType="text",
        tooltipField="row_tooltip",
    )
    grid_builder.configure_column(
        "display_start_date",
        header_name="Start date",
        width=190,
        minWidth=180,
        flex=1,
        type=["textColumn"],
        filter="agTextColumnFilter",
        cellDataType="text",
        tooltipField="row_tooltip",
    )
    grid_builder.configure_column("status", header_name="Status", tooltipField="row_tooltip")
    grid_builder.configure_column(
        "children_label",
        header_name="Compound",
        width=130,
        minWidth=125,
        tooltipField="row_tooltip",
    )
    grid_builder.configure_column(
        "status_change_count",
        header_name="Status changes",
        tooltipField="row_tooltip",
    )
    grid_builder.configure_column("WOBJ", header_name="WOBJ", tooltipField="row_tooltip")
    grid_builder.configure_column("WSUB", header_name="WSUB", tooltipField="row_tooltip")
    grid_builder.configure_column("Urgency", header_name="Urgency", tooltipField="row_tooltip")
    grid_builder.configure_column(
        "size_minutes",
        header_name="Size_minutes",
        tooltipField="row_tooltip",
    )
    grid_builder.configure_column("rrule", header_name="rrule", tooltipField="row_tooltip")
    grid_height = 64 + (min(len(grid_df.index), visible_row_limit) * 42)
    grid_response = AgGrid(
        grid_df,
        gridOptions=grid_builder.build(),
        height=grid_height,
        fit_columns_on_grid_load=True,
        allow_unsafe_jscode=False,
        theme="streamlit",
        update_on=["selectionChanged"],
        key=f"{grid_key_prefix}_{show_all_columns}_{st.session_state['tasks_grid_version']}_{visible_row_limit}",
        defaultColDef={
            "cellStyle": {"fontSize": "16px"},
            "tooltipField": "row_tooltip",
        }
    )

    selected_row = get_aggrid_selected_row(grid_response)
    if selected_row:
        if "start_date_raw" in selected_row:
            selected_row["start_date"] = selected_row.pop("start_date_raw")
        if "due_date_raw" in selected_row:
            selected_row["due_date"] = selected_row.pop("due_date_raw")
    return selected_row


def render_task_row_actions(
    selected_row,
    filtered_tasks_df,
    *,
    key_prefix,
    adaptation=None,
    allow_create_subtask=True,
):
    """Render the row actions shared by the main task view and search results."""

    # selected_description = (selected_row.get("description") or "").strip()
    # if selected_description:
    #     st.caption(f"{selected_row['title']}: {selected_description}")
    # else:
    #     st.caption(selected_row["title"])

    is_parent_task = bool(selected_row.get("has_subtasks"))
    action_columns = st.columns(4 if is_parent_task else 5)
    next_action_column = 0

    top_twenty_instance_ids = task_adaptation.get_top_twenty_percent_instance_ids(
        filtered_tasks_df,
        adaptation,
    )
    if (
        adaptation
        and adaptation.warn_if_open_outside_top_twenty_percent
        and selected_row.get("instance_id") not in top_twenty_instance_ids
    ):
        st.warning("This task is outside the top 20% of the current priority ranking.")

    if not is_parent_task:
        with action_columns[next_action_column]:
            if st.button(
                "Open",
                key=f"{key_prefix}_open_{selected_row['instance_id']}",
                use_container_width=True,
                disabled=selected_row["status"] in {"completed"},
            ):
                st.session_state[OPEN_TASK_DIALOG_TASK_KEY] = selected_row
                st.session_state[OPEN_TASK_DIALOG_SOURCE_KEY] = "manual"
                st.session_state[OPEN_TASK_DIALOG_GRID_CONTEXT_KEY] = build_open_task_grid_context()
        next_action_column += 1

    with action_columns[next_action_column]:
        if st.button(
            "Edit task",
            key=f"{key_prefix}_edit_{selected_row['instance_id']}",
            use_container_width=True,
        ):
            edit_task_form(selected_row)
    next_action_column += 1

    with action_columns[next_action_column]:
        if st.button(
            "Delete task",
            key=f"{key_prefix}_delete_{selected_row['instance_id']}",
            use_container_width=True,
        ):
            open_delete_task_dialog(selected_row)
    next_action_column += 1

    with action_columns[next_action_column]:
        if allow_create_subtask:
            if st.button(
                "Create subtask",
                key=f"{key_prefix}_subtask_{selected_row['instance_id']}",
                use_container_width=True,
                disabled=selected_row["status"] in {"completed", "stale"},
            ):
                open_new_task_dialog(parent_task=selected_row)
                st.rerun()
    next_action_column += 1

    with action_columns[next_action_column]:
        if st.button(
            "Mark as done",
            key=f"{key_prefix}_done_{selected_row['instance_id']}",
            use_container_width=True,
            # Debt can now be completed directly for cases where the
            # user already finished the work outside the scheduled slot.
            disabled=selected_row["status"] in {"completed", "stale"},
        ):
            request_task_completion_feedback(selected_row, "mark_done")
            st.rerun()


def render_selected_parent_subtasks(
    selected_parent_row,
    visible_subtasks_df,
    *,
    grid_key_prefix,
    actions_key_prefix,
    visible_row_limit,
    forced_show_all_columns=None,
    adaptation=None,
):
    """Render the shared secondary grid for a selected compound task.

    Both My Tasks and Task Search use the same parent/child interaction: the
    primary grid contains task instances, and selecting a compound parent should
    reveal only the child instances that belong to that exact parent instance.
    The caller provides the already-visible subtasks dataframe so page-level
    filters, such as routines or never-visible statuses, stay owned by the page.

    This helper intentionally centralizes the rest of the behavior: it checks
    whether the selected row is compound, filters child rows by parent task id
    and parent instance id, optionally applies adaptive ordering, renders the
    child grid using the same adaptive visible-row limit as the root task grid,
    and exposes row actions for the selected child without allowing another
    subtask to be created from it.
    """

    if not bool(selected_parent_row.get("has_subtasks")):
        return

    child_tasks_df = get_child_tasks_for_parent_instance(
        visible_subtasks_df,
        selected_parent_row,
    )
    child_tasks_df = task_adaptation.sort_tasks_for_intervention(
        child_tasks_df,
        adaptation,
    )

    st.markdown("### Subtasks")
    st.caption(f"Subtasks for: {selected_parent_row['title']}")
    if child_tasks_df.empty:
        st.info("This parent task has no subtasks matching the current filters.")
        return

    # Once the secondary child grid is visible, it becomes the active working
    # surface for guided-open flows. We persist only the parent identifiers
    # needed to rebuild that child list on the next rerun; the visual row
    # selection itself remains owned by AgGrid and Streamlit.
    st.session_state[ACTIVE_TASK_GRID_KIND_KEY] = "subtasks"
    st.session_state[ACTIVE_SUBTASK_PARENT_TASK_ID_KEY] = selected_parent_row.get(
        "task_id"
    )
    st.session_state[
        ACTIVE_SUBTASK_PARENT_INSTANCE_ID_KEY
    ] = selected_parent_row.get("instance_id")

    child_visible_row_limit = (
        adaptation.max_visible_tasks
        if adaptation and adaptation.max_visible_tasks
        else visible_row_limit
    )
    selected_child_row = render_task_grid_table(
        child_tasks_df,
        grid_key_prefix=f"{grid_key_prefix}_{selected_parent_row['instance_id']}",
        visible_row_limit=child_visible_row_limit,
        forced_show_all_columns=forced_show_all_columns,
    )
    if selected_child_row:
        render_task_row_actions(
            selected_child_row,
            child_tasks_df,
            key_prefix=actions_key_prefix,
            adaptation=adaptation,
            allow_create_subtask=False,
        )


def render_open_task_pending_notice():
    """Show recovery controls when the Open task dialog was dismissed externally."""

    if st.session_state.get("show_welcome_dialog"):
        return

    pending_task = st.session_state.get(OPEN_TASK_DIALOG_TASK_KEY)
    if not pending_task:
        return

    st.info(
        f"Opening task setup is still pending for **{pending_task.get('title', 'this task')}**."
    )
    reopen_column, cancel_column = st.columns([1, 1], gap="small")
    with reopen_column:
        if st.button("Reopen setup", key="reopen_open_task_dialog", use_container_width=True):
            st.rerun()
    with cancel_column:
        if st.button("Cancel opening", key="cancel_open_task_dialog", use_container_width=True):
            clear_open_task_dialog_state()
            st.rerun()


def render_tasks_page():
    """Render the main task-management page, grid, and task actions."""

    title_column, add_column, search_column = st.columns([1, 0.18, 0.10], gap="small")
    with title_column:
        st.markdown('<span class="my-tasks-title-anchor"></span>', unsafe_allow_html=True)
        st.title("My Tasks")
    with add_column:
        st.markdown('<span class="my-tasks-add-anchor"></span>', unsafe_allow_html=True)
        if st.button(
            ":material/add:",
            type="primary",
            help="Add task",
            use_container_width=True,
        ):
            open_new_task_dialog()
            st.rerun()
    with search_column:
        st.markdown('<span class="my-tasks-search-anchor"></span>', unsafe_allow_html=True)
        if st.button(
            ":material/search:",
            key="my_tasks_open_search",
            help="Search tasks",
            use_container_width=False,
        ):
            st.session_state["current_page"] = "task_search"
            st.rerun()

    render_adaptive_page_notices()

    try:
        tasks_df = get_tasks_dataframe()
        adaptation, adaptation_context = get_current_task_adaptation(tasks_df)
        maybe_apply_task_adaptation_parameters(adaptation)

        if adaptation:
            maybe_render_adaptive_notice(adaptation)
        render_open_task_pending_notice()

        if tasks_df.empty:
            st.session_state.pop(LOGIN_AUTO_OPEN_STATE_KEY, None)
            st.session_state.pop(GUIDED_OPEN_REQUEST_PENDING_KEY, None)
            st.info("You do not have any tasks yet.")
            new_task_dialog_parent = st.session_state.get(NEW_TASK_DIALOG_PARENT_KEY, None)
            if NEW_TASK_DIALOG_PARENT_KEY in st.session_state and not has_active_flow_dialog():
                new_task_form(parent_task=new_task_dialog_parent)
            return

        (
            routine_toggle_column,
            completed_toggle_column,
            completed_days_column,
            completed_days_unit_column,
            all_fields_toggle_column,
        ) = st.columns([1.05, 1.25, 0.42, 0.34, 1.2], gap="small")
        current_filter_settings = get_my_tasks_filter_settings()
        with routine_toggle_column:
            show_routines = st.toggle(
                "Show routines",
                value=bool(current_filter_settings["show_routines"]),
                key="tasks_grid_show_routines",
                help=(
                    "Shows tasks that the app identifies as routines. Today, that includes daily "
                    "or weekly recurrent tasks. Monthly recurrent tasks today appear as regular actions."
                ),
            )
        if st.session_state.get(ACTIVE_TASK_GRID_KIND_KEY) != "subtasks":
            st.session_state[ACTIVE_TASK_GRID_KIND_KEY] = (
                "periodic" if show_routines else "tasks"
            )
        with completed_toggle_column:
            show_completed_tasks = st.toggle(
                "Show completed tasks",
                value=bool(current_filter_settings["show_completed_tasks"]),
                key="tasks_grid_show_completed_tasks",
            )
        completed_days = int(current_filter_settings["completed_days"])
        if show_completed_tasks:
            with completed_days_column:
                completed_days = st.number_input(
                    "Completed task lookback days",
                    label_visibility="collapsed",
                    min_value=1,
                    value=int(current_filter_settings["completed_days"]),
                    step=1,
                    key="tasks_grid_completed_days",
                )
            with completed_days_unit_column:
                st.markdown(
                    '<span class="completed-days-unit-label">days</span>',
                    unsafe_allow_html=True,
                )
        with all_fields_toggle_column:
            show_all_columns = st.toggle(
                "Show all task fields",
                value=bool(current_filter_settings["show_all_columns"]),
                key="tasks_grid_filter_show_all_fields",
            )
        st.session_state[MY_TASKS_FILTER_SETTINGS_KEY] = {
            "active_grid_kind": st.session_state[ACTIVE_TASK_GRID_KIND_KEY],
            "show_routines": bool(show_routines),
            "show_completed_tasks": bool(show_completed_tasks),
            "completed_days": int(completed_days),
            "show_all_columns": bool(show_all_columns),
        }
        guided_auto_open_adaptation, _ = get_guided_auto_open_adaptation(tasks_df)
        # Guided open should operate on one coherent business grid. When a
        # latent execution state is waiting (for example after login or a quick
        # "Set Frozen/Engaged"), that pending state owns the ranking used both
        # for rendering and for the next open proposal.
        active_grid_adaptation = guided_auto_open_adaptation or adaptation
        current_active_grid = build_my_tasks_active_grid(
            tasks_df,
            active_grid_adaptation,
        )
        render_grid = active_task_grid.build_my_tasks_active_grid(
            tasks_df,
            active_grid_adaptation,
            show_routines=bool(show_routines),
            show_completed_tasks=bool(show_completed_tasks),
            completed_instance_ids=(
                {
                    entry["instance_id"]
                    for entry in get_user_status_log_entries(
                        {"completed"},
                        date_from=(
                            datetime.now(pytz.UTC)
                            - timedelta(days=int(completed_days))
                        ),
                    )
                    if entry.get("status") == "completed"
                }
                if show_completed_tasks
                else set()
            ),
            never_visible_statuses=GRID_NEVER_VISIBLE_STATUSES,
            active_statuses=GRID_ACTIVE_STATUSES,
        )
        filtered_tasks_df = render_grid.visible_df
        root_tasks_df = render_grid.root_df

        if filtered_tasks_df.empty:
            st.session_state.pop(LOGIN_AUTO_OPEN_STATE_KEY, None)
            st.session_state.pop(GUIDED_OPEN_REQUEST_PENDING_KEY, None)
            empty_label = "routines" if show_routines else "actions"
            st.info(f"You do not have any {empty_label} yet.")
            return

        # Subtasks keep their own secondary grid. Even when the main root-task
        # grid hides completed work, completed child tasks remain visible once a
        # parent is selected so the parent context shows the whole child history.
        base_visible_tasks_df = tasks_df[
            (tasks_df["is_routine"] == show_routines)
            & (~tasks_df["status"].isin(GRID_NEVER_VISIBLE_STATUSES))
        ].reset_index(drop=True)
        _, all_visible_subtasks_df = split_root_tasks_and_subtasks(base_visible_tasks_df)
        if root_tasks_df.empty:
            st.session_state.pop(LOGIN_AUTO_OPEN_STATE_KEY, None)
            st.session_state.pop(GUIDED_OPEN_REQUEST_PENDING_KEY, None)
            st.info("There are no root tasks matching the current filters.")
            return

        # The next guided-open proposal now comes from the active grid object
        # instead of being reconstructed from ad hoc page-specific dataframes.
        primary_task = active_task_grid.get_next_open_candidate(
            current_active_grid,
            offered_instance_ids=get_adaptive_offered_instance_ids(),
        )
        if not primary_task and not st.session_state.get(OPEN_TASK_DIALOG_TASK_KEY):
            st.session_state.pop(ADAPTIVE_ACTIVE_PARENT_TASK_ID_KEY, None)
            st.session_state.pop(LOGIN_AUTO_OPEN_STATE_KEY, None)
            st.session_state.pop(GUIDED_OPEN_REQUEST_PENDING_KEY, None)
        maybe_queue_adaptive_auto_open(
            active_grid_adaptation,
            primary_task,
            source=(
                "login_auto_open"
                if guided_auto_open_adaptation and primary_task
                else "adaptive_auto_open"
            ),
            execution_state_name=(
                guided_auto_open_adaptation.state_name
                if guided_auto_open_adaptation and primary_task
                else None
            ),
        )

        visible_row_limit = (
            active_grid_adaptation.max_visible_tasks
            if active_grid_adaptation and active_grid_adaptation.max_visible_tasks
            else 5
        )
        selected_root_row = render_task_grid_table(
            root_tasks_df,
            grid_key_prefix=f"tasks_root_grid_{show_routines}_{show_completed_tasks}",
            visible_row_limit=visible_row_limit,
            forced_show_all_columns=show_all_columns,
            extra_visible_columns=["children_label"],
        )
        guided_parent_task_id = st.session_state.get(ADAPTIVE_ACTIVE_PARENT_TASK_ID_KEY)
        if not selected_root_row and guided_parent_task_id:
            guided_parent_matches = root_tasks_df[
                root_tasks_df["task_id"] == guided_parent_task_id
            ].to_dict("records")
            if guided_parent_matches:
                # Adaptive auto-open may be working through a parent container
                # even when the user has not manually clicked that root row.
                # Reusing the ranked parent row here keeps the child grid in
                # view so the guided sequence remains understandable.
                selected_root_row = guided_parent_matches[0]

        if selected_root_row:
            if not bool(selected_root_row.get("has_subtasks")):
                # Selecting a simple root row means the active working surface
                # has moved back to the main grid, so any stale child-grid
                # parent context should be cleared.
                st.session_state[ACTIVE_TASK_GRID_KIND_KEY] = (
                    "periodic" if show_routines else "tasks"
                )
                st.session_state.pop(ACTIVE_SUBTASK_PARENT_TASK_ID_KEY, None)
                st.session_state.pop(ACTIVE_SUBTASK_PARENT_INSTANCE_ID_KEY, None)
            render_task_row_actions(
                selected_root_row,
                root_tasks_df,
                key_prefix="tasks_root",
                adaptation=adaptation,
            )

            render_selected_parent_subtasks(
                selected_root_row,
                all_visible_subtasks_df,
                grid_key_prefix="tasks_subtasks_grid",
                actions_key_prefix="tasks_subtasks",
                visible_row_limit=visible_row_limit,
                forced_show_all_columns=show_all_columns,
                adaptation=active_grid_adaptation,
            )

        if not has_active_flow_dialog() and not should_prompt_welcome_dialog():
            open_dialog_task = get_valid_open_dialog_task()
            if open_dialog_task:
                open_task_dialog(open_dialog_task)
            new_task_dialog_parent = st.session_state.get(NEW_TASK_DIALOG_PARENT_KEY, None)
            if NEW_TASK_DIALOG_PARENT_KEY in st.session_state:
                new_task_form(parent_task=new_task_dialog_parent)
    except Exception as e:
        handle_api_exception(e, f"Could not load tasks: {e}")


def render_task_search_page():
    """Render a dedicated search page for title/description task lookups."""

    st.title("Task Search")
    st.caption("Search tasks by title or description and act on the matching rows.")
    render_adaptive_page_notices()

    try:
        tasks_df = get_tasks_dataframe()
        if tasks_df.empty:
            st.info("You do not have any tasks yet.")
            return
        current_filter_settings = get_task_search_filter_settings()
        adaptation, _ = get_current_task_adaptation(tasks_df)
        guided_auto_open_adaptation, _ = get_guided_auto_open_adaptation(tasks_df)
        # Search results can also become the active working grid. Guided open
        # therefore uses the same adaptation-aware builder here instead of
        # falling back to the generic My Tasks dataset.
        active_grid_adaptation = guided_auto_open_adaptation or adaptation

        search_column, routine_column, stale_column = st.columns([2.2, 0.9, 0.9], gap="small")
        with search_column:
            search_query = st.text_input(
                "Search text",
                placeholder="Type part of a title or description",
                value=current_filter_settings["search_query"],
                key="task_search_query",
            )
        with routine_column:
            include_routines = st.toggle(
                "Include routines",
                value=current_filter_settings["include_routines"],
                key="task_search_include_routines",
            )
        with stale_column:
            include_stale = st.toggle(
                "Show archived",
                value=current_filter_settings["include_stale"],
                key="task_search_include_stale",
            )
        if st.session_state.get(ACTIVE_TASK_GRID_KIND_KEY) != "subtasks":
            st.session_state[ACTIVE_TASK_GRID_KIND_KEY] = "search_results"
        st.session_state[TASK_SEARCH_FILTER_SETTINGS_KEY] = {
            "search_query": str(search_query or ""),
            "include_routines": bool(include_routines),
            "include_stale": bool(include_stale),
        }

        if not search_query.strip():
            st.session_state.pop(LOGIN_AUTO_OPEN_STATE_KEY, None)
            st.info("Enter a search string to show matching tasks.")
            return

        current_active_grid = build_task_search_active_grid(
            tasks_df,
            active_grid_adaptation,
            filter_settings_override=st.session_state[TASK_SEARCH_FILTER_SETTINGS_KEY],
        )
        render_grid = active_task_grid.build_task_search_active_grid(
            tasks_df,
            active_grid_adaptation,
            search_query=st.session_state[TASK_SEARCH_FILTER_SETTINGS_KEY]["search_query"],
            include_routines=st.session_state[TASK_SEARCH_FILTER_SETTINGS_KEY]["include_routines"],
            include_stale=st.session_state[TASK_SEARCH_FILTER_SETTINGS_KEY]["include_stale"],
            never_visible_statuses=GRID_NEVER_VISIBLE_STATUSES,
        )
        results_df = render_grid.visible_df

        if results_df.empty:
            st.session_state.pop(LOGIN_AUTO_OPEN_STATE_KEY, None)
            st.session_state.pop(GUIDED_OPEN_REQUEST_PENDING_KEY, None)
            st.info("No tasks matched that search.")
            return

        if active_grid_adaptation and active_grid_adaptation.auto_open_first_task:
            primary_task = active_task_grid.get_next_open_candidate(
                current_active_grid,
                offered_instance_ids=get_adaptive_offered_instance_ids(),
            )
            if not primary_task and not st.session_state.get(OPEN_TASK_DIALOG_TASK_KEY):
                st.session_state.pop(LOGIN_AUTO_OPEN_STATE_KEY, None)
                st.session_state.pop(GUIDED_OPEN_REQUEST_PENDING_KEY, None)
            maybe_queue_adaptive_auto_open(
                active_grid_adaptation,
                primary_task,
                source=(
                    "login_auto_open"
                    if guided_auto_open_adaptation and primary_task
                    else "adaptive_auto_open"
                ),
                execution_state_name=(
                    guided_auto_open_adaptation.state_name
                    if guided_auto_open_adaptation and primary_task
                    else None
                ),
            )

        st.caption(f"{len(results_df)} matching task(s).")
        selected_row = render_task_grid_table(
            results_df,
            grid_key_prefix="task_search_grid",
            visible_row_limit=min(max(len(results_df.index), 1), 8),
            extra_visible_columns=["children_label"],
        )

        if selected_row:
            if not bool(selected_row.get("has_subtasks")):
                # The search results grid is the active working surface again
                # once the user focuses a simple row instead of a compound one.
                st.session_state[ACTIVE_TASK_GRID_KIND_KEY] = "search_results"
                st.session_state.pop(ACTIVE_SUBTASK_PARENT_TASK_ID_KEY, None)
                st.session_state.pop(ACTIVE_SUBTASK_PARENT_INSTANCE_ID_KEY, None)
            render_task_row_actions(
                selected_row,
                results_df,
                key_prefix="task_search",
                adaptation=None,
            )
            visible_tasks_df = tasks_df.copy()
            if not include_stale:
                visible_tasks_df = visible_tasks_df[
                    ~visible_tasks_df["status"].isin(GRID_NEVER_VISIBLE_STATUSES)
                ].reset_index(drop=True)
            if not include_routines:
                visible_tasks_df = visible_tasks_df[
                    ~visible_tasks_df["is_routine"]
                ].reset_index(drop=True)
            _, visible_subtasks_df = split_root_tasks_and_subtasks(visible_tasks_df)
            render_selected_parent_subtasks(
                selected_row,
                visible_subtasks_df,
                grid_key_prefix="task_search_subtasks_grid",
                actions_key_prefix="task_search_subtasks",
                visible_row_limit=(
                    active_grid_adaptation.max_visible_tasks
                    if active_grid_adaptation and active_grid_adaptation.max_visible_tasks
                    else 8
                ),
                adaptation=active_grid_adaptation,
            )

        if not has_active_flow_dialog() and not should_prompt_welcome_dialog():
            open_dialog_task = get_valid_open_dialog_task()
            if open_dialog_task:
                open_task_dialog(open_dialog_task)
            new_task_dialog_parent = st.session_state.get(NEW_TASK_DIALOG_PARENT_KEY, None)
            if NEW_TASK_DIALOG_PARENT_KEY in st.session_state:
                new_task_form(parent_task=new_task_dialog_parent)
    except Exception as e:
        handle_api_exception(e, f"Could not load task search: {e}")


def render_state_time_page():
    """Render the report that summarises user-state time for session, week, and month."""

    st.title("State Time")
    first_day = get_first_day_of_week()
    now_utc = datetime.now(pytz.UTC)
    week_start, week_end = get_week_period_bounds(now_utc)
    month_start, month_end = get_month_period_bounds(now_utc)

    st.caption(
        f"Current week starts on {first_day}. "
        f"Week: {week_start.strftime('%Y-%m-%d %H:%M UTC')} to {week_end.strftime('%Y-%m-%d %H:%M UTC')}. "
        f"Month: {month_start.strftime('%Y-%m-%d %H:%M UTC')} to {month_end.strftime('%Y-%m-%d %H:%M UTC')}."
    )

    try:
        session_summaries = get_user_session_summaries()
        latest_session = session_summaries[0] if session_summaries else None
        latest_session_start = (
            parse_task_datetime(latest_session.get("session_started_at"))
            if latest_session
            else None
        )
        latest_session_end = (
            parse_task_datetime(latest_session.get("session_ended_at"))
            if latest_session
            else None
        )
        session_rows = (
            get_user_state_time_summary(latest_session_start, latest_session_end)
            if latest_session_start and latest_session_end
            else []
        )
        week_rows = get_user_state_time_summary(week_start, week_end)
        month_rows = get_user_state_time_summary(month_start, month_end)
    except Exception as error:
        handle_api_exception(error, f"Could not load state time summary: {error}")
        return

    session_column, week_column, month_column = st.columns(3, gap="large")

    with session_column:
        st.subheader("Last session")
        if not latest_session:
            st.info("No session window was found in the state log yet.")
        else:
            st.caption(
                "Session: "
                f"{latest_session_start.strftime('%Y-%m-%d %H:%M UTC')} "
                f"to {latest_session_end.strftime('%Y-%m-%d %H:%M UTC')}"
            )
            st.metric(
                "Duration",
                format_duration_from_seconds(latest_session.get("duration_seconds")),
            )
            if not session_rows:
                st.info("No logged state time for the current session yet.")
            else:
                session_df = pd.DataFrame(session_rows)
                session_df["time_spent"] = session_df["seconds_in_state"].apply(format_duration_from_seconds)
                st.dataframe(
                    session_df[["state_name", "time_spent", "hours_in_state"]],
                    use_container_width=True,
                    hide_index=True,
                )

    st.subheader(f"Last {STATE_TIME_RECENT_SESSIONS_LIMIT} sessions")
    if not session_summaries:
        st.info("No recent sessions were found in the state log yet.")
    else:
        recent_sessions_df = pd.DataFrame(session_summaries)
        # Format the RPC output into a compact session-history table that can
        # be scanned quickly without exposing implementation-specific fields.
        recent_sessions_df["started_at_display"] = recent_sessions_df["session_started_at"].apply(format_task_datetime)
        recent_sessions_df["ended_at_display"] = recent_sessions_df["session_ended_at"].apply(format_task_datetime)
        recent_sessions_df["duration_display"] = recent_sessions_df["duration_seconds"].apply(format_duration_from_seconds)
        recent_sessions_df["session_label"] = recent_sessions_df["session_index"].apply(lambda value: f"Session {value}")
        recent_sessions_df["current_label"] = recent_sessions_df["is_current_session"].apply(
            lambda value: "Yes" if value else "No"
        )
        st.dataframe(
            recent_sessions_df[
                [
                    "session_label",
                    "current_label",
                    "started_at_display",
                    "ended_at_display",
                    "duration_display",
                ]
            ].rename(
                columns={
                    "session_label": "Session",
                    "current_label": "Current",
                    "started_at_display": "Started at",
                    "ended_at_display": "Ended at",
                    "duration_display": "Duration",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )

    with week_column:
        st.subheader("Current week")
        if not week_rows:
            st.info("No logged state time for the current week yet.")
        else:
            week_df = pd.DataFrame(week_rows)
            week_df["time_spent"] = week_df["seconds_in_state"].apply(format_duration_from_seconds)
            st.dataframe(
                week_df[["state_name", "time_spent", "hours_in_state"]],
                use_container_width=True,
                hide_index=True,
            )

    with month_column:
        st.subheader("Current month")
        if not month_rows:
            st.info("No logged state time for the current month yet.")
        else:
            month_df = pd.DataFrame(month_rows)
            month_df["time_spent"] = month_df["seconds_in_state"].apply(format_duration_from_seconds)
            st.dataframe(
                month_df[["state_name", "time_spent", "hours_in_state"]],
                use_container_width=True,
                hide_index=True,
            )


def render_task_outcomes_report_page():
    """Render the completed/stale task outcomes report backed by the status log."""

    st.title("Task Outcomes")
    now_utc = datetime.now(pytz.UTC)
    week_start = now_utc - timedelta(days=7)
    month_start = now_utc - timedelta(days=30)

    st.caption(
        "This report shows completed and stale task instances ordered by status-log date."
    )

    def build_outcomes_dataframe(date_from, date_to):
        entries = get_user_status_log_entries({"completed", "stale"}, date_from=date_from, date_to=date_to)
        if not entries:
            return pd.DataFrame()

        dataframe = pd.DataFrame(entries)
        dataframe["changed_at_display"] = dataframe["log_changed_at"].apply(format_task_datetime)
        dataframe["display_due_date"] = dataframe["due_date"].apply(format_task_datetime)
        dataframe["display_start_date"] = dataframe["start_date"].apply(format_task_datetime)
        dataframe["status_label"] = dataframe["log_status"].str.title()
        dataframe = dataframe.sort_values(by="log_changed_at", ascending=False)
        return dataframe[
            [
                "changed_at_display",
                "status_label",
                "title",
                "description",
                "display_start_date",
                "display_due_date",
                "instance_number",
            ]
        ].rename(
            columns={
                "changed_at_display": "Changed at",
                "status_label": "Status",
                "title": "Title",
                "description": "Description",
                "display_start_date": "Start date",
                "display_due_date": "Due date",
                "instance_number": "Instance",
            }
        )

    week_column, month_column = st.columns(2, gap="large")

    with week_column:
        st.subheader("Last week")
        week_df = build_outcomes_dataframe(week_start, now_utc)
        if week_df.empty:
            st.info("No completed or stale tasks were logged in the last week.")
        else:
            st.dataframe(week_df, use_container_width=True, hide_index=True)

    with month_column:
        st.subheader("Last month")
        month_df = build_outcomes_dataframe(month_start, now_utc)
        if month_df.empty:
            st.info("No completed or stale tasks were logged in the last month.")
        else:
            st.dataframe(month_df, use_container_width=True, hide_index=True)


@st.dialog("Keep worthy completed tasks")
def keep_worthy_preference_info_dialog():
    """Explain the delete-preservation preference for worthy completed tasks."""

    st.write(
        "If you enable this preference, delete flows will keep completed tasks "
        "that contain meaningful feedback instead of deleting them when the "
        "underlying delete policy supports preserving worthy history."
    )
    st.write(
        "A task is considered worthy when the user has provided any of these:"
    )
    st.markdown(
        "\n".join(
            [
                "- Final comments",
                "- Actual size",
                "- Actual friction",
                "- Actual consequence",
            ]
        )
    )
    st.write(
        "This is useful when you want to clean the active list without losing "
        "completed tasks that already contain valuable review data."
    )


def render_preferences_page():
    """Render the user preferences page and persist profile-level settings."""

    st.title("Edit Preferences")
    preferences = ensure_user_profile_cache().get("preferences", {})

    with st.form("edit_preferences_form"):
        st.markdown('<span class="preferences-form-anchor"></span>', unsafe_allow_html=True)
        st.subheader("General")
        general_left, general_middle, general_right = st.columns(3, gap="small")
        language_options = list(SUPPORTED_LANGUAGES)
        language_value = preferences.get("language", "english")
        language_index = language_options.index(language_value) if language_value in language_options else 0
        time_management_options = list(SUPPORTED_TIME_MANAGEMENT_METHODS)
        time_management_value = preferences.get("time-mgmt", "Pomodoro")
        time_management_index = (
            time_management_options.index(time_management_value)
            if time_management_value in time_management_options
            else 0
        )
        first_day_options = list(VALID_FIRST_DAY_OF_WEEK_VALUES)
        first_day_value = preferences.get("first_day_of_week", "SU")
        first_day_index = first_day_options.index(first_day_value) if first_day_value in first_day_options else 0
        with general_left:
            language = st.selectbox(
                "Language",
                options=language_options,
                index=language_index,
            )
            notifications = st.checkbox(
                "Enable notifications",
                value=bool(preferences.get("notifications", True)),
                help="Allow the app to show supported reminders and status messages.",
            )
        with general_middle:
            time_management = st.selectbox(
                "Time management method",
                options=time_management_options,
                index=time_management_index,
            )
            enable_minute_chime = st.checkbox(
                "Enable minute chime",
                value=bool(preferences.get("enable_minute_chime", True)),
                help="Play a short chime each elapsed minute during active work timers.",
            )
        with general_right:
            first_day_of_week = st.selectbox(
                "First day of week",
                options=first_day_options,
                index=first_day_index,
                help="Use ISO-style short codes: SU, MO, TU, WE, TH, FR, SA.",
            )
            keep_worthy = st.checkbox(
                "Keep worthy completed tasks",
                value=bool(preferences.get(KEEP_WORTHY_PREFERENCE_KEY, False)),
                help="Preserve completed tasks with comments or actual feedback values during supported delete flows.",
            )
        st.subheader("Planning")
        planning_left, planning_middle = st.columns(2, gap="small")
        with planning_left:
            average_session_time = st.number_input(
                "Average session time",
                min_value=30,
                value=int(preferences.get("average_session_time", 120)),
                step=15,
                help="Average session time, in minutes.",
            )
        with planning_middle:
            # Expose this temporarily so Planner timing can be tuned quickly
            # during development without editing the database by hand.
            planner_minutes = st.number_input(
                "Planner reminder delay",
                min_value=1,
                value=int(preferences.get("planner_minutes", DEFAULT_PLANNER_TIMEOUT_MINUTES)),
                step=1,
                help="Temporary development control for the Planner reminder timer, in minutes.",
            )

        custom_sizes = []
        current_custom_sizes = preferences.get("custom_sizes", [15, 30, 60, 180, 720])
        st.markdown(
            (
                '<span title="Set your personal minute estimates for the five size tiers '
                'used to capture task effort across the application.">'
                'Actual minutes, size tiers &#9432;</span>'
            ),
            unsafe_allow_html=True,
        )
        size_columns = st.columns(5, gap="small")
        for index in range(5):
            default_value = current_custom_sizes[index] if index < len(current_custom_sizes) else 15
            with size_columns[index]:
                custom_sizes.append(
                    st.number_input(
                        f"Size tier {index + 1}",
                        min_value=1,
                        value=int(default_value),
                        step=5,
                        key=f"preferences_size_tier_{index}",
                        label_visibility="collapsed",
                    )
                )

        st.subheader("Working")
        working_left, working_middle, working_right, working_far_right = st.columns(4, gap="small")
        with working_left:
            sprint_time = st.number_input(
                "Sprint duration",
                min_value=1,
                value=int(preferences.get("sprint", 30)),
                step=5,
                help="Sprint duration, in minutes.",
            )
        with working_middle:
            rest_duration = st.number_input(
                "Rest duration",
                min_value=1,
                value=int(
                    preferences.get(
                        "rest_duration",
                        DEFAULT_REST_DURATION_MINUTES,
                    )
                ),
                step=1,
                help="Duration, in minutes, of rest time in Pomodoro sessions and in scenarios where resting is clearly advised.",
            )
        with working_right:
            max_continuous_work_minutes = st.number_input(
                "Focus limit",
                min_value=15,
                value=int(
                    preferences.get(
                        "max_continuous_work_minutes",
                        DEFAULT_MAX_CONTINUOUS_WORK_MINUTES,
                    )
                ),
                step=15,
                help="Focus limit, in minutes. Chunk mode forces a Pomodoro-style rest once this accumulated work time is reached.",
            )
        with working_far_right:
            chunk_min_floor_minutes = st.number_input(
                "Focus floor",
                min_value=1,
                value=int(
                    preferences.get(
                        "chunk_min_floor_minutes",
                        DEFAULT_CHUNK_MIN_FLOOR_MINUTES,
                    )
                ),
                step=1,
                help=(
                    "Focus floor, in minutes. In Chunk mode, the app tries not to "
                    "suggest work blocks that are so short they feel pointless or "
                    "disruptive. If the calculated Chunk cycle would be shorter than "
                    "this floor, the app treats that as a sign that the remaining "
                    "time may no longer be enough for a useful focus block and may "
                    "ask whether you want to extend the planned session."
                ),
            )
        if st.form_submit_button("Save preferences", type="primary"):
            if first_day_of_week not in VALID_FIRST_DAY_OF_WEEK_VALUES:
                st.error("First day of week must be one of: SU, MO, TU, WE, TH, FR, SA.")
                return

            try:
                save_user_profile_updates(
                    preferences_updates={
                        "average_session_time": int(average_session_time),
                        "custom_sizes": [int(value) for value in custom_sizes],
                        "chunk_min_floor_minutes": int(chunk_min_floor_minutes),
                        "max_continuous_work_minutes": int(max_continuous_work_minutes),
                        "rest_duration": int(rest_duration),
                        "sprint": int(sprint_time),
                        "planner_minutes": int(planner_minutes),
                        "language": language,
                        "time-mgmt": time_management,
                        "first_day_of_week": first_day_of_week,
                        "notifications": notifications,
                        "enable_minute_chime": enable_minute_chime,
                        KEEP_WORTHY_PREFERENCE_KEY: keep_worthy,
                    },
                )
                st.session_state["current_page"] = "tasks"
                st.success("Preferences updated successfully.")
                st.rerun()
            except Exception as e:
                handle_api_exception(e, f"Could not update preferences: {e}")


@st.dialog("Welcome")
def welcome_session_dialog():
    user_profile = ensure_user_profile_cache()
    state_options = get_initial_session_state_options()
    state_ids = [item["id"] for item in state_options]
    current_state_id = user_profile.get("state_id")
    state_index = None
    first_name = user_profile.get("first_name")

    if current_state_id in state_ids:
        state_index = state_ids.index(current_state_id)

    st.markdown('<span class="welcome-form-anchor"></span>', unsafe_allow_html=True)
    with st.form("welcome_session_form"):
        registration_welcome_message = st.session_state.get(REGISTRATION_WELCOME_MESSAGE_KEY)
        if registration_welcome_message:
            st.write(registration_welcome_message)
        elif first_name:
            st.write(f"Welcome, {first_name}. Before we start, tell us how you're arriving to this session.")
        else:
            st.write("Welcome. Before we start, tell us how you're arriving to this session.")
        selected_state = st.selectbox(
            "State",
            options=state_options,
            index=state_index,
            placeholder="Select your current state",
            format_func=format_state_option,
            label_visibility="collapsed",
        )
        expected_work_time = st.number_input(
            "Expected work time for the session (minutes)",
            min_value=10,
            value=int(get_effective_session_work_time()),
            step=15,
        )
        start_in_planner = st.checkbox(
            "I want to start by managing tasks first",
            value=False,
        )

        if st.form_submit_button("Save and continue", type="primary"):
            if not selected_state:
                st.error("Select a state before continuing.")
                return

            try:
                declared_state = selected_state["name"]
                start_with_latent_execution_state = (
                    not start_in_planner
                    and declared_state in INITIAL_SESSION_STATE_NAMES
                )
                st.session_state["session_expected_work_time"] = int(expected_work_time)
                dispatch_user_state_event(
                    user_state_machine.LOGIN_DECLARED_EVENT,
                    declared_state=declared_state,
                    start_in_planner=(
                        start_in_planner
                        or start_with_latent_execution_state
                    ),
                )
                if start_with_latent_execution_state:
                    # The declared execution state is remembered immediately,
                    # but the user must stay in Planner until a task is
                    # actually opened through the task FSM.
                    queue_guided_open_for_state(declared_state)
                else:
                    st.session_state.pop(LOGIN_AUTO_OPEN_STATE_KEY, None)
                st.session_state["show_welcome_dialog"] = False
                st.session_state.pop(REGISTRATION_WELCOME_MESSAGE_KEY, None)
                st.success("Session prepared.")
                st.rerun()
            except Exception as e:
                handle_api_exception(e, f"Could not save the welcome setup: {e}")


def render_sidebar():
    """Render the left navigation/sidebar for authenticated users."""

    user_profile = ensure_user_profile_cache()
    persona_name = get_persona_name_by_id(user_profile.get("persona_id")) or "No persona"
    state_name = get_state_name_by_id(user_profile.get("state_id")) or "No state"
    state_description = get_state_description_by_id(user_profile.get("state_id"))
    adaptation, _ = get_current_task_adaptation(get_tasks_dataframe())
    adaptive_priority_label = (
        adaptation.sort_rule.label
        if adaptation and adaptation.sort_rule
        else "default order"
    )
    state_quick_switch_disabled = is_guided_cycle_active_for_open_task()
    state_description_html = (
        f'<div class="sidebar-state-description">{html.escape(state_description)}</div>'
        if state_description
        else ""
    )

    # Group the sidebar controls into clear sections so the quick actions,
    # navigation, and account actions read like one deliberate control panel.
    st.sidebar.markdown(
        f"""
        <div class="sidebar-panel">
            <div class="sidebar-kicker">Session</div>
            <div class="sidebar-profile-name">{html.escape(persona_name)}</div>
            <div class="sidebar-chip-row">
                <span class="sidebar-chip">State: {html.escape(state_name)}</span>
            </div>
            {state_description_html}
            <div class="sidebar-adaptive-priority">
                Adaptive priority: {html.escape(adaptive_priority_label)}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.sidebar.markdown('<div class="sidebar-section-title">Quick State</div>', unsafe_allow_html=True)
    state_switch_left, state_switch_right = st.sidebar.columns(2, gap="small")
    with state_switch_left:
        if st.button(
            "Set Frozen",
            key="sidebar_set_frozen",
            use_container_width=True,
            disabled=state_quick_switch_disabled,
        ):
            # Quick state switches now stage a latent execution mode instead of
            # forcing an immediate user-state transition before any task is
            # actually opened.
            queue_guided_open_for_state("Frozen")
            st.rerun()
    with state_switch_right:
        if st.button(
            "Set Engaged",
            key="sidebar_set_engaged",
            use_container_width=True,
            disabled=state_quick_switch_disabled,
        ):
            # Quick state switches now stage a latent execution mode instead of
            # forcing an immediate user-state transition before any task is
            # actually opened.
            queue_guided_open_for_state("Engaged")
            st.rerun()
    if state_quick_switch_disabled:
        st.sidebar.caption("State quick switches are disabled while a guided task cycle is active.")

    st.sidebar.markdown('<div class="sidebar-section-title">Navigation</div>', unsafe_allow_html=True)
    if st.sidebar.button("My Tasks", use_container_width=True):
        st.session_state["current_page"] = "tasks"
        st.rerun()

    if st.sidebar.button("State Time", use_container_width=True):
        st.session_state["current_page"] = "state_time"
        st.rerun()

    if st.sidebar.button("Task Outcomes", use_container_width=True):
        st.session_state["current_page"] = "task_outcomes"
        st.rerun()

    st.sidebar.markdown('<div class="account-menu-spacer"></div>', unsafe_allow_html=True)
    st.sidebar.markdown("---")
    with st.sidebar.expander("My Account", icon=":material/account_circle:"):
        if st.button("Edit Preferences", key="account_edit_preferences", use_container_width=True):
            st.session_state["current_page"] = "preferences"
            st.rerun()
        if st.button("Log Out", key="account_logout", use_container_width=True):
            logout()


# --- FORMULARIO DE REGISTRO (Pop-up) ---
@st.dialog("Create account")
def registration_form():
    """Render the registration form for new users."""

    st.markdown('<span class="registration-form-anchor"></span>', unsafe_allow_html=True)
    with st.form("signup_form"):
        full_name = st.text_input("Full name") # Dato para tu tabla 'profiles'
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
	    #
        # TO-DO: Meter aqui en el form los controles de los campos del usuario que necesitamos
        born = st.date_input(
            "Birth date",
            value=date(2000, 1, 1),
            min_value=date(1900, 1, 1),
            max_value=date.today(),
        )
        personas = get_personas()
        persona_options = {
            persona["name"]: persona_id for persona_id, persona in personas.items()
        }
        selected_persona_name = st.selectbox(
            "Persona",
            options=list(persona_options.keys()),
            disabled=not bool(persona_options),
        )
        persona_id = persona_options.get(selected_persona_name)

        if persona_id:
            st.caption(personas[persona_id]["self_describing"])
        average_session_time = st.number_input(
            "Expected work time for an average session (minutes)",
            min_value=30,
            value=120,
            step=30,
        )
	    #
        submit_column, cancel_column = st.columns(2, gap="small")
        with submit_column:
            register_clicked = st.form_submit_button(
                "Create account",
                type="primary",
                use_container_width=True,
            )
        with cancel_column:
            cancel_clicked = st.form_submit_button("Cancel", use_container_width=True)

        if cancel_clicked:
            st.rerun()

        if register_clicked:
            try:
                if not persona_id:
                    st.error("Personas could not be loaded. Please check the Supabase connection.")
                    return

                # 1. Crear usuario en Auth
                auth_res = supabase.auth.sign_up({"email": email, "password": password})
                user_id = auth_res.user.id
                
                if user_id:
                    auth_payload = save_auth_cookie(auth_res)
                    if not auth_payload:
                        reset_authenticated_app_state(clear_cookie=True)
                        st.info(
                            "Account created. Check your email to confirm it before signing in."
                        )
                        return

                    # GUARDAR EN SESSION STATE PARA CONSUMO EN TODAS LAS PAGINAS Y FORMULARIOS DE LA APLICACION
                    st.session_state["user_id"] = user_id
                    
                    preferences = {
                        "language": "english",
                        "average_session_time": average_session_time,
                        "custom_sizes": [15, 30, 60, 180, 720],
                        "max_continuous_work_minutes": DEFAULT_MAX_CONTINUOUS_WORK_MINUTES,
                        "sprint": 30,
                        "planner_minutes": DEFAULT_PLANNER_TIMEOUT_MINUTES,
                        "time-mgmt":"Pomodoro",
                        "first_day_of_week": "SU",
                        "notifications": True,
                        "enable_minute_chime": True,
                        KEEP_WORTHY_PREFERENCE_KEY: True,
                    }
                    born_date = to_supabase_date(born)

                    # 2. Insertar en la tabla 'profiles' de tu DDL
                    profile_res = supabase.table("profiles").insert({
                        "id": user_id, 
                        "full_name": full_name,
                        # TO-DO: los siguientes campos de profiles hay que capturarlos del form de registro
                        "born": born_date,
                        "preferences": preferences,
                        "persona_id": persona_id
                    }).execute()

                    refresh_user_profile_cache()
                    first_name = extract_first_name(full_name)
                    age = calculate_age(born)
                    persona_description = personas[persona_id]["description"]
                    with st.spinner("Preparing your welcome..."):
                        st.session_state[REGISTRATION_WELCOME_MESSAGE_KEY] = (
                            generate_registration_welcome_message(
                                first_name=first_name,
                                age=age,
                                persona_description=persona_description,
                            )
                        )
                    st.session_state["session_expected_work_time"] = None
                    st.session_state[RESUMABLE_SESSION_ELAPSED_SECONDS_KEY] = 0
                    st.session_state[CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY] = {}
                    st.session_state["show_welcome_dialog"] = True
                    st.session_state["current_page"] = "tasks"
                    
                    st.success("Registration completed. Check your inbox to finish account confirmation.")
                    st.rerun()
          
            except Exception as e:
                st.error(f"Registration failed: {e}")

# --- FORMULARIO DE LOGIN (Pop-up) ---
@st.dialog("Sign in")
def login_form():
    """Render the login form and bootstrap the authenticated session."""

    st.markdown('<span class="login-form-anchor"></span>', unsafe_allow_html=True)
    with st.form("login_form"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        
        if st.form_submit_button("Sign in"):
            try:
                # Autenticar con Supabase
                res = supabase.auth.sign_in_with_password({
                    "email": email,
                    "password": password
                })
                
                # Guardar el ID en el session_state
                auth_payload = save_auth_cookie(res)
                if not auth_payload:
                    reset_authenticated_app_state(clear_cookie=True)
                    st.error("Login failed: Supabase did not return an active session.")
                    return

                st.session_state["user_id"] = res.user.id
                refresh_user_profile_cache()
                clear_open_task_dialog_state()
                st.session_state[AUTH_RESTORED_FROM_COOKIE_KEY] = False
                st.session_state["current_page"] = "tasks"
                resumed_recent_suspension = apply_recent_suspension_state_after_login()
                if resumed_recent_suspension:
                    st.session_state["show_welcome_dialog"] = False
                else:
                    user_profile = ensure_user_profile_cache()
                    st.session_state["session_expected_work_time"] = None
                    st.session_state[RESUMABLE_SESSION_ELAPSED_SECONDS_KEY] = 0
                    st.session_state[CHUNK_REMAINING_MINUTES_BY_INSTANCE_KEY] = {}
                    st.session_state["show_welcome_dialog"] = (
                        user_profile.get("state_id") is None
                        or is_recovery_state_id(user_profile.get("state_id"))
                    )
                st.success("Welcome back!")
                st.rerun()  # Recargamos para actualizar la interfaz
                
            except Exception as e:
                handle_api_exception(e, f"Login failed: {e}")

# --- LÓGICA DE CIERRE DE SESIÓN ---
def logout(reason="user_logout", force=False):
    """Close the session, optionally showing the voluntary-logout confirmation first."""

    if not force and reason == "user_logout":
        st.session_state[LOGOUT_CONFIRM_DIALOG_KEY] = True
        st.rerun()
        return

    finalize_session_for_recovery(reason)

    try:
        supabase.auth.sign_out()
    except Exception:
        pass

    reset_authenticated_app_state(clear_cookie=True)
    st.rerun()


if hasattr(st, "fragment"):
    @st.fragment(run_every="1s")
    def render_inactivity_logout_watcher():
        tick_work_timer()
        tick_planner_timer()
        ensure_planner_timer_matches_state()
        body_doubling.move_body_doubling_flow_to_review_if_needed()
        clear_expired_open_task_guidance_message()
        clear_expired_rest_message()
        maybe_schedule_minute_chime()
        render_pending_minute_chime()
        render_pending_engaged_cheer()
        body_doubling.render_body_doubling_session_overlay(get_body_doubling_services())
        render_pomodoro_overlay()
else:
    def render_inactivity_logout_watcher():
        tick_work_timer()
        tick_planner_timer()
        ensure_planner_timer_matches_state()
        body_doubling.move_body_doubling_flow_to_review_if_needed()
        clear_expired_open_task_guidance_message()
        clear_expired_rest_message()
        maybe_schedule_minute_chime()
        render_pending_minute_chime()
        render_pending_engaged_cheer()
        body_doubling.render_body_doubling_session_overlay(get_body_doubling_services())
        render_pomodoro_overlay()


# Auth bootstrap:
# - normal case: restore the Supabase session from the encrypted auth cookie;
# - recoverable timeout case: do not auto-restore, because the user was
#   deliberately sent out of the work session and must explicitly re-enter via
#   the normal Sign in button;
# - expired suspension case: delete auth/suspension cookies before rendering.
if "user_id" not in st.session_state:
    st.session_state["user_id"] = None

restore_auth_session_from_cookie()
if st.session_state.get("user_id"):
    if not ensure_fresh_auth_session():
        st.rerun()


# --- FLUJO PRINCIPAL ---
if st.session_state["user_id"]:
        # ESTO ES LO QUE VE EL USUARIO LOGUEADO
    try:
        handle_overlay_action_query_params()
        render_inactivity_logout_watcher()
        process_pending_open_task_start()
        flow_dialog_rendered = False
        if st.session_state.get(LOGOUT_CONFIRM_DIALOG_KEY):
            logout_confirmation_dialog()
            flow_dialog_rendered = True
        if body_doubling.should_render_body_doubling_session_only():
            render_body_doubling_session_controls()
            st.stop()
        if should_render_pomodoro_session_only():
            render_pomodoro_session_controls()
            st.stop()
        if should_render_pomodoro_session_with_guidance_only():
            render_open_task_guidance_dialog()
            render_pomodoro_session_controls()
            st.stop()
        if not flow_dialog_rendered and st.session_state.get(BODY_DOUBLING_RESULT_DIALOG_KEY):
            body_doubling.render_body_doubling_result_dialog(get_body_doubling_services())
            flow_dialog_rendered = True
        elif not flow_dialog_rendered and st.session_state.get(body_doubling.BODY_DOUBLING_EXTRA_STEP_DIALOG_KEY):
            body_doubling.render_body_doubling_extra_step_dialog(get_body_doubling_services())
            flow_dialog_rendered = True
        elif not flow_dialog_rendered and st.session_state.get(body_doubling.BODY_DOUBLING_REVIEW_DIALOG_KEY):
            body_doubling.render_body_doubling_review_dialog(get_body_doubling_services())
            flow_dialog_rendered = True
        elif not flow_dialog_rendered and st.session_state.get(body_doubling.BODY_DOUBLING_SCOPE_DIALOG_KEY):
            body_doubling.render_body_doubling_scope_dialog(get_body_doubling_services())
            flow_dialog_rendered = True
        elif not flow_dialog_rendered and st.session_state.get(SPRINT_REVIEW_PENDING_KEY):
            render_sprint_review_dialog()
            flow_dialog_rendered = True
        elif not flow_dialog_rendered and st.session_state.get(CHUNK_REVIEW_PENDING_KEY):
            render_chunk_review_dialog()
            flow_dialog_rendered = True
        elif not flow_dialog_rendered and st.session_state.get(REST_RESUME_PROMPT_PENDING_KEY):
            render_rest_resume_prompt_dialog()
            flow_dialog_rendered = True
        elif not flow_dialog_rendered and st.session_state.get(REST_MESSAGE_EXPIRES_AT_KEY) is not None:
            render_rest_message_dialog()
            flow_dialog_rendered = True
        elif not flow_dialog_rendered and st.session_state.get(OPEN_TASK_GUIDANCE_EXPIRES_AT_KEY) is not None:
            render_open_task_guidance_dialog()
            flow_dialog_rendered = True
        if not flow_dialog_rendered and st.session_state.get(TASK_COMPLETION_FEEDBACK_REQUEST_KEY):
            render_task_completion_feedback_dialog()
            flow_dialog_rendered = True
        render_sidebar()

        if not flow_dialog_rendered and should_prompt_welcome_dialog():
            st.session_state["show_welcome_dialog"] = True
            welcome_session_dialog()

        if st.session_state.get("current_page") == "preferences":
            render_preferences_page()
        elif st.session_state.get("current_page") == "state_time":
            render_state_time_page()
        elif st.session_state.get("current_page") == "task_outcomes":
            render_task_outcomes_report_page()
        elif st.session_state.get("current_page") == "task_search":
            render_task_search_page()
        else:
            render_tasks_page()
    except Exception as e:
        handle_api_exception(e, f"Error accessing the app: {e}")

else:
    # ESTO ES LA LANDING PAGE
    st.title("Welcome to AI-ADHD-Companion")
    render_session_summary_message()
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("Create account", type="primary", use_container_width=True):
            registration_form()
    with col2:
        if st.button("Sign in", use_container_width=True):
            suspension_payload = get_valid_work_session_suspension_payload()
            if suspension_payload:
                if resume_suspended_work_session():
                    st.rerun()
                login_form()
            else:
                login_form()
