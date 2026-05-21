"""Central adaptive message inventory and first-phase renderer helpers.

Phase 1 keeps the implementation intentionally lightweight: the catalog owns
message structure, conditional fallback parts, and explicit intensity filters.
OpenAI/TTS/timed modal orchestration can be layered on top later without
spreading more hardcoded copy across Streamlit components.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import streamlit as st


INTENSITY_HIGH = "high"
INTENSITY_MEDIUM = "medium"
INTENSITY_LOW = "low"
ALL_MESSAGE_INTENSITIES = (
    INTENSITY_HIGH,
    INTENSITY_MEDIUM,
    INTENSITY_LOW,
)
INTENSITY_ALIASES = {
    "high": INTENSITY_HIGH,
    "alta": INTENSITY_HIGH,
    "medium": INTENSITY_MEDIUM,
    "media": INTENSITY_MEDIUM,
    "low": INTENSITY_LOW,
    "baja": INTENSITY_LOW,
}
DEFAULT_RENDERER = "info"
VALID_RENDERERS = {
    "info": st.info,
    "warning": st.warning,
    "success": st.success,
    "error": st.error,
    "write": st.write,
    "caption": st.caption,
}


@dataclass(frozen=True)
class MessagePart:
    """One conditional fragment of a fallback message."""

    text: str
    when: str | None = None
    allowed_intensities: tuple[str, ...] = ALL_MESSAGE_INTENSITIES


@dataclass(frozen=True)
class MessageButton:
    """One optional action button that may depend on runtime conditions."""

    label: str
    when: str | None = None


@dataclass(frozen=True)
class MessageDefinition:
    """Static metadata for one adaptive message entry."""

    message_id: str
    use_openai: bool
    fallback_parts: tuple[MessagePart, ...]
    prompt_string: str | None = None
    prompt_file: Path | None = None
    use_tts: bool = False
    buttons: tuple[str, ...] = ()
    button_parts: tuple[MessageButton, ...] = ()
    timer: int = 0


@dataclass(frozen=True)
class MessageDisplayResult:
    """Return what the renderer actually did for the caller."""

    displayed: bool
    text: str
    button_clicked: str | None = None
    timer_seconds: int = 0


@dataclass(frozen=True)
class DeleteDialogCopy:
    """Static text used by delete-confirmation dialog headers."""

    title: str
    target_label: str
    subtitle: str


class _SafeFormatDict(dict):
    """Leave unknown placeholders untouched during fallback formatting."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


DELETE_DIALOG_COPY: dict[str, DeleteDialogCopy] = {
    "simple_task": DeleteDialogCopy(
        title="Delete task",
        target_label="Task",
        subtitle="This will apply the delete policy for the selected task instance.",
    ),
    "compound_task": DeleteDialogCopy(
        title="Delete compound task",
        target_label="Compound task",
        subtitle="Delete policies may affect child tasks, parent summaries, and future recurrence scheduling.",
    ),
    "subtask": DeleteDialogCopy(
        title="Delete subtask",
        target_label="Subtask",
        subtitle="The parent task may be updated to reflect changes in dates and dimensions after this subtask is deleted.",
    ),
}


def get_delete_dialog_copy(copy_key: str) -> DeleteDialogCopy:
    """Return header copy for one delete-dialog type."""

    return DELETE_DIALOG_COPY.get(copy_key, DELETE_DIALOG_COPY["simple_task"])


