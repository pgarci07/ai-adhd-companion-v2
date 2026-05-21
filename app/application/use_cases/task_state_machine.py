"""Task-domain state machine used by the UI and future automation.

The goal of this module is to keep task-status transitions, validation rules,
and related side effects outside the Streamlit page layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


# Canonical task-status names persisted in the database and consumed by the UI.
READY_STATUS = "ready"
OPEN_STATUS = "open"
ASLEEP_STATUS = "asleep"
DEBT_STATUS = "debt"
COMPLETED_STATUS = "completed"
STALE_STATUS = "stale"
# Completed and stale remain terminal for in-place edits, but the UI may still
# request a cloned replacement instance when reopening is explicitly allowed.
FINAL_TASK_STATUSES = {COMPLETED_STATUS}

# Stable identifiers that document which domain event produced a transition.
CREATE_TASK_EVENT = "create_task"
ACTIVATE_WORK_EVENT = "activate_work"
PAUSE_TASK_EVENT = "pause_task"
RESUME_TASK_EVENT = "resume_task"
MARK_COMPLETED_EVENT = "mark_completed"
MARK_DEBT_EVENT = "mark_debt"
MARK_DEBT_FROM_ASLEEP_EVENT = "mark_debt_from_asleep"
REOPEN_DEBT_EVENT = "reopen_debt"
REOPEN_STALE_EVENT = "reopen_stale"
REOPEN_COMPLETED_EVENT = "reopen_completed"
MARK_STALE_EVENT = "mark_stale"

# Cross-FSM notifications emitted by the task-status domain model when a task
# transition should explicitly inform the user-state machine.
USER_STATE_TASK_OPENED_EVENT = "task_opened"
USER_STATE_TASK_COMPLETED_EVENT = "task_completed"


@dataclass(frozen=True)
class TaskUserStateEvent:
    """One explicit notification that should be sent to the user-state FSM.

    These events are intentionally lightweight: the database status log remains
    the source of truth for task-history analytics, while this payload only
    carries the task attributes that may help the user-state FSM react within
    the current session.
    """

    event_name: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class TaskTransitionDecision:
    """Internal description of a permitted transition."""

    event: str
    action_id: int
    target_status: str
    requires_clone: bool = False


@dataclass(frozen=True)
class TaskTransitionResult:
    """Outcome returned to the UI after attempting a task-state transition."""

    previous_status: str
    target_status: str
    changed: bool
    action_id: int | None = None
    effective_task_row: dict[str, Any] | None = None
    propagated_instance_ids: tuple[str, ...] = ()
    created_instance_id: str | None = None
    ui_message: str | None = None
    user_state_events: tuple[TaskUserStateEvent, ...] = ()


class TaskTransitionError(ValueError):
    """Raised when the requested task transition is invalid."""

    pass


class LoggedTaskModel:
    """Encapsulate task retrieval and state transitions against Supabase."""

    def __init__(self, *, supabase_client: Any) -> None:
        self._supabase = supabase_client

    def list_user_task_rows(self) -> list[dict[str, Any]]:
        """Load the current user's task-instance rows in the UI-friendly shape."""

        response = self._supabase.rpc("get_user_task_rows").execute()
        rows = []
        for row in response.data or []:
            rows.append(
                {
                    "instance_id": row.get("instance_id"),
                    "task_id": row.get("task_id"),
                    "instance_number": row.get("instance_number"),
                    "parent_instance_id": row.get("parent_instance_id"),
                    "list_id": row.get("list_id"),
                    "title": row.get("title", "Untitled"),
                    "description": row.get("description"),
                    "start_date": row.get("start_date"),
                    "due_date": row.get("due_date"),
                    "status": row.get("status", READY_STATUS),
                    "rrule": row.get("rrule"),
                    "is_active": row.get("is_active"),
                    "is_routine": row.get("is_routine"),
                    "size_id": row.get("size_id"),
                    "consequence_id": row.get("consequence_id"),
                    "friction_id": row.get("friction_id"),
                    "final_comments": row.get("final_comments"),
                    "actual_duration": row.get("actual_duration"),
                    "actual_friction_id": row.get("actual_friction_id"),
                    "is_adaptive": row.get("is_adaptive"),
                    "parent_task_id": row.get("parent_task_id"),
                }
            )
        return rows

    def transition_to_status(
        self,
        task_row: dict[str, Any],
        target_status: str,
        *,
        task_rows: list[dict[str, Any]] | None = None,
        reopened_start_at: str | None = None,
        reopened_due_at: str | None = None,
    ) -> TaskTransitionResult:
        """Apply one validated status change and return the effective outcome."""

        previous_status = str(task_row.get("status") or READY_STATUS).lower()
        target_status = str(target_status or "").lower()

        if previous_status == target_status:
            return TaskTransitionResult(
                previous_status=previous_status,
                target_status=target_status,
                changed=False,
                effective_task_row=task_row,
            )

        decision = self.transition(
            previous_status,
            target_status,
            has_subtasks=bool(task_row.get("has_subtasks")),
            is_recurrent=bool(task_row.get("rrule")),
        )
        resolved_rows = task_rows or self.list_user_task_rows()

        if decision.requires_clone:
            created_task_row = self._clone_historical_instance(
                task_row,
                reopened_start_at=reopened_start_at,
                reopened_due_at=reopened_due_at,
            )
            self._apply_status(created_task_row["instance_id"], OPEN_STATUS)
            opened_task_row = {**created_task_row, "status": OPEN_STATUS}
            return TaskTransitionResult(
                previous_status=previous_status,
                target_status=OPEN_STATUS,
                changed=True,
                action_id=decision.action_id,
                effective_task_row=opened_task_row,
                created_instance_id=created_task_row["instance_id"],
                ui_message=(
                    "A new task instance was created and opened with the new schedule."
                ),
                user_state_events=(
                    self._build_user_state_event(
                        USER_STATE_TASK_OPENED_EVENT,
                        opened_task_row,
                        previous_status=previous_status,
                    ),
                ),
            )

        self._apply_status(task_row["instance_id"], decision.target_status)
        propagated_ids: list[str] = []
        if decision.target_status in {COMPLETED_STATUS, DEBT_STATUS, STALE_STATUS}:
            propagated_ids = self._propagate_status_to_descendants(
                task_row,
                decision.target_status,
                resolved_rows,
            )

        effective_task_row = {**task_row, "status": decision.target_status}
        user_state_events: list[TaskUserStateEvent] = []
        if decision.target_status == OPEN_STATUS and previous_status != OPEN_STATUS:
            user_state_events.append(
                self._build_user_state_event(
                    USER_STATE_TASK_OPENED_EVENT,
                    effective_task_row,
                    previous_status=previous_status,
                )
            )
        if decision.target_status == COMPLETED_STATUS and previous_status != COMPLETED_STATUS:
            user_state_events.append(
                self._build_user_state_event(
                    USER_STATE_TASK_COMPLETED_EVENT,
                    effective_task_row,
                    previous_status=previous_status,
                )
            )

        return TaskTransitionResult(
            previous_status=previous_status,
            target_status=decision.target_status,
            changed=True,
            action_id=decision.action_id,
            effective_task_row=effective_task_row,
            propagated_instance_ids=tuple(propagated_ids),
            user_state_events=tuple(user_state_events),
        )

    def maybe_complete_parent_from_children(self, child_task_row: dict[str, Any]) -> bool:
        """Mark a parent instance as completed when all its child instances are completed."""

        parent_instance_id = child_task_row.get("parent_instance_id")
        if not parent_instance_id:
            return False

        task_rows = self.list_user_task_rows()
        sibling_rows = [
            row
            for row in task_rows
            if row.get("parent_instance_id") == parent_instance_id
        ]
        if not sibling_rows:
            return False

        if any(str(row.get("status") or READY_STATUS).lower() != COMPLETED_STATUS for row in sibling_rows):
            return False

        parent_row = next(
            (row for row in task_rows if row.get("instance_id") == parent_instance_id),
            None,
        )
        if not parent_row:
            return False

        parent_status = str(parent_row.get("status") or READY_STATUS).lower()
        if parent_status == COMPLETED_STATUS:
            return False

        self._apply_status(parent_instance_id, COMPLETED_STATUS)
        return True

    @staticmethod
    def transition(
        current_status: str,
        target_status: str,
        *,
        has_subtasks: bool = False,
        is_recurrent: bool = False,
    ) -> TaskTransitionDecision:
        """Validate a transition request and map it to the domain action id."""

        current_status = str(current_status or READY_STATUS).lower()
        target_status = str(target_status or "").lower()

        if current_status in FINAL_TASK_STATUSES and not (
            current_status == COMPLETED_STATUS and target_status == OPEN_STATUS
        ):
            raise TaskTransitionError(
                f"Tasks in '{current_status}' do not have outgoing transitions."
            )

        if target_status == OPEN_STATUS and has_subtasks:
            raise TaskTransitionError(
                "A parent task with subtasks cannot transition to 'Open'."
            )

        transitions = {
            (READY_STATUS, OPEN_STATUS): TaskTransitionDecision(ACTIVATE_WORK_EVENT, 2, OPEN_STATUS),
            (READY_STATUS, DEBT_STATUS): TaskTransitionDecision(MARK_DEBT_EVENT, 6, DEBT_STATUS),
            (READY_STATUS, COMPLETED_STATUS): TaskTransitionDecision(MARK_COMPLETED_EVENT, 5, COMPLETED_STATUS),
            (OPEN_STATUS, ASLEEP_STATUS): TaskTransitionDecision(PAUSE_TASK_EVENT, 3, ASLEEP_STATUS),
            (OPEN_STATUS, DEBT_STATUS): TaskTransitionDecision(MARK_DEBT_EVENT, 6, DEBT_STATUS),
            (OPEN_STATUS, COMPLETED_STATUS): TaskTransitionDecision(MARK_COMPLETED_EVENT, 5, COMPLETED_STATUS),
            (ASLEEP_STATUS, OPEN_STATUS): TaskTransitionDecision(RESUME_TASK_EVENT, 4, OPEN_STATUS),
            (ASLEEP_STATUS, DEBT_STATUS): TaskTransitionDecision(MARK_DEBT_FROM_ASLEEP_EVENT, 7, DEBT_STATUS),
            (ASLEEP_STATUS, COMPLETED_STATUS): TaskTransitionDecision(MARK_COMPLETED_EVENT, 5, COMPLETED_STATUS),
            # Debt tasks may still be worked directly. If they are not
            # completed, the scheduling logic can later move the open instance
            # back to debt because the due date is already in the past.
            (DEBT_STATUS, COMPLETED_STATUS): TaskTransitionDecision(MARK_COMPLETED_EVENT, 5, COMPLETED_STATUS),
            (DEBT_STATUS, OPEN_STATUS): TaskTransitionDecision(REOPEN_DEBT_EVENT, 8, OPEN_STATUS),
            (DEBT_STATUS, STALE_STATUS): TaskTransitionDecision(MARK_STALE_EVENT, 9, STALE_STATUS),
            # Stale tasks cannot be reopened in place. The user must request a
            # fresh cloned instance with a new schedule before working again.
            (STALE_STATUS, OPEN_STATUS): TaskTransitionDecision(REOPEN_STALE_EVENT, 8, OPEN_STATUS, requires_clone=True),
            # Completed tasks stay immutable in-place, but the UI may create a
            # fresh follow-up instance by cloning the completed one.
            (COMPLETED_STATUS, OPEN_STATUS): TaskTransitionDecision(REOPEN_COMPLETED_EVENT, 8, OPEN_STATUS, requires_clone=True),
        }

        decision = transitions.get((current_status, target_status))
        if decision is None:
            raise TaskTransitionError(
                f"Transition from '{current_status}' to '{target_status}' is not allowed."
            )
        if current_status == STALE_STATUS and target_status == OPEN_STATUS and is_recurrent:
            raise TaskTransitionError(
                "Recurring stale tasks cannot be reopened manually. Their next occurrences are generated from the recurrence rule."
            )
        return decision

    def _apply_status(
        self,
        instance_id: str,
        new_status: str,
        *,
        changed_at: datetime | None = None,
    ) -> None:
        """Persist a task-status change through the status-log RPC."""

        payload: dict[str, Any] = {
            "p_instance_id": instance_id,
            "p_new_status": new_status,
        }
        if changed_at is not None:
            payload["p_changed_at"] = changed_at.isoformat()

        self._supabase.rpc("set_task_instance_status", payload).execute()

    @staticmethod
    def _build_user_state_event(
        event_name: str,
        task_row: dict[str, Any],
        *,
        previous_status: str,
    ) -> TaskUserStateEvent:
        """Build one session-scoped user-state notification.

        The user-state FSM does not need audit identifiers or verbose task
        labels here because the database already stores the full task-status
        history. Instead, this payload keeps only lightweight behavioural hints
        that may shape in-session state transitions or future adaptive tuning.
        """

        return TaskUserStateEvent(
            event_name=event_name,
            payload={
                # `current_status` and `previous_status` preserve the local
                # transition semantics without making the user-state machine
                # inspect the task grid or the task-status log directly.
                "current_status": task_row.get("status"),
                "previous_status": previous_status,
                # These ranking and routine signals are the most plausible
                # session-level inputs for future persona/state refinements.
                "wobj": task_row.get("WOBJ"),
                "wsub": task_row.get("WSUB"),
                "urgency": task_row.get("urgency"),
                "is_routine": task_row.get("is_routine"),
            },
        )

    def _propagate_status_to_descendants(
        self,
        task_row: dict[str, Any],
        new_status: str,
        task_rows: list[dict[str, Any]],
    ) -> list[str]:
        """Mirror a terminal parent status to all descendant task instances."""

        descendant_rows = self._get_descendants(task_row.get("instance_id"), task_rows)
        changed_ids: list[str] = []
        for child_row in descendant_rows:
            child_status = str(child_row.get("status") or READY_STATUS).lower()
            if child_status == new_status:
                continue
            self._apply_status(child_row["instance_id"], new_status)
            changed_ids.append(child_row["instance_id"])
        return changed_ids

    def _get_descendants(
        self,
        parent_instance_id: str | None,
        task_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return the full descendant tree for one parent instance id."""

        if not parent_instance_id:
            return []

        children_by_parent: dict[str, list[dict[str, Any]]] = {}
        for row in task_rows:
            parent_id = row.get("parent_instance_id")
            if parent_id:
                children_by_parent.setdefault(parent_id, []).append(row)

        descendants: list[dict[str, Any]] = []
        queue = list(children_by_parent.get(parent_instance_id, []))
        while queue:
            row = queue.pop(0)
            descendants.append(row)
            queue.extend(children_by_parent.get(row.get("instance_id"), []))
        return descendants

    def _clone_historical_instance(
        self,
        task_row: dict[str, Any],
        *,
        reopened_start_at: str | None,
        reopened_due_at: str | None,
    ) -> dict[str, Any]:
        """Create a new task instance when reopening a stale historical one."""

        if not reopened_start_at or not reopened_due_at:
            raise TaskTransitionError(
                "Historical tasks need a new start and due date before they can be reopened."
            )

        insert_payload = {
            "task_id": task_row["task_id"],
            "user_id": task_row.get("user_id"),
            "instance_number": task_row.get("instance_number") or 1,
            "parent_instance_id": task_row.get("parent_instance_id"),
            "start_date": reopened_start_at,
            "due_date": reopened_due_at,
            "is_exception": True,
            "original_start_date": task_row.get("start_date"),
            "original_due_date": task_row.get("due_date"),
        }
        response = self._supabase.table("task_instances").insert(insert_payload).execute()
        created_row = (response.data or [{}])[0]
        instance_id = created_row.get("id")
        if not instance_id:
            raise TaskTransitionError("Could not create the reopened debt instance.")

        return {
            **task_row,
            "instance_id": instance_id,
            "start_date": created_row.get("start_date", reopened_start_at),
            "due_date": created_row.get("due_date", reopened_due_at),
            "status": READY_STATUS,
        }
