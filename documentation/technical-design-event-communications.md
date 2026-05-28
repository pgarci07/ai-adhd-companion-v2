# Technical Design Guide: Event Communications Between the UI and the State Machines

## Purpose

This document describes how event communication works across the application runtime, with particular attention to:

- the Streamlit UI layer;
- the user state machine;
- the task state machine; and
- the business layer that selects the next candidate task from the active grid.

The guide is intended for engineers working on behaviour, orchestration, and session flow. It explains where responsibilities sit, how events travel, and why the current design keeps the state machines decoupled from one another.

## Architectural Overview

The event architecture is built around three distinct responsibilities.

### 1. UI Orchestration Layer

The UI layer, centred on `app/ui/main.py` and supported by specialised modules such as `app/ui/chunk.py`, is responsible for:

- collecting user intent from buttons, dialogs, timers, and overlays;
- calling the relevant domain model;
- applying concrete side effects such as timer changes, cache refreshes, notices, and reruns; and
- relaying cross-domain notifications between the two state machines.

The UI is deliberately the orchestration boundary. It is the only layer that knows about Streamlit session state, rendered pages, timers, and ephemeral interaction flow.

### 2. User State Machine

The user state machine lives in `app/application/use_cases/user_state_machine.py`. It models the user's session-level behavioural state.

Its canonical states are:

- `Planner`
- `Frozen`
- `Engaged`
- `Recovery`

It receives high-level events such as login declaration, task opening, task completion, work ending, candidate exhaustion, planner timeout, and logout. It returns a structured `TransitionResult` that describes:

- the new state;
- whether anything changed;
- UI messages to surface;
- timer instructions;
- recovery-clean-up intent; and
- session summary information.

The user state machine does not manipulate Streamlit directly.

### 3. Task State Machine

The task state machine lives in `app/application/use_cases/task_state_machine.py`. It governs task-instance status transitions such as:

- `ready -> open`
- `open -> completed`
- `open -> asleep`
- `open -> debt`
- reopen flows from `debt`, `stale`, or `completed`

It validates whether the transition is legal, persists the status change, and emits lightweight cross-FSM notifications when the user state machine should react to task activity.

The task state machine does not change the user state by itself.

## Core Design Principle

The two state machines do not call each other directly.

Instead:

1. the UI calls one state machine;
2. that state machine returns a structured result;
3. the UI interprets the result and applies side effects; and
4. if needed, the UI dispatches a second event into the other state machine.

This gives three benefits:

- each state machine remains conceptually small and testable;
- UI-specific side effects stay outside the domain logic; and
- cross-domain behaviour is explicit rather than hidden inside nested model calls.

## Event Flow Channels

## UI to User State Machine

The main gateway is `dispatch_user_state_event(...)` in `app/ui/main.py`.

This function:

1. resolves the current authenticated user;
2. loads the cached profile and the persisted current state;
3. calls `LoggedUserModel.transition(...)`; and
4. passes the result to `apply_user_state_transition_result(...)`.

This is the standard route for events such as:

- `LOGIN_DECLARED_EVENT`
- `MANUAL_SET_STATE_EVENT`
- `WORK_ENDED_EVENT`
- `TASK_REJECTED_EVENT`
- `AUTO_OPEN_CANDIDATES_EXHAUSTED_EVENT`
- `PLANNER_TIMER_ELAPSED_EVENT`
- `LOGOUT_EVENT`

The user state machine therefore remains the single source of truth for session-level behavioural transitions.

## UI to Task State Machine

The main gateway is `update_task_status(...)` in `app/ui/main.py`.

This function:

1. calls `LoggedTaskModel.transition_to_status(...)`;
2. refreshes the task grid version;
3. clears or preserves timer and overlay artefacts depending on the new task status;
4. consumes any `user_state_events` emitted by the task transition; and
5. redispatches those events into the user state machine.

This is the main bridge between task status changes and user session behaviour.

## Task State Machine to User State Machine

This channel is indirect by design.

