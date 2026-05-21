"""User-domain state machine and session counters.

This module keeps user-state transitions, runtime parameter overrides, and
session summary data independent from the Streamlit implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, MutableMapping


# Canonical state names persisted in the user-state log and shown in the UI.
PLANNER_STATE = "Planner"
FROZEN_STATE = "Frozen"
ENGAGED_STATE = "Engaged"
RECOVERY_STATE = "Recovery"

# Stable event identifiers used to drive the user-state machine.
LOGIN_DECLARED_EVENT = "login_declared"
MANUAL_SET_STATE_EVENT = "manual_set_state"
TASK_OPENED_EVENT = "task_opened"
TASK_REJECTED_EVENT = "task_rejected"
AUTO_OPEN_CANDIDATES_EXHAUSTED_EVENT = "auto_open_candidates_exhausted"
TASK_COMPLETED_EVENT = "task_completed"
WORK_ENDED_EVENT = "work_ended"
NO_ACTIVE_TASK_AFTER_COMPLETION_EVENT = "no_active_task_after_completion"
MICROSTEP_COMPLETED_EVENT = "microstep_completed"
PLANNER_TIMER_ELAPSED_EVENT = "planner_timer_elapsed"
LOGOUT_EVENT = "logout"

# Session keys that store FSM state and temporary runtime overrides.
USER_FSM_CONTEXT_KEY = "user_state_machine_context"
USER_FSM_PARAMETER_OVERRIDES_KEY = "user_state_machine_parameter_overrides"


@dataclass(frozen=True)
class UserStateMachineConfig:
    """Resolved runtime values for the behaviour parameters."""

    planner_minutes: int = 3
    completed_tasks_threshold: int = 1
    completed_microsteps_threshold: int = 3
    rejected_tasks_threshold: int = 2
    planner_warning_limit: int = 2


@dataclass
class UserStateMachineContext:
    """Session-scoped counters and memory used by the state machine."""

    current_state: str | None = None
    memory_state: str | None = None
    planner_iterations: int = 0
    completed_tasks_in_session: int = 0
    consecutive_completed_tasks: int = 0
    completed_microsteps_in_session: int = 0
    consecutive_completed_microsteps: int = 0
    consecutive_rejected_tasks: int = 0
    transition_counts_by_origin: dict[str, int] = field(default_factory=dict)
    last_event: str | None = None
    # This payload is a session-scoped hint from the task FSM, not a historical
    # audit record. Persistent task analytics belong to the database logs.
    last_event_payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the FSM context for Streamlit session storage."""

        return {
            "current_state": self.current_state,
            "memory_state": self.memory_state,
            "planner_iterations": self.planner_iterations,
            "completed_tasks_in_session": self.completed_tasks_in_session,
            "consecutive_completed_tasks": self.consecutive_completed_tasks,
            "completed_microsteps_in_session": self.completed_microsteps_in_session,
            "consecutive_completed_microsteps": self.consecutive_completed_microsteps,
            "consecutive_rejected_tasks": self.consecutive_rejected_tasks,
            "transition_counts_by_origin": dict(self.transition_counts_by_origin),
            "last_event": self.last_event,
            "last_event_payload": dict(self.last_event_payload),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "UserStateMachineContext":
        """Restore the FSM context and absorb legacy field names when present."""

        payload = payload or {}
        return cls(
            current_state=payload.get("current_state"),
            memory_state=payload.get("memory_state"),
            planner_iterations=int(payload.get("planner_iterations", 0) or 0),
            completed_tasks_in_session=int(payload.get("completed_tasks_in_session", 0) or 0),
            consecutive_completed_tasks=int(payload.get("consecutive_completed_tasks", 0) or 0),
            completed_microsteps_in_session=int(payload.get("completed_microsteps_in_session", 0) or 0),
            consecutive_completed_microsteps=int(payload.get("consecutive_completed_microsteps", 0) or 0),
            consecutive_rejected_tasks=int(payload.get("consecutive_rejected_tasks", 0) or 0),
            transition_counts_by_origin=dict(payload.get("transition_counts_by_origin") or {}),
            last_event=payload.get("last_event"),
            last_event_payload=dict(payload.get("last_event_payload") or {}),
        )


@dataclass(frozen=True)
class TransitionMessage:
    """Structured UI message emitted by the FSM without depending on UI code."""

    message_id: str
    zone: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the message for Streamlit session state."""

        return {
            "message_id": self.message_id,
            "zone": self.zone,
            "params": dict(self.params),
        }


@dataclass(frozen=True)
class TransitionResult:
    """Describe the side effects produced by one user-state transition."""

    previous_state: str | None
    current_state: str | None
    current_state_id: int | None
    changed: bool
    callbacks: list[str]
    ui_messages: list[TransitionMessage]
    start_planner_timer: bool = False
    stop_planner_timer: bool = False
    reset_planner_timer: bool = False
    requires_recovery_cleanup: bool = False
    should_end_session: bool = False
    summary_message: str | None = None
    context: UserStateMachineContext | None = None


class LoggedUserModel:
    """Persist and evolve the user-state model against Supabase and session state."""

    def __init__(self, *, supabase_client: Any, session_store: MutableMapping[str, Any]) -> None:
        self._supabase = supabase_client
        self._session = session_store

    def _get_context(self) -> UserStateMachineContext:
        """Read the FSM context from the active Streamlit session."""

        return UserStateMachineContext.from_dict(self._session.get(USER_FSM_CONTEXT_KEY))

    def _save_context(self, context: UserStateMachineContext) -> None:
        """Persist the FSM context back into session storage."""

        self._session[USER_FSM_CONTEXT_KEY] = context.to_dict()

    def clear_context(self) -> None:
        """Remove the FSM context when a session ends or resets."""

        self._session.pop(USER_FSM_CONTEXT_KEY, None)

    def get_runtime_parameter_overrides(self) -> dict[str, Any]:
        """Return temporary overrides for the P/T/M/Z/Y parameters."""

        return dict(self._session.get(USER_FSM_PARAMETER_OVERRIDES_KEY) or {})

    def set_runtime_parameter_overrides(self, overrides: dict[str, Any] | None) -> None:
        """Store temporary runtime overrides for one upcoming transition."""

        cleaned_overrides = {
            key: value
            for key, value in (overrides or {}).items()
            if value is not None
        }
        if cleaned_overrides:
            self._session[USER_FSM_PARAMETER_OVERRIDES_KEY] = cleaned_overrides
        else:
            self._session.pop(USER_FSM_PARAMETER_OVERRIDES_KEY, None)

    def reset_runtime_parameter_overrides(self) -> None:
        """Drop all temporary P/T/M/Z/Y runtime overrides."""

        self._session.pop(USER_FSM_PARAMETER_OVERRIDES_KEY, None)

    def build_config(self, preferences: dict[str, Any] | None) -> UserStateMachineConfig:
        """Build the effective FSM config from preferences plus session overrides."""

        preferences = preferences or {}
        runtime_overrides = self.get_runtime_parameter_overrides()

        def get_config_value(override_key: str, preference_key: str, default_value: int) -> int:
            raw_value = runtime_overrides.get(override_key, preferences.get(preference_key, default_value))
            return max(1, int(raw_value))

        return UserStateMachineConfig(
            planner_minutes=get_config_value(
                "P",
                "planner_minutes",
                UserStateMachineConfig.planner_minutes,
            ),
            completed_tasks_threshold=get_config_value("T", "state_tasks_threshold", 1),
            completed_microsteps_threshold=get_config_value("M", "state_microsteps_threshold", 3),
            rejected_tasks_threshold=get_config_value("Z", "state_rejected_tasks_threshold", 2),
            planner_warning_limit=get_config_value("Y", "planner_warning_limit", 2),
        )

    def get_normal_config(self, preferences: dict[str, Any] | None) -> UserStateMachineConfig:
        """Resolve the baseline config while ignoring temporary runtime overrides."""

        current_overrides = self.get_runtime_parameter_overrides()
        try:
            self.reset_runtime_parameter_overrides()
            return self.build_config(preferences)
        finally:
            self.set_runtime_parameter_overrides(current_overrides)

    def load_profile(self, user_id: str) -> dict[str, Any]:
        """Load the user's profile plus current state id in one UI-friendly dict."""

        profile_response = (
            self._supabase.table("profiles")
            .select("full_name, role, born, persona_id, preferences")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
        profile = (profile_response.data or [{}])[0]
        state_id = self.get_current_state_id(user_id)
        return {
            "full_name": profile.get("full_name"),
            "role": profile.get("role") or "user",
            "born": profile.get("born"),
            "persona_id": profile.get("persona_id"),
            "preferences": profile.get("preferences") or {},
            "state_id": state_id,
        }

    def get_current_state_id(self, user_id: str) -> int | None:
        """Read the latest state id from the user-state log."""

        response = (
            self._supabase.table("user_state_log")
            .select("state_id")
            .eq("user_id", user_id)
            .order("experienced_at", desc=True)
            .order("id", desc=True)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        return rows[0]["state_id"] if rows else None

    def set_state(self, user_id: str, state_id: int, *, experienced_at: datetime | None = None) -> None:
        """Append a new user-state log entry through the dedicated RPC."""

        experienced_at_value = experienced_at.isoformat() if experienced_at else None
        self._supabase.rpc(
            "set_user_state",
            {
                "p_user_id": user_id,
                "p_state_id": state_id,
                "p_experienced_at": experienced_at_value,
            },
        ).execute()

    def update_preferences(self, user_id: str, preferences: dict[str, Any]) -> None:
        """Persist a full preferences document for the current user."""

        (
            self._supabase.table("profiles")
            .update({"preferences": preferences})
            .eq("id", user_id)
            .execute()
        )

    def sync_context(self, *, current_state_name: str | None) -> UserStateMachineContext:
        """Ensure the in-session FSM context matches the persisted state on first use."""

        context = self._get_context()
        if context.current_state is None:
            context.current_state = current_state_name
            if current_state_name in {FROZEN_STATE, ENGAGED_STATE, RECOVERY_STATE}:
                context.memory_state = current_state_name
            self._save_context(context)
        return context

    def transition(
        self,
        *,
        user_id: str,
        current_state_name: str | None,
        state_id_by_name: dict[str, int],
        event: str,
        preferences: dict[str, Any] | None = None,
        declared_state: str | None = None,
        start_in_planner: bool = False,
        target_state: str | None = None,
        event_payload: dict[str, Any] | None = None,
        session_end_reason: str | None = None,
    ) -> TransitionResult:
        """Advance the user-state machine and persist any resulting state change."""

        try:
            config = self.build_config(preferences)
            context = self.sync_context(current_state_name=current_state_name)
            previous_state = context.current_state
            event_payload = dict(event_payload or {})
            callbacks: list[str] = []
            ui_messages: list[TransitionMessage] = []
            start_planner_timer = False
            stop_planner_timer = False
            reset_planner_timer = False
            requires_recovery_cleanup = False
            should_end_session = False

            if previous_state:
                transition_count = int(context.transition_counts_by_origin.get(previous_state, 0))
                context.transition_counts_by_origin[previous_state] = transition_count + 1

            if event == LOGIN_DECLARED_EVENT:
                context.completed_tasks_in_session = 0
                context.consecutive_completed_tasks = 0
                context.completed_microsteps_in_session = 0
                context.consecutive_completed_microsteps = 0
                context.consecutive_rejected_tasks = 0
                context.planner_iterations = 0
                if start_in_planner:
                    context.current_state = PLANNER_STATE
                    context.memory_state = declared_state or FROZEN_STATE
                    start_planner_timer = True
                    # ui_messages.append(
                    #     f"Planner mode started. Planner memory saved as {declared_state or FROZEN_STATE}."
                    # )
                else:
                    context.current_state = declared_state
                    context.memory_state = declared_state
                    stop_planner_timer = True

            elif event == MANUAL_SET_STATE_EVENT:
                if target_state == PLANNER_STATE:
                    context.current_state = PLANNER_STATE
                    context.memory_state = previous_state or context.memory_state
                    context.planner_iterations = 0
                    context.consecutive_rejected_tasks = 0
                    start_planner_timer = True
                elif target_state == FROZEN_STATE:
                    if previous_state == ENGAGED_STATE:
                        callbacks.append("engaged_to_frozen")
                        ui_messages.append(TransitionMessage("STATE_ENGAGED_TO_FROZEN", "adaptive"))
                    context.current_state = FROZEN_STATE
                    context.memory_state = FROZEN_STATE
                    context.consecutive_rejected_tasks = 0
                    stop_planner_timer = True
                elif target_state == ENGAGED_STATE:
                    if previous_state == FROZEN_STATE:
                        callbacks.append("frozen_to_engaged")
                        ui_messages.append(TransitionMessage("STATE_FROZEN_TO_ENGAGED", "adaptive"))
                    context.current_state = ENGAGED_STATE
                    context.memory_state = ENGAGED_STATE
                    context.consecutive_rejected_tasks = 0
                    stop_planner_timer = True
                elif target_state == RECOVERY_STATE:
                    context.current_state = RECOVERY_STATE
                    context.memory_state = previous_state or context.memory_state
                    stop_planner_timer = True
                    ui_messages.append(TransitionMessage("STATE_CHANGED_TO_RECOVERY", "adaptive"))

            elif event == TASK_OPENED_EVENT and context.current_state == PLANNER_STATE:
                # Opening a task while in Planner normally returns the user to
                # the remembered execution state. Adaptive flows can override
                # that target explicitly for the current transition.
                remembered_state = context.memory_state or RECOVERY_STATE
                payload_target_state = event_payload.get("planner_open_target_state")
                if target_state in {FROZEN_STATE, ENGAGED_STATE}:
                    next_state = target_state
                elif payload_target_state in {FROZEN_STATE, ENGAGED_STATE}:
                    next_state = payload_target_state
                elif remembered_state == ENGAGED_STATE:
                    next_state = ENGAGED_STATE
                else:
                    next_state = FROZEN_STATE
                context.current_state = next_state
                context.memory_state = next_state
                context.consecutive_rejected_tasks = 0
                stop_planner_timer = True

            elif event == TASK_REJECTED_EVENT:
                context.consecutive_rejected_tasks += 1
                rejection_origin_state = previous_state
                if rejection_origin_state not in {FROZEN_STATE, ENGAGED_STATE}:
                    rejection_origin_state = context.memory_state
                if (
                    rejection_origin_state in {FROZEN_STATE, ENGAGED_STATE}
                    and context.consecutive_rejected_tasks >= config.rejected_tasks_threshold
                ):
                    context.current_state = PLANNER_STATE
                    context.memory_state = rejection_origin_state
                    context.planner_iterations = 0
                    context.consecutive_completed_tasks = 0
                    context.consecutive_completed_microsteps = 0
                    context.consecutive_rejected_tasks = 0
                    start_planner_timer = True
                    ui_messages.append(
                        TransitionMessage(
                            "STATE_RETURNED_TO_PLANNER_AFTER_REJECTIONS",
                            "adaptive",
                            {"threshold": config.rejected_tasks_threshold},
                        )
                    )

            elif event == AUTO_OPEN_CANDIDATES_EXHAUSTED_EVENT:
                exhaustion_origin_state = previous_state
                if exhaustion_origin_state not in {FROZEN_STATE, ENGAGED_STATE}:
                    exhaustion_origin_state = context.memory_state
                if exhaustion_origin_state in {FROZEN_STATE, ENGAGED_STATE}:
                    context.current_state = PLANNER_STATE
                    context.memory_state = exhaustion_origin_state
                    context.planner_iterations = 0
                    context.consecutive_completed_tasks = 0
                    context.consecutive_completed_microsteps = 0
                    context.consecutive_rejected_tasks = 0
                    start_planner_timer = True
                    ui_messages.append(
                        TransitionMessage(
                            "STATE_RETURNED_TO_PLANNER_NO_MORE_AUTO_OPEN_TASKS",
                            "adaptive",
                        )
                    )

            elif event == TASK_COMPLETED_EVENT:
                context.completed_tasks_in_session += 1
                context.consecutive_completed_tasks += 1
                context.consecutive_rejected_tasks = 0
                if (
                    previous_state == FROZEN_STATE
                    and context.completed_tasks_in_session >= config.completed_tasks_threshold
                ):
                    context.current_state = ENGAGED_STATE
                    context.memory_state = ENGAGED_STATE
                    callbacks.append("frozen_to_engaged")
                    ui_messages.append(TransitionMessage("STATE_FROZEN_TO_ENGAGED_MOMENTUM", "adaptive"))

            elif event in {WORK_ENDED_EVENT, NO_ACTIVE_TASK_AFTER_COMPLETION_EVENT}:
                if previous_state in {FROZEN_STATE, ENGAGED_STATE}:
                    context.current_state = PLANNER_STATE
                    context.memory_state = previous_state
                    context.planner_iterations = 0
                    context.consecutive_rejected_tasks = 0
                    start_planner_timer = True
                    ui_messages.append(
                        TransitionMessage(
                            "STATE_RETURNED_TO_PLANNER_AFTER_WORK_ENDED",
                            "adaptive",
                        )
                    )

            elif event == MICROSTEP_COMPLETED_EVENT:
                context.completed_microsteps_in_session += 1
                context.consecutive_completed_microsteps += 1
                context.consecutive_rejected_tasks = 0
                if (
                    previous_state == FROZEN_STATE
                    and context.completed_microsteps_in_session >= config.completed_microsteps_threshold
                ):
                    context.current_state = ENGAGED_STATE
                    context.memory_state = ENGAGED_STATE
                    callbacks.append("frozen_to_engaged")
                    ui_messages.append(TransitionMessage("STATE_FROZEN_TO_ENGAGED_MICROSTEPS", "adaptive"))

            elif event == PLANNER_TIMER_ELAPSED_EVENT and context.current_state == PLANNER_STATE:
                context.planner_iterations += 1
                # `planner_warning_limit` represents how many reminders the user
                # can receive before the session is escalated to Recovery. The
                # logout should therefore happen only after all warnings were
                # already shown, not on the same tick as the final warning.
                if context.planner_iterations > config.planner_warning_limit:
                    context.current_state = RECOVERY_STATE
                    context.memory_state = previous_state or context.memory_state
                    stop_planner_timer = True
                    requires_recovery_cleanup = True
                    should_end_session = True
                    ui_messages.append(TransitionMessage("PLANNER_LIMIT_REACHED", "timing"))
                else:
                    reset_planner_timer = True
                    ui_messages.append(
                        TransitionMessage(
                            "PLANNER_REMINDER",
                            "timing",
                            {
                                "current": context.planner_iterations,
                                "limit": config.planner_warning_limit,
                                "minutes_until_recovery": (
                                    config.planner_warning_limit
                                    - context.planner_iterations
                                    + 1
                                )
                                * config.planner_minutes,
                            },
                        )
                    )

            elif event == LOGOUT_EVENT and previous_state in {PLANNER_STATE, FROZEN_STATE, ENGAGED_STATE}:
                context.current_state = RECOVERY_STATE
                context.memory_state = previous_state
                stop_planner_timer = True
                requires_recovery_cleanup = True
                should_end_session = True
                ui_messages.append(TransitionMessage("STATE_SESSION_CLOSED_RECOVERY", "adaptive"))

            context.last_event = event
            context.last_event_payload = event_payload
            changed = context.current_state != previous_state
            current_state_id = state_id_by_name.get(context.current_state) if context.current_state else None
            if changed and current_state_id is not None:
                self.set_state(user_id, current_state_id)

            self._save_context(context)

            return TransitionResult(
                previous_state=previous_state,
                current_state=context.current_state,
                current_state_id=current_state_id,
                changed=changed,
                callbacks=callbacks,
                ui_messages=ui_messages,
                start_planner_timer=start_planner_timer,
                stop_planner_timer=stop_planner_timer,
                reset_planner_timer=reset_planner_timer,
                requires_recovery_cleanup=requires_recovery_cleanup,
                should_end_session=should_end_session,
                summary_message=self.build_session_summary(context, session_end_reason or event),
                context=context,
            )
        finally:
            self.reset_runtime_parameter_overrides()

    @staticmethod
    def build_session_summary(context: UserStateMachineContext, reason: str) -> str:
        """Build a compact textual summary of the just-finished user session."""

        return (
            "Session summary: "
            f"state={context.current_state or 'None'}, "
            f"reason={reason}, "
            f"completed_tasks={context.completed_tasks_in_session}, "
            f"consecutive_completed_tasks={context.consecutive_completed_tasks}, "
            f"completed_microsteps={context.completed_microsteps_in_session}, "
            f"consecutive_completed_microsteps={context.consecutive_completed_microsteps}, "
            f"planner_iterations={context.planner_iterations}."
        )
