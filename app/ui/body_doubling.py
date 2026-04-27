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


BODY_DOUBLING_FLOW_KEY = "body_doubling_flow"
BODY_DOUBLING_SCOPE_DIALOG_KEY = "body_doubling_scope_dialog"
BODY_DOUBLING_REVIEW_DIALOG_KEY = "body_doubling_review_dialog"
BODY_DOUBLING_EXTRA_STEP_DIALOG_KEY = "body_doubling_extra_step_dialog"
WSUB_LEVEL1 = 6
BODY_DOUBLING_ZONE3_SECONDS = 15
BODY_DOUBLING_ZONE2_SECONDS = 30
BODY_DOUBLING_RETRY_OPTIONS = ("Retry", "Skip", "Finish")


@dataclass(frozen=True)
class BodyDoublingServices:
    get_user_preferences: Callable[[], dict[str, Any]]
    update_task_status: Callable[[dict[str, Any], str], None]
    log_openai_event: Callable[..., None]
    get_openai_logger: Callable[[], Any]
    extract_openai_text: Callable[[Any], str | None]
    openai_class: Any
    openai_model: str


def extract_json_block(text):
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


def get_body_doubling_scope_label(task_row):
    size_weight = task_row.get("size_weight") or task_row.get("size_id") or 0
    friction_weight = task_row.get("friction_weight") or task_row.get("friction_id") or 0

    if size_weight and float(size_weight) > 2:
        return "smaller"
    if friction_weight and float(friction_weight) > 2:
        return "feasible"
    return "smaller"