When `LoggedTaskModel.transition_to_status(...)` completes, it may emit one or more `TaskUserStateEvent` values. At present, the key notifications are:

- `task_opened`
- `task_completed`

These are intentionally lightweight. They carry only the behavioural hints needed by the user state machine during the current session, not a full audit trail. The database remains the durable source of truth for task history.

The UI then loops over `transition_result.user_state_events` and dispatches each one through `dispatch_user_state_event(...)`.

This means:

- the task state machine describes what happened to the task;
- the UI decides that the user state machine should be informed; and
- the user state machine decides whether the user's behavioural state changes.

## User State Machine to UI

The user state machine returns a `TransitionResult`, which is then interpreted by `apply_user_state_transition_result(...)`.

That function is the main adapter from domain-level transition semantics to UI-level side effects. It is responsible for:

- keeping the cached profile state id in sync;
- preserving or shutting down guided auto-open chains;
- clearing adaptive offered-task memory when appropriate;
- enabling, disabling, resetting, or starting the planner timer;
- disabling work timers when returning to `Planner` or entering `Recovery`;
- running recovery clean-up when instructed;
- storing transition notices for later rendering; and
- scheduling follow-on UI reactions such as notices or success messages.

This arrangement ensures that the user state machine can stay free of Streamlit-specific behaviour while still expressing everything the UI needs to know.

## The Role of `current_state` and `memory_state`

The user state machine keeps two related concepts in session context.

### `current_state`

This is the user's actual active state at the present moment, such as `Planner`, `Frozen`, `Engaged`, or `Recovery`.

### `memory_state`

This is the remembered execution state that should be restored when the user is in `Planner` and then opens a task.

This distinction is essential.

Example:

1. the user arrives and declares `Frozen`;
2. they choose to begin in `Planner`;
3. the machine stores:
   - `current_state = Planner`
   - `memory_state = Frozen`
4. when a task is truly opened, `TASK_OPENED_EVENT` returns the user from `Planner` to the remembered execution state.

Without `memory_state`, `Planner` would lose the context needed to restore the correct working mode.

## Canonical User Events and Their Meaning

### `LOGIN_DECLARED_EVENT`

Raised when the user declares their arriving state at the start of a session. It initialises session counters and places the user either directly into an execution state or into `Planner` with a remembered state.

### `MANUAL_SET_STATE_EVENT`

Raised when the UI explicitly requests a manual state change. It is used for deliberate state control rather than adaptive state movement.

### `TASK_OPENED_EVENT`

Usually originates from the task state machine after a task reaches `open`. If the user is currently in `Planner`, the event moves them back into the remembered or explicitly requested execution state.

### `TASK_REJECTED_EVENT`

Raised when the user rejects a guided opening proposal. The user state machine counts rejections and can return the user to `Planner` after the configured threshold.

### `AUTO_OPEN_CANDIDATES_EXHAUSTED_EVENT`

Raised when the guided-open logic can no longer find another valid candidate in the active grid. The user remains in, or returns to, `Planner`, and the guided chain is shut down.

### `TASK_COMPLETED_EVENT`

Raised after a task reaches `completed`. It can influence momentum rules, for example allowing a transition from `Frozen` to `Engaged` after enough completions.

### `WORK_ENDED_EVENT`

Raised when a chunk, sprint, or equivalent work phase ends, even if the task itself remains `open`. The user state machine interprets this as a return from execution mode to `Planner`.

### `NO_ACTIVE_TASK_AFTER_COMPLETION_EVENT`

Raised when a completion flow ends and there is no active task left to continue with. It shares the same transition branch as `WORK_ENDED_EVENT`.

### `MICROSTEP_COMPLETED_EVENT`

Raised when the UI records microstep progress rather than a full task completion. It can influence adaptive movement from `Frozen` to `Engaged`.

### `PLANNER_TIMER_ELAPSED_EVENT`

Raised by timer flow when the planner interval expires. The machine either emits a reminder and resets the planner timer or escalates the session to `Recovery` once warnings are exhausted.

### `LOGOUT_EVENT`