MESSAGE_CATALOG: dict[str, MessageDefinition] = {
    "S1": MessageDefinition(
        message_id="S1",
        use_openai=True,
        fallback_parts=(
            MessagePart(
                text="You just entered the app to get things done. If you feel frozen, start by opening one small task, complete it and follow on.",
            ),
        ),
        prompt_string="Give me a short version of the following paragraph for an ADHD profile {persona} (age {age}) with {intensity} intensity.",
        prompt_file=None,
        use_tts=False,
        buttons=("Got it!",),
        timer=0,
    ),
    "ADAPTIVE_PROCRASTINATOR_FROZEN_GUIDANCE": MessageDefinition(
        message_id="ADAPTIVE_PROCRASTINATOR_FROZEN_GUIDANCE",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text="The app is narrowing the field to a few low-friction tasks so starting feels lighter.",
                allowed_intensities=ALL_MESSAGE_INTENSITIES,
            ),
        ),
    ),
    "ADAPTIVE_PROCRASTINATOR_ENGAGED_GUIDANCE": MessageDefinition(
        message_id="ADAPTIVE_PROCRASTINATOR_ENGAGED_GUIDANCE",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text="The app is prioritising the most worthwhile tasks while keeping the tone light.",
                allowed_intensities=ALL_MESSAGE_INTENSITIES,
            ),
        ),
    ),
    "ADAPTIVE_PROCRASTINATOR_PLANNER_GUIDANCE": MessageDefinition(
        message_id="ADAPTIVE_PROCRASTINATOR_PLANNER_GUIDANCE",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text="The app is limiting choice while you are in Planner mode so the next step stays concrete.",
                allowed_intensities=ALL_MESSAGE_INTENSITIES,
            ),
        ),
    ),
    "ADAPTIVE_HYPER_FOCUSED_FROZEN_GUIDANCE": MessageDefinition(
        message_id="ADAPTIVE_HYPER_FOCUSED_FROZEN_GUIDANCE",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text="The app is guiding you toward high-value tasks and lowering the threshold to escape indecision faster.",
                allowed_intensities=ALL_MESSAGE_INTENSITIES,
            ),
        ),
    ),
    "ADAPTIVE_HYPER_FOCUSED_ENGAGED_GUIDANCE": MessageDefinition(
        message_id="ADAPTIVE_HYPER_FOCUSED_ENGAGED_GUIDANCE",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text="The app is keeping attention on the highest-value work and protecting breaks so momentum stays sustainable.",
                allowed_intensities=ALL_MESSAGE_INTENSITIES,
            ),
        ),
    ),
    "ADAPTIVE_HYPER_FOCUSED_PLANNER_GUIDANCE": MessageDefinition(
        message_id="ADAPTIVE_HYPER_FOCUSED_PLANNER_GUIDANCE",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text="The app is keeping the task ranking visible and will warn if you move away from the most valuable options.",
                allowed_intensities=ALL_MESSAGE_INTENSITIES,
            ),
        ),
    ),
    "ADAPTIVE_OVERWHELMED_PLANNER_FROZEN_GUIDANCE": MessageDefinition(
        message_id="ADAPTIVE_OVERWHELMED_PLANNER_FROZEN_GUIDANCE",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text="The app is restricting the visible list to reduce planning stress and keep attention on a tiny number of tasks.",
                allowed_intensities=ALL_MESSAGE_INTENSITIES,
            ),
        ),
    ),
    "ADAPTIVE_OVERWHELMED_PLANNER_ENGAGED_GUIDANCE": MessageDefinition(
        message_id="ADAPTIVE_OVERWHELMED_PLANNER_ENGAGED_GUIDANCE",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text="The app is keeping the field narrow so planning does not expand while you already have momentum.",
                allowed_intensities=ALL_MESSAGE_INTENSITIES,
            ),
        ),
    ),
    "ADAPTIVE_OVERWHELMED_PLANNER_PLANNER_GUIDANCE": MessageDefinition(
        message_id="ADAPTIVE_OVERWHELMED_PLANNER_PLANNER_GUIDANCE",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text="The app is strongly narrowing the list in Planner mode to avoid spiralling back into list management.",
                allowed_intensities=ALL_MESSAGE_INTENSITIES,
            ),
        ),
    ),
    "PLANNER_TIMEOUT_PROCRASTINATOR": MessageDefinition(
        message_id="PLANNER_TIMEOUT_PROCRASTINATOR",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text="Completing a task now is usually wiser than staying in list-management mode for longer.",
                allowed_intensities=(INTENSITY_MEDIUM, INTENSITY_HIGH),
            ),
        ),
    ),
    "PLANNER_TIMEOUT_OVERWHELMED_PLANNER": MessageDefinition(
        message_id="PLANNER_TIMEOUT_OVERWHELMED_PLANNER",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text="Completing a task now is usually more helpful than staying in planning mode any longer.",
                allowed_intensities=(INTENSITY_MEDIUM, INTENSITY_HIGH),
            ),
        ),
    ),
    "STATE_ENGAGED_TO_FROZEN": MessageDefinition(
        message_id="STATE_ENGAGED_TO_FROZEN",
        use_openai=False,
        fallback_parts=(
            MessagePart(text="State changed from Engaged to Frozen."),
        ),
    ),
    "STATE_FROZEN_TO_ENGAGED": MessageDefinition(
        message_id="STATE_FROZEN_TO_ENGAGED",
        use_openai=False,
        fallback_parts=(
            MessagePart(text="State changed from Frozen to Engaged."),
        ),
    ),
    "STATE_CHANGED_TO_RECOVERY": MessageDefinition(
        message_id="STATE_CHANGED_TO_RECOVERY",
        use_openai=False,
        fallback_parts=(
            MessagePart(text="State changed to Recovery."),
        ),
    ),
    "STATE_RETURNED_TO_PLANNER_AFTER_REJECTIONS": MessageDefinition(
        message_id="STATE_RETURNED_TO_PLANNER_AFTER_REJECTIONS",
        use_openai=False,
        fallback_parts=(
            MessagePart(text="Returned to Planner after {threshold} consecutive task rejections." 
                        "Don't stay here too long. Leave and enjoy, or open and work out some tasks in your lists."),
        ),
    ),
    "STATE_RETURNED_TO_PLANNER_NO_MORE_AUTO_OPEN_TASKS": MessageDefinition(
        message_id="STATE_RETURNED_TO_PLANNER_NO_MORE_AUTO_OPEN_TASKS",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text="Returned to Planner because there are no more auto-open task candidates in the current view. "
                     "Choose what to work on next from your lists."
            ),
        ),
    ),
    "STATE_RETURNED_TO_PLANNER_AFTER_WORK_ENDED": MessageDefinition(
        message_id="STATE_RETURNED_TO_PLANNER_AFTER_WORK_ENDED",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text="Returned to Planner after finishing the current work cycle. Choose what to work on next from your lists."
            ),
        ),
    ),
    "POMODORO_REST_OVER_RESUME_WORK": MessageDefinition(
        message_id="POMODORO_REST_OVER_RESUME_WORK",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text="Rest is over! Should we go back to work?",
                allowed_intensities=ALL_MESSAGE_INTENSITIES,
            ),
        ),
    ),
    "STATE_FROZEN_TO_ENGAGED_MOMENTUM": MessageDefinition(
        message_id="STATE_FROZEN_TO_ENGAGED_MOMENTUM",
        use_openai=False,
        fallback_parts=(
            MessagePart(text="Nice momentum. You moved from Frozen to Engaged."),
        ),
    ),
    "STATE_FROZEN_TO_ENGAGED_MICROSTEPS": MessageDefinition(
        message_id="STATE_FROZEN_TO_ENGAGED_MICROSTEPS",
        use_openai=False,
        fallback_parts=(
            MessagePart(text="Micro-steps are working. You moved from Frozen to Engaged."),
        ),
    ),
    "STATE_SESSION_CLOSED_RECOVERY": MessageDefinition(
        message_id="STATE_SESSION_CLOSED_RECOVERY",
        use_openai=False,
        fallback_parts=(
            MessagePart(text="Session closed and moved to Recovery."),
        ),
    ),
    "STATE_OPEN_TASK_RECOVERY_CLEANUP": MessageDefinition(
        message_id="STATE_OPEN_TASK_RECOVERY_CLEANUP",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text="The open task was moved to {status} as part of the Recovery transition."),
        ),
    ),
    "PLANNER_REMINDER": MessageDefinition(
        message_id="PLANNER_REMINDER",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text=(
                    "Planner reminder {current} of {limit}. "
                    "You have {minutes_until_recovery} minutes to open any task and work or be sent off! ;-D"
                ),
                allowed_intensities=ALL_MESSAGE_INTENSITIES,
            ),
        ),
    ),
    "PLANNER_LIMIT_REACHED": MessageDefinition(
        message_id="PLANNER_LIMIT_REACHED",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text="Planner time limit reached. Moving session to Recovery.",
                allowed_intensities=ALL_MESSAGE_INTENSITIES,
            ),
        ),
    ),
    "WORK_TIMER_FINISHED_PLANNER": MessageDefinition(
        message_id="WORK_TIMER_FINISHED_PLANNER",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text="Work timer finished. Your state has been changed to Planner.",
                allowed_intensities=ALL_MESSAGE_INTENSITIES,
            ),
        ),
    ),
    "D1": MessageDefinition(
        message_id="D1",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text="You requested to delete a completed task that has useful data for analytics, but your user preferences indicate you wish to keep all such tasks. It will not be deleted unless you change preferences.",
            ),
        ),
        buttons=("Ok",),
    ),
    "D1.1": MessageDefinition(
        message_id="D1.1",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text="You requested to delete a task.",
            ),
        ),
        buttons=("Proceed", "Cancel"),
    ),
    "D1.2": MessageDefinition(
        message_id="D1.2",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text="A subtask will be deleted.",
            ),
            MessagePart(
                text="When deleting a subtask, the parent task will be updated to reflect possible changes in its dates and/or characteristics. Proceed?",
                allowed_intensities=(INTENSITY_HIGH,),
            ),
            MessagePart(
                text="Proceed?",
                allowed_intensities=(INTENSITY_MEDIUM, INTENSITY_LOW),
            ),
        ),
        buttons=("Proceed", "Cancel"),
    ),
    "D2": MessageDefinition(
        message_id="D2",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text="You requested to delete a task that is completed or has completed subtasks that have useful data for analytics, but your user preferences indicate you wish to keep all such tasks. It will not be deleted unless you change preferences.",
            ),
        ),
        buttons=("Ok",),
    ),
    "D2.1": MessageDefinition(
        message_id="D2.1",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text="Delete task and subtasks?",
            ),
        ),
        buttons=("Do it!", "Cancel"),
    ),
    "D3": MessageDefinition(
        message_id="D3",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text="You requested to delete a completed task that has useful data for analytics, but your user preferences indicate you wish to keep all such tasks. You may just want to delete future instances.",
            ),
        ),
        buttons=("Remove recurrency", "Cancel"),
    ),
    "D3.1": MessageDefinition(
        message_id="D3.1",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text="You requested to delete a recurring task that has several instances already in your list.",
            ),
            MessagePart(
                text="The selected one has feedback info and will not be deleted unless you change preferences first.",
                when="warn_worthy",
            ),
            MessagePart(
                text="What would you want to delete?",
            ),
        ),
        button_parts=(
            MessageButton("Selected instance", when="show_selected_instance_button"),
            MessageButton("Selected and future instances", when="show_selected_and_future_button"),
            MessageButton("Future instances", when="show_future_button"),
            MessageButton("All instances", when="show_all_instances_button"),
            MessageButton("Cancel"),
        ),
    ),
    "D4": MessageDefinition(
        message_id="D4",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text="You requested to delete a subtask of a recurring task.",
            ),
            MessagePart(
                text="This subtask is completed and has valuable info about completion. No matter your preferences, it will be deleted so that it doesn’t appear in future instances.",
                when="current_worthy",
            ),
        ),
        buttons=("Go!", "Cancel"),
    ),
    "D4.1": MessageDefinition(
        message_id="D4.1",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text="You requested to delete a subtask of a recurring task that has several instances already in your list.",
            ),
            MessagePart(
                text="The selected subtask has feedback info and will not be deleted unless you change preferences first.",
                when="warn_worthy",
            ),
            MessagePart(
                text="Also, there are newer instances of this subtask there. Deleting only the selection will not prevent it from appearing in future instances of the parent task.",
                when="show_future_explanation",
                allowed_intensities=(INTENSITY_HIGH,),
            ),
            MessagePart(
                text="What would you want to delete?",
            ),
        ),
        button_parts=(
            MessageButton("Selected instance", when="show_selected_instance_button"),
            MessageButton("Selected and future instances", when="show_selected_and_future_button"),
            MessageButton("Future instances", when="show_future_button"),
            MessageButton("All instances", when="show_all_instances_button"),
            MessageButton("Cancel"),
        ),
    ),
    "D5": MessageDefinition(
        message_id="D5",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text="You requested to delete a recurrent completed task with subtasks that have useful data for analytics, but your user preferences indicate you wish to keep all such tasks. You may just want to delete future instances of the group instead.",
            ),
        ),
        buttons=("Remove recurrency", "Cancel"),
    ),
    "D5.1": MessageDefinition(
        message_id="D5.1",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text="You requested to delete a recurring task with subtasks that has several instances already in your list.",
            ),
            MessagePart(
                text="The selected one has feedback info and will not be deleted unless you change preferences first.",
                when="warn_worthy",
            ),
            MessagePart(
                text="What would you want to delete?",
            ),
        ),
        button_parts=(
            MessageButton("Selected instance", when="show_selected_instance_button"),
            MessageButton("Selected and future instances", when="show_selected_and_future_button"),
            MessageButton("Future instances", when="show_future_button"),
            MessageButton("All instances", when="show_all_instances_button"),
            MessageButton("Cancel"),
        ),
    ),
    "E4": MessageDefinition(
        message_id="E4",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text="Changes in dates or dimensions may affect the container parent task.",
                allowed_intensities=(INTENSITY_HIGH,),
            ),
        ),
        buttons=("Ok",),
    ),
    "E6": MessageDefinition(
        message_id="E6",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text=(
                    "There are already instances in your list waiting to be worked out. "
                    "Changes in the periodicity of this compound task will only apply to future "
                    "instances that will be scheduled after the ones that are already there. "
                    "If you wish to see the changes sooner, delete ready tasks and the Scheduler "
                    "will do the job tonight. All other changes will apply to existing instances."
                ),
                allowed_intensities=(INTENSITY_MEDIUM, INTENSITY_HIGH,),
            ),
        ),
        buttons=("Ok",),
    ),
    "E7": MessageDefinition(
        message_id="E7",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text=(
                    "There are already instances in your list waiting to be worked out. "
                    "Changes in the periodicity of this task will only apply to future instances "
                    "that will be scheduled after the ones that are already there. If you wish to "
                    "see the changes sooner, delete ready tasks and the Scheduler will do the job "
                    "tonight. All other changes will apply to existing instances, including "
                    "prioritization if dates and/or attributes are modified."
                ),
                allowed_intensities=(INTENSITY_MEDIUM, INTENSITY_HIGH,),
            ),
        ),
        buttons=("Ok",),
    ),
    "E8": MessageDefinition(
        message_id="E8",
        use_openai=False,
        fallback_parts=(
            MessagePart(
                text=(
                    "There are already instances of this subtask, and the compound task it belongs "
                    "to, in your list waiting to be worked out. Changes in dates of this task may "
                    "affect dates in parent compound tasks, and both task changes will only apply "
                    "to future instances that will be scheduled after the ones that are already "
                    "there. If you wish to see the changes sooner, delete parent tasks scheduled "
                    "after the ones you are changing and the Scheduler will do the job tonight. "
                    "All other changes will apply to existing instances of this subtask, including "
                    "prioritization if attributes are modified."
                ),
                allowed_intensities=(INTENSITY_MEDIUM, INTENSITY_HIGH,),
            ),
        ),
        buttons=("Ok",),
    ),
}


