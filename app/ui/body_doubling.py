"""Body-Doubling flow helpers, dialogs, and overlay rendering."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
import html
import json
import logging
import os

import pandas as pd
import pytz
import streamlit as st


# Session keys that drive the different body-doubling phases and dialogs.
BODY_DOUBLING_FLOW_KEY = "body_doubling_flow"
BODY_DOUBLING_SCOPE_DIALOG_KEY = "body_doubling_scope_dialog"
BODY_DOUBLING_REVIEW_DIALOG_KEY = "body_doubling_review_dialog"
BODY_DOUBLING_EXTRA_STEP_DIALOG_KEY = "body_doubling_extra_step_dialog"
BODY_DOUBLING_RESULT_DIALOG_KEY = "body_doubling_result_dialog"
BODY_DOUBLING_RESULT_NOTICE_KEY = "body_doubling_result_notice"
# Preference key used to suppress the final body-doubling result message.
BODY_DOUBLING_HIDE_RESULT_NOTICE_PREFERENCE_KEY = "hide_body_doubling_result_notice"
# Thresholds that decide when to fade overlay zones.
BODY_DOUBLING_ZONE3_SECONDS = 15
BODY_DOUBLING_ZONE2_SECONDS = 30
# Standard retry choices shown in the review step.
BODY_DOUBLING_RETRY_OPTIONS = ("Retry", "Skip", "Finish")


@dataclass(frozen=True)
class BodyDoublingServices:
    """Runtime dependencies injected from the main Streamlit module."""

    get_user_preferences: Callable[[], dict[str, Any]]
    update_task_status: Callable[[dict[str, Any], str], None]
    log_openai_event: Callable[..., None]
    get_openai_logger: Callable[[], Any]
    extract_openai_text: Callable[[Any], str | None]
    openai_class: Any
    openai_model: str
    schedule_work_timer: Callable[[int, Callable[..., None], str], None]
    disable_work_timer: Callable[[], None]
    get_work_timer_snapshot: Callable[[], Any]
    get_effective_current_state_name: Callable[[], str | None] | None = None
    get_current_persona_profile_context: Callable[[], dict[str, Any]] | None = None
    get_persona_decompose_threshold: Callable[[], int | None] | None = None
    clear_task_completion_feedback_request: Callable[[], None] | None = None
    save_user_preferences: Callable[[dict[str, Any]], Any] | None = None
    notify_microstep_completed: Callable[[], None] | None = None
    notify_work_ended: Callable[[], None] | None = None
    request_task_completion_feedback: Callable[..., None] | None = None


def extract_json_block(text):
    """Parse the first usable JSON object from a model response."""

    if not text:
        return None

    cleaned_text = text.strip()
    if cleaned_text.startswith("```"):
        cleaned_text = cleaned_text.strip("`")
        if cleaned_text.startswith("json"):
            cleaned_text = cleaned_text[4:].strip()

    try:
        return json.loads(cleaned_text)
    except json.JSONDecodeError:
        pass

    start_index = cleaned_text.find("{")
    end_index = cleaned_text.rfind("}")
    if start_index == -1 or end_index == -1 or end_index <= start_index:
        return None

    try:
        return json.loads(cleaned_text[start_index : end_index + 1])
    except json.JSONDecodeError:
        return None


def get_task_duration_minutes(task_row, services: BodyDoublingServices):
    """Resolve the task duration in minutes using row data or user preferences."""

    size_minutes = task_row.get("size_minutes")
    if pd.notna(size_minutes):
        try:
            return int(size_minutes)
        except (TypeError, ValueError):
            pass

    user_preferences = services.get_user_preferences()
    custom_sizes = user_preferences.get("custom_sizes", [15, 30, 60, 180, 720])
    size_id = task_row.get("size_id")
    if size_id and 0 < int(size_id) <= len(custom_sizes):
        return int(custom_sizes[int(size_id) - 1])

    return int(user_preferences.get("average_session_time", 30))


def format_elapsed_session_time(total_seconds):
    """Format elapsed session time compactly for the Body-Doubling overlay.

    The overlay starts with raw seconds because very short micro-sessions are
    easiest to scan that way. Once the counter reaches one minute we switch to
    `M:SS`, and once it reaches one hour we switch again to `H:MM:SS`.
    """

    total_seconds = max(0, int(total_seconds or 0))
    if total_seconds < 60:
        return f"{total_seconds}s"

    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def get_body_doubling_scope_label(task_row):
    """Pick the wording used when asking the model to shrink the task scope."""

    size_weight = task_row.get("size_weight") or task_row.get("size_id") or 0
    friction_weight = task_row.get("friction_weight") or task_row.get("friction_id") or 0

    if size_weight and float(size_weight) > 2:
        return "smaller"
    if friction_weight and float(friction_weight) > 2:
        return "feasible"
    return "smaller"


def get_fallback_body_doubling_microsteps(task_row, services: BodyDoublingServices):
    """Return deterministic microsteps when OpenAI is unavailable."""

    task_title = task_row.get("title", "the task")
    task_minutes = get_task_duration_minutes(task_row, services)
    fallback_minutes = max(5, min(15, task_minutes // 3 or 5))
    return [
        {
            "order": 1,
            "name": "Set up",
            "description": f"Open what you need for {task_title} and remove one obvious point of friction.",
            "estimated_duration_minutes": fallback_minutes,
        },
        {
            "order": 2,
            "name": "Tiny first move",
            "description": f"Do the smallest meaningful action that counts as real progress on {task_title}.",
            "estimated_duration_minutes": fallback_minutes,
        },
        {
            "order": 3,
            "name": "Wrap the micro-win",
            "description": "Leave a clean next step so it is easier to continue later.",
            "estimated_duration_minutes": fallback_minutes,
        },
    ]


def normalise_body_doubling_microsteps(payload):
    """Normalise AI-generated microsteps into the app's canonical structure."""

    if not isinstance(payload, dict):
        return []

    raw_steps = payload.get("microsteps") or payload.get("steps") or []
    if not isinstance(raw_steps, list):
        return []

    normalised_steps = []
    for index, step in enumerate(raw_steps, start=1):
        if not isinstance(step, dict):
            continue

        name = (step.get("name") or step.get("title") or f"Micro-step {index}").strip()
        description = (
            step.get("description")
            or step.get("details")
            or step.get("extended_description")
            or "Keep it light and concrete."
        )
        estimated_duration = step.get("estimated_duration_minutes") or step.get("duration_minutes") or 5

        try:
            estimated_duration = int(float(estimated_duration))
        except (TypeError, ValueError):
            estimated_duration = 5

        normalised_steps.append(
            {
                "order": int(step.get("order") or index),
                "name": name[:80],
                "description": str(description).strip()[:400],
                "estimated_duration_minutes": max(1, estimated_duration),
            }
        )

    return sorted(normalised_steps, key=lambda item: item["order"])