Raised during explicit session closure. It moves the user to `Recovery`, triggers clean-up, and marks the session as ended.

## Guided Task Opening and the Active Grid

Guided task opening is not owned by either state machine. It belongs to the business helper module `app/application/use_cases/active_task_grid.py`.

This separation matters because candidate selection depends on:

- the page the user is currently on;
- the exact visible ordering of the grid;
- whether the user is working in the root task list or a subtask grid; and
- which rows are merely visible versus genuinely workable.

`ActiveTaskGrid` is the business object that captures that surface.

It contains:

- `visible_df`: the ordered set of rows conceptually visible to the user;
- `workable_df`: the subset eligible for real work proposals;
- `root_df`: the root rows used for candidate traversal;
- `subtasks_df`: the child rows available for compound-task expansion; and
- `adaptation`: the current task adaptation governing ranking and auto-open behaviour.

The candidate selector `get_next_open_candidate(...)` follows these rules:

1. proposals come from the currently active grid, not from a separately rebuilt list with different filters;
2. root ordering is respected;
3. when a root row is a compound task, the candidate becomes the first workable child of that concrete parent instance;
4. already offered instance ids are skipped; and
5. if no valid candidate remains, the function returns `None`.

This is the key mechanism that allows the UI to ask, "what should be proposed next from the grid the user is actually working with?"

## Compound Tasks and Cross-Row Continuation

Compound-task handling follows the visible root ordering.

If a root row is compound:

1. the selector locates the visible child rows belonging to that exact parent instance;
2. it sorts and filters those child rows;
3. it proposes the first workable child that has not already been offered.

When the final workable child has been consumed, the selector continues to the next root row in the parent grid order. In other words, completion or exhaustion of a compound task's children can naturally chain into the next visible root task, provided another valid candidate exists.

## Work Completion Versus Task Completion

The design distinguishes between:

- task status completion; and
- the end of a work phase.

`TASK_COMPLETED_EVENT` means the task reached `completed`.

`WORK_ENDED_EVENT` means the user has finished the present work cycle, even if the task remains `open`.

This distinction is important because:

- a task may remain open across multiple chunk or Pomodoro cycles; and
- the user may return to `Planner` between work cycles without the task being completed.

That distinction is also what allows guided continuation to move to another candidate only when appropriate.

## Recovery and Session Suspension

Recovery flows are initiated by the user state machine but executed by the UI.

Two main patterns exist:

### Explicit Logout

The UI dispatches `LOGOUT_EVENT`, the user state machine moves the user to `Recovery`, and UI clean-up clears the authenticated session and related runtime state.

### Recoverable Session Suspension

When planner timeout or similar flow triggers a recoverable suspension, the UI calls `suspend_authenticated_work_session(...)`.

This stores a short-lived suspension marker that includes the resumable execution state, clears in-memory UI state, but deliberately leaves authenticated browser state in place so the user may resume inside the grace window.

This is distinct from a true logout.

## Typical End-to-End Flows

### Flow A: Login, Planner, Open Task

1. The user signs in and declares their arriving state.
2. The UI dispatches `LOGIN_DECLARED_EVENT`.
3. The user state machine initialises session counters and enters either:
   - `Planner` with a remembered execution state; or
   - a direct execution state.
4. The UI computes the current active grid.
5. If guided open is enabled, the UI finds the next candidate.
6. The user accepts the proposal.
7. The UI calls `update_task_status(..., "open")`.
8. The task state machine changes the task status and emits `task_opened`.
9. The UI redispatches `TASK_OPENED_EVENT` to the user state machine.
10. If the user was in `Planner`, they return to the remembered execution state.

### Flow B: Finish a Chunk While the Task Stays Open

1. The user finishes a chunk or sprint.
2. The UI ends the work timer and clears overlays as needed.
3. The UI calls `notify_work_ended()`.
4. `notify_work_ended()` dispatches `WORK_ENDED_EVENT`.
5. The user state machine returns the user from `Frozen` or `Engaged` to `Planner`.
6. `apply_user_state_transition_result(...)` decides whether the guided chain should continue.
7. If another candidate exists in the active grid, the chain may continue.
8. If no candidate exists, the guided chain is shut down and the user simply remains in `Planner`.

