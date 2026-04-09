import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import streamlit as st
import pandas as pd
from datetime import datetime, time, timedelta
import pytz # Recomendado para manejo de zonas horarias
from app.application.use_cases.personas_catalog import PERSONAS, supabase

LOOKUP_TABLES = (
    "dim_task_sizes",
    "dim_task_consequences",
    "dim_task_frictions",
)

# Inicializo el user id en session state para evitar errores
if "user_id" not in st.session_state:
    st.session_state["user_id"] = None
if "show_welcome_dialog" not in st.session_state:
    st.session_state["show_welcome_dialog"] = False
if "session_expected_work_time" not in st.session_state:
    st.session_state["session_expected_work_time"] = None
if "current_page" not in st.session_state:
    st.session_state["current_page"] = "tasks"

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="AI-ADHD", layout="centered")

# función para convertir un string a datetime, lo he incluido como una forma
# sencilla de convertir cosas como "1997" a algo insertable en Supabase TIMESTAMPTZ
# pero si Streamlit tiene algun control que ya se convierta una caja st.text_input
# en un date picker pues no se necesitaria
def to_supabase_timestamp(year_str):
    # Convertimos el string "1969" a un objeto datetime (1 de Enero a las 00:00)
    # y le asignamos la zona horaria UTC
    dt = datetime.strptime(year_str, "%Y").replace(tzinfo=pytz.UTC)    
    # Retornamos el formato ISO 8601 que Supabase ama
    return dt.isoformat()


def combine_date_and_time(selected_date, selected_time):
    combined = datetime.combine(selected_date, selected_time)
    return combined.replace(tzinfo=pytz.UTC).isoformat()


def combine_date_and_time_value(selected_date, selected_time):
    combined = datetime.combine(selected_date, selected_time)
    return combined.replace(tzinfo=pytz.UTC)


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
    st.session_state["states_cache"] = response.data or []


def ensure_states_cache():
    if "states_cache" not in st.session_state:
        load_states_cache()


def get_states_options():
    ensure_states_cache()
    return st.session_state["states_cache"]


def load_user_profile_cache():
    default_profile = {
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
            .select("state_id, preferences")
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
            "is_active, size_id, consequence_id, friction_id, is_adaptive)"
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

    dataframe["selection_label"] = dataframe.apply(
        lambda row: (
            f"{row['title']} | {row['priority_label']} | urgency {row['Urgency']} "
            f"| due {row['display_due_date']} | {row['status']}"
        ),
        axis=1,
    )

    # --- sort by priority first ---
    dataframe = dataframe.sort_values(
        by=["Urgency", "due_date"],
        ascending=[False, True]
    ).reset_index(drop=True)

    return dataframe


def format_lookup_option(item):
    return f"{item['label']} - {item['self_describing']}"


def format_state_option(item):
    return f"{item['name']} - {item['self_describing']}"


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

    start_time = st.time_input("Start time", value=time(9, 0))
    due_date = st.date_input("Due date", key="new_task_due_date")
    due_time = st.time_input("Due time", value=time(17, 0))
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
        "Adaptive": task_row["is_adaptive"],
        "Priority": task_row.get("priority_label"),
        "WOBJ": task_row.get("WOBJ"),
        "WSUB": task_row.get("WSUB"),
        "Urgency": task_row.get("Urgency"),
    }

    for label, value in details.items():
        st.write(f"**{label}:** {value}")


def delete_task(task_row):
    supabase.table("task_instances").delete().eq("task_id", task_row["task_id"]).execute()
    supabase.table("tasks").delete().eq("id", task_row["task_id"]).execute()

def update_task_status(task_row, new_status):
    supabase.table("task_instances").update(
        {"status": new_status}
    ).eq("id", task_row["instance_id"]).execute()

def render_tasks_page():
    st.title("My Tasks")
    top_left, top_right = st.columns([3, 1])

    with top_left:
        st.caption("Tasks ordered by due date, from latest to earliest.")

    with top_right:
        if st.button("Add Task", type="primary", use_container_width=True):
            new_task_form()

    try:
        tasks_df = get_tasks_dataframe()
        if tasks_df.empty:
            st.info("You do not have any tasks yet.")
            return

        st.dataframe(
            tasks_df[["title", "display_due_date", "display_start_date", "status"]],
            use_container_width=True,
            hide_index=True,
            column_config={
                "title": "Title",
                "display_due_date": "Due date",
                "display_start_date": "Start date",
                "status": "Status",
            },
        )

        selected_label = st.selectbox(
            "Select a task",
            options=tasks_df["selection_label"].tolist(),
            index=None,
            placeholder="Choose a task to manage",
        )

        if selected_label:
            selected_row = tasks_df.loc[
                tasks_df["selection_label"] == selected_label
            ].iloc[0].to_dict()

            action_col1, action_col2, action_col3, action_col4 = st.columns(4)
            with action_col1:
                if st.button("View full details", use_container_width=True):
                    task_details_dialog(selected_row)
            with action_col2:
                if st.button("Delete task", use_container_width=True):
                    delete_task(selected_row)
                    st.success("Task deleted successfully.")
                    st.rerun()
            with action_col3:
                if st.button("Create subtask", use_container_width=True):
                    new_task_form(parent_task=selected_row)
            with action_col4:
                 if st.button("Mark as done", use_container_width=True):
                    update_task_status(selected_row, "completed")
                    st.success("Task marked as completed.")
                    st.rerun()
    except Exception as e:
        st.error(f"Could not load tasks: {e}")


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
                st.error(f"Could not update preferences: {e}")


@st.dialog("Bienvenido")
def welcome_session_dialog():
    user_profile = ensure_user_profile_cache()
    state_options = get_states_options()
    state_ids = [item["id"] for item in state_options]
    current_state_id = user_profile.get("state_id")
    state_index = None

    if current_state_id in state_ids:
        state_index = state_ids.index(current_state_id)

    with st.form("welcome_session_form"):
        st.write("Antes de empezar, cuéntanos cómo llegas a esta sesión.")
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
                st.success("Sesión preparada.")
                st.rerun()
            except Exception as e:
                st.error(f"No se pudo guardar la bienvenida: {e}")


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
        born = st.text_input("Birth date")
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
                    born_date = to_supabase_timestamp(born)

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
                    
                    st.success("Registro completado y ID guardado en sesión. Mira en tu buzón para completar el registro.")
          
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
                st.session_state["user_id"] = res.user.id
                refresh_user_profile_cache()
                st.session_state["session_expected_work_time"] = None
                st.session_state["show_welcome_dialog"] = True
                st.session_state["current_page"] = "tasks"
                st.success("¡Bienvenido de nuevo!")
                st.rerun()  # Recargamos para actualizar la interfaz
                
            except Exception as e:
                st.error("Email o contraseña incorrectos")

# --- LÓGICA DE CIERRE DE SESIÓN ---
def logout():
    supabase.auth.sign_out()
    st.session_state["user_id"] = None
    st.session_state.pop("user_profile", None)
    st.session_state["session_expected_work_time"] = None
    st.session_state["show_welcome_dialog"] = False
    st.session_state["current_page"] = "tasks"
    st.rerun()


# Verificamos si hay sesión activa al cargar
if "user_id" not in st.session_state:
    st.session_state["user_id"] = None

# --- FLUJO PRINCIPAL ---
if st.session_state["user_id"]:
    # ESTO ES LO QUE VE EL USUARIO LOGUEADO
    render_sidebar()

    if st.session_state.get("show_welcome_dialog"):
        welcome_session_dialog()

    if st.session_state.get("current_page") == "preferences":
        render_preferences_page()
    else:
        render_tasks_page()

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