def build_body_doubling_microsteps_prompt(task_row, scope_label, services: BodyDoublingServices):
    """Build the OpenAI prompt used to request body-doubling microsteps."""

    task_minutes = get_task_duration_minutes(task_row, services)
    profile_context = (
        services.get_current_persona_profile_context()
        if callable(services.get_current_persona_profile_context)
        else {}
    )
    current_state_name = (
        services.get_effective_current_state_name()
        if callable(services.get_effective_current_state_name)
        else None
    )
    return (
        "You are helping a user start a task using body-doubling.\n"
        "Return valid JSON only. No markdown. No explanation outside the JSON.\n"
        "Create a short list of microsteps that make the task easier to start, lighter, and slightly more fun.\n"
        "Take your time to think before answering so the microsteps are genuinely helpful and not silly, patronising, or trivial.\n"
        "Use this JSON shape exactly:\n"
        '{"microsteps":[{"order":1,"name":"...","description":"...","estimated_duration_minutes":5}]}\n\n'
        "User context:\n"
        f"- Persona/profile: {profile_context.get('persona_name') or 'not provided'}\n"
        f"- Persona/profile description: {profile_context.get('persona_description') or 'not provided'}\n"
        f"- Age: {profile_context.get('age') if profile_context.get('age') is not None else 'not provided'}\n"
        f"- Current state: {current_state_name or 'not provided'}\n\n"
        "Task context:\n"
        f"- Title: {task_row.get('title') or 'Untitled'}\n"
        f"- Description: {task_row.get('description') or 'No description provided'}\n"
        f"- WSUB: {task_row.get('WSUB')}\n"
        f"- Size weight: {task_row.get('size_weight')}\n"
        f"- Friction weight: {task_row.get('friction_weight')}\n"
        f"- Estimated duration in minutes: {task_minutes}\n"
        f"- Scope intent: make this {scope_label}\n"
        "Keep between 2 and 5 microsteps. Each one should feel concrete, low-pressure, and immediately actionable."
    )


def should_decompose_body_doubling_task(task_row, services: BodyDoublingServices):
    """Return whether the current persona wants OpenAI microstep decomposition.

    The decomposition threshold belongs to the persona, not just the task.
    Different user archetypes need different amounts of scaffolding. A null
    threshold disables decomposition for the persona entirely.
    """

    if not callable(services.get_persona_decompose_threshold):
        return False

    threshold = services.get_persona_decompose_threshold()
    if threshold is None:
        return False

    try:
        wsub_value = float(task_row.get("WSUB"))
    except (TypeError, ValueError):
        wsub_value = 0

    return wsub_value >= float(threshold)


def generate_body_doubling_microsteps(task_row, services: BodyDoublingServices):
    """Generate microsteps with OpenAI and fall back safely when needed."""

    task_title = task_row.get("title", "Untitled")
    scope_label = get_body_doubling_scope_label(task_row)
    api_key = os.environ.get("OPENAI_API_KEY")

    if services.openai_class is None or not api_key:
        services.log_openai_event(
            logging.WARNING,
            "Using fallback body-doubling microsteps.",
            model=services.openai_model,
            task_title=task_title,
            scope_label=scope_label,
            reason="missing_openai_or_api_key",
        )
        return get_fallback_body_doubling_microsteps(task_row, services)

    try:
        services.log_openai_event(
            logging.INFO,
            "Requesting body-doubling microsteps from OpenAI.",
            model=services.openai_model,
            task_title=task_title,
            scope_label=scope_label,
            wsub=task_row.get("WSUB"),
        )
        client = services.openai_class(api_key=api_key)
        response = client.responses.create(
            model=services.openai_model,
            input=build_body_doubling_microsteps_prompt(task_row, scope_label, services),
            max_output_tokens=500,
        )
        payload = extract_json_block(services.extract_openai_text(response))
        microsteps = normalise_body_doubling_microsteps(payload)
        if microsteps:
            return microsteps

        services.log_openai_event(
            logging.ERROR,
            "OpenAI body-doubling microsteps were not parseable; using fallback.",
            model=services.openai_model,
            task_title=task_title,
            response_type=type(response).__name__,
        )
        return get_fallback_body_doubling_microsteps(task_row, services)
    except Exception as error:
        services.get_openai_logger().exception(
            "OpenAI body-doubling microstep generation failed; using fallback. context=%s",
            json.dumps(
                {
                    "model": services.openai_model,
                    "task_title": task_title,
                    "scope_label": scope_label,
                    "error": repr(error),
                },
                ensure_ascii=False,
            ),
        )
        return get_fallback_body_doubling_microsteps(task_row, services)


def get_fallback_body_doubling_push_message(flow):
    """Return a deterministic mid-session encouragement message."""

    target_label = flow.get("current_target_name") or flow["task"]["title"]
    return (
        f"For this micro-session, just focus on {target_label}. "
        "Keep it light, start untidily if needed, and let a tiny bit of progress be enough."
    )


def get_fallback_body_doubling_final_message(flow):
    """Return a deterministic completion message for the final dialog."""

    task_title = flow["task"].get("title", "the task")
    return (
        f"Strong finish. You stayed with {task_title} one step at a time and got it over the line. "
        "That kind of steady follow-through counts."
    )


def build_body_doubling_push_prompt(flow):
    """Build the OpenAI prompt for the in-session encouragement message."""

    task = flow["task"]
    return (
        "Write one short British English message for the middle of a body-doubling micro-session.\n"
        "Tone: warm, practical, low-pressure, gently encouraging.\n"
        "No lists. No emojis. Keep it under 45 words.\n\n"
        "Context:\n"
        f"- Task title: {task.get('title')}\n"
        f"- Task description: {task.get('description') or 'No description provided'}\n"
        f"- Current target: {flow.get('current_target_name')}\n"
        f"- Current target description: {flow.get('current_target_description')}\n"
        f"- Session duration minutes: {flow.get('session_duration_minutes')}\n"
    )


def generate_body_doubling_push_message(flow, services: BodyDoublingServices):
    """Generate a short mid-session encouragement message."""

    api_key = os.environ.get("OPENAI_API_KEY")
    task_title = flow["task"].get("title", "Untitled")

    if services.openai_class is None or not api_key:
        services.log_openai_event(
            logging.WARNING,
            "Using fallback body-doubling push message.",
            model=services.openai_model,
            task_title=task_title,
        )
        return get_fallback_body_doubling_push_message(flow)

    try:
        client = services.openai_class(api_key=api_key)
        response = client.responses.create(
            model=services.openai_model,
            input=build_body_doubling_push_prompt(flow),
            max_output_tokens=80,
        )
        message = services.extract_openai_text(response)
        if message:
            return message
    except Exception as error:
        services.get_openai_logger().exception(
            "OpenAI body-doubling push message generation failed; using fallback. context=%s",
            json.dumps(
                {
                    "model": services.openai_model,
                    "task_title": task_title,
                    "error": repr(error),
                },
                ensure_ascii=False,
            ),
        )

    return get_fallback_body_doubling_push_message(flow)