### Flow C: Complete a Task

1. The UI requests completion.
2. If needed, it first queues a completion-feedback dialog.
3. The UI calls `update_task_status(..., "completed")`.
4. The task state machine persists the new status.
5. The task state machine emits `task_completed`.
6. The UI redispatches `TASK_COMPLETED_EVENT`.
7. Parent-completion rules may also run if all child tasks are now completed.

## Design Constraints and Intentional Trade-Offs

### Domain Purity Over Convenience

The state machines return structured results rather than manipulating the UI directly. This is slightly more verbose, but it gives clearer separation and better testability.

### UI as the Explicit Integration Layer

Cross-FSM communication is visible in the UI code rather than hidden inside model internals. This makes event choreography easier to reason about during debugging.

### Session Context as a Runtime, Not an Audit Layer

The user state machine context is a session-scoped runtime memory. It is not a substitute for the persistent task or user-state logs stored in the database.

### Active Grid Semantics Matter

Candidate selection is based on the grid the user is actually working with. This avoids a subtle but important class of bugs where the system proposes a task from a different filter set or page than the one currently on screen.

---

## Appendix A: Function Reference

The entries below list the key functions and classes involved in event communication. Parameter names are reproduced in their code-facing form for accuracy.

### `LoggedUserModel.transition`

- **Module:** `app/application/use_cases/user_state_machine.py`
- **Signature:** `transition(*, user_id, current_state_name, state_id_by_name, event, preferences=None, declared_state=None, start_in_planner=False, target_state=None, event_payload=None, session_end_reason=None) -> TransitionResult`
- **Parameters:**
  - `user_id`: authenticated user identifier.
  - `current_state_name`: persisted current state name loaded from the latest user-state log row.
  - `state_id_by_name`: mapping from canonical state names to persisted state ids.
  - `event`: the user-state event to process.
  - `preferences`: profile preferences document used to build runtime behaviour configuration.
  - `declared_state`: arriving state declared by the user at login time.
  - `start_in_planner`: whether login should begin in `Planner`.
  - `target_state`: explicit target override for transitions that support one.
  - `event_payload`: lightweight session-scoped payload associated with the event.
  - `session_end_reason`: human-readable reason used for session-summary text.
- **Result:** `TransitionResult`
- **Description:** Central transition engine for the user state machine. It builds effective configuration, restores or syncs session context, applies the transition rule for the incoming event, persists any state change, stores updated runtime context, and returns a structured description of what the UI should do next.

### `LoggedUserModel.sync_context`

- **Module:** `app/application/use_cases/user_state_machine.py`
- **Signature:** `sync_context(*, current_state_name) -> UserStateMachineContext`
- **Parameters:**
  - `current_state_name`: persisted state name to align with the in-session context on first use.
- **Result:** `UserStateMachineContext`
- **Description:** Ensures the session-scoped FSM context is initialised consistently with the persisted state record. It is the bridge between durable state and in-memory runtime state at the start of a session or rerun.

### `LoggedUserModel.build_config`

- **Module:** `app/application/use_cases/user_state_machine.py`
- **Signature:** `build_config(preferences) -> UserStateMachineConfig`
- **Parameters:**
  - `preferences`: persisted preferences dictionary for the current user.
- **Result:** `UserStateMachineConfig`
- **Description:** Resolves effective state-machine parameters from profile preferences plus any temporary runtime overrides. These parameters include planner duration, thresholds for completed tasks and microsteps, rejection threshold, and warning limit.

### `LoggedUserModel.build_session_summary`

- **Module:** `app/application/use_cases/user_state_machine.py`
- **Signature:** `build_session_summary(context, reason) -> str`
- **Parameters:**
  - `context`: final session context snapshot.
  - `reason`: reason string associated with session end or summary generation.
- **Result:** `str`
- **Description:** Produces a compact textual summary of the session. This is intended for controlled UI use rather than as the authoritative audit source.