def normalize_message_intensity(intensity: str | None) -> str:
    """Return the canonical intensity name expected by the catalog."""

    cleaned_intensity = str(intensity or "").strip().lower()
    return INTENSITY_ALIASES.get(cleaned_intensity, INTENSITY_HIGH)


def get_message_definition(message_id: str) -> MessageDefinition:
    """Fetch one message definition or fail loudly during development."""

    if message_id not in MESSAGE_CATALOG:
        raise KeyError(f"Unknown adaptive message id: {message_id}")
    return MESSAGE_CATALOG[message_id]


def _matches_condition(condition_name: str | None, **params: Any) -> bool:
    """Evaluate one simple boolean condition declared in the catalog."""

    if not condition_name:
        return True
    return bool(params.get(condition_name))


def should_display_message(message_id: str, intensity: str | None, **params: Any) -> bool:
    """Return whether at least one fallback segment survives filtering."""

    message_definition = get_message_definition(message_id)
    normalized_intensity = normalize_message_intensity(intensity)
    for part in message_definition.fallback_parts:
        if not _matches_condition(part.when, **params):
            continue
        allowed_intensities = tuple(
            normalize_message_intensity(value)
            for value in part.allowed_intensities
        )
        if normalized_intensity in allowed_intensities:
            return True
    return False