def build_body_doubling_final_prompt(flow):
    """Build the OpenAI prompt for the final celebratory message."""

    microsteps = flow.get("microsteps") or []
    listed_microsteps = "\n".join(
        f"- {step.get('name')}: {step.get('description')}"
        for step in microsteps
    ) or "- No microsteps recorded"
    extra_steps = "\n".join(
        f"- {step}"
        for step in flow.get("custom_microstep_descriptions", [])
    ) or "- None"

    return (
        "Write one short celebratory message in British English for the successful end of a body-doubling session.\n"
        "Tone: warm, encouraging, a bit more enthusiastic than usual, but still grounded.\n"
        "No lists. No emojis. Keep it under 60 words.\n\n"
        "Context:\n"
        f"- Task title: {flow['task'].get('title')}\n"
        f"- Task description: {flow['task'].get('description') or 'No description provided'}\n"
        f"- Planned microsteps:\n{listed_microsteps}\n"
        f"- Extra user-added microsteps:\n{extra_steps}\n"
    )


def generate_body_doubling_final_message(flow, services: BodyDoublingServices):
    """Generate the final success message shown after a completed flow."""

    api_key = os.environ.get("OPENAI_API_KEY")
    task_title = flow["task"].get("title", "Untitled")

    if services.openai_class is None or not api_key:
        services.log_openai_event(
            logging.WARNING,
            "Using fallback body-doubling final message.",
            model=services.openai_model,
            task_title=task_title,
        )
        return get_fallback_body_doubling_final_message(flow)

    try:
        client = services.openai_class(api_key=api_key)
        response = client.responses.create(
            model=services.openai_model,
            input=build_body_doubling_final_prompt(flow),
            max_output_tokens=100,
        )
        message = services.extract_openai_text(response)
        if message:
            return message
    except Exception as error:
        services.get_openai_logger().exception(
            "OpenAI body-doubling final message generation failed; using fallback. context=%s",
            json.dumps(
                {
                    "model": services.openai_model,
                    "task_title": task_title,
                    "error": repr(error),
                },
                ensure_ascii=False,
            ),
        )

    return get_fallback_body_doubling_final_message(flow)


def clear_body_doubling_flow():
    """Clear all body-doubling flow and dialog state from the session."""

    st.session_state.pop(BODY_DOUBLING_FLOW_KEY, None)
    st.session_state.pop(BODY_DOUBLING_SCOPE_DIALOG_KEY, None)
    st.session_state.pop(BODY_DOUBLING_REVIEW_DIALOG_KEY, None)
    st.session_state.pop(BODY_DOUBLING_EXTRA_STEP_DIALOG_KEY, None)


def get_body_doubling_flow():
    """Return the current body-doubling flow payload from session state."""

    return st.session_state.get(BODY_DOUBLING_FLOW_KEY)


def set_body_doubling_flow(flow):
    """Persist the body-doubling flow payload into session state."""

    st.session_state[BODY_DOUBLING_FLOW_KEY] = flow


def get_current_body_doubling_target(flow, services: BodyDoublingServices):
    """Return the active microstep or whole-task target for the current flow."""

    if flow.get("uses_microsteps"):
        microsteps = flow.get("microsteps") or []
        current_index = flow.get("current_microstep_index", 0)
        if 0 <= current_index < len(microsteps):
            return microsteps[current_index]
        return None

    return {
        "order": 1,
        "name": flow.get("micro_session_goal") or flow["task"]["title"],
        "description": flow["task"].get("description") or "Stay with this task for the length of the micro-session.",
        "estimated_duration_minutes": flow.get("session_duration_minutes") or get_task_duration_minutes(flow["task"], services),
    }


def prepare_body_doubling_setup(flow, services: BodyDoublingServices):
    """Populate setup fields for the next micro-session and open the setup dialog."""

    current_target = get_current_body_doubling_target(flow, services)
    flow["phase"] = "setup"
    flow["current_target_name"] = current_target["name"] if current_target else flow["task"]["title"]
    flow["current_target_description"] = current_target["description"] if current_target else flow["task"].get("description")
    flow["current_target_estimated_minutes"] = (
        current_target["estimated_duration_minutes"]
        if current_target
        else get_task_duration_minutes(flow["task"], services)
    )
    set_body_doubling_flow(flow)
    st.session_state[BODY_DOUBLING_SCOPE_DIALOG_KEY] = True
    st.session_state.pop(BODY_DOUBLING_REVIEW_DIALOG_KEY, None)
    st.session_state.pop(BODY_DOUBLING_EXTRA_STEP_DIALOG_KEY, None)


def start_body_doubling_flow(task_row, services: BodyDoublingServices):
    """Initialise a brand-new body-doubling flow for one task."""

    uses_pomodoro_timer = bool(task_row.get("use_pomodoro_sprints"))
    flow = {
        "task": task_row,
        "phase": "setup",
        "micro_session_goal": "",
        "session_duration_minutes": None,
        "session_started_at": None,
        "session_ends_at": None,
        "session_message": None,
        "custom_microstep_descriptions": [],
        "microstep_outcomes": [],
        "pending_terminal_action": None,
        "uses_pomodoro_timer": uses_pomodoro_timer,
        "timer_source": "work_timer" if uses_pomodoro_timer else "body_doubling",
    }
    services.disable_work_timer()

    if should_decompose_body_doubling_task(task_row, services):
        flow["uses_microsteps"] = True
        flow["microsteps"] = generate_body_doubling_microsteps(task_row, services)
        flow["current_microstep_index"] = 0
        flow["scope_message"] = f"Let's make this {get_body_doubling_scope_label(task_row)}."
    else:
        flow["uses_microsteps"] = False
        flow["microsteps"] = []
        flow["current_microstep_index"] = None
        flow["scope_message"] = "What exactly do you want to achieve in this micro-session?"

    prepare_body_doubling_setup(flow, services)


def get_body_doubling_duration_options(services: BodyDoublingServices, flow):
    """Return the duration choices available for the current flow state."""

    sprint_minutes = int(services.get_user_preferences().get("sprint", 30))
    options = []
    if flow.get("uses_microsteps"):
        options.append("Skip")
    if flow.get("uses_pomodoro_timer"):
        options.append(f"{sprint_minutes} min (Sprint)")
        return options

    options.extend(["5 min", "10 min", "15 min", f"{sprint_minutes} min (Sprint)"])
    return options