### `LoggedTaskModel.transition_to_status`

- **Module:** `app/application/use_cases/task_state_machine.py`
- **Signature:** `transition_to_status(task_row, target_status, *, task_rows=None, reopened_start_at=None, reopened_due_at=None) -> TaskTransitionResult`
- **Parameters:**
  - `task_row`: the task instance row to change.
  - `target_status`: requested target status.
  - `task_rows`: optional full task-row set used for descendant propagation and related checks.
  - `reopened_start_at`: optional new start timestamp for cloned reopen flows.
  - `reopened_due_at`: optional new due timestamp for cloned reopen flows.
- **Result:** `TaskTransitionResult`
- **Description:** Main entry point to the task state machine. It validates the requested transition, persists the change, handles clone-based reopen flows where required, propagates terminal statuses to descendants when necessary, and emits user-state notifications for the UI to relay.

### `LoggedTaskModel.transition`

- **Module:** `app/application/use_cases/task_state_machine.py`
- **Signature:** `transition(current_status, target_status, *, has_subtasks=False, is_recurrent=False) -> TaskTransitionDecision`
- **Parameters:**
  - `current_status`: current status of the task instance.
  - `target_status`: requested new status.
  - `has_subtasks`: whether the instance is a parent with subtasks.
  - `is_recurrent`: whether the task is recurrent.
- **Result:** `TaskTransitionDecision`
- **Description:** Pure validation and mapping function that determines whether a transition is allowed and, if so, which action id and domain event it corresponds to.

### `LoggedTaskModel.maybe_complete_parent_from_children`

- **Module:** `app/application/use_cases/task_state_machine.py`
- **Signature:** `maybe_complete_parent_from_children(child_task_row) -> bool`
- **Parameters:**
  - `child_task_row`: child task instance row whose completion may trigger parent completion.
- **Result:** `bool`
- **Description:** Checks whether all sibling child instances under the same parent instance are completed and, if so, marks the parent instance as completed as well.

### `LoggedTaskModel._build_user_state_event`

- **Module:** `app/application/use_cases/task_state_machine.py`
- **Signature:** `_build_user_state_event(event_name, task_row, *, previous_status) -> TaskUserStateEvent`
- **Parameters:**
  - `event_name`: task-to-user notification name.
  - `task_row`: effective task row after transition.
  - `previous_status`: status before the transition.
- **Result:** `TaskUserStateEvent`
- **Description:** Builds the lightweight session-scoped payload used to inform the user state machine about meaningful task activity such as opening or completion.

### `build_my_tasks_active_grid`

- **Module:** `app/application/use_cases/active_task_grid.py`
- **Signature:** `build_my_tasks_active_grid(tasks_df, adaptation, *, show_routines, show_completed_tasks, completed_instance_ids, never_visible_statuses, active_statuses) -> ActiveTaskGrid`
- **Parameters:**
  - `tasks_df`: enriched tasks dataframe.
  - `adaptation`: current task adaptation, if any.
  - `show_routines`: whether the current page view is showing routines.
  - `show_completed_tasks`: whether completed tasks should be displayed.
  - `completed_instance_ids`: instance ids allowed to remain visible in completed mode.
  - `never_visible_statuses`: statuses that should never appear in the visible grid.
  - `active_statuses`: statuses allowed in the active view.
- **Result:** `ActiveTaskGrid`
- **Description:** Builds the active-grid object for the My Tasks page, ensuring that the visible ordering used for rendering is the same ordering used by guided task-opening logic.

### `build_task_search_active_grid`

- **Module:** `app/application/use_cases/active_task_grid.py`
- **Signature:** `build_task_search_active_grid(tasks_df, adaptation, *, search_query, include_routines, include_stale, never_visible_statuses) -> ActiveTaskGrid`
- **Parameters:**
  - `tasks_df`: enriched tasks dataframe.
  - `adaptation`: current task adaptation, if any.
  - `search_query`: text query currently applied.
  - `include_routines`: whether routine tasks are included in the result.
  - `include_stale`: whether stale tasks may appear.
  - `never_visible_statuses`: statuses suppressed from the visible grid.