def get_fallback_body_doubling_microsteps(task_row, services: BodyDoublingServices):
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
    task_minutes = get_task_duration_minutes(task_row, services)
    return (
        "You are helping a user start a task using body-doubling.\n"
        "Return valid JSON only. No markdown. No explanation outside the JSON.\n"
        "Create a short list of microsteps that make the task easier to start, lighter, and slightly more fun.\n"
        "Use this JSON shape exactly:\n"
        '{"microsteps":[{"order":1,"name":"...","description":"...","estimated_duration_minutes":5}]}\n\n'
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


def generate_body_doubling_microsteps(task_row, services: BodyDoublingServices):
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
    target_label = flow.get("current_target_name") or flow["task"]["title"]
    return (
        f"For this micro-session, just focus on {target_label}. "
        "Keep it light, start untidily if needed, and let a tiny bit of progress be enough."
    )


def get_fallback_body_doubling_final_message(flow):
    task_title = flow["task"].get("title", "the task")
    return (
        f"Strong finish. You stayed with {task_title} one step at a time and got it over the line. "
        "That kind of steady follow-through counts."
    )


def build_body_doubling_push_prompt(flow):
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
    st.session_state.pop(BODY_DOUBLING_FLOW_KEY, None)
    st.session_state.pop(BODY_DOUBLING_SCOPE_DIALOG_KEY, None)
    st.session_state.pop(BODY_DOUBLING_REVIEW_DIALOG_KEY, None)
    st.session_state.pop(BODY_DOUBLING_EXTRA_STEP_DIALOG_KEY, None)


def get_body_doubling_flow():
    return st.session_state.get(BODY_DOUBLING_FLOW_KEY)


def set_body_doubling_flow(flow):
    st.session_state[BODY_DOUBLING_FLOW_KEY] = flow


def get_current_body_doubling_target(flow, services: BodyDoublingServices):
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
    flow = {
        "task": task_row,
        "phase": "setup",
        "micro_session_goal": "",
        "session_duration_minutes": None,
        "session_started_at": None,
        "session_ends_at": None,
        "session_message": None,
        "custom_microstep_descriptions": [],
        "pending_terminal_action": None,
    }

    wsub_value = task_row.get("WSUB")
    try:
        wsub_value = float(wsub_value)
    except (TypeError, ValueError):
        wsub_value = 0

    if wsub_value > WSUB_LEVEL1:
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
    sprint_minutes = int(services.get_user_preferences().get("sprint", 30))
    options = []
    if flow.get("uses_microsteps"):
        options.append("Skip")
    options.extend(["5 min", "10 min", "15 min", f"{sprint_minutes} min (Sprint)"])
    return options


def parse_body_doubling_duration_choice(choice):
    if choice == "Skip":
        return None

    if not choice:
        return None

    if "min" in choice:
        return int(choice.split(" ")[0])

    return None


def append_custom_body_doubling_microstep(flow, description):
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
    flow["phase"] = "extra_step_check"
    flow["pending_terminal_action"] = pending_terminal_action
    flow["extra_step_source_reason"] = source_reason
    set_body_doubling_flow(flow)
    st.session_state.pop(BODY_DOUBLING_SCOPE_DIALOG_KEY, None)
    st.session_state.pop(BODY_DOUBLING_REVIEW_DIALOG_KEY, None)
    st.session_state[BODY_DOUBLING_EXTRA_STEP_DIALOG_KEY] = True


def finalise_body_doubling_after_extra_step_decision(flow, services: BodyDoublingServices):
    pending_terminal_action = flow.get("pending_terminal_action")
    if pending_terminal_action == "completed":
        services.update_task_status(flow["task"], "completed")
        st.success(generate_body_doubling_final_message(flow, services))
    else:
        services.update_task_status(flow["task"], "asleep")
        st.info("No self-punishment. The task has been moved to asleep for now.")

    clear_body_doubling_flow()
    st.rerun()


def advance_body_doubling_microstep(flow, services: BodyDoublingServices):
    microsteps = flow.get("microsteps") or []
    current_index = flow.get("current_microstep_index", 0)
    if current_index + 1 >= len(microsteps):
        open_body_doubling_extra_step_dialog(
            flow,
            pending_terminal_action="asleep",
            source_reason="skip_last_microstep",
        )
        st.rerun()
        return

    flow["current_microstep_index"] = current_index + 1
    prepare_body_doubling_setup(flow, services)
    st.rerun()


def start_body_doubling_micro_session(flow, duration_minutes, services: BodyDoublingServices):
    current_target = get_current_body_doubling_target(flow, services)
    started_at = datetime.now(pytz.UTC).timestamp()
    flow["phase"] = "session"
    flow["session_duration_minutes"] = int(duration_minutes)
    flow["session_started_at"] = started_at
    flow["session_ends_at"] = started_at + (int(duration_minutes) * 60)
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


def move_body_doubling_flow_to_review_if_needed():
    flow = get_body_doubling_flow()
    if not flow or flow.get("phase") != "session":
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
    flow = get_body_doubling_flow()
    if not flow:
        return

    if flow.get("uses_microsteps"):
        microsteps = flow.get("microsteps") or []
        current_index = flow.get("current_microstep_index", 0)
        current_target = get_current_body_doubling_target(flow, services)
        st.write(flow["scope_message"])
        st.info(
            f"Micro-step {current_index + 1} of {len(microsteps)}: "
            f"**{current_target['name']}**"
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
        )
        flow["micro_session_goal"] = goal_value.strip()
        set_body_doubling_flow(flow)
        st.write("Choose the length of the next micro-session.")

    duration_choice = st.selectbox(
        "Duration",
        options=get_body_doubling_duration_options(services, flow),
        index=None,
        placeholder="Choose a duration",
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
    if st.session_state.get(BODY_DOUBLING_SCOPE_DIALOG_KEY):
        body_doubling_scope_dialog(services)


@st.dialog("Body-Doubling next step")
def body_doubling_extra_step_dialog(services: BodyDoublingServices):
    flow = get_body_doubling_flow()
    if not flow:
        return

    st.write("Do you think there is another micro-step you still need, which the app has not included?")
    add_extra_choice = st.selectbox(
        "Additional micro-step needed?",
        options=["No", "Yes"],
        index=None,
        placeholder="Choose yes or no",
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
    if st.session_state.get(BODY_DOUBLING_EXTRA_STEP_DIALOG_KEY):
        body_doubling_extra_step_dialog(services)


def render_body_doubling_session_overlay(services: BodyDoublingServices):
    flow = get_body_doubling_flow()
    if not flow or flow.get("phase") != "session":
        return

    now_timestamp = datetime.now(pytz.UTC).timestamp()
    remaining_seconds = max(0, int(flow["session_ends_at"] - now_timestamp))
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
    total_seconds = max(1, int((flow.get("session_duration_minutes") or 1) * 60))
    progress_percentage = min(100, max(0, int((elapsed_seconds / total_seconds) * 100)))
    minutes = remaining_seconds // 60
    seconds = remaining_seconds % 60
    formatted_remaining = f"{minutes:02d}:{seconds:02d}"

    target_title = flow.get("current_target_name") or flow["task"]["title"]
    target_description = flow.get("current_target_description") or flow["task"].get("description") or ""
    task_duration_minutes = get_task_duration_minutes(flow["task"], services)
    current_target_minutes = flow.get("current_target_estimated_minutes") or task_duration_minutes
    safe_target_title = html.escape(str(target_title))
    safe_target_description = html.escape(str(target_description))
    safe_session_message = html.escape(str(flow.get("session_message") or ""))
    zone2_html = (
        f"<h2>{safe_target_title}</h2>"
        f"<p>{safe_target_description}</p>"
        f"<p><strong>Micro-step estimated duration:</strong> {current_target_minutes} min</p>"
        f"<p><strong>Total task size:</strong> {task_duration_minutes} min</p>"
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
        padding: 2rem;
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
        max-width: 1100px;
        margin: 0 auto;
        display: grid;
        grid-template-rows: auto 1fr;
        gap: 1.25rem;
    }}
    .body-doubling-header {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 1rem;
    }}
    .body-doubling-badge {{
        display: inline-flex;
        align-items: center;
        gap: 0.5rem;
        padding: 0.55rem 0.9rem;
        border-radius: 999px;
        background: rgba(16, 36, 58, 0.1);
        border: 1px solid rgba(16, 36, 58, 0.12);
        font-size: 0.9rem;
        font-weight: 700;
        letter-spacing: 0.04em;
        text-transform: uppercase;
    }}
    .body-doubling-progress {{
        flex: 1;
        height: 12px;
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
        background: rgba(248, 251, 255, 0.78);
        border: 1px solid rgba(255,255,255,0.55);
        box-shadow: 0 24px 80px rgba(31, 54, 78, 0.18);
        backdrop-filter: blur(4px);
        -webkit-backdrop-filter: blur(4px);
        border-radius: 28px;
        display: grid;
        grid-template-rows: 33% 33% auto;
        overflow: hidden;
    }}
    .body-doubling-zone {{
        display: flex;
        align-items: center;
        justify-content: center;
        text-align: center;
        padding: 1.4rem 2rem;
    }}
    .body-doubling-zone + .body-doubling-zone {{
        border-top: 1px solid rgba(16, 36, 58, 0.08);
    }}
    .body-doubling-zone h1 {{
        font-size: clamp(4rem, 10vw, 7rem);
        margin: 0;
        line-height: 1;
        letter-spacing: -0.06em;
        font-variant-numeric: tabular-nums;
    }}
    .body-doubling-zone h3 {{
        margin: 0 0 0.6rem 0;
        font-size: 0.95rem;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: rgba(16, 36, 58, 0.65);
    }}
    .body-doubling-zone h2 {{
        font-size: clamp(1.6rem, 3.6vw, 2.5rem);
        margin: 0 0 0.8rem 0;
        line-height: 1.1;
    }}
    .body-doubling-zone p {{
        font-size: 1.08rem;
        margin: 0.3rem 0;
        line-height: 1.45;
    }}
    .body-doubling-faded {{
        color: rgba(16, 36, 58, 0.35);
    }}
    .body-doubling-faded h2,
    .body-doubling-faded p,
    .body-doubling-faded strong {{
        color: rgba(16, 36, 58, 0.35);
    }}
    .body-doubling-metadata {{
        display: inline-flex;
        flex-wrap: wrap;
        justify-content: center;
        gap: 0.6rem;
        margin-top: 0.85rem;
    }}
    .body-doubling-pill {{
        padding: 0.45rem 0.7rem;
        border-radius: 999px;
        background: rgba(16, 36, 58, 0.08);
        border: 1px solid rgba(16, 36, 58, 0.08);
        font-size: 0.95rem;
    }}
    .body-doubling-support {{
        max-width: 720px;
    }}
    .body-doubling-support p {{
        font-size: clamp(1.1rem, 2vw, 1.35rem);
        margin: 0;
    }}
    .body-doubling-hidden {{
        opacity: 0;
        visibility: hidden;
    }}
    @media (max-width: 720px) {{
        .body-doubling-overlay {{
            padding: 1rem;
        }}
        .body-doubling-card {{
            border-radius: 22px;
            grid-template-rows: 30% 35% auto;
        }}
        .body-doubling-zone {{
            padding: 1rem 1.1rem;
        }}
        .body-doubling-header {{
            flex-direction: column;
            align-items: stretch;
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
                    <div>
                        <h3>Micro-session timer</h3>
                        <h1>{formatted_remaining}</h1>
                    </div>
                </div>
                <div class="body-doubling-zone">
                    <div class="{'' if not hide_target else 'body-doubling-faded'}">
                        {zone2_html}
                        <div class="body-doubling-metadata">
                            <span class="body-doubling-pill">Session elapsed: {elapsed_seconds}s</span>
                            <span class="body-doubling-pill">Remaining: {remaining_seconds}s</span>
                        </div>
                    </div>
                </div>
                <div class="body-doubling-zone">
                    <div class="body-doubling-support {'' if not hide_message else 'body-doubling-hidden'}">
                        {zone3_html}
                    </div>
                </div>
            </div>
        </div>
    </div>
    """
    st.markdown(overlay_html, unsafe_allow_html=True)


@st.dialog("Body-Doubling review")
def body_doubling_review_dialog(services: BodyDoublingServices):
    flow = get_body_doubling_flow()
    if not flow:
        return

    target_label = flow.get("current_target_name") or flow["task"]["title"]
    st.write(f"Micro-session finished for **{target_label}**.")

    outcome_choice = st.selectbox(
        "How did it go?",
        options=["Completed", "Partial or no progress"],
        index=None,
        placeholder="Choose an outcome",
    )

    retry_choice = None
    if outcome_choice == "Partial or no progress":
        retry_options = ["Retry", "Finish"]
        if flow.get("uses_microsteps"):
            retry_options.insert(1, "Skip")
        retry_choice = st.selectbox(
            "What do you want to do next?",
            options=retry_options,
            index=None,
            placeholder="Choose the next step",
        )

    if st.button("OK", type="primary", use_container_width=True):
        if outcome_choice is None:
            st.error("Choose how the micro-session went before continuing.")
            return

        if outcome_choice == "Completed":
            if flow.get("uses_microsteps"):
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

            services.update_task_status(flow["task"], "completed")
            st.success("Excellent. The task has been marked as completed.")
            clear_body_doubling_flow()
            st.rerun()
            return

        if retry_choice is None:
            st.error("Choose whether to retry or finish.")
            return

        if retry_choice == "Retry":
            prepare_body_doubling_setup(flow, services)
            st.info("Let's try another micro-session.")
            st.rerun()
            return

        if retry_choice == "Skip":
            advance_body_doubling_microstep(flow, services)
            return

        services.update_task_status(flow["task"], "asleep")
        st.info("No self-punishment. The task has been moved to asleep for now.")
        clear_body_doubling_flow()
        st.rerun()


def render_body_doubling_review_dialog(services: BodyDoublingServices):
    if st.session_state.get(BODY_DOUBLING_REVIEW_DIALOG_KEY):
        body_doubling_review_dialog(services)


def should_render_body_doubling_session_only():
    flow = get_body_doubling_flow()
    return bool(flow and flow.get("phase") == "session")