def parse_body_doubling_duration_choice(choice):
    """Parse a UI duration label into minutes, or None for skip."""

    if choice == "Skip":
        return None

    if not choice:
        return None

    if "min" in choice:
        return int(choice.split(" ")[0])

    return None


def append_custom_body_doubling_microstep(flow, description):
    """Append a user-authored microstep to the active flow."""

    cleaned_description = description.strip()
    if not cleaned_description:
        return False

    microsteps = flow.get("microsteps") or []
    next_order = len(microsteps) + 1
    estimated_duration = max(5, int(flow.get("session_duration_minutes") or 5))
    microsteps.append(
        {
            "order": next_order,
            "name": f"Extra micro-step {next_order}",
            "description": cleaned_description,
            "estimated_duration_minutes": estimated_duration,
            "is_user_added": True,
        }
    )
    flow["microsteps"] = microsteps
    flow["uses_microsteps"] = True
    flow["current_microstep_index"] = len(microsteps) - 1
    flow.setdefault("custom_microstep_descriptions", []).append(cleaned_description)
    flow["pending_terminal_action"] = None
    return True


def open_body_doubling_extra_step_dialog(flow, pending_terminal_action, source_reason):
    """Pause the flow and ask whether another microstep should be added."""

    flow["phase"] = "extra_step_check"
    flow["pending_terminal_action"] = pending_terminal_action
    flow["extra_step_source_reason"] = source_reason
    set_body_doubling_flow(flow)
    st.session_state.pop(BODY_DOUBLING_SCOPE_DIALOG_KEY, None)
    st.session_state.pop(BODY_DOUBLING_REVIEW_DIALOG_KEY, None)
    st.session_state[BODY_DOUBLING_EXTRA_STEP_DIALOG_KEY] = True


def record_body_doubling_microstep_outcome(flow, outcome):
    """Record the outcome of the current microstep in the flow state."""

    if not flow.get("uses_microsteps"):
        return

    microsteps = flow.get("microsteps") or []
    current_index = flow.get("current_microstep_index", 0)
    if current_index is None or current_index < 0 or current_index >= len(microsteps):
        return

    outcomes = list(flow.get("microstep_outcomes") or [])
    while len(outcomes) <= current_index:
        outcomes.append(None)

    outcomes[current_index] = {
        "index": current_index,
        "name": microsteps[current_index].get("name") or f"Micro-step {current_index + 1}",
        "outcome": outcome,
    }
    flow["microstep_outcomes"] = outcomes


def get_last_non_skipped_microstep_outcome(flow):
    """Return the last recorded microstep outcome that was not skipped."""

    outcomes = flow.get("microstep_outcomes") or []
    for item in reversed(outcomes):
        if not item:
            continue
        outcome = item.get("outcome")
        if outcome and outcome != "skipped":
            return item
    return None


def get_body_doubling_microstep_label(step, fallback_name=None):
    """Return the most informative label for one planned or recorded microstep."""

    if step and step.get("is_user_added"):
        description = str(step.get("description") or "").strip()
        if description:
            preview_length = 60
            if len(description) > preview_length:
                return f"{description[:preview_length].rstrip()}..."
            return description

    if step:
        return step.get("name") or fallback_name
    return fallback_name


def get_recorded_microstep_outcome(flow, index=None):
    """Read one recorded microstep outcome by index or current microstep."""

    outcomes = flow.get("microstep_outcomes") or []
    target_index = flow.get("current_microstep_index", 0) if index is None else index
    if target_index is None or target_index < 0 or target_index >= len(outcomes):
        return None
    recorded = outcomes[target_index]
    if not recorded:
        return None
    return recorded.get("outcome")


def resolve_body_doubling_final_status(flow):
    """Decide the final task status from the recorded microstep outcomes."""

    if not flow.get("uses_microsteps"):
        pending_terminal_action = flow.get("pending_terminal_action")
        return "completed" if pending_terminal_action == "completed" else "asleep"

    last_non_skipped = get_last_non_skipped_microstep_outcome(flow)
    if last_non_skipped and last_non_skipped.get("outcome") == "completed":
        return "completed"
    return "open"


def get_body_doubling_result_notice(flow, final_status):
    """Build the final user-facing notice shown after body-doubling ends."""

    task_title = flow.get("task", {}).get("title") or "the task"
    last_non_skipped = get_last_non_skipped_microstep_outcome(flow)
    step_name = None
    if last_non_skipped:
        microsteps = flow.get("microsteps") or []
        step_index = last_non_skipped.get("index")
        step = None
        if isinstance(step_index, int) and 0 <= step_index < len(microsteps):
            step = microsteps[step_index]
        step_name = get_body_doubling_microstep_label(step, last_non_skipped.get("name"))

    if final_status == "completed":
        if step_name:
            return (
                f"The last micro-step that was not skipped was **{step_name}**, and it was completed. "
                f"The task **{task_title}** has been marked as **completed**."
            )
        return f"The task **{task_title}** has been marked as **completed**."

    if step_name:
        return (
            f"The last micro-step that was not skipped was **{step_name}**, and it was not completed. "
            f"The task **{task_title}** will stay **open** so you can come back to it without sending it to asleep."
        )

    return (
        f"All remaining micro-steps were skipped, so the task **{task_title}** will stay **open** for now."
    )


def maybe_open_body_doubling_result_dialog(
    flow,
    services: BodyDoublingServices,
    final_status,
    *,
    notify_work_ended_on_close: bool = False,
):
    """Queue the final result dialog unless the user opted out of it."""

    if services.get_user_preferences().get(BODY_DOUBLING_HIDE_RESULT_NOTICE_PREFERENCE_KEY, False):
        return False

    result_notice = {
        "final_status": final_status,
        "message": get_body_doubling_result_notice(flow, final_status),
        "notify_work_ended_on_close": bool(notify_work_ended_on_close),
    }
    if final_status == "completed":
        result_notice["final_message"] = generate_body_doubling_final_message(flow, services)

    st.session_state[BODY_DOUBLING_RESULT_NOTICE_KEY] = result_notice
    st.session_state[BODY_DOUBLING_RESULT_DIALOG_KEY] = True
    return True


