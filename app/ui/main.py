import sys
import os
import json
import base64
import logging
from pathlib import Path

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import streamlit as st
import pandas as pd
from datetime import datetime, time, timedelta, date
import pytz # Recomendado para manejo de zonas horarias
from st_aggrid import AgGrid, GridOptionsBuilder
from app.ui import body_doubling
from app.ui.state.timers import (
    INACTIVITY_TIMER_KEY,
    WORK_TIMER_KEY,
    get_inactivity_timer,
    get_work_timer,
)
from app.application.use_cases.personas_catalog import PERSONAS, supabase

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

try:
    from streamlit_cookies_manager import EncryptedCookieManager
except ImportError:
    EncryptedCookieManager = None

LOOKUP_TABLES = (
    "dim_task_sizes",
    "dim_task_consequences",
    "dim_task_frictions",
)
INITIAL_SESSION_STATE_NAMES = {"Frozen", "Engaged"}
USER_SELECTABLE_STATE_NAMES = {"Frozen", "Engaged", "Recovery"}
GUIDED_TASK_PERSONA_NAME = "Overwhelmed Planner"
GUIDED_TASK_STATE_NAME = "Frozen"
AUTH_COOKIE_KEY = "supabase_auth_session"
AUTH_SESSION_STATE_KEY = "supabase_auth_session_payload"
AUTH_REFRESH_MARGIN_SECONDS = 60
REGISTRATION_WELCOME_MESSAGE_KEY = "registration_welcome_message"
OPEN_TASK_GUIDANCE_MESSAGE_KEY = "open_task_guidance_message"
OPEN_TASK_GUIDANCE_EXPIRES_AT_KEY = "open_task_guidance_expires_at"
OPEN_TASK_DIALOG_TASK_KEY = "open_task_dialog_task"
OPEN_TASK_PENDING_CONTEXT_KEY = "open_task_pending_context"
OPEN_TASK_GUIDANCE_MODAL_SECONDS = 15
SPRINT_REVIEW_PENDING_KEY = "sprint_review_pending"
REST_MESSAGE_KEY = "rest_message"
REST_MESSAGE_EXPIRES_AT_KEY = "rest_message_expires_at"
REST_MESSAGE_MODAL_SECONDS = 15
WELCOME_PROMPT_PATH = Path(__file__).resolve().parents[1] / "application" / "prompts" / "welcome_message.txt"
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
OPENAI_LOG_PATH = Path(__file__).resolve().parents[2] / "logs" / "openai.log"
TIMER_LOG_PATH = Path(__file__).resolve().parents[2] / "logs" / "timers.log"
INACTIVITY_LOGOUT_SECONDS = 15 * 60
WORK_TIMER_SECONDS = 20 * 60
WORK_TIMER_EXPIRY_STATE_NAME = "Distracted"

# Inicializo el user id en session state para evitar errores
if "user_id" not in st.session_state:
    st.session_state["user_id"] = None
if "show_welcome_dialog" not in st.session_state:
    st.session_state["show_welcome_dialog"] = False
if "session_expected_work_time" not in st.session_state:
    st.session_state["session_expected_work_time"] = None
if "current_page" not in st.session_state:
    st.session_state["current_page"] = "tasks"
if "tasks_grid_version" not in st.session_state:
    st.session_state["tasks_grid_version"] = 0

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="AI-ADHD", layout="wide")

if EncryptedCookieManager:
    cookies = EncryptedCookieManager(
        prefix="ai-adhd-companion/",
        password=os.environ.get("COOKIES_PASSWORD", os.environ.get("SUPABASE_KEY", "ai-adhd-dev-cookie")),
    )
    if not cookies.ready():
        st.stop()
else:
    cookies = None

def to_supabase_date(date_value):
    if not date_value:
        return None
    return date_value.isoformat()


def combine_date_and_time(selected_date, selected_time):
    combined = datetime.combine(selected_date, selected_time)
    return combined.replace(tzinfo=pytz.UTC).isoformat()


def combine_date_and_time_value(selected_date, selected_time):
    combined = datetime.combine(selected_date, selected_time)
    return combined.replace(tzinfo=pytz.UTC)


def get_next_available_time(base_datetime=None):
    current_dt = base_datetime.astimezone(pytz.UTC) if base_datetime else datetime.now(pytz.UTC)
    next_hour = current_dt.replace(minute=0, second=0, microsecond=0)
    if current_dt.minute > 0 or current_dt.second > 0 or current_dt.microsecond > 0:
        next_hour += timedelta(hours=1)
    return next_hour.time().replace(tzinfo=None)


def build_rrule(
    frequency,
    interval_value,
    byweekday_values=None,
    until_value=None,
):
    parts = [f"FREQ={frequency}", f"INTERVAL={interval_value}"]

    if byweekday_values:
        parts.append(f"BYDAY={','.join(byweekday_values)}")

    if until_value:
        parts.append(f"UNTIL={until_value.strftime('%Y%m%dT%H%M%SZ')}")

    return ";".join(parts)


def format_task_datetime(value):
    if not value:
        return "-"

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(pytz.UTC).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return str(value)


def is_expired_jwt_error(error):
    error_text = str(error)
    return "JWT expired" in error_text or "PGRST303" in error_text


def reset_auth_state():
    clear_auth_cookie()
    st.session_state["user_id"] = None
    st.session_state.pop(AUTH_SESSION_STATE_KEY, None)
    st.session_state.pop("user_profile", None)
    st.session_state.pop("lookup_cache", None)
    st.session_state.pop("states_cache", None)
    st.session_state.pop("all_states_cache", None)
    st.session_state.pop(REGISTRATION_WELCOME_MESSAGE_KEY, None)
    st.session_state.pop(OPEN_TASK_GUIDANCE_MESSAGE_KEY, None)
    st.session_state.pop(OPEN_TASK_GUIDANCE_EXPIRES_AT_KEY, None)
    st.session_state.pop(OPEN_TASK_DIALOG_TASK_KEY, None)
    st.session_state.pop(OPEN_TASK_PENDING_CONTEXT_KEY, None)
    st.session_state.pop(SPRINT_REVIEW_PENDING_KEY, None)
    st.session_state.pop(REST_MESSAGE_KEY, None)
    st.session_state.pop(REST_MESSAGE_EXPIRES_AT_KEY, None)
    st.session_state.pop(body_doubling.BODY_DOUBLING_FLOW_KEY, None)
    st.session_state.pop(body_doubling.BODY_DOUBLING_SCOPE_DIALOG_KEY, None)
    st.session_state.pop(body_doubling.BODY_DOUBLING_REVIEW_DIALOG_KEY, None)
    st.session_state.pop(body_doubling.BODY_DOUBLING_EXTRA_STEP_DIALOG_KEY, None)
    st.session_state.pop(INACTIVITY_TIMER_KEY, None)
    st.session_state.pop(WORK_TIMER_KEY, None)
    st.session_state["session_expected_work_time"] = None
    st.session_state["show_welcome_dialog"] = False
    st.session_state["current_page"] = "tasks"
    st.session_state["tasks_grid_version"] = 0


def clear_auth_cookie():
    if cookies is None:
        return

    try:
        if AUTH_COOKIE_KEY in cookies:
            del cookies[AUTH_COOKIE_KEY]
            cookies.save()
    except Exception:
        pass


def decode_jwt_expiry(access_token):
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
    expires_at = getattr(session, "expires_at", None)
    if expires_at is not None:
        return int(expires_at)

    expires_in = getattr(session, "expires_in", None)
    if expires_in is not None:
        return int(datetime.now(pytz.UTC).timestamp()) + int(expires_in)

    return decode_jwt_expiry(getattr(session, "access_token", None))


def get_auth_payload_from_response(auth_response):
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
    if expires_at is None:
        return True

    try:
        expires_at = int(expires_at)
    except (TypeError, ValueError):
        return True

    now_timestamp = int(datetime.now(pytz.UTC).timestamp())
    return expires_at <= now_timestamp + AUTH_REFRESH_MARGIN_SECONDS