- **Result:** `ActiveTaskGrid`
- **Description:** Builds the active-grid object for the Task Search page so that search results remain a proper working surface for guided open behaviour.

### `build_subtasks_active_grid`

- **Module:** `app/application/use_cases/active_task_grid.py`
- **Signature:** `build_subtasks_active_grid(visible_subtasks_df, adaptation) -> ActiveTaskGrid`
- **Parameters:**
  - `visible_subtasks_df`: visible child-task dataframe for the selected parent.
  - `adaptation`: current task adaptation, if any.
- **Result:** `ActiveTaskGrid`
- **Description:** Builds an active grid for a subtask surface, ensuring that when the user is operating inside a child grid, proposals come from that child grid rather than from the root list.

### `get_next_open_candidate`

- **Module:** `app/application/use_cases/active_task_grid.py`
- **Signature:** `get_next_open_candidate(active_grid, *, offered_instance_ids=None) -> dict | None`
- **Parameters:**
  - `active_grid`: active grid business object that represents the current working surface.
  - `offered_instance_ids`: instance ids already proposed during the current guided chain.
- **Result:** `dict | None`
- **Description:** Returns the next candidate task that should be proposed for opening from the active grid. It respects the visible root ordering, expands compound rows to concrete workable children, skips already offered candidates, and returns `None` when no valid candidate remains.

### `dispatch_user_state_event`

- **Module:** `app/ui/main.py`
- **Signature:** `dispatch_user_state_event(event, *, declared_state=None, start_in_planner=False, target_state=None, event_payload=None, session_end_reason=None)`
- **Parameters:**
  - `event`: user-state event identifier to dispatch.
  - `declared_state`: optional arriving state used at login.
  - `start_in_planner`: whether the session should begin in `Planner`.
  - `target_state`: explicit target override for the transition.
  - `event_payload`: optional event payload dictionary.
  - `session_end_reason`: optional reason string for session summary generation.
- **Result:** `TransitionResult | None`
- **Description:** UI-facing entry point for the user state machine. It loads the current user context, calls the user domain model, applies the result, and returns the transition result for callers that need it.

### `apply_user_state_transition_result`

- **Module:** `app/ui/main.py`
- **Signature:** `apply_user_state_transition_result(result)`
- **Parameters:**
  - `result`: `TransitionResult` returned by the user state machine.
- **Result:** None
- **Description:** Interprets a user-state transition in UI terms. It synchronises cached profile state, controls planner and work timers, preserves or clears guided chains, triggers recovery clean-up, stores notices, and performs rerun-related preparation.

### `update_task_status`

- **Module:** `app/ui/main.py`
- **Signature:** `update_task_status(task_row, new_status, *, reopened_start_at=None, reopened_due_at=None, planner_open_target_state=None)`
- **Parameters:**
  - `task_row`: task instance row to update.
  - `new_status`: requested task status.
  - `reopened_start_at`: optional start timestamp for clone-based reopen flows.
  - `reopened_due_at`: optional due timestamp for clone-based reopen flows.
  - `planner_open_target_state`: optional execution-state target to use when opening from `Planner`.
- **Result:** `TaskTransitionResult`
- **Description:** UI-facing entry point for task status changes. It delegates the transition to the task state machine, refreshes grid-local runtime state, clears overlay artefacts when needed, and forwards any emitted task-to-user notifications into the user state machine.

### `notify_work_ended`

- **Module:** `app/ui/main.py`
- **Signature:** `notify_work_ended()`
- **Parameters:** None
- **Result:** None
- **Description:** Dispatches `WORK_ENDED_EVENT` when the current execution state is `Frozen` or `Engaged`. It is the bridge used when a work phase ends without necessarily completing the task.

### `request_task_completion_feedback`

- **Module:** `app/ui/main.py`
- **Signature:** `request_task_completion_feedback(task_row, source, **payload)`
- **Parameters:**
  - `task_row`: task instance row about to be completed.
  - `source`: UI source label for the request.
  - `payload`: extra completion-flow payload stored with the pending dialog request.