def finalise_body_doubling_after_extra_step_decision(flow, services: BodyDoublingServices):
    """Apply the pending terminal action after the extra-step prompt closes."""

    final_status = resolve_body_doubling_final_status(flow)
    if final_status == "completed" and callable(services.request_task_completion_feedback):
        st.session_state.pop(BODY_DOUBLING_EXTRA_STEP_DIALOG_KEY, None)
        st.session_state.pop(BODY_DOUBLING_REVIEW_DIALOG_KEY, None)
        services.request_task_completion_feedback(
            flow["task"],
            "body_doubling_final_completed",
            flow_snapshot=flow,
        )
        st.rerun()
        return

    services.update_task_status(flow["task"], final_status)
    maybe_open_body_doubling_result_dialog(
        flow,
        services,
        final_status,
        notify_work_ended_on_close=(final_status != "completed"),
    )
    clear_body_doubling_flow()
    st.rerun()


def advance_body_doubling_microstep(flow, services: BodyDoublingServices):
    """Move the flow to the next microstep or finish when none remain."""

    if get_recorded_microstep_outcome(flow) is None:
        record_body_doubling_microstep_outcome(flow, "skipped")
    microsteps = flow.get("microsteps") or []
    current_index = flow.get("current_microstep_index", 0)
    if current_index + 1 >= len(microsteps):
        open_body_doubling_extra_step_dialog(
            flow,
            pending_terminal_action="open",
            source_reason="skip_last_microstep",
        )
        st.rerun()
        return

    flow["current_microstep_index"] = current_index + 1
    prepare_body_doubling_setup(flow, services)
    st.rerun()


def start_body_doubling_micro_session(flow, duration_minutes, services: BodyDoublingServices):
    """Start the timed body-doubling session for the current target."""

    current_target = get_current_body_doubling_target(flow, services)
    started_at = datetime.now(pytz.UTC).timestamp()
    timer_source = "work_timer" if flow.get("uses_pomodoro_timer") else "body_doubling"

    if flow.get("uses_pomodoro_timer"):
        services.schedule_work_timer(
            int(duration_minutes),
            expire_body_doubling_pomodoro_session,
            "body_doubling_pomodoro_session",
        )
        timer_snapshot = services.get_work_timer_snapshot()
        expires_at = timer_snapshot.expires_at
        if timer_snapshot.duration_seconds is not None and expires_at is not None:
            started_at = float(expires_at) - float(timer_snapshot.duration_seconds)
        else:
            expires_at = started_at + (int(duration_minutes) * 60)
    else:
        expires_at = started_at + (int(duration_minutes) * 60)

    flow["phase"] = "session"
    flow["session_duration_minutes"] = int(duration_minutes)
    flow["session_started_at"] = started_at
    flow["session_ends_at"] = expires_at
    flow["timer_source"] = timer_source
    flow["current_target_name"] = current_target["name"] if current_target else flow["task"]["title"]
    flow["current_target_description"] = current_target["description"] if current_target else flow["task"].get("description")
    flow["current_target_estimated_minutes"] = (
        current_target["estimated_duration_minutes"]
        if current_target
        else get_task_duration_minutes(flow["task"], services)
    )
    flow["session_message"] = generate_body_doubling_push_message(flow, services)
    set_body_doubling_flow(flow)
    st.session_state.pop(BODY_DOUBLING_SCOPE_DIALOG_KEY, None)
    st.session_state.pop(BODY_DOUBLING_REVIEW_DIALOG_KEY, None)
    st.rerun()


def expire_body_doubling_pomodoro_session(timer=None):
    """Move a Pomodoro-backed body-doubling session into the review phase."""

    flow = get_body_doubling_flow()
    if not flow or flow.get("phase") != "session":
        return

    flow["phase"] = "review"
    set_body_doubling_flow(flow)
    st.session_state[BODY_DOUBLING_REVIEW_DIALOG_KEY] = True
    st.rerun()


def end_body_doubling_micro_session():
    """Interrupt the active micro-session and move to the normal review step.

    This mirrors the destination reached by the countdown expiry. The caller is
    responsible for stopping any external timer, such as the shared Pomodoro
    work timer, before invoking this helper.
    """

    flow = get_body_doubling_flow()
    if not flow or flow.get("phase") != "session":
        return

    flow["phase"] = "review"
    set_body_doubling_flow(flow)
    st.session_state[BODY_DOUBLING_REVIEW_DIALOG_KEY] = True
    st.rerun()


def move_body_doubling_flow_to_review_if_needed():
    """Advance non-Pomodoro body-doubling sessions when their timer expires."""

    flow = get_body_doubling_flow()
    if not flow or flow.get("phase") != "session":
        return

    if flow.get("timer_source") == "work_timer":
        return

    session_ends_at = flow.get("session_ends_at")
    if session_ends_at is None:
        return

    if datetime.now(pytz.UTC).timestamp() >= float(session_ends_at):
        flow["phase"] = "review"
        set_body_doubling_flow(flow)
        st.session_state[BODY_DOUBLING_REVIEW_DIALOG_KEY] = True
        st.rerun()


@st.dialog("Body-Doubling setup")
def body_doubling_scope_dialog(services: BodyDoublingServices):
    """Render the setup dialog that defines the next micro-session."""

    flow = get_body_doubling_flow()
    if not flow:
        return

    task_title = html.escape(str(flow.get("task", {}).get("title") or "Untitled task"))
    st.markdown(
        f'<div class="body-doubling-setup-task-title">{task_title}</div>',
        unsafe_allow_html=True,
    )

    if flow.get("uses_pomodoro_timer"):
        st.info("Pomodoro is managing the countdown for this session. Body-Doubling is only guiding the task and review flow.")

    if flow.get("uses_microsteps"):
        microsteps = flow.get("microsteps") or []
        current_index = flow.get("current_microstep_index", 0)
        current_target = get_current_body_doubling_target(flow, services)
        current_target_label = get_body_doubling_microstep_label(current_target)
        st.write(flow["scope_message"])
        st.info(
            f"Micro-step {current_index + 1} of {len(microsteps)}: "
            f"**{current_target_label}**"
        )
        st.write(current_target["description"])
        st.caption(
            f"Estimated duration: {current_target['estimated_duration_minutes']} minutes."
        )
        st.write("Choose the length of the next micro-session.")
    else:
        st.write(flow["scope_message"])
        default_goal = flow.get("micro_session_goal", "")
        goal_value = st.text_input(
            "Micro-session goal",
            value=default_goal,
            placeholder="For example: draft the first paragraph or open the tax form",
            label_visibility="collapsed",
        )
        flow["micro_session_goal"] = goal_value.strip()
        set_body_doubling_flow(flow)
        st.write("Choose the length of the next micro-session.")

    duration_choice = st.selectbox(
        "Duration",
        options=get_body_doubling_duration_options(services, flow),
        index=None,
        placeholder="Choose a duration",
        label_visibility="collapsed",
        key=f"body_doubling_duration_{flow['task']['instance_id']}_{flow.get('current_microstep_index', 'task')}",
    )

    start_column, cancel_column = st.columns(2)
    with start_column:
        if st.button("Start micro-session", type="primary", use_container_width=True):
            if duration_choice is None:
                st.error("Choose a duration before starting.")
                return

            if not flow.get("uses_microsteps") and not flow.get("micro_session_goal"):
                st.error("Tell the app what you want to achieve in this micro-session.")
                return

            if duration_choice == "Skip":
                advance_body_doubling_microstep(flow, services)
                return

            duration_minutes = parse_body_doubling_duration_choice(duration_choice)
            start_body_doubling_micro_session(flow, duration_minutes, services)

    with cancel_column:
        if st.button("Cancel", use_container_width=True):
            clear_body_doubling_flow()
            st.rerun()