def refresh_auth_session(refresh_token):
    if not refresh_token:
        reset_auth_state()
        return None

    try:
        auth_response = supabase.auth.refresh_session(refresh_token)
    except Exception:
        reset_auth_state()
        return None

    user = getattr(auth_response, "user", None)
    session = getattr(auth_response, "session", None)
    if not user or not session:
        reset_auth_state()
        return None

    save_auth_cookie(auth_response)
    st.session_state["user_id"] = user.id
    return auth_response


def restore_auth_session_from_cookie():
    if cookies is None or st.session_state.get("user_id"):
        return

    raw_session = load_auth_cookie_payload()
    if not raw_session:
        return

    access_token = raw_session.get("access_token")
    refresh_token = raw_session.get("refresh_token")
    if not access_token or not refresh_token:
        reset_auth_state()
        return

    expires_at = raw_session.get("expires_at") or decode_jwt_expiry(access_token)
    if should_refresh_access_token(expires_at):
        refresh_auth_session(refresh_token)
        return

    try:
        auth_response = supabase.auth.set_session(access_token, refresh_token)
    except Exception:
        refresh_auth_session(refresh_token)
        return

    user = getattr(auth_response, "user", None)
    if not user:
        reset_auth_state()
        return

    save_auth_cookie(auth_response)
    st.session_state["user_id"] = user.id


def ensure_fresh_auth_session():
    raw_session = load_auth_cookie_payload()
    if not raw_session:
        return not st.session_state.get("user_id")

    refresh_token = raw_session.get("refresh_token")
    access_token = raw_session.get("access_token")
    expires_at = raw_session.get("expires_at") or decode_jwt_expiry(access_token)

    if should_refresh_access_token(expires_at):
        return refresh_auth_session(refresh_token) is not None

    return True


def handle_api_exception(error, fallback_message="Could not complete the request."):
    if is_expired_jwt_error(error):
        try:
            supabase.auth.sign_out()
        except Exception:
            pass

        reset_auth_state()
        st.error("Your session expired. Please sign in again.")
        st.rerun()
        return True

    st.error(fallback_message)
    return False


def expire_inactivity_session(timer=None):
    st.warning("Session closed automatically after 15 minutes of inactivity.")
    logout()


def start_inactivity_logout_timer():
    append_timer_log_line("request_start | timer=inactivity_timer source=start_inactivity_logout_timer")
    timer = get_inactivity_timer(st.session_state)
    timer.start(
        duration=INACTIVITY_LOGOUT_SECONDS,
        on_expiry=expire_inactivity_session,
    )


def disable_inactivity_logout_timer():
    append_timer_log_line("request_disable | timer=inactivity_timer source=disable_inactivity_logout_timer")
    get_inactivity_timer(st.session_state).disable()


def tick_inactivity_logout_timer(*, user_interaction=False):
    if not st.session_state.get("user_id"):
        return

    timer = get_inactivity_timer(st.session_state)
    timer.configure(
        duration=INACTIVITY_LOGOUT_SECONDS,
        on_expiry=expire_inactivity_session,
    )

    timer.tick(user_interaction=user_interaction)