def resolve_message_text(message_id: str, intensity: str | None, **params: Any) -> str:
    """Build the fallback text that matches the current conditions/intensity."""

    message_definition = get_message_definition(message_id)
    normalized_intensity = normalize_message_intensity(intensity)
    rendered_parts: list[str] = []

    for part in message_definition.fallback_parts:
        if not _matches_condition(part.when, **params):
            continue
        allowed_intensities = tuple(
            normalize_message_intensity(value)
            for value in part.allowed_intensities
        )
        if normalized_intensity not in allowed_intensities:
            continue
        rendered_parts.append(
            part.text.format_map(_SafeFormatDict(params)).strip()
        )

    return " ".join(fragment for fragment in rendered_parts if fragment).strip()


def resolve_message_buttons(message_id: str, **params: Any) -> list[str]:
    """Return the visible buttons for one message definition.

    `button_parts` is the preferred expressive format for conditional buttons.
    Legacy `buttons` tuples remain supported for simple static actions.
    """

    message_definition = get_message_definition(message_id)
    if message_definition.button_parts:
        return [
            button.label
            for button in message_definition.button_parts
            if _matches_condition(button.when, **params)
        ]
    return list(message_definition.buttons)


def display_message(
    message_id: str,
    intensity: str | None,
    *,
    renderer: str = DEFAULT_RENDERER,
    key_prefix: str | None = None,
    **params: Any,
) -> MessageDisplayResult:
    """Render one catalog message and return the user action, if any.

    Phase 1 centralises fallback composition and button rendering. Timed
    auto-close, OpenAI rewriting, and TTS are intentionally deferred, but the
    metadata is already preserved in the catalog for the next phase.
    """

    message_definition = get_message_definition(message_id)
    rendered_text = resolve_message_text(message_id, intensity, **params)
    if not rendered_text:
        return MessageDisplayResult(
            displayed=False,
            text="",
            timer_seconds=message_definition.timer,
        )

    render_callable = VALID_RENDERERS.get(renderer, VALID_RENDERERS[DEFAULT_RENDERER])
    render_callable(rendered_text)

    button_clicked = None
    visible_buttons = resolve_message_buttons(message_id, **params)
    if visible_buttons:
        button_columns = st.columns(len(visible_buttons), gap="small")
        for index, label in enumerate(visible_buttons):
            button_key = f"{key_prefix or message_id}_button_{index}"
            with button_columns[index]:
                if st.button(label, key=button_key, use_container_width=True):
                    button_clicked = label

    return MessageDisplayResult(
        displayed=True,
        text=rendered_text,
        button_clicked=button_clicked,
        timer_seconds=message_definition.timer,
    )