def render_body_doubling_scope_dialog(services: BodyDoublingServices):
    """Show the setup dialog when the corresponding session flag is enabled."""

    if st.session_state.get(BODY_DOUBLING_SCOPE_DIALOG_KEY):
        body_doubling_scope_dialog(services)


@st.dialog("Body-Doubling next step")
def body_doubling_extra_step_dialog(services: BodyDoublingServices):
    """Render the dialog that asks whether another microstep is needed."""

    flow = get_body_doubling_flow()
    if not flow:
        return

    safe_task_title = html.escape(str(flow.get("task", {}).get("title") or "Untitled"))
    st.markdown(
        f'<div class="open-task-title">{safe_task_title}</div>',
        unsafe_allow_html=True,
    )
    st.write("Do you think there is another micro-step you still need, which the app has not included?")
    add_extra_choice = st.selectbox(
        "Additional micro-step needed?",
        options=["No", "Yes"],
        index=0,
    )

    extra_step_description = ""
    if add_extra_choice == "Yes":
        extra_step_description = st.text_area(
            "Describe the extra micro-step",
            placeholder="Describe what you want to achieve in the new micro-step",
        ).strip()

    if st.button("OK", type="primary", use_container_width=True):
        if add_extra_choice is None:
            st.error("Choose whether another micro-step is needed.")
            return

        if add_extra_choice == "Yes":
            if not extra_step_description:
                st.error("Describe the extra micro-step before continuing.")
                return

            append_custom_body_doubling_microstep(flow, extra_step_description)
            prepare_body_doubling_setup(flow, services)
            st.rerun()
            return

        finalise_body_doubling_after_extra_step_decision(flow, services)


def render_body_doubling_extra_step_dialog(services: BodyDoublingServices):
    """Show the extra-step dialog when its session flag is enabled."""

    if st.session_state.get(BODY_DOUBLING_EXTRA_STEP_DIALOG_KEY):
        body_doubling_extra_step_dialog(services)


