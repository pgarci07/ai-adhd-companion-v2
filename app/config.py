"""Application-level configuration constants.

Keep this module focused on product/runtime defaults that are shared or likely
to be reused. UI session-state keys and message copy should stay close to their
own modules.
"""

from __future__ import annotations

from datetime import date
import os
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = APP_ROOT.parent

# Prompt and log file locations for AI-generated messaging and diagnostics.
WELCOME_PROMPT_PATH = APP_ROOT / "application" / "prompts" / "welcome_message.txt"
LOGOUT_FAREWELL_PROMPT_PATH = APP_ROOT / "application" / "prompts" / "logout_farewell.txt"
OPENAI_LOG_PATH = PROJECT_ROOT / "logs" / "openai.log"
TIMER_LOG_PATH = PROJECT_ROOT / "logs" / "timer_ops.log"

# Runtime defaults for external services and guided timers.
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
CHUNK_TIMER_DEFAULT_SECONDS = int(2.5 * 60)
DEFAULT_CHUNK_MIN_FLOOR_MINUTES = 5
DEFAULT_SESSION_EXTENSION_TOLERANCE = 0.15
CHUNK_PERSONA_STATE_MODIFIERS = {
    "hyper-focused": {
        "Engaged": 1.2,
        "Frozen": 0.8,
    },
    "overwhelmed planner": {
        "Engaged": 0.8,
        "Frozen": 0.5,
    },
    "procrastinator": {
        "Engaged": 0.7,
        "Frozen": 0.3,
    },
}
POMODORO_SPRINT_TEST_MINUTES = 2
POMODORO_REST_MINUTES = 2
DEFAULT_REST_DURATION_MINUTES = POMODORO_REST_MINUTES
DEFAULT_PLANNER_TIMEOUT_MINUTES = 5
DEFAULT_MAX_CONTINUOUS_WORK_MINUTES = 90
WORK_TIMER_EXPIRY_STATE_NAME = "Planner"
PLANNER_TIMER_SOURCE_LABEL = "user_state_machine_planner"
# A Planner timeout does not destroy the Supabase auth cookie immediately.
# Instead, the app stores a recoverable work-session suspension marker so the
# same browser can resume without credentials for this many hours.
WORK_SESSION_RESUME_GRACE_HOURS = 8
# After this many hours in Recovery, the previous Frozen/Engaged state is no
# longer assumed to be accurate and the Welcome dialog asks the user again.
WORK_SESSION_STATE_REPROMPT_HOURS = 1

# Supported values for profile-level preference selectors.
SUPPORTED_LANGUAGES = ("english",)
SUPPORTED_TIME_MANAGEMENT_METHODS = ("Pomodoro",)
VALID_FIRST_DAY_OF_WEEK_VALUES = ("SU", "MO", "TU", "WE", "TH", "FR", "SA")

# Task-grid visibility and default filter policies.
GRID_ACTIVE_STATUSES = {"ready", "open", "asleep", "debt"}
GRID_NEVER_VISIBLE_STATUSES = {"stale"}
DEFAULT_COMPLETED_TASK_LOOKBACK_DAYS = 7

# Task editing date bounds.
EDIT_TASK_MIN_DATE = date(1900, 1, 1)
EDIT_TASK_MAX_DATE = date(2100, 12, 31)

# UI timing/report defaults that are product behavior rather than CSS.
OPEN_TASK_GUIDANCE_MODAL_SECONDS = 15
REST_MESSAGE_MODAL_SECONDS = 15
STATE_TIME_RECENT_SESSIONS_LIMIT = 5