- **Result:** None
- **Description:** Queues the completion-feedback dialog in session state before the task completion transition is carried out.

### `complete_open_task_flow`

- **Module:** `app/ui/main.py`
- **Signature:** `complete_open_task_flow(context)`
- **Parameters:**
  - `context`: execution context dictionary for the open-task flow, including the task row, timer mode choices, duration, origin page, and optional planner target state.
- **Result:** None
- **Description:** Executes the selected task-opening flow after the user has confirmed the open operation. It updates the task status to `open`, starts the appropriate timer or support flow, clears dialog state, and reruns the UI.

### `guided_chain_has_next_candidate`

- **Module:** `app/ui/main.py`
- **Signature:** `guided_chain_has_next_candidate() -> bool`
- **Parameters:** None
- **Result:** `bool`
- **Description:** Rebuilds the current guided active grid and determines whether another candidate still exists for the active guided chain. It is used when deciding whether a `WORK_ENDED_EVENT` should preserve or close guided continuation.

### `get_current_guided_active_grid`

- **Module:** `app/ui/main.py`
- **Signature:** `get_current_guided_active_grid(tasks_df, adaptation)`
- **Parameters:**
  - `tasks_df`: current tasks dataframe.
  - `adaptation`: current task adaptation.
- **Result:** `ActiveTaskGrid`
- **Description:** Reconstructs the active grid that currently owns guided-open decisions, based on the current page and its live filter state.

### `suspend_authenticated_work_session`

- **Module:** `app/ui/main.py`
- **Signature:** `suspend_authenticated_work_session(reason, result)`
- **Parameters:**
  - `reason`: suspension reason string.
  - `result`: `TransitionResult` that triggered suspension.
- **Result:** None
- **Description:** Performs recoverable session suspension. It stores the resume marker, clears in-memory UI state, preserves the authenticated browser session, and reruns the app shell.

### `get_resume_state_from_transition_result`

- **Module:** `app/ui/main.py`
- **Signature:** `get_resume_state_from_transition_result(result)`
- **Parameters:**
  - `result`: `TransitionResult` from a transition that moved the user towards `Recovery`.
- **Result:** `str`
- **Description:** Extracts the execution state that should be restored during a recoverable resume flow, usually from `memory_state`.

### `get_logged_user_model`

- **Module:** `app/ui/main.py`
- **Signature:** `get_logged_user_model()`
- **Parameters:** None
- **Result:** `LoggedUserModel`
- **Description:** Creates the user-domain model wrapper bound to the current Supabase client and Streamlit session store.

### `get_logged_task_model`

- **Module:** `app/ui/main.py`
- **Signature:** `get_logged_task_model()`
- **Parameters:** None
- **Result:** `LoggedTaskModel`
- **Description:** Creates the task-domain model wrapper bound to the current Supabase client.

---

## Appendix B: Event Ownership Summary

| Event | Origin | First Consumer | Possible Follow-On |
| --- | --- | --- | --- |
| `LOGIN_DECLARED_EVENT` | UI welcome flow | User FSM | Planner timer start; guided open proposal |
| `TASK_OPENED_EVENT` | Task FSM via UI bridge | User FSM | Return from `Planner` to execution state |
| `TASK_COMPLETED_EVENT` | Task FSM via UI bridge | User FSM | Momentum transition; parent completion checks |
| `WORK_ENDED_EVENT` | UI work-cycle flow | User FSM | Return to `Planner`; guided continuation decision |
| `TASK_REJECTED_EVENT` | UI guided-open rejection flow | User FSM | Rejection counting; return to `Planner` |
| `AUTO_OPEN_CANDIDATES_EXHAUSTED_EVENT` | UI candidate-selection flow | User FSM | Guided chain termination |
| `PLANNER_TIMER_ELAPSED_EVENT` | Timer flow | User FSM | Reminder or move to `Recovery` |
| `LOGOUT_EVENT` | UI logout flow | User FSM | Recovery clean-up; end session |