def render_body_doubling_session_overlay(services: BodyDoublingServices):
    """Render the full-screen overlay used during an active body-doubling session."""

    flow = get_body_doubling_flow()
    if not flow or flow.get("phase") != "session":
        return

    now_timestamp = datetime.now(pytz.UTC).timestamp()
    if flow.get("timer_source") == "work_timer":
        timer_snapshot = services.get_work_timer_snapshot()
        if not timer_snapshot.running or timer_snapshot.expires_at is None:
            return
        remaining_seconds = max(0, int(timer_snapshot.expires_at - now_timestamp))
        total_seconds = max(1, int(timer_snapshot.duration_seconds or (flow.get("session_duration_minutes") or 1) * 60))
    else:
        remaining_seconds = max(0, int(flow["session_ends_at"] - now_timestamp))
        total_seconds = max(1, int((flow.get("session_duration_minutes") or 1) * 60))
    hide_message = (
        flow.get("session_started_at") is not None
        and now_timestamp - float(flow["session_started_at"]) >= BODY_DOUBLING_ZONE3_SECONDS
    )
    hide_target = (
        flow.get("session_started_at") is not None
        and now_timestamp - float(flow["session_started_at"]) >= BODY_DOUBLING_ZONE2_SECONDS
    )

    elapsed_seconds = max(
        0,
        int(now_timestamp - float(flow.get("session_started_at") or now_timestamp))
    )
    formatted_elapsed = format_elapsed_session_time(elapsed_seconds)
    progress_percentage = min(100, max(0, int((elapsed_seconds / total_seconds) * 100)))
    minutes = remaining_seconds // 60
    seconds = remaining_seconds % 60
    formatted_remaining = f"{minutes:02d}:{seconds:02d}"

    task_title = flow["task"].get("title") or "Untitled task"
    current_target = get_current_body_doubling_target(flow, services)
    target_title = get_body_doubling_microstep_label(
        current_target,
        flow.get("current_target_name") or flow["task"]["title"],
    )
    target_description = flow.get("current_target_description") or flow["task"].get("description") or ""
    task_duration_minutes = get_task_duration_minutes(flow["task"], services)
    safe_task_title = html.escape(str(task_title))
    safe_target_title = html.escape(str(target_title))
    safe_target_description = html.escape(str(target_description))
    safe_session_message = html.escape(str(flow.get("session_message") or ""))
    zone2_html = (
        f"<div class=\"body-doubling-target-task-title\">{safe_task_title}</div>"
        f"<div class=\"body-doubling-target-microstep-title\">{safe_target_title}</div>"
        f"<div class=\"body-doubling-target-microstep-description\">{safe_target_description}</div>"
    )

    zone3_html = ""
    if not hide_message:
        zone3_html = f"<p>{safe_session_message}</p>"

    overlay_html = f"""
    <style>
    .body-doubling-overlay {{
        position: fixed;
        inset: 0;
        z-index: 99999;
        padding: 5.75rem 1.25rem 1.25rem 1.25rem;
        color: #10243a;
        isolation: isolate;
    }}
    .body-doubling-backdrop {{
        position: absolute;
        inset: 0;
        z-index: 0;
        background: linear-gradient(180deg, #c8d8ec 0%, #b0c4de 45%, #9db6d2 100%);
        opacity: 1;
    }}
    .body-doubling-shell {{
        position: relative;
        z-index: 1;
        height: 100%;
        max-height: calc(100vh - 7rem);
        max-width: 980px;
        margin: 0 auto;
        display: grid;
        grid-template-rows: auto 1fr;
        gap: 0.7rem;
        padding-top: 0;
    }}
    .body-doubling-header {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 1rem;
        min-height: 2.5rem;
    }}
    .body-doubling-badge {{
        display: inline-flex;
        align-items: center;
        gap: 0.5rem;
        padding: 0.48rem 0.8rem;
        border-radius: 999px;
        background: rgba(16, 36, 58, 0.1);
        border: 1px solid rgba(16, 36, 58, 0.12);
        font-size: 0.82rem;
        font-weight: 700;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        color: #10243a;
        box-shadow: 0 8px 24px rgba(31, 54, 78, 0.08);
    }}
    .body-doubling-progress {{
        flex: 1;
        height: 10px;
        border-radius: 999px;
        overflow: hidden;
        background: rgba(16, 36, 58, 0.12);
        border: 1px solid rgba(16, 36, 58, 0.1);
    }}
    .body-doubling-progress-fill {{
        height: 100%;
        width: {progress_percentage}%;
        background: linear-gradient(90deg, #2f6ea5 0%, #4f88bc 100%);
        border-radius: 999px;
    }}
    .body-doubling-card {{
        min-height: 0;
        max-height: 100%;
        background: rgba(248, 251, 255, 0.78);
        border: 1px solid rgba(255,255,255,0.55);
        box-shadow: 0 24px 80px rgba(31, 54, 78, 0.18);
        backdrop-filter: blur(4px);
        -webkit-backdrop-filter: blur(4px);
        border-radius: 28px;
        display: grid;
        grid-template-rows: minmax(136px, 0.72fr) minmax(156px, 0.88fr) minmax(104px, 0.52fr) minmax(82px, 0.3fr);
        overflow: hidden;
    }}
    .body-doubling-zone {{
        display: flex;
        align-items: center;
        justify-content: center;
        text-align: center;
        padding: 0.8rem 1.35rem;
    }}
    .body-doubling-zone + .body-doubling-zone {{
        border-top: 1px solid rgba(16, 36, 58, 0.08);
    }}
    .body-doubling-zone h1 {{
        font-size: clamp(4.4rem, 10vw, 7.2rem);
        margin: 0;
        line-height: 0.95;
        letter-spacing: -0.08em;
        font-variant-numeric: tabular-nums;
        font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
    }}
    .body-doubling-zone h3 {{
        margin: 0 0 0.6rem 0;
        font-size: 0.95rem;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: rgba(16, 36, 58, 0.65);
    }}
    .body-doubling-zone p {{
        font-size: 1.08rem;
        margin: 0.3rem 0;
        line-height: 1.45;
    }}
    .body-doubling-zone-timer-content,
    .body-doubling-zone-target-content,
    .body-doubling-zone-action-content {{
        width: min(100%, 900px);
    }}
    .body-doubling-zone-timer-content {{
        display: flex;
        align-items: center;
        justify-content: center;
        min-height: 100%;
    }}
    .body-doubling-zone-target-content {{
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 0.55rem;
    }}
    .body-doubling-target-task-title {{
        font-size: clamp(1.3rem, 2.3vw, 1.8rem);
        font-weight: 400;
        line-height: 1.2;
        max-width: 860px;
        overflow-wrap: anywhere;
    }}
    .body-doubling-target-microstep-title {{
        font-size: clamp(1.3rem, 2.3vw, 1.8rem);
        font-weight: 700;
        line-height: 1.2;
        max-width: 860px;
        overflow-wrap: anywhere;
    }}
    .body-doubling-target-microstep-description {{
        font-size: clamp(0.95rem, 1.35vw, 1.08rem);
        font-weight: 600;
        line-height: 1.4;
        max-width: 860px;
        overflow-wrap: anywhere;
    }}
    .body-doubling-faded {{
        color: rgba(16, 36, 58, 0.35);
    }}
    .body-doubling-faded p,
    .body-doubling-faded strong,
    .body-doubling-faded .body-doubling-target-task-title,
    .body-doubling-faded .body-doubling-target-microstep-title,
    .body-doubling-faded .body-doubling-target-microstep-description {{
        color: rgba(16, 36, 58, 0.35);
    }}
    .body-doubling-metadata {{
        display: inline-flex;
        flex-wrap: wrap;
        justify-content: center;
        gap: 0.6rem;
        margin-top: 0.55rem;
    }}
    .body-doubling-pill {{
        padding: 0.34rem 0.56rem;
        border-radius: 999px;
        background: rgba(16, 36, 58, 0.08);
        border: 1px solid rgba(16, 36, 58, 0.08);
        font-size: 0.8rem;
        white-space: nowrap;
    }}
    .body-doubling-support {{
        max-width: 720px;
    }}
    .body-doubling-support p {{
        font-size: clamp(0.92rem, 1.45vw, 1.05rem);
        margin: 0;
    }}
    .body-doubling-action-slot {{
        width: min(100%, 560px);
        min-height: 3.7rem;
        border-radius: 18px;
        border: 1px solid transparent;
        background: transparent;
        box-shadow: none;
    }}
    .body-doubling-hidden {{
        opacity: 0;
        visibility: hidden;
    }}
    @media (max-width: 720px) {{
        .body-doubling-overlay {{
            padding: 4.8rem 0.7rem 0.7rem 0.7rem;
        }}
        .body-doubling-card {{
            border-radius: 22px;
            grid-template-rows: minmax(126px, 0.64fr) minmax(176px, 0.92fr) minmax(108px, 0.54fr) minmax(78px, 0.28fr);
        }}
        .body-doubling-zone {{
            padding: 0.7rem 0.85rem;
        }}
        .body-doubling-header {{
            flex-direction: column;
            align-items: stretch;
        }}
        .body-doubling-target-task-title,
        .body-doubling-target-microstep-title {{
            font-size: clamp(1.1rem, 4.6vw, 1.45rem);
        }}
        .body-doubling-target-microstep-description {{
            font-size: 0.9rem;
        }}
    }}
    </style>
    <div class="body-doubling-overlay">
        <div class="body-doubling-backdrop"></div>
        <div class="body-doubling-shell">
            <div class="body-doubling-header">
                <div class="body-doubling-badge">Body-Doubling in progress</div>
                <div class="body-doubling-progress">
                    <div class="body-doubling-progress-fill"></div>
                </div>
            </div>
            <div class="body-doubling-card">
                <div class="body-doubling-zone">
                    <div class="body-doubling-zone-timer-content">
                        <h1>{formatted_remaining}</h1>
                    </div>
                </div>
                <div class="body-doubling-zone">
                    <div class="body-doubling-zone-target-content {'' if not hide_target else 'body-doubling-faded'}">
                        {zone2_html}
                        <div class="body-doubling-metadata">
                            <span class="body-doubling-pill">Est. task duration: {task_duration_minutes} min</span>
                            <span class="body-doubling-pill">Session elapsed: {formatted_elapsed}</span>
                            <span class="body-doubling-pill">Timer source: {'Pomodoro' if flow.get('timer_source') == 'work_timer' else 'Micro-session'}</span>
                        </div>
                    </div>
                </div>
                <div class="body-doubling-zone">
                    <div class="body-doubling-support {'' if not hide_message else 'body-doubling-hidden'}">
                        {zone3_html}
                    </div>
                </div>
                <div class="body-doubling-zone">
                    <div class="body-doubling-zone-action-content">
                        <div class="body-doubling-action-slot"></div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    """
    st.markdown(overlay_html, unsafe_allow_html=True)