def get_state_id_by_name(state_name):
    response = (
        supabase.table("states")
        .select("id")
        .eq("name", state_name)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    return rows[0]["id"] if rows else None


def set_current_user_state_by_name(state_name):
    state_id = get_state_id_by_name(state_name)
    if state_id is None:
        raise ValueError(f"State not found: {state_name}")

    (
        supabase.table("profiles")
        .update({"state_id": state_id})
        .eq("id", st.session_state["user_id"])
        .execute()
    )

    if "user_profile" in st.session_state:
        st.session_state["user_profile"] = {
            **st.session_state["user_profile"],
            "state_id": state_id,
        }


def expire_work_timer(timer=None):
    if not st.session_state.get("user_id"):
        return

    try:
        set_current_user_state_by_name(WORK_TIMER_EXPIRY_STATE_NAME)
        st.warning("Work timer finished. Your state has been changed to Distracted.")
        st.rerun()
    except Exception as error:
        handle_api_exception(error, f"Could not update state after work timer expiry: {error}")


def start_work_timer():
    append_timer_log_line("request_start | timer=work_timer source=start_work_timer")
    timer = get_work_timer(st.session_state)
    timer.start(
        duration=WORK_TIMER_SECONDS,
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


def clear_expired_rest_message():
    expires_at = st.session_state.get(REST_MESSAGE_EXPIRES_AT_KEY)
    if expires_at is None:
        return

    if datetime.now(pytz.UTC).timestamp() >= float(expires_at):
        clear_rest_message()
        st.rerun()


def eoSprint(timer=None):
    st.session_state[SPRINT_REVIEW_PENDING_KEY] = True
    disable_work_timer()
    st.rerun()


def eoChunk(timer=None):
    st.info("end of chunk")
    disable_work_timer()


def eoRest(timer=None):
    set_rest_message("Rest is over.")
    disable_work_timer()
    st.rerun()


def reset_work_timer_for_open_task(use_pomodoro_sprints):
    user_preferences = get_user_preferences()
    duration_minutes = (
        int(user_preferences.get("sprint", 30))
        if use_pomodoro_sprints
        else 29
    )
    callback = eoSprint if use_pomodoro_sprints else eoChunk
    append_timer_log_line(
        (
            "request_reset | timer=work_timer source=reset_work_timer_for_open_task "
            f"use_pomodoro_sprints={use_pomodoro_sprints} "
            f"duration_minutes={duration_minutes} callback={callback.__name__}"
        )
    )

    get_work_timer(st.session_state).reset(
        duration=duration_minutes * 60,
        on_expiry=callback,
    )


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
    ensure_lookup_cache()
    table_cache = st.session_state["lookup_cache"].get(table_name, {})
    return table_cache.get("options", [])


def get_lookup_weights(table_name):
    ensure_lookup_cache()
    table_cache = st.session_state["lookup_cache"].get(table_name, {})
    return table_cache.get("weights", {})


def refresh_lookup_cache():
    load_lookup_cache()


def load_states_cache():
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
    ensure_states_cache()
    return st.session_state["states_cache"]


def get_all_states_options():
    ensure_all_states_cache()
    return st.session_state["all_states_cache"]


def get_initial_session_state_options():
    return [
        state
        for state in get_states_options()
        if state.get("name") in INITIAL_SESSION_STATE_NAMES
    ]


def extract_first_name(full_name):
    if not full_name:
        return None

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
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with TIMER_LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(f"{timestamp} INFO {message}\n")


def get_body_doubling_services():
    return body_doubling.BodyDoublingServices(
        get_user_preferences=get_user_preferences,
        update_task_status=update_task_status,
        log_openai_event=log_openai_event,
        get_openai_logger=get_openai_logger,
        extract_openai_text=extract_openai_text,
        openai_class=OpenAI,
        openai_model=OPENAI_MODEL,
    )


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
        return (
            "Write a warm, concise Spanish welcome message for a newly registered "
            "AI-ADHD user. Be encouraging and practical."
        )


def get_fallback_registration_welcome(first_name):
    display_name = first_name or "there"
    return (
        f"Welcome, {display_name}. I am glad you are here: we will help you turn "
        "your tasks into clearer, smaller, more manageable steps.\n\n"
        "Before we begin, tell us how you are arriving to this session so we can "
        "adapt the plan to your energy and the time you have available."
    )


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
        return get_fallback_registration_welcome(first_name)

    if not api_key:
        log_openai_event(
            logging.WARNING,
            "OPENAI_API_KEY is not configured; using fallback welcome message.",
            model=OPENAI_MODEL,
            first_name=first_name,
            age=age,
            persona_description=persona_description,
        )
        return get_fallback_registration_welcome(first_name)

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
            return get_fallback_registration_welcome(first_name)

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
        return get_fallback_registration_welcome(first_name)


def get_fallback_open_task_guidance(task_title, use_pomodoro_sprints, use_body_doubling):
    timing_text = (
        "Work with the sprint timer: focus on just the next small step until it rings."
        if use_pomodoro_sprints
        else "Use this work chunk to make steady progress without worrying about finishing everything."
    )
    body_doubling_text = (
        "If body-doubling helps, keep someone nearby or visible and let their presence anchor you."
        if use_body_doubling
        else "You can do this solo: keep the task visible and remove one distraction before starting."
    )
    return (
        f"Task opened: {task_title}.\n\n"
        f"{timing_text} {body_doubling_text}"
    )


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
        return get_fallback_open_task_guidance(
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
        return get_fallback_open_task_guidance(
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
            return get_fallback_open_task_guidance(
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
        return get_fallback_open_task_guidance(
            task_title,
            use_pomodoro_sprints,
            use_body_doubling,
        )


def load_user_profile_cache():
    default_profile = {
        "full_name": None,
        "first_name": None,
        "persona_id": None,
        "role": "user",
        "born": None,
        "age": None,
        "state_id": None,
        "preferences": {
            "average_session_time": 120,
            "custom_sizes": [15, 30, 60, 180, 720],
        },
    }
    user_id = st.session_state.get("user_id")

    if not user_id:
        st.session_state["user_profile"] = default_profile
        return default_profile

    try:
        response = (
            supabase.table("profiles")
            .select("full_name, role, born, persona_id, state_id, preferences")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
        profile = (response.data or [default_profile])[0]
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


def get_persona_name_by_id(persona_id):
    if not persona_id:
        return None

    persona = PERSONAS.get(persona_id)
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


def get_current_persona_name():
    user_profile = ensure_user_profile_cache()
    return get_persona_name_by_id(user_profile.get("persona_id"))


def get_current_state_name():
    user_profile = ensure_user_profile_cache()
    return get_state_name_by_id(user_profile.get("state_id"))


def get_effective_session_work_time():
    session_work_time = st.session_state.get("session_expected_work_time")
    if session_work_time is not None:
        return session_work_time

    return get_user_preferences().get("average_session_time", 120)


def save_user_profile_updates(preferences_updates=None, state_id=None):
    user_profile = ensure_user_profile_cache()
    current_preferences = user_profile.get("preferences", {})
    updated_preferences = {
        **current_preferences,
        **(preferences_updates or {}),
    }
    payload = {"preferences": updated_preferences}

    if state_id is not None:
        payload["state_id"] = state_id

    (
        supabase.table("profiles")
        .update(payload)
        .eq("id", st.session_state["user_id"])
        .execute()
    )

    refreshed_profile = {
        **user_profile,
        "preferences": updated_preferences,
    }
    if state_id is not None:
        refreshed_profile["state_id"] = state_id

    st.session_state["user_profile"] = refreshed_profile
    return refreshed_profile


def should_prompt_welcome_dialog():
    if not st.session_state.get("user_id"):
        return False

    if st.session_state.get("show_welcome_dialog"):
        return True

    user_profile = ensure_user_profile_cache()
    return user_profile.get("state_id") is None


ensure_lookup_cache()
ensure_states_cache()


def parse_task_datetime(value):
    if not value:
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(pytz.UTC)
    except ValueError:
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
    response = (
        supabase.table("task_instances")
        .select(
            "id, task_id, instance_number, parent_instance_id, start_date, due_date, "
            "status, "
            "tasks!inner(id, list_id, title, description, parent_task_id, rrule, "
            "is_active, is_routine, size_id, consequence_id, friction_id, is_adaptive)"
        )
        .order("due_date", desc=True)
        .execute()
    )

    rows = []
    for row in response.data or []:
        task_payload = row.get("tasks") or {}
        rows.append(
            {
                "instance_id": row.get("id"),
                "task_id": row.get("task_id"),
                "instance_number": row.get("instance_number"),
                "parent_instance_id": row.get("parent_instance_id"),
                "list_id": task_payload.get("list_id"),
                "title": task_payload.get("title", "Untitled"),
                "description": task_payload.get("description"),
                "start_date": row.get("start_date"),
                "due_date": row.get("due_date"),
                "status": row.get("status", "-"),
                "rrule": task_payload.get("rrule"),
                "is_active": task_payload.get("is_active"),
                "is_routine": task_payload.get("is_routine"),
                "size_id": task_payload.get("size_id"),
                "consequence_id": task_payload.get("consequence_id"),
                "friction_id": task_payload.get("friction_id"),
                "is_adaptive": task_payload.get("is_adaptive"),
                "parent_task_id": task_payload.get("parent_task_id"),
            }
        )

    return rows

def get_tasks_dataframe():
    rows = get_task_rows()
    dataframe = pd.DataFrame(rows)
    if dataframe.empty:
        return dataframe

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

    # --- map ids to weights ---
    dataframe["size_weight"] = dataframe["size_id"].map(size_weights)
    dataframe["consequence_weight"] = dataframe["consequence_id"].map(consequence_weights)
    dataframe["friction_weight"] = dataframe["friction_id"].map(friction_weights)
    dataframe["size_minutes"] = dataframe["size_weight"].map(size_to_time)

    # --- priority scores ---
    dataframe["Urgency"] = dataframe.apply(
        lambda row: get_urgency_score(
            row["due_date"],
            row["size_minutes"],
            average_session_time,
            session_expected_work_time,
            now_utc,
        ),
        axis=1,
    )

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
    dataframe["display_start_date"] = dataframe["start_date"].apply(format_task_datetime)
    dataframe["display_due_date"] = dataframe["due_date"].apply(format_task_datetime)
    task_title_map = dataframe.set_index("task_id")["title"].to_dict()
    task_routine_map = dataframe.set_index("task_id")["is_routine"].to_dict()
    dataframe["parent_title"] = dataframe["parent_task_id"].map(task_title_map)
    dataframe["parent_is_routine"] = dataframe["parent_task_id"].map(task_routine_map)
    dataframe["is_routine"] = (
        dataframe["is_routine"].fillna(False)
        | dataframe["parent_is_routine"].fillna(False)
    )
    dataframe["is_subtask"] = dataframe["parent_task_id"].notna()
    parent_task_ids = set(dataframe["parent_task_id"].dropna())
    dataframe["has_subtasks"] = dataframe["task_id"].isin(parent_task_ids)
    dataframe["task_type"] = dataframe["is_subtask"].apply(
        lambda is_subtask: "Subtask" if is_subtask else "Task"
    )
    dataframe["display_title"] = dataframe.apply(
        lambda row: (
            f"  ↳ {row['title']}"
            if row["is_subtask"]
            else row["title"]
        ),
        axis=1,
    )

    dataframe["selection_label"] = dataframe.apply(
        lambda row: (
            f"{row['task_type']}: {row['title']} | {row['priority_label']} | urgency {row['Urgency']} "
            f"| due {row['display_due_date']} | {row['status']}"
        ),
        axis=1,
    )

    # --- sort parent tasks by due date ascending and keep subtasks under their parent ---
    sorted_dataframe = dataframe.sort_values(
        by=["due_date", "Urgency"],
        ascending=[True, False]
    ).reset_index(drop=True)
    children_by_parent = {}
    ordered_indices = []

    for index, row in sorted_dataframe.iterrows():
        parent_task_id = row["parent_task_id"]
        if pd.isna(parent_task_id):
            ordered_indices.append(index)
        else:
            children_by_parent.setdefault(parent_task_id, []).append(index)

    final_indices = []

    def append_task_branch(row_index):
        final_indices.append(row_index)
        task_id = sorted_dataframe.iloc[row_index]["task_id"]
        for child_index in children_by_parent.get(task_id, []):
            append_task_branch(child_index)

    for parent_index in ordered_indices:
        append_task_branch(parent_index)

    remaining_indices = [
        index for index in sorted_dataframe.index
        if index not in final_indices
    ]
    final_indices.extend(remaining_indices)
    dataframe = sorted_dataframe.iloc[final_indices].reset_index(drop=True)

    return dataframe


def format_lookup_option(item):
    return f"{item['label']} - {item['self_describing']}"


def format_state_option(item):
    return f"{item['name']} - {item['self_describing']}"


def parse_datetime_value(value):
    parsed = parse_task_datetime(value)
    if parsed is None:
        return None
    return parsed.astimezone(pytz.UTC)


def parse_rrule_components(rrule_value):
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
    for index, option in enumerate(options):
        if option["id"] == selected_id:
            return index
    return None


def has_rrule_value(rrule_value):
    return bool(parse_rrule_components(rrule_value))


def validate_subtask_date_range(parent_task, start_at_value, due_at_value):
    if not parent_task:
        return True

    parent_start = parse_datetime_value(parent_task.get("start_date"))
    parent_due = parse_datetime_value(parent_task.get("due_date"))

    if not parent_start or not parent_due:
        st.error("Could not validate the subtask dates because the parent task date range is incomplete.")
        return False

    if start_at_value < parent_start or due_at_value > parent_due:
        st.error(
            "Subtask dates must be within the parent task range: "
            f"{format_task_datetime(parent_task.get('start_date'))} to "
            f"{format_task_datetime(parent_task.get('due_date'))}."
        )
        return False

    return True


def get_task_row_by_instance_id(instance_id):
    rows = get_task_rows()
    for row in rows:
        if row.get("instance_id") == instance_id:
            return row
    return None


def refresh_parent_task_for_subtask(parent_task):
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


def get_guided_task_context(tasks_df):
    persona_name = get_current_persona_name()
    state_name = get_current_state_name()

    if persona_name != GUIDED_TASK_PERSONA_NAME or state_name != GUIDED_TASK_STATE_NAME:
        return None

    if tasks_df.empty:
        return None

    candidate_df = tasks_df[
        (~tasks_df["is_routine"])
        & (~tasks_df["has_subtasks"])
        & (tasks_df["status"].isin(["ready", "asleep", "open"]))
    ].copy()

    if candidate_df.empty:
        return None

    open_candidates = candidate_df[candidate_df["status"] == "open"]
    if not open_candidates.empty:
        chosen_task = open_candidates.sort_values(
            by=["Urgency", "WSUB", "due_date"],
            ascending=[False, True, True],
        ).iloc[0]
    else:
        chosen_task = candidate_df.sort_values(
            by=["WSUB", "Urgency", "due_date"],
            ascending=[True, False, True],
        ).iloc[0]

    reason = (
        "Guided mode is active for Overwhelmed Planner + Frozen. "
        "The app is narrowing the choice to one concrete next task: "
        "lowest start-up friction first, with urgency used as the tie-breaker."
    )

    return {
        "persona_name": persona_name,
        "state_name": state_name,
        "task": chosen_task.to_dict(),
        "reason": reason,
    }


def get_aggrid_selected_row(grid_response):
    selected_rows = None

    if hasattr(grid_response, "selected_rows"):
        selected_rows = grid_response.selected_rows
    elif isinstance(grid_response, dict):
        selected_rows = grid_response.get("selected_rows")

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


def update_task_series(task_row, task_payload, start_at_value, due_at_value):
    supabase.rpc(
        "update_task_series_from_instance",
        {
            "p_task_id": task_row["task_id"],
            "p_instance_id": task_row["instance_id"],
            "p_list_id": task_payload["list_id"],
            "p_title": task_payload["title"],
            "p_description": task_payload["description"],
            "p_rrule": task_payload["rrule"],
            "p_size_id": task_payload["size_id"],
            "p_consequence_id": task_payload["consequence_id"],
            "p_friction_id": task_payload["friction_id"],
            "p_new_start_date": start_at_value.isoformat(),
            "p_new_due_date": due_at_value.isoformat(),
        },
    ).execute()


@st.dialog("Nueva tarea")
def new_task_form(parent_task=None):
    user_lists = get_user_lists()
    list_options = {item["name"]: item["id"] for item in user_lists}
    list_names = list(list_options.keys())
    size_options = get_lookup_options("dim_task_sizes")
    consequence_options = get_lookup_options("dim_task_consequences")
    friction_options = get_lookup_options("dim_task_frictions")

    if "new_task_start_date" not in st.session_state:
        st.session_state["new_task_start_date"] = datetime.now(pytz.UTC).date()
    if "new_task_due_date" not in st.session_state:
        st.session_state["new_task_due_date"] = st.session_state["new_task_start_date"]
    if "new_task_last_start_date" not in st.session_state:
        st.session_state["new_task_last_start_date"] = st.session_state["new_task_start_date"]
    if "new_task_start_time" not in st.session_state:
        st.session_state["new_task_start_time"] = get_next_available_time()
    if "new_task_due_time" not in st.session_state:
        st.session_state["new_task_due_time"] = time(17, 0)

    parent_task_id = parent_task["task_id"] if parent_task else None
    parent_instance_number = parent_task["instance_number"] if parent_task else None

    if parent_task:
        st.caption(f"Creating a subtask for: {parent_task['title']}")

    title = st.text_input("Title")
    description = st.text_area("Description")
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
        selected_list_name = st.selectbox("List", options=list_names)
    else:
        selected_list_name = None
        st.info("This user does not have any available list yet.")
    start_date = st.date_input(
        "Start date",
        key="new_task_start_date",
    )
    if st.session_state["new_task_start_date"] != st.session_state["new_task_last_start_date"]:
        st.session_state["new_task_due_date"] = st.session_state["new_task_start_date"]
        st.session_state["new_task_last_start_date"] = st.session_state["new_task_start_date"]

    start_time = st.time_input("Start time", key="new_task_start_time")
    due_date = st.date_input("Due date", key="new_task_due_date")
    due_time = st.time_input("Due time", key="new_task_due_time")
    selected_size = st.selectbox(
        "Task size",
        options=size_options,
        index=None,
        placeholder="Select a size",
        format_func=format_lookup_option,
    )
    selected_consequence = st.selectbox(
        "Consequence",
        options=consequence_options,
        index=None,
        placeholder="Select a consequence",
        format_func=format_lookup_option,
    )
    selected_friction = st.selectbox(
        "Friction",
        options=friction_options,
        index=None,
        placeholder="Select a friction",
        format_func=format_lookup_option,
    )
    is_recurrent = False
    if parent_task_id:
        st.caption("Subtasks cannot be recurrent.")
    else:
        is_recurrent = st.checkbox("Recurrent task")

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
        )
        recurrence_interval = st.number_input(
            "Repeat every",
            min_value=1,
            value=1,
            step=1,
        )

        if recurrence_frequency == "WEEKLY":
            selected_weekdays = st.multiselect(
                "Week days",
                options=list(weekday_options.keys()),
                default=[start_date.strftime("%A")],
            )

        has_end_date = st.checkbox("Set recurrence end date")
        if has_end_date:
            recurrence_end_date = st.date_input(
                "Recurrence end date",
                value=due_date,
                min_value=start_date,
            )

    if st.button("Create task", type="primary", use_container_width=True):
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

            start_at_value = combine_date_and_time_value(start_date, start_time)
            due_at_value = combine_date_and_time_value(due_date, due_time)
            if due_at_value < start_at_value:
                st.error("Due date must be later than or equal to start date.")
                return

            if not validate_subtask_date_range(current_parent_task, start_at_value, due_at_value):
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

            start_at = start_at_value.isoformat()
            due_at = due_at_value.isoformat()

            supabase.rpc(
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

            st.success("Task created successfully.")
            st.rerun()
        except Exception as e:
            st.error(f"Error creating task: {e}")


@st.dialog("Editar tarea")
def edit_task_form(task_row):
    if task_row["status"] in {"completed", "archived"}:
        st.error("Completed or archived tasks cannot be edited.")
        return

    user_lists = get_user_lists()
    list_options = {item["name"]: item["id"] for item in user_lists}
    list_names = list(list_options.keys())
    size_options = get_lookup_options("dim_task_sizes")
    consequence_options = get_lookup_options("dim_task_consequences")
    friction_options = get_lookup_options("dim_task_frictions")
    parsed_start = parse_datetime_value(task_row["start_date"]) or datetime.now(pytz.UTC)
    parsed_due = parse_datetime_value(task_row["due_date"]) or parsed_start
    today_utc = datetime.now(pytz.UTC).date()
    initial_start_date = max(parsed_start.date(), today_utc)
    initial_due_date = max(parsed_due.date(), initial_start_date)
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

    is_series_task = has_rrule_value(task_row.get("rrule"))
    apply_scope = "Single task"
    if is_series_task:
        apply_scope = st.radio(
            "Apply changes to",
            options=["Only this occurrence", "This and future occurrences"],
            horizontal=True,
        )
        if apply_scope == "Only this occurrence":
            st.info(
                "This option only updates the selected occurrence dates. "
                "Task template fields stay unchanged because they belong to the whole series."
            )
        else:
            st.info(
                "This option updates the task template and shifts future open occurrences. "
                "Completed, archived, or exception instances are left untouched."
            )
    series_fields_disabled = is_series_task and apply_scope == "Only this occurrence"

    current_list_name = next(
        (name for name, list_id in list_options.items() if list_id == task_row["list_id"]),
        None,
    )
    title = st.text_input("Title", value=task_row["title"], disabled=series_fields_disabled)
    description = st.text_area(
        "Description",
        value=task_row["description"] or "",
        disabled=series_fields_disabled,
    )

    if current_list_name and list_names:
        default_list_index = list_names.index(current_list_name)
        selected_list_name = st.selectbox(
            "List",
            options=list_names,
            index=default_list_index,
            disabled=series_fields_disabled,
        )
    elif current_list_name:
        selected_list_name = current_list_name
        st.text_input("List", value=current_list_name, disabled=True)
    else:
        selected_list_name = None
        st.info("This user does not have any available list yet.")

    start_date = st.date_input("Start date", value=initial_start_date, min_value=today_utc)
    start_time = st.time_input("Start time", value=parsed_start.time().replace(tzinfo=None))
    due_date = st.date_input("Due date", value=initial_due_date, min_value=start_date)
    due_time = st.time_input("Due time", value=parsed_due.time().replace(tzinfo=None))
    selected_size = st.selectbox(
        "Task size",
        options=size_options,
        index=get_option_index(size_options, task_row["size_id"]),
        format_func=format_lookup_option,
        disabled=series_fields_disabled,
    )
    selected_consequence = st.selectbox(
        "Consequence",
        options=consequence_options,
        index=get_option_index(consequence_options, task_row["consequence_id"]),
        format_func=format_lookup_option,
        disabled=series_fields_disabled,
    )
    selected_friction = st.selectbox(
        "Friction",
        options=friction_options,
        index=get_option_index(friction_options, task_row["friction_id"]),
        format_func=format_lookup_option,
        disabled=series_fields_disabled,
    )

    if task_row["parent_task_id"]:
        st.caption("Subtasks cannot be recurrent.")
        is_recurrent = False
    elif series_fields_disabled:
        st.caption("Recurrence settings can only be changed for this and future occurrences.")
    else:
        is_recurrent = st.checkbox("Recurrent task", value=is_recurrent)

    if is_recurrent and not series_fields_disabled:
        frequency_options = ["DAILY", "WEEKLY", "MONTHLY"]
        recurrence_frequency = st.selectbox(
            "Frequency",
            options=frequency_options,
            index=frequency_options.index(recurrence_frequency) if recurrence_frequency in frequency_options else 0,
        )
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
            "Set recurrence end date",
            value=recurrence_end_date is not None,
        )
        if has_end_date:
            recurrence_end_date = st.date_input(
                "Recurrence end date",
                value=initial_recurrence_end_date or due_date,
                min_value=start_date,
            )
        else:
            recurrence_end_date = None

    if st.button("Save task changes", type="primary", use_container_width=True):
        try:
            start_at_value = combine_date_and_time_value(start_date, start_time)
            due_at_value = combine_date_and_time_value(due_date, due_time)
            if due_at_value < start_at_value:
                st.error("Due date must be later than or equal to start date.")
                return

            if is_series_task and apply_scope == "Only this occurrence":
                update_single_task_instance(
                    task_row=task_row,
                    start_at_value=start_at_value,
                    due_at_value=due_at_value,
                    mark_as_exception=True,
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

                recurrence_until = None
                if recurrence_end_date:
                    recurrence_until = combine_date_and_time_value(recurrence_end_date, due_time)
                    if recurrence_until < start_at_value:
                        st.error("Recurrence end date must be later than the start date.")
                        return

                rrule_value = None
                if is_recurrent:
                    rrule_value = build_rrule(
                        frequency=recurrence_frequency,
                        interval_value=int(recurrence_interval),
                        byweekday_values=[
                            weekday_options[day_name] for day_name in selected_weekdays
                        ],
                        until_value=recurrence_until,
                    )

                task_payload = {
                    "list_id": list_id,
                    "title": title.strip(),
                    "description": description.strip() or None,
                    "rrule": rrule_value,
                    "size_id": size_id,
                    "consequence_id": consequence_id,
                    "friction_id": friction_id,
                }

                if is_series_task and apply_scope == "This and future occurrences":
                    update_task_series(
                        task_row=task_row,
                        task_payload=task_payload,
                        start_at_value=start_at_value,
                        due_at_value=due_at_value,
                    )
                else:
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


@st.dialog("Delete task")
def delete_task_dialog(task_row):
    try:
        context = get_delete_task_context(task_row)
    except Exception as e:
        handle_api_exception(e, f"Could not inspect delete impact: {e}")
        return

    is_recurring = bool(context.get("is_recurring"))
    has_subtasks = bool(context.get("has_subtasks"))
    allow_all = bool(context.get("allow_all"))
    all_worthy_count = int(context.get("all_worthy_count", 0) or 0)
    current_family_worthy = bool(context.get("current_family_worthy"))

    st.write(f"Delete target: **{task_row['title']}**")

    delete_scope = "current"
    if is_recurring:
        scope_options = {
            "Current one": "current",
            "Future ones (including current)": "future",
        }
        if allow_all:
            scope_options["All"] = "all"

        selected_scope_label = st.radio(
            "Choose what to delete",
            options=list(scope_options.keys()),
        )
        delete_scope = scope_options[selected_scope_label]

    if delete_scope == "current" and current_family_worthy:
        st.warning(
            "This selected occurrence has valuable completed data"
            + (" in itself or in its subtasks." if has_subtasks else ".")
        )

    keep_worthy = False
    if delete_scope == "all" and all_worthy_count > 0:
        st.warning(
            f"There are {all_worthy_count} valuable completed occurrence(s) in this series."
        )
        keep_choice = st.radio(
            "What do you want to do with those valuable occurrences?",
            options=["Keep them", "Delete everything"],
        )
        keep_worthy = keep_choice == "Keep them"

    confirm_message = {
        "current": "Delete the selected occurrence",
        "future": "Delete current and future occurrences",
        "all": "Delete all selected scope",
    }[delete_scope]
    st.caption("This action can affect subtasks automatically through database cascades.")

    if st.button(confirm_message, type="primary", use_container_width=True):
        try:
            supabase.rpc(
                "delete_task_by_policy",
                {
                    "p_task_id": task_row["task_id"],
                    "p_instance_id": task_row["instance_id"],
                    "p_scope": delete_scope,
                    "p_keep_worthy": keep_worthy,
                },
            ).execute()
            st.success("Task deleted successfully.")
            st.rerun()
        except Exception as e:
            handle_api_exception(e, f"Could not delete task: {e}")

def update_task_status(task_row, new_status):
    supabase.table("task_instances").update(
        {"status": new_status}
    ).eq("id", task_row["instance_id"]).execute()
    st.session_state["tasks_grid_version"] += 1


def get_open_task_row(exclude_instance_id=None):
    for row in get_task_rows():
        if row.get("status") != "open":
            continue
        if exclude_instance_id and row.get("instance_id") == exclude_instance_id:
            continue
        return row
    return None


def clear_open_task_dialog_state():
    st.session_state.pop(OPEN_TASK_DIALOG_TASK_KEY, None)
    st.session_state.pop(OPEN_TASK_PENDING_CONTEXT_KEY, None)


def build_open_task_context(task_row, pomodoro_choice, body_doubling_choice):
    use_pomodoro_sprints = pomodoro_choice == "Yes"
    use_body_doubling = body_doubling_choice == "Yes"
    duration_minutes = (
        int(get_user_preferences().get("sprint", 30))
        if use_pomodoro_sprints
        else 29
    )
    return {
        "task_row": task_row,
        "use_pomodoro_sprints": use_pomodoro_sprints,
        "use_body_doubling": use_body_doubling,
        "duration_minutes": duration_minutes,
    }


def complete_open_task_flow(context):
    task_row = context["task_row"]
    use_pomodoro_sprints = context["use_pomodoro_sprints"]
    use_body_doubling = context["use_body_doubling"]
    duration_minutes = context["duration_minutes"]

    st.session_state["use_body_doubling"] = use_body_doubling
    update_task_status(task_row, "open")
    reset_work_timer_for_open_task(use_pomodoro_sprints)
    if use_body_doubling:
        with st.spinner("Preparing Body-Doubling support..."):
            body_doubling.start_body_doubling_flow(task_row, get_body_doubling_services())
    else:
        with st.spinner("Preparing task support..."):
            set_open_task_guidance_message(
                generate_open_task_guidance_message(
                    task_row=task_row,
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
    existing_open_task = context["existing_open_task"]
    st.warning(
        "You already have an open task: "
        f"**{existing_open_task['title']}**"
    )
    st.write(f"Did you complete {existing_open_task['title']}?")

    completed_column, asleep_column, cancel_column = st.columns(3)
    try:
        with completed_column:
            if st.button("Yes, completed", type="primary", use_container_width=True):
                update_task_status(existing_open_task, "completed")
                complete_open_task_flow(context)

        with asleep_column:
            if st.button("No, send to sleep", use_container_width=True):
                update_task_status(existing_open_task, "asleep")
                complete_open_task_flow(context)

        with cancel_column:
            if st.button("Cancel", use_container_width=True):
                clear_open_task_dialog_state()
                st.rerun()
    except Exception as e:
        handle_api_exception(e, f"Could not resolve the previously open task: {e}")


@st.dialog("Open task")
def open_task_dialog(task_row):
    pending_context = st.session_state.get(OPEN_TASK_PENDING_CONTEXT_KEY)
    if pending_context:
        render_existing_open_task_resolution(pending_context)
        return

    st.write(f"Open task: **{task_row['title']}**")

    pomodoro_choice = st.selectbox(
        "Use Pomodoro sprints?",
        options=["Yes", "No"],
        index=None,
        placeholder="Choose yes or no",
    )
    body_doubling_choice = st.selectbox(
        "Use Body-Doubling?",
        options=["Yes", "No"],
        index=None,
        placeholder="Choose yes or no",
    )

    if st.button("OK", type="primary", use_container_width=True):
        if pomodoro_choice is None or body_doubling_choice is None:
            st.error("Please answer both questions before opening the task.")
            return

        try:
            context = build_open_task_context(
                task_row,
                pomodoro_choice,
                body_doubling_choice,
            )
            existing_open_task = get_open_task_row(
                exclude_instance_id=task_row["instance_id"]
            )
            if existing_open_task:
                context["existing_open_task"] = existing_open_task
                st.session_state[OPEN_TASK_PENDING_CONTEXT_KEY] = context
                render_existing_open_task_resolution(context)
                return

            complete_open_task_flow(context)
        except Exception as e:
            handle_api_exception(e, f"Could not open task: {e}")

    if st.button("Cancel", use_container_width=True):
        clear_open_task_dialog_state()
        st.rerun()


@st.dialog("Task support")
def open_task_guidance_dialog():
    message = st.session_state.get(OPEN_TASK_GUIDANCE_MESSAGE_KEY)
    if not message:
        return

    st.write(message)
    st.caption("This message will close automatically in 15 seconds.")


def render_open_task_guidance_dialog():
    expires_at = st.session_state.get(OPEN_TASK_GUIDANCE_EXPIRES_AT_KEY)
    if expires_at is None:
        return

    if datetime.now(pytz.UTC).timestamp() >= float(expires_at):
        clear_open_task_guidance_message()
        return

    open_task_guidance_dialog()


@st.dialog("Sprint review")
def sprint_review_dialog():
    st.write("sprint is over")

    completed_choice = st.selectbox(
        "Did you complete the task?",
        options=["Yes", "No"],
        index=None,
        placeholder="Choose yes or no",
    )
    rest_choice = st.selectbox(
        "Do you want to continue and move to rest?",
        options=["Yes", "No"],
        index=None,
        placeholder="Choose yes or no",
    )

    if st.button("OK", type="primary", use_container_width=True):
        if completed_choice is None or rest_choice is None:
            st.error("Please answer both questions before continuing.")
            return

        try:
            open_task = get_open_task_row()
            if completed_choice == "Yes":
                if open_task:
                    update_task_status(open_task, "completed")
                else:
                    st.warning("There is no open task to mark as completed.")

            if rest_choice == "Yes":
                append_timer_log_line("request_reset | timer=work_timer source=sprint_review_rest duration_minutes=10 callback=eoRest")
                get_work_timer(st.session_state).reset(
                    duration=10 * 60,
                    on_expiry=eoRest,
                )
            else:
                disable_work_timer()

            st.session_state.pop(SPRINT_REVIEW_PENDING_KEY, None)
            st.rerun()
        except Exception as e:
            handle_api_exception(e, f"Could not finish sprint review: {e}")


def render_sprint_review_dialog():
    if st.session_state.get(SPRINT_REVIEW_PENDING_KEY):
        sprint_review_dialog()


@st.dialog("Rest")
def rest_message_dialog():
    message = st.session_state.get(REST_MESSAGE_KEY)
    if not message:
        return

    st.write(message)
    st.caption("This message will close automatically in 15 seconds.")


def render_rest_message_dialog():
    expires_at = st.session_state.get(REST_MESSAGE_EXPIRES_AT_KEY)
    if expires_at is None:
        return

    if datetime.now(pytz.UTC).timestamp() >= float(expires_at):
        clear_rest_message()
        return

    rest_message_dialog()


def render_guided_task_actions(task_row):
    st.caption(f"Guided task: {task_row['title']}")
    open_column, asleep_column, complete_column = st.columns(3)

    with open_column:
        open_label = "Continue" if task_row["status"] == "open" else "Open"
        if st.button(
            open_label,
            key=f"guided_open_{task_row['instance_id']}",
            type="primary",
            use_container_width=True,
            disabled=task_row["status"] in {"completed", "archived"},
        ):
            st.session_state[OPEN_TASK_DIALOG_TASK_KEY] = task_row

    with asleep_column:
        if st.button(
            "Not now",
            key=f"guided_asleep_{task_row['instance_id']}",
            use_container_width=True,
            disabled=task_row["status"] in {"completed", "archived", "asleep"},
        ):
            update_task_status(task_row, "asleep")
            st.success("Guided task moved to asleep.")
            st.rerun()

    with complete_column:
        if st.button(
            "Mark as done",
            key=f"guided_done_{task_row['instance_id']}",
            use_container_width=True,
            disabled=task_row["status"] in {"completed", "archived"},
        ):
            update_task_status(task_row, "completed")
            st.success("Guided task marked as completed.")
            st.rerun()


def render_tasks_page():
    st.title("My Tasks")

    try:
        tasks_df = get_tasks_dataframe()
        guided_task_context = get_guided_task_context(tasks_df)
        guided_mode_active = guided_task_context is not None
        top_left, top_right = st.columns([3, 1])

        with top_left:
            if guided_mode_active:
                st.warning(guided_task_context["reason"])
            else:
                st.caption("Tasks ordered by due date, from latest to earliest.")

        with top_right:
            if st.button(
                "Add Task",
                type="primary",
                use_container_width=True,
                disabled=guided_mode_active,
            ):
                new_task_form()

        if tasks_df.empty:
            st.info("You do not have any tasks yet.")
            return

        default_visible_columns = [
            "display_title",
            "display_due_date",
            "status",
            "WOBJ",
        ]
        all_fields_visible_columns = [
            "display_title",
            "display_due_date",
            "status",
            "WOBJ",
            "Urgency",
            "WSUB",
            "size_minutes",
            "display_start_date",
            "rrule",
        ]
        show_routines = st.toggle(
            "Show routines",
            value=False,
            disabled=guided_mode_active,
        )
        if guided_mode_active:
            show_routines = False
        filtered_tasks_df = tasks_df[
            tasks_df["is_routine"] == show_routines
        ].reset_index(drop=True)

        if guided_mode_active:
            guided_instance_id = guided_task_context["task"]["instance_id"]
            filtered_tasks_df = filtered_tasks_df[
                filtered_tasks_df["instance_id"] == guided_instance_id
            ].reset_index(drop=True)

        if filtered_tasks_df.empty:
            empty_label = "routines" if show_routines else "actions"
            st.info(f"You do not have any {empty_label} yet.")
            return

        show_all_columns = st.toggle("Show all task fields", value=False)
        visible_columns = (
            all_fields_visible_columns
            if show_all_columns
            else default_visible_columns
        )
        ordered_grid_columns = visible_columns + [
            column_name
            for column_name in filtered_tasks_df.columns
            if column_name not in visible_columns
        ]

        grid_df = filtered_tasks_df[ordered_grid_columns].copy()
        grid_builder = GridOptionsBuilder.from_dataframe(grid_df)
        grid_builder.configure_selection(
            selection_mode="single",
            use_checkbox=False,
            suppressRowDeselection=False,
        )
        for column_name in grid_df.columns:
            grid_builder.configure_column(
                column_name,
                hide=column_name not in visible_columns,
            )

        grid_builder.configure_column(
            "display_title",
            header_name="Title",
            width=360,
            minWidth=300,
            flex=2,
            cellStyle={"fontWeight": "bold"},
        )
        grid_builder.configure_column("display_due_date", header_name="Due date")
        grid_builder.configure_column("display_start_date", header_name="Start date")
        grid_builder.configure_column("status", header_name="Status")
        grid_builder.configure_column("WOBJ", header_name="WOBJ")
        grid_builder.configure_column("WSUB", header_name="WSUB")
        grid_builder.configure_column("Urgency", header_name="Urgency")
        grid_builder.configure_column("size_minutes", header_name="Size_minutes")
        grid_builder.configure_column("rrule", header_name="rrule")
        grid_builder.configure_grid_options(domLayout="normal")

        grid_response = AgGrid(
            grid_df,
            gridOptions=grid_builder.build(),
            height=280,
            fit_columns_on_grid_load=True,
            allow_unsafe_jscode=False,
            theme="streamlit",
            update_on=["selectionChanged"],
            key=f"tasks_grid_{show_routines}_{show_all_columns}_{st.session_state['tasks_grid_version']}",
            defaultColDef= {
                "cellStyle": {"fontSize": "16px"}
            }
        )

        selected_row = get_aggrid_selected_row(grid_response)
        if guided_mode_active:
            selected_row = guided_task_context["task"]

        if selected_row:
            if not guided_mode_active:
                selected_description = (selected_row.get("description") or "").strip()
                if selected_description:
                    st.caption(f"{selected_row['title']}: {selected_description}")
                else:
                    st.caption(selected_row["title"])

            if guided_mode_active:
                render_guided_task_actions(selected_row)
            else:
                is_parent_task = bool(selected_row.get("has_subtasks"))
                action_columns = st.columns(4 if is_parent_task else 5)
                next_action_column = 0

                if not is_parent_task:
                    with action_columns[next_action_column]:
                        if st.button(
                            "Open",
                            use_container_width=True,
                            disabled=selected_row["status"] in {"completed", "archived"},
                        ):
                            st.session_state[OPEN_TASK_DIALOG_TASK_KEY] = selected_row
                    next_action_column += 1

                with action_columns[next_action_column]:
                    if st.button(
                        "Edit task",
                        use_container_width=True,
                        disabled=selected_row["status"] in {"completed", "archived"},
                    ):
                        edit_task_form(selected_row)
                next_action_column += 1

                with action_columns[next_action_column]:
                    if st.button("Delete task", use_container_width=True):
                        delete_task_dialog(selected_row)
                next_action_column += 1

                with action_columns[next_action_column]:
                    if st.button("Create subtask", use_container_width=True):
                        new_task_form(parent_task=selected_row)
                next_action_column += 1

                with action_columns[next_action_column]:
                     if st.button("Mark as done", use_container_width=True):
                        update_task_status(selected_row, "completed")
                        st.success("Task marked as completed.")
                        st.rerun()

        open_dialog_task = st.session_state.get(OPEN_TASK_DIALOG_TASK_KEY)
        if open_dialog_task:
            open_task_dialog(open_dialog_task)
    except Exception as e:
        handle_api_exception(e, f"Could not load tasks: {e}")


def render_preferences_page():
    st.title("Edit Preferences")
    user_profile = ensure_user_profile_cache()
    preferences = user_profile.get("preferences", {})
    state_options = get_states_options()
    state_ids = [item["id"] for item in state_options]
    current_state_id = user_profile.get("state_id")
    state_index = state_ids.index(current_state_id) if current_state_id in state_ids else None

    with st.form("edit_preferences_form"):
        st.subheader("Profile")
        selected_state = st.selectbox(
            "Current state",
            options=state_options,
            index=state_index,
            placeholder="Select your current state",
            format_func=format_state_option,
        )

        st.subheader("Planning")
        average_session_time = st.number_input(
            "Average session time (minutes)",
            min_value=30,
            value=int(preferences.get("average_session_time", 120)),
            step=15,
        )
        sprint_time = st.number_input(
            "Sprint duration (minutes)",
            min_value=5,
            value=int(preferences.get("sprint", 30)),
            step=5,
        )
        custom_sizes = []
        size_labels = ["Size 1", "Size 2", "Size 3", "Size 4", "Size 5"]
        current_custom_sizes = preferences.get("custom_sizes", [15, 30, 60, 180, 720])
        for index, label in enumerate(size_labels):
            default_value = current_custom_sizes[index] if index < len(current_custom_sizes) else 15
            custom_sizes.append(
                st.number_input(
                    f"{label} in minutes",
                    min_value=1,
                    value=int(default_value),
                    step=5,
                )
            )

        st.subheader("General")
        language = st.text_input("Language", value=preferences.get("language", "english"))
        time_management = st.text_input(
            "Time management method",
            value=preferences.get("time-mgmt", "Pomodoro"),
        )
        notifications = st.checkbox(
            "Enable notifications",
            value=bool(preferences.get("notifications", True)),
        )

        if st.form_submit_button("Save preferences", type="primary"):
            if not selected_state:
                st.error("Selecciona un state para guardar las preferencias.")
                return

            try:
                updated_profile = save_user_profile_updates(
                    preferences_updates={
                        "average_session_time": int(average_session_time),
                        "custom_sizes": [int(value) for value in custom_sizes],
                        "sprint": int(sprint_time),
                        "language": language.strip() or preferences.get("language", "english"),
                        "time-mgmt": time_management.strip() or preferences.get("time-mgmt", "Pomodoro"),
                        "notifications": notifications,
                    },
                    state_id=selected_state["id"],
                )
                st.session_state["user_profile"] = updated_profile
                st.session_state["current_page"] = "tasks"
                st.success("Preferences updated successfully.")
                st.rerun()
            except Exception as e:
                handle_api_exception(e, f"Could not update preferences: {e}")


@st.dialog("Bienvenido")
def welcome_session_dialog():
    user_profile = ensure_user_profile_cache()
    state_options = get_initial_session_state_options()
    state_ids = [item["id"] for item in state_options]
    current_state_id = user_profile.get("state_id")
    state_index = None
    first_name = user_profile.get("first_name")

    if current_state_id in state_ids:
        state_index = state_ids.index(current_state_id)

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
        )
        expected_work_time = st.number_input(
            "Expected work time for the session (minutes)",
            min_value=30,
            value=int(get_effective_session_work_time()),
            step=15,
        )

        if st.form_submit_button("Guardar y continuar", type="primary"):
            if not selected_state:
                st.error("Selecciona un state para continuar.")
                return

            try:
                st.session_state["session_expected_work_time"] = int(expected_work_time)
                (
                    supabase.table("profiles")
                    .update({
                        "state_id": selected_state["id"],
                    })
                    .eq("id", st.session_state["user_id"])
                    .execute()
                )
                st.session_state["user_profile"] = {
                    **user_profile,
                    "state_id": selected_state["id"],
                }
                st.session_state["show_welcome_dialog"] = False
                st.session_state.pop(REGISTRATION_WELCOME_MESSAGE_KEY, None)
                st.success("Sesión preparada.")
                st.rerun()
            except Exception as e:
                handle_api_exception(e, f"No se pudo guardar la bienvenida: {e}")


def render_sidebar():
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {
            display: flex;
            flex-direction: column;
            min-height: 100vh;
        }

        [data-testid="stSidebar"] .account-menu-spacer {
            flex-grow: 1;
            min-height: 2rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.sidebar.write(f"Usuario: {st.session_state['user_id']}")

    if st.sidebar.button("My Tasks", use_container_width=True):
        st.session_state["current_page"] = "tasks"
        st.rerun()

    st.sidebar.markdown('<div class="account-menu-spacer"></div>', unsafe_allow_html=True)
    st.sidebar.markdown("---")
    with st.sidebar.expander("My Account", icon=":material/account_circle:"):
        if st.button("Editar preferencias", key="account_edit_preferences", use_container_width=True):
            st.session_state["current_page"] = "preferences"
            st.rerun()
        if st.button("Cerrar sesión", key="account_logout", use_container_width=True):
            logout()


# --- FORMULARIO DE REGISTRO (Pop-up) ---
@st.dialog("Crear nueva cuenta")
def registration_form():
    with st.form("signup_form"):
        full_name = st.text_input("Nombre completo") # Dato para tu tabla 'profiles'
        email = st.text_input("Email")
        password = st.text_input("Contraseña", type="password")
	    #
        # TO-DO: Meter aqui en el form los controles de los campos del usuario que necesitamos
        born = st.date_input(
            "Birth date",
            value=date(2000, 1, 1),
            min_value=date(1900, 1, 1),
            max_value=date.today(),
        )
        persona_options = {
            persona["name"]: persona_id for persona_id, persona in PERSONAS.items()
        }
        selected_persona_name = st.selectbox(
            "Persona",
            options=list(persona_options.keys()),
            disabled=not bool(persona_options),
        )
        persona_id = persona_options.get(selected_persona_name)

        if persona_id:
            st.caption(PERSONAS[persona_id]["self_describing"])
        average_session_time = st.number_input(
            "Expected work time for an average session (minutes)",
            min_value=30,
            value=120,
            step=30,
        )
	    #
        # --- EN TU FORMULARIO DE REGISTRO ---
        if st.form_submit_button("Registrarse"):
            try:
                if not persona_id:
                    st.error("No se pudieron cargar las personas. Revisa la conexión con Supabase.")
                    return

                # 1. Crear usuario en Auth
                auth_res = supabase.auth.sign_up({"email": email, "password": password})
                user_id = auth_res.user.id
                
                if user_id:
                    auth_payload = save_auth_cookie(auth_res)
                    if not auth_payload:
                        reset_auth_state()
                        st.info(
                            "Cuenta creada. Revisa tu email para confirmar la cuenta antes de iniciar sesión."
                        )
                        return

                    # GUARDAR EN SESSION STATE PARA CONSUMO EN TODAS LAS PAGINAS Y FORMULARIOS DE LA APLICACION
                    st.session_state["user_id"] = user_id
                    
                    preferences = {
                        "language": "english",
                        "average_session_time": average_session_time,
                        "custom_sizes": [15, 30, 60, 180, 720],
                        "sprint": 30,
                        "time-mgmt":"Pomodoro",
                        "notifications": True,
                    }
                    born_date = to_supabase_date(born)

                    # 2. Insertar en la tabla 'profiles' de tu DDL
                    profile_res = supabase.table("profiles").insert({
                        "id": user_id, 
                        "full_name": full_name,
                        # TO-DO: los siguientes campos de profiles hay que capturarlos del form de registro
                        "born": born_date,
                        "preferences": preferences,
                        "persona_id": persona_id,
                        "state_id": None                
                    }).execute()

                    refresh_user_profile_cache()
                    first_name = extract_first_name(full_name)
                    age = calculate_age(born)
                    persona_description = PERSONAS[persona_id]["description"]
                    with st.spinner("Preparando tu bienvenida..."):
                        st.session_state[REGISTRATION_WELCOME_MESSAGE_KEY] = (
                            generate_registration_welcome_message(
                                first_name=first_name,
                                age=age,
                                persona_description=persona_description,
                            )
                        )
                    st.session_state["session_expected_work_time"] = None
                    st.session_state["show_welcome_dialog"] = True
                    st.session_state["current_page"] = "tasks"
                    start_inactivity_logout_timer()
                    
                    st.success("Registro completado y ID guardado en sesión. Mira en tu buzón para completar el registro.")
                    st.rerun()
          
            except Exception as e:
                st.error(f"Error en el registro: {e}")

# --- FORMULARIO DE LOGIN (Pop-up) ---
@st.dialog("Iniciar Sesión")
def login_form():
    with st.form("login_form"):
        email = st.text_input("Email")
        password = st.text_input("Contraseña", type="password")
        
        if st.form_submit_button("Entrar"):
            try:
                # Autenticar con Supabase
                res = supabase.auth.sign_in_with_password({
                    "email": email,
                    "password": password
                })
                
                # Guardar el ID en el session_state
                auth_payload = save_auth_cookie(res)
                if not auth_payload:
                    reset_auth_state()
                    st.error("Login failed: Supabase did not return an active session.")
                    return

                st.session_state["user_id"] = res.user.id
                refresh_user_profile_cache()
                st.session_state["session_expected_work_time"] = None
                st.session_state["show_welcome_dialog"] = True
                st.session_state["current_page"] = "tasks"
                start_inactivity_logout_timer()
                start_work_timer()
                st.success("¡Bienvenido de nuevo!")
                st.rerun()  # Recargamos para actualizar la interfaz
                
            except Exception as e:
                handle_api_exception(e, f"Login failed: {e}")

# --- LÓGICA DE CIERRE DE SESIÓN ---
def logout():
    try:
        supabase.auth.sign_out()
    except Exception:
        pass

    reset_auth_state()
    st.rerun()


if hasattr(st, "fragment"):
    @st.fragment(run_every="1s")
    def render_inactivity_logout_watcher():
        tick_inactivity_logout_timer(user_interaction=False)
        tick_work_timer()
        body_doubling.move_body_doubling_flow_to_review_if_needed()
        clear_expired_open_task_guidance_message()
        clear_expired_rest_message()
        body_doubling.render_body_doubling_session_overlay(get_body_doubling_services())
else:
    def render_inactivity_logout_watcher():
        tick_inactivity_logout_timer(user_interaction=False)
        tick_work_timer()
        body_doubling.move_body_doubling_flow_to_review_if_needed()
        clear_expired_open_task_guidance_message()
        clear_expired_rest_message()
        body_doubling.render_body_doubling_session_overlay(get_body_doubling_services())


# Verificamos si hay sesión activa al cargar
if "user_id" not in st.session_state:
    st.session_state["user_id"] = None

restore_auth_session_from_cookie()
if st.session_state.get("user_id"):
    ensure_fresh_auth_session()
    tick_inactivity_logout_timer(user_interaction=True)


# --- FLUJO PRINCIPAL ---
if st.session_state["user_id"]:
        # ESTO ES LO QUE VE EL USUARIO LOGUEADO
    try:
        render_inactivity_logout_watcher()
        if body_doubling.should_render_body_doubling_session_only():
            st.stop()
        if st.session_state.get(body_doubling.BODY_DOUBLING_EXTRA_STEP_DIALOG_KEY):
            body_doubling.render_body_doubling_extra_step_dialog(get_body_doubling_services())
        elif st.session_state.get(body_doubling.BODY_DOUBLING_REVIEW_DIALOG_KEY):
            body_doubling.render_body_doubling_review_dialog(get_body_doubling_services())
        elif st.session_state.get(body_doubling.BODY_DOUBLING_SCOPE_DIALOG_KEY):
            body_doubling.render_body_doubling_scope_dialog(get_body_doubling_services())
        elif st.session_state.get(SPRINT_REVIEW_PENDING_KEY):
            render_sprint_review_dialog()
        elif st.session_state.get(REST_MESSAGE_EXPIRES_AT_KEY) is not None:
            render_rest_message_dialog()
        else:
            render_open_task_guidance_dialog()
        render_sidebar()

        if should_prompt_welcome_dialog():
            st.session_state["show_welcome_dialog"] = True
            welcome_session_dialog()

        if st.session_state.get("current_page") == "preferences":
            render_preferences_page()
        else:
            render_tasks_page()
    except Exception as e:
        handle_api_exception(e, f"Error accessing the app: {e}")

else:
    # ESTO ES LA LANDING PAGE
    st.title("Bienvenido a AI-ADHD")
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("Crear Cuenta", type="primary", use_container_width=True):
            registration_form()
    with col2:
        if st.button("Iniciar Sesión", use_container_width=True):
            login_form()