@st.dialog("Body-Doubling review")
def body_doubling_review_dialog(services: BodyDoublingServices):
    """Render the post-session review dialog for body-doubling."""

    flow = get_body_doubling_flow()
    if not flow:
        return

    target_label = flow.get("current_target_name") or flow["task"]["title"]
    st.write(f"Micro-session finished for **{target_label}**.")
    uses_microsteps = bool(flow.get("uses_microsteps"))
    action_columns = st.columns(4 if uses_microsteps else 3, gap="small")

    try:
        with action_columns[0]:
            primary_completion_label = (
                "Micro-step complete" if uses_microsteps else "Task complete"
            )
            if st.button(
                primary_completion_label,
                key="body_doubling_review_task_complete",
                type="primary",
                use_container_width=True,
            ):
                if uses_microsteps:
                    record_body_doubling_microstep_outcome(flow, "completed")
                    if callable(services.notify_microstep_completed):
                        services.notify_microstep_completed()
                    microsteps = flow.get("microsteps") or []
                    current_index = flow.get("current_microstep_index", 0)
                    is_last_microstep = current_index >= len(microsteps) - 1
                    if is_last_microstep:
                        open_body_doubling_extra_step_dialog(
                            flow,
                            pending_terminal_action="completed",
                            source_reason="completed_last_microstep",
                        )
                        st.rerun()
                        return

                    flow["current_microstep_index"] = current_index + 1
                    prepare_body_doubling_setup(flow, services)
                    st.success("Nice. On to the next micro-step.")
                    st.rerun()
                    return

                if callable(services.request_task_completion_feedback):
                    st.session_state.pop(BODY_DOUBLING_REVIEW_DIALOG_KEY, None)
                    services.request_task_completion_feedback(
                        flow["task"],
                        "body_doubling_simple_completed",
                    )
                    st.rerun()
                    return

                services.update_task_status(flow["task"], "completed")
                st.success("Excellent. The task has been marked as completed.")
                clear_body_doubling_flow()
                st.rerun()
                return

        with action_columns[1]:
            if st.button(
                "New cycle",
                key="body_doubling_review_new_cycle",
                use_container_width=True,
            ):
                if callable(services.clear_task_completion_feedback_request):
                    services.clear_task_completion_feedback_request()
                prepare_body_doubling_setup(flow, services)
                st.info("Let's try another micro-session.")
                st.rerun()
                return

        action_index = 2
        if uses_microsteps:
            with action_columns[action_index]:
                if st.button(
                    "Skip",
                    key="body_doubling_review_skip",
                    use_container_width=True,
                ):
                    if callable(services.clear_task_completion_feedback_request):
                        services.clear_task_completion_feedback_request()
                    record_body_doubling_microstep_outcome(flow, "partial")
                    advance_body_doubling_microstep(flow, services)
                    return
            action_index += 1

        with action_columns[action_index]:
            if st.button(
                "Finish",
                key="body_doubling_review_finish",
                use_container_width=True,
            ):
                if callable(services.clear_task_completion_feedback_request):
                    services.clear_task_completion_feedback_request()

                if uses_microsteps:
                    record_body_doubling_microstep_outcome(flow, "partial")
                    final_status = resolve_body_doubling_final_status(flow)
                    if final_status == "completed" and callable(services.request_task_completion_feedback):
                        st.session_state.pop(BODY_DOUBLING_EXTRA_STEP_DIALOG_KEY, None)
                        st.session_state.pop(BODY_DOUBLING_REVIEW_DIALOG_KEY, None)
                        services.request_task_completion_feedback(
                            flow["task"],
                            "body_doubling_final_completed",
                            flow_snapshot=flow,
                        )
                        st.rerun()
                        return

                    services.update_task_status(flow["task"], final_status)
                    maybe_open_body_doubling_result_dialog(
                        flow,
                        services,
                        final_status,
                        notify_work_ended_on_close=(final_status != "completed"),
                    )
                    clear_body_doubling_flow()
                    st.rerun()
                    return

                services.update_task_status(flow["task"], "asleep")
                st.info("No self-punishment. The task has been moved to asleep for now.")
                clear_body_doubling_flow()
                st.rerun()
                return
    except Exception as error:
        st.error(f"Could not finish Body-Doubling review: {error}")


def render_body_doubling_review_dialog(services: BodyDoublingServices):
    """Show the review dialog when its session flag is enabled."""

    if st.session_state.get(BODY_DOUBLING_REVIEW_DIALOG_KEY):
        body_doubling_review_dialog(services)


@st.dialog("Body-Doubling result")
def body_doubling_result_dialog(services: BodyDoublingServices):
    """Render the final result dialog shown when a body-doubling flow finishes."""

    result_notice = st.session_state.get(BODY_DOUBLING_RESULT_NOTICE_KEY)
    if not result_notice:
        return

    final_status = result_notice.get("final_status")
    if final_status == "completed":
        final_message = result_notice.get("final_message")
        if final_message:
            st.success(final_message)
    st.info(result_notice.get("message") or "Body-Doubling has finished.")

    hide_future_messages = st.checkbox("Do not show this message anymore")

    if st.button("Close", type="primary", use_container_width=True):
        if hide_future_messages and callable(services.save_user_preferences):
            services.save_user_preferences(
                {BODY_DOUBLING_HIDE_RESULT_NOTICE_PREFERENCE_KEY: True}
            )
        elif hide_future_messages:
            preferences = dict(services.get_user_preferences())
            preferences[BODY_DOUBLING_HIDE_RESULT_NOTICE_PREFERENCE_KEY] = True

        if result_notice.get("notify_work_ended_on_close") and callable(services.notify_work_ended):
            services.notify_work_ended()
        st.session_state.pop(BODY_DOUBLING_RESULT_NOTICE_KEY, None)
        st.session_state.pop(BODY_DOUBLING_RESULT_DIALOG_KEY, None)
        st.rerun()


def render_body_doubling_result_dialog(services: BodyDoublingServices):
    """Show the final result dialog when its session flag is enabled."""

    if st.session_state.get(BODY_DOUBLING_RESULT_DIALOG_KEY):
        body_doubling_result_dialog(services)


def should_render_body_doubling_session_only():
    """Return whether the app should render only the active body-doubling overlay."""

    flow = get_body_doubling_flow()
    return bool(flow and flow.get("phase") == "session")
