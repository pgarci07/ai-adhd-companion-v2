-- Clean, publishable Supabase schema for AI-ADHD-Companion.
-- Derived from the full Supabase schema dump and normalised for maintenance.
-- This file contains structure only: no seed data and no personal data.

-- ============================================
-- Extensions
-- ============================================
CREATE EXTENSION IF NOT EXISTS pg_cron WITH SCHEMA pg_catalog;

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;

CREATE EXTENSION IF NOT EXISTS supabase_vault WITH SCHEMA vault;

CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA extensions;

CREATE EXTENSION IF NOT EXISTS pg_net WITH SCHEMA extensions;

-- ============================================
-- Types
-- ============================================
CREATE TYPE public.task_status AS ENUM (
    'ready',
    'open',
    'asleep',
    'completed',
    'stale',
    'debt'
);

-- ============================================
-- Foundation Functions
-- ============================================
CREATE OR REPLACE FUNCTION public.default_preference_settings() RETURNS jsonb
    LANGUAGE sql IMMUTABLE
    AS
$$
    SELECT '{
        "language": "english",
        "average_session_time": 120,
        "custom_sizes": [15, 30, 60, 180, 720],
        "sprint": 30,
        "planner_minutes": 5,
        "time-mgmt": "Pomodoro",
        "first_day_of_week": "SU",
        "notifications": true,
        "enable_minute_chime": true,
        "keep_worthy": true,
        "rest_duration": 2,
        "max_continuous_work_minutes": 90,
        "chunk_min_floor_minutes": 5
    }'::jsonb;
$$;

CREATE OR REPLACE FUNCTION public.gen_uuid_v7() RETURNS uuid
    LANGUAGE plpgsql
    SET search_path TO 'public', 'extensions'
    AS
$$
declare
  v_time bigint;
  v_uuid bytea;
begin
  v_time := (extract(epoch from clock_timestamp()) * 1000)::bigint;
  v_uuid := decode(lpad(to_hex(v_time), 12, '0'), 'hex') || gen_random_bytes(10);

  v_uuid := set_byte(v_uuid, 6, (get_byte(v_uuid, 6) & 15) | 112);
  v_uuid := set_byte(v_uuid, 8, (get_byte(v_uuid, 8) & 63) | 128);

  return encode(v_uuid, 'hex')::uuid;
end;
$$;

-- ============================================
-- Tables
-- ============================================
CREATE TABLE IF NOT EXISTS public.dim_task_consequences (
    id integer NOT NULL,
    label character varying(20) NOT NULL,
    self_describing character varying(256) NOT NULL,
    weight integer DEFAULT 1 NOT NULL,
    ui_color character(7)
);

CREATE TABLE IF NOT EXISTS public.dim_task_frictions (
    id integer NOT NULL,
    label character varying(20) NOT NULL,
    self_describing character varying(256) NOT NULL,
    weight integer DEFAULT 1 NOT NULL,
    ui_color character(7)
);

CREATE TABLE IF NOT EXISTS public.dim_task_sizes (
    id integer NOT NULL,
    label character varying(20) NOT NULL,
    self_describing character varying(256) NOT NULL,
    weight integer DEFAULT 1 NOT NULL,
    ui_color character(7)
);

CREATE TABLE IF NOT EXISTS public.lists (
    id uuid DEFAULT public.gen_uuid_v7() NOT NULL,
    user_id uuid DEFAULT auth.uid() NOT NULL,
    name character varying(50) NOT NULL,
    description text
);

CREATE TABLE IF NOT EXISTS public.personas (
    id smallint NOT NULL,
    name character varying(25) NOT NULL,
    description text NOT NULL,
    self_describing character varying(256) NOT NULL,
    decompose_threshold integer
);

CREATE TABLE IF NOT EXISTS public.profiles (
    id uuid NOT NULL,
    full_name text,
    avatar_url text,
    role text DEFAULT 'user'::text,
    born date,
    preferences jsonb DEFAULT public.default_preference_settings(),
    persona_id smallint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE IF NOT EXISTS public.states (
    id smallint NOT NULL,
    name character varying(25) NOT NULL,
    description text NOT NULL,
    self_describing character varying(256) NOT NULL
);

CREATE TABLE IF NOT EXISTS public.task_instance_status_log (
    id uuid DEFAULT public.gen_uuid_v7() NOT NULL,
    instance_changed_id uuid NOT NULL,
    new_status_id public.task_status NOT NULL,
    changed_at timestamp with time zone DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.task_instances (
    id uuid DEFAULT public.gen_uuid_v7() NOT NULL,
    task_id uuid NOT NULL,
    user_id uuid NOT NULL,
    instance_number integer DEFAULT 1 NOT NULL,
    parent_instance_id uuid,
    start_date timestamp with time zone DEFAULT now() NOT NULL,
    due_date timestamp with time zone DEFAULT public.get_tomorrow_midnight() NOT NULL,
    actual_friction_id integer,
    actual_duration integer,
    final_comments text,
    is_exception boolean DEFAULT false,
    original_start_date timestamp with time zone,
    original_due_date timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT chk_deadline_after_start CHECK ((due_date >= start_date))
);

CREATE TABLE IF NOT EXISTS public.tasks (
    id uuid DEFAULT public.gen_uuid_v7() NOT NULL,
    list_id uuid NOT NULL,
    user_id uuid NOT NULL,
    title text NOT NULL,
    description text,
    parent_task_id uuid,
    rrule text,
    is_active boolean DEFAULT true NOT NULL,
    size_id integer NOT NULL,
    consequence_id integer NOT NULL,
    friction_id integer NOT NULL,
    is_adaptive boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    is_routine boolean DEFAULT false NOT NULL,
    CONSTRAINT subtask_no_recurrence CHECK (((parent_task_id IS NULL) OR (rrule IS NULL))),
    CONSTRAINT task_not_own_parent CHECK ((id <> parent_task_id))
);

CREATE TABLE IF NOT EXISTS public.user_state_log (
    id uuid DEFAULT public.gen_uuid_v7() NOT NULL,
    user_id uuid NOT NULL,
    state_id smallint NOT NULL,
    experienced_at timestamp with time zone DEFAULT now() NOT NULL
);

-- ============================================
-- Sequences
-- ============================================
CREATE SEQUENCE IF NOT EXISTS public.dim_task_consequences_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

CREATE SEQUENCE IF NOT EXISTS public.dim_task_frictions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

CREATE SEQUENCE IF NOT EXISTS public.dim_task_sizes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

CREATE SEQUENCE IF NOT EXISTS public.personas_id_seq
    AS smallint
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

CREATE SEQUENCE IF NOT EXISTS public.states_id_seq
    AS smallint
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- ============================================
-- Sequence Ownership
-- ============================================
ALTER SEQUENCE public.dim_task_consequences_id_seq OWNED BY public.dim_task_consequences.id;

ALTER SEQUENCE public.dim_task_frictions_id_seq OWNED BY public.dim_task_frictions.id;

ALTER SEQUENCE public.dim_task_sizes_id_seq OWNED BY public.dim_task_sizes.id;

ALTER SEQUENCE public.personas_id_seq OWNED BY public.personas.id;

ALTER SEQUENCE public.states_id_seq OWNED BY public.states.id;

-- ============================================
-- Column Defaults
-- ============================================
ALTER TABLE ONLY public.dim_task_consequences ALTER COLUMN id SET DEFAULT nextval('public.dim_task_consequences_id_seq'::regclass);

ALTER TABLE ONLY public.dim_task_frictions ALTER COLUMN id SET DEFAULT nextval('public.dim_task_frictions_id_seq'::regclass);

ALTER TABLE ONLY public.dim_task_sizes ALTER COLUMN id SET DEFAULT nextval('public.dim_task_sizes_id_seq'::regclass);

ALTER TABLE ONLY public.personas ALTER COLUMN id SET DEFAULT nextval('public.personas_id_seq'::regclass);

ALTER TABLE ONLY public.states ALTER COLUMN id SET DEFAULT nextval('public.states_id_seq'::regclass);

-- ============================================
-- Comments
-- ============================================
COMMENT ON COLUMN public.personas.decompose_threshold IS 'Inclusive WSUB threshold used by Body-Doubling to decide whether OpenAI should decompose a task into microsteps for this persona. Null disables decomposition for the persona.';

-- ============================================
-- Functions and RPCs
-- ============================================
CREATE OR REPLACE FUNCTION public.age_task_instance_statuses(p_debt_days integer, p_stale_days integer, p_dry_run boolean DEFAULT false) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public'
    AS
$$
DECLARE
    v_now timestamptz := now();
    v_debt_cutoff timestamptz := v_now - make_interval(days => p_debt_days);
    v_stale_cutoff timestamptz := v_now - make_interval(days => p_stale_days);
    v_debt_candidates jsonb := '[]'::jsonb;
    v_stale_candidates jsonb := '[]'::jsonb;
    v_moved_to_debt integer := 0;
    v_moved_to_stale integer := 0;
    v_candidate record;
BEGIN
    WITH debt_candidates AS (
        SELECT
            ti.id AS instance_id,
            ti.due_date,
            latest.new_status_id AS current_status
        FROM public.task_instances ti
        JOIN LATERAL (
            SELECT tisl.new_status_id, tisl.changed_at, tisl.id
            FROM public.task_instance_status_log tisl
            WHERE tisl.instance_changed_id = ti.id
            ORDER BY tisl.changed_at DESC, tisl.id DESC
            LIMIT 1
        ) latest ON true
        WHERE latest.new_status_id IN ('ready', 'open', 'asleep')
          AND ti.due_date < v_debt_cutoff
    )
    SELECT
        COALESCE(
            jsonb_agg(
                jsonb_build_object(
                    'instanceId', dc.instance_id,
                    'dueDate', dc.due_date,
                    'currentStatus', dc.current_status
                )
                ORDER BY dc.due_date, dc.instance_id
            ),
            '[]'::jsonb
        ),
        COUNT(*)
    INTO v_debt_candidates, v_moved_to_debt
    FROM debt_candidates dc;

    WITH latest_statuses AS (
        SELECT DISTINCT ON (tisl.instance_changed_id)
            tisl.instance_changed_id AS instance_id,
            tisl.new_status_id AS current_status,
            tisl.changed_at
        FROM public.task_instance_status_log tisl
        ORDER BY
            tisl.instance_changed_id,
            tisl.changed_at DESC,
            tisl.id DESC
    ),
    stale_candidates AS (
        SELECT
            ls.instance_id,
            ls.changed_at AS debt_since,
            ls.current_status
        FROM latest_statuses ls
        WHERE ls.current_status = 'debt'
          AND ls.changed_at < v_stale_cutoff
    )
    SELECT
        COALESCE(
            jsonb_agg(
                jsonb_build_object(
                    'instanceId', sc.instance_id,
                    'debtSince', sc.debt_since,
                    'currentStatus', sc.current_status
                )
                ORDER BY sc.debt_since, sc.instance_id
            ),
            '[]'::jsonb
        ),
        COUNT(*)
    INTO v_stale_candidates, v_moved_to_stale
    FROM stale_candidates sc;

    IF NOT p_dry_run THEN
        FOR v_candidate IN
            SELECT (value->>'instanceId')::uuid AS instance_id
            FROM jsonb_array_elements(v_debt_candidates)
        LOOP
            PERFORM public.set_task_instance_status(
                v_candidate.instance_id,
                'debt',
                v_now
            );
        END LOOP;

        FOR v_candidate IN
            SELECT (value->>'instanceId')::uuid AS instance_id
            FROM jsonb_array_elements(v_stale_candidates)
        LOOP
            PERFORM public.set_task_instance_status(
                v_candidate.instance_id,
                'stale',
                v_now
            );
        END LOOP;
    END IF;

    RETURN jsonb_build_object(
        'movedToDebt', v_moved_to_debt,
        'movedToStale', v_moved_to_stale,
        'debtCandidates', v_debt_candidates,
        'staleCandidates', v_stale_candidates
    );
END;
$$;

CREATE OR REPLACE FUNCTION public.create_task_and_instances(p_list_id uuid, p_title text, p_description text DEFAULT NULL::text, p_start_date timestamp with time zone DEFAULT NULL::timestamp with time zone, p_due_date timestamp with time zone DEFAULT NULL::timestamp with time zone, p_parent_task_id uuid DEFAULT NULL::uuid, p_parent_instance_number integer DEFAULT 1, p_rrule text DEFAULT NULL::text, p_size_id integer DEFAULT NULL::integer, p_consequence_id integer DEFAULT NULL::integer, p_friction_id integer DEFAULT NULL::integer, p_is_adaptive boolean DEFAULT true) RETURNS uuid
    LANGUAGE plpgsql
    AS
$$
DECLARE
    v_user_id UUID := auth.uid();
    v_new_task_id UUID;
    v_exec_period INTERVAL;
    v_start_delay INTERVAL;
    v_first_parent_start TIMESTAMPTZ;
    v_parent_instance RECORD;
    v_calc_start TIMESTAMPTZ;
    v_calc_due TIMESTAMPTZ;
    v_is_first BOOLEAN := TRUE;
BEGIN
    IF v_user_id IS NULL THEN RAISE EXCEPTION 'Not authenticated'; END IF;

    IF p_parent_task_id IS NOT NULL THEN
        IF EXISTS (
            SELECT 1
            FROM public.tasks
            WHERE id = p_parent_task_id
              AND parent_task_id IS NOT NULL
        ) THEN
            RAISE EXCEPTION 'Nesting Error: Subtasks cannot have their own subtasks.';
        END IF;
    END IF;

    INSERT INTO public.tasks (
        user_id, list_id, title, description, parent_task_id,
        rrule, is_active, size_id, consequence_id, friction_id, is_adaptive
    ) VALUES (
        v_user_id, p_list_id, p_title, p_description, p_parent_task_id,
        p_rrule, TRUE, p_size_id, p_consequence_id, p_friction_id, p_is_adaptive
    )
    RETURNING id INTO v_new_task_id;

    IF p_parent_task_id IS NOT NULL THEN
        v_exec_period := p_due_date - p_start_date;

        v_first_parent_start := (
            SELECT start_date
            FROM public.task_instances
            WHERE task_id = p_parent_task_id
              AND instance_number = p_parent_instance_number
        );

        v_start_delay := p_start_date - v_first_parent_start;

        FOR v_parent_instance IN
            SELECT id, start_date, due_date, instance_number
            FROM public.task_instances
            WHERE task_id = p_parent_task_id
              AND instance_number >= p_parent_instance_number
              AND public.get_current_task_instance_status(id) IN (
                  'ready',
                  'open',
                  'asleep',
                  'debt'
              )
            ORDER BY instance_number ASC
        LOOP
            IF v_is_first THEN
                v_calc_start := p_start_date;
                v_calc_due := p_due_date;
                v_is_first := FALSE;
            ELSE
                v_calc_start := v_parent_instance.start_date + v_start_delay;
                v_calc_due := v_calc_start + v_exec_period;
            END IF;

            INSERT INTO public.task_instances (
                task_id, user_id, parent_instance_id, start_date, due_date,
                instance_number, original_start_date, original_due_date
            ) VALUES (
                v_new_task_id, v_user_id, v_parent_instance.id, v_calc_start, v_calc_due,
                v_parent_instance.instance_number, v_calc_start, v_calc_due
            );
        END LOOP;

    ELSE
        INSERT INTO public.task_instances (
            task_id, user_id, start_date, due_date, instance_number,
            original_start_date, original_due_date
        ) VALUES (
            v_new_task_id, v_user_id, p_start_date, p_due_date, 1,
            p_start_date, p_due_date
        );
    END IF;

    RETURN v_new_task_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.delete_task_by_policy(p_task_id uuid, p_instance_id uuid, p_scope text, p_keep_worthy boolean DEFAULT false) RETURNS void
    LANGUAGE plpgsql
    AS
$$
DECLARE
    v_user_id UUID := auth.uid();
    v_task RECORD;
    v_instance RECORD;
    v_total_instance_count INTEGER := 0;
    v_past_instance_count INTEGER := 0;
    v_all_worthy_count INTEGER := 0;
BEGIN
    IF v_user_id IS NULL THEN
        RAISE EXCEPTION 'Not authenticated';
    END IF;

    SELECT
        t.id,
        t.user_id,
        t.rrule,
        t.parent_task_id,
        t.is_active,
        parent_task.rrule AS parent_rrule,
        EXISTS (
            SELECT 1
            FROM public.tasks child_task
            WHERE child_task.parent_task_id = t.id
        ) AS has_subtasks
    INTO v_task
    FROM public.tasks t
    LEFT JOIN public.tasks parent_task
      ON parent_task.id = t.parent_task_id
    WHERE t.id = p_task_id;

    IF NOT FOUND OR v_task.user_id <> v_user_id THEN
        RAISE EXCEPTION 'Task not found or not allowed';
    END IF;

    SELECT
        ti.id,
        ti.instance_number,
        ti.parent_instance_id
    INTO v_instance
    FROM public.task_instances ti
    WHERE ti.id = p_instance_id
      AND ti.task_id = p_task_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Task instance not found';
    END IF;

    SELECT
        COUNT(*),
        COUNT(*) FILTER (WHERE ti.instance_number < v_instance.instance_number)
    INTO
        v_total_instance_count,
        v_past_instance_count
    FROM public.task_instances ti
    WHERE ti.task_id = p_task_id;

    IF v_task.has_subtasks THEN
        SELECT COUNT(*)
        INTO v_all_worthy_count
        FROM public.task_instances parent_instance
        WHERE parent_instance.task_id = p_task_id
          AND public.is_worthy_instance_family(parent_instance.id);
    ELSE
        SELECT COUNT(*)
        INTO v_all_worthy_count
        FROM public.task_instances ti
        WHERE ti.task_id = p_task_id
          AND public.is_worthy_task_instance(ti.id);
    END IF;

    IF p_scope = 'remove_recurrency' THEN
        UPDATE public.tasks
        SET rrule = NULL
        WHERE id = p_task_id;
        RETURN;
    END IF;

    IF v_task.rrule IS NULL AND COALESCE(v_task.parent_rrule, '') = '' THEN
        DELETE FROM public.task_instances
        WHERE task_id = p_task_id;

        DELETE FROM public.tasks
        WHERE id = p_task_id;
        RETURN;
    END IF;

    IF v_task.rrule IS NOT NULL THEN
        IF p_scope = 'current' THEN
            DELETE FROM public.task_instances
            WHERE id = p_instance_id;

            IF v_total_instance_count <= 1 THEN
                DELETE FROM public.tasks
                WHERE id = p_task_id;
            END IF;
            RETURN;
        END IF;

        IF p_scope = 'selected_future' THEN
            DELETE FROM public.task_instances
            WHERE task_id = p_task_id
              AND instance_number >= v_instance.instance_number;

            IF v_past_instance_count > 0 THEN
                UPDATE public.tasks
                SET is_active = FALSE
                WHERE id = p_task_id;
            ELSE
                DELETE FROM public.tasks
                WHERE id = p_task_id;
            END IF;
            RETURN;
        END IF;

        IF p_scope = 'future' THEN
            DELETE FROM public.task_instances
            WHERE task_id = p_task_id
              AND instance_number > v_instance.instance_number;

            UPDATE public.tasks
            SET is_active = FALSE
            WHERE id = p_task_id;
            RETURN;
        END IF;

        IF p_scope = 'all' THEN
            IF p_keep_worthy AND v_all_worthy_count > 0 THEN
                IF v_task.has_subtasks THEN
                    DELETE FROM public.task_instances
                    WHERE task_id = p_task_id
                      AND NOT public.is_worthy_instance_family(id);
                ELSE
                    DELETE FROM public.task_instances
                    WHERE task_id = p_task_id
                      AND NOT public.is_worthy_task_instance(id);
                END IF;

                UPDATE public.tasks
                SET is_active = FALSE
                WHERE id = p_task_id;

                IF NOT EXISTS (
                    SELECT 1
                    FROM public.task_instances
                    WHERE task_id = p_task_id
                ) THEN
                    DELETE FROM public.tasks
                    WHERE id = p_task_id;
                END IF;
            ELSE
                DELETE FROM public.task_instances
                WHERE task_id = p_task_id;

                DELETE FROM public.tasks
                WHERE id = p_task_id;
            END IF;
            RETURN;
        END IF;
    END IF;

    IF v_task.parent_task_id IS NOT NULL AND COALESCE(v_task.parent_rrule, '') <> '' THEN
        IF p_scope = 'current' THEN
            DELETE FROM public.task_instances
            WHERE id = p_instance_id;

            IF v_total_instance_count <= 1 THEN
                DELETE FROM public.tasks
                WHERE id = p_task_id;
            END IF;
            RETURN;
        END IF;

        IF p_scope = 'selected_future' THEN
            DELETE FROM public.task_instances
            WHERE task_id = p_task_id
              AND instance_number >= v_instance.instance_number;

            IF v_past_instance_count = 0 THEN
                DELETE FROM public.tasks
                WHERE id = p_task_id;
            END IF;
            RETURN;
        END IF;

        IF p_scope = 'future' THEN
            DELETE FROM public.task_instances
            WHERE task_id = p_task_id
              AND instance_number > v_instance.instance_number;
            RETURN;
        END IF;

        IF p_scope = 'all' THEN
            IF p_keep_worthy AND v_all_worthy_count > 0 THEN
                DELETE FROM public.task_instances
                WHERE task_id = p_task_id
                  AND (
                      instance_number > v_instance.instance_number
                      OR (
                          instance_number <= v_instance.instance_number
                          AND NOT public.is_worthy_task_instance(id)
                      )
                  );

                IF NOT EXISTS (
                    SELECT 1
                    FROM public.task_instances
                    WHERE task_id = p_task_id
                ) THEN
                    DELETE FROM public.tasks
                    WHERE id = p_task_id;
                END IF;
            ELSE
                DELETE FROM public.task_instances
                WHERE task_id = p_task_id;

                DELETE FROM public.tasks
                WHERE id = p_task_id;
            END IF;
            RETURN;
        END IF;
    END IF;

    RAISE EXCEPTION 'Unsupported delete scope: %', p_scope;
END;
$$;

CREATE OR REPLACE FUNCTION public.ensure_initial_task_instance_status() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public'
    AS
$$
BEGIN
    IF public.get_current_task_instance_status(NEW.id) IS NULL THEN
        PERFORM public.set_task_instance_status(
            NEW.id,
            'ready',
            COALESCE(NEW.created_at, now())
        );
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.fn_create_default_list_for_new_user() RETURNS trigger
    LANGUAGE plpgsql
    AS
$$
BEGIN
    INSERT INTO public.lists (user_id, name, description)
    VALUES (
        NEW.id, -- En Supabase, el ID del perfil suele ser el mismo UUID del usuario
        'my list',
        'you default list; feel free to change the name and description any time'
    );
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.fn_enforce_task_exclusivity() RETURNS trigger
    LANGUAGE plpgsql
    AS
$$
BEGIN
    IF NEW.is_recurring IS TRUE THEN
        IF EXISTS (SELECT 1 FROM tasks WHERE parent_task_id = NEW.id) THEN
            RAISE EXCEPTION
                'Constraint Violation: A task with subtasks cannot be made recurring.';
        END IF;
    END IF;

    IF NEW.parent_task_id IS NOT NULL THEN
        IF EXISTS (
            SELECT 1
            FROM tasks
            WHERE id = NEW.parent_task_id
              AND is_recurring IS TRUE
        ) THEN
            RAISE EXCEPTION
                'Constraint Violation: Cannot add subtasks to a recurring task.';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.fn_set_instance_user_id_from_task() RETURNS trigger
    LANGUAGE plpgsql
    AS
$$
BEGIN
    SELECT user_id INTO NEW.user_id FROM tasks WHERE id = NEW.task_id;
    IF NEW.user_id IS NULL THEN
        RAISE EXCEPTION
            'Integrity error: parent task % does not exist or has no owner',
            NEW.task_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.fn_set_task_user_id_from_list() RETURNS trigger
    LANGUAGE plpgsql
    AS
$$
BEGIN
    SELECT user_id INTO NEW.user_id FROM lists WHERE id = NEW.list_id;
    IF NEW.user_id IS NULL THEN
        RAISE EXCEPTION 'Error: List % does not have a valid owner', NEW.list_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.fn_update_timestamp() RETURNS trigger
    LANGUAGE plpgsql
    AS
$$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.get_current_task_instance_status(p_instance_id uuid) RETURNS public.task_status
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS
$$
    SELECT tisl.new_status_id
    FROM public.task_instance_status_log tisl
    WHERE tisl.instance_changed_id = p_instance_id
    ORDER BY tisl.changed_at DESC, tisl.id DESC
    LIMIT 1;
$$;

CREATE OR REPLACE FUNCTION public.get_current_user_state_id(p_user_id uuid) RETURNS smallint
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'public'
    AS
$$
    SELECT usl.state_id
    FROM public.user_state_log usl
    WHERE usl.user_id = p_user_id
    ORDER BY usl.experienced_at DESC, usl.id DESC
    LIMIT 1;
$$;

CREATE OR REPLACE FUNCTION public.get_task_delete_context(p_task_id uuid, p_instance_id uuid) RETURNS jsonb
    LANGUAGE plpgsql
    AS
$$
DECLARE
    v_user_id UUID := auth.uid();
    v_task RECORD;
    v_instance RECORD;
    v_current_worthy BOOLEAN := FALSE;
    v_current_family_worthy BOOLEAN := FALSE;
    v_all_worthy_count INTEGER := 0;
    v_total_instance_count INTEGER := 0;
    v_past_instance_count INTEGER := 0;
    v_future_instance_count INTEGER := 0;
    v_keep_worthy_preference BOOLEAN := FALSE;
BEGIN
    IF v_user_id IS NULL THEN
        RAISE EXCEPTION 'Not authenticated';
    END IF;

    SELECT
        t.id,
        t.user_id,
        t.rrule,
        t.parent_task_id,
        parent_task.rrule AS parent_rrule,
        EXISTS (
            SELECT 1
            FROM public.tasks child_task
            WHERE child_task.parent_task_id = t.id
        ) AS has_subtasks
    INTO v_task
    FROM public.tasks t
    LEFT JOIN public.tasks parent_task
      ON parent_task.id = t.parent_task_id
    WHERE t.id = p_task_id;

    IF NOT FOUND OR v_task.user_id <> v_user_id THEN
        RAISE EXCEPTION 'Task not found or not allowed';
    END IF;

    SELECT
        ti.id,
        ti.instance_number,
        ti.parent_instance_id
    INTO v_instance
    FROM public.task_instances ti
    WHERE ti.id = p_instance_id
      AND ti.task_id = p_task_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Task instance not found';
    END IF;

    SELECT
        COUNT(*),
        COUNT(*) FILTER (WHERE ti.instance_number < v_instance.instance_number),
        COUNT(*) FILTER (WHERE ti.instance_number > v_instance.instance_number)
    INTO
        v_total_instance_count,
        v_past_instance_count,
        v_future_instance_count
    FROM public.task_instances ti
    WHERE ti.task_id = p_task_id;

    SELECT COALESCE((preferences ->> 'keep_worthy')::boolean, FALSE)
    INTO v_keep_worthy_preference
    FROM public.profiles
    WHERE id = v_user_id;

    IF v_task.has_subtasks THEN
        v_current_family_worthy := public.is_worthy_instance_family(v_instance.id);
        v_current_worthy := v_current_family_worthy;

        SELECT COUNT(*)
        INTO v_all_worthy_count
        FROM public.task_instances parent_instance
        WHERE parent_instance.task_id = p_task_id
          AND public.is_worthy_instance_family(parent_instance.id);
    ELSE
        v_current_worthy := public.is_worthy_task_instance(v_instance.id);
        v_current_family_worthy := v_current_worthy;

        SELECT COUNT(*)
        INTO v_all_worthy_count
        FROM public.task_instances ti
        WHERE ti.task_id = p_task_id
          AND public.is_worthy_task_instance(ti.id);
    END IF;

    RETURN jsonb_build_object(
        'is_recurring', v_task.rrule IS NOT NULL,
        'is_subtask', v_task.parent_task_id IS NOT NULL,
        'parent_is_recurring', COALESCE(v_task.parent_rrule, '') <> '',
        'has_subtasks', v_task.has_subtasks,
        'instance_number', v_instance.instance_number,
        'total_instance_count', v_total_instance_count,
        'has_past_instances', v_past_instance_count > 0,
        'has_future_instances', v_future_instance_count > 0,
        'current_worthy', v_current_worthy,
        'current_family_worthy', v_current_family_worthy,
        'all_worthy_count', v_all_worthy_count,
        'keep_worthy_preference', v_keep_worthy_preference,
        'warn_worthy', v_keep_worthy_preference AND v_current_family_worthy,
        'warn_any_worthy', v_keep_worthy_preference AND v_all_worthy_count > 0
    );
END;
$$;

CREATE OR REPLACE FUNCTION public.get_tomorrow_midnight() RETURNS timestamp with time zone
    LANGUAGE sql
    AS
$$
  SELECT date_trunc('day', now() + interval '1 day');
$$;

CREATE OR REPLACE FUNCTION public.get_user_session_summaries(p_user_id uuid, p_limit integer DEFAULT 5) RETURNS TABLE(session_index integer, session_started_at timestamp with time zone, session_ended_at timestamp with time zone, is_current_session boolean, duration_seconds bigint, duration_minutes numeric, duration_hours numeric)
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'public'
    AS
$$
WITH ordered_log AS (
    SELECT
        usl.id,
        usl.experienced_at,
        s.name AS state_name,
        COALESCE(
            SUM(
                CASE
                    WHEN s.name = 'Recovery' THEN 1
                    ELSE 0
                END
            ) OVER (
                ORDER BY usl.experienced_at, usl.id
                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
            ),
            0
        )::integer AS session_group
    FROM public.user_state_log usl
    JOIN public.states s
      ON s.id = usl.state_id
    WHERE usl.user_id = p_user_id
),
session_state_rows AS (
    SELECT
        ol.session_group,
        ol.experienced_at
    FROM ordered_log ol
    WHERE ol.state_name <> 'Recovery'
),
session_recoveries AS (
    SELECT
        ol.session_group,
        MIN(ol.experienced_at) AS recovery_at
    FROM ordered_log ol
    WHERE ol.state_name = 'Recovery'
    GROUP BY ol.session_group
),
session_bounds AS (
    SELECT
        ssr.session_group,
        MIN(ssr.experienced_at) AS session_started_at,
        COALESCE(sr.recovery_at, now()) AS session_ended_at,
        sr.recovery_at IS NULL AS is_current_session
    FROM session_state_rows ssr
    LEFT JOIN session_recoveries sr
      ON sr.session_group = ssr.session_group
    GROUP BY ssr.session_group, sr.recovery_at
),
ranked_sessions AS (
    SELECT
        ROW_NUMBER() OVER (ORDER BY sb.session_started_at DESC)::integer AS session_index,
        sb.session_started_at,
        sb.session_ended_at,
        sb.is_current_session,
        EXTRACT(EPOCH FROM (sb.session_ended_at - sb.session_started_at))::bigint AS duration_seconds
    FROM session_bounds sb
    WHERE sb.session_ended_at > sb.session_started_at
)
SELECT
    rs.session_index,
    rs.session_started_at,
    rs.session_ended_at,
    rs.is_current_session,
    rs.duration_seconds,
    ROUND(rs.duration_seconds / 60.0, 2) AS duration_minutes,
    ROUND(rs.duration_seconds / 3600.0, 2) AS duration_hours
FROM ranked_sessions rs
ORDER BY rs.session_index ASC
LIMIT GREATEST(COALESCE(p_limit, 5), 1);
$$;

CREATE OR REPLACE FUNCTION public.get_user_state_time_summary(p_user_id uuid, p_date_from timestamp with time zone, p_date_to timestamp with time zone DEFAULT now()) RETURNS TABLE(state_id smallint, state_name character varying, seconds_in_state bigint, minutes_in_state numeric, hours_in_state numeric)
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'public'
    AS
$$
WITH bounds AS (
    SELECT
        p_user_id AS user_id,
        p_date_from AS date_from,
        COALESCE(p_date_to, now()) AS date_to
),
prior_state AS (
    SELECT
        b.user_id,
        usl.state_id,
        b.date_from AS experienced_at,
        usl.id
    FROM bounds b
    JOIN LATERAL (
        SELECT id, state_id
        FROM public.user_state_log
        WHERE user_id = b.user_id
          AND experienced_at < b.date_from
        ORDER BY experienced_at DESC, id DESC
        LIMIT 1
    ) usl ON TRUE
),
window_events AS (
    SELECT
        usl.user_id,
        usl.state_id,
        usl.experienced_at,
        usl.id
    FROM public.user_state_log usl
    JOIN bounds b
      ON b.user_id = usl.user_id
    WHERE usl.experienced_at >= b.date_from
      AND usl.experienced_at < b.date_to
),
timeline AS (
    SELECT * FROM prior_state
    UNION ALL
    SELECT * FROM window_events
),
segments AS (
    SELECT
        t.state_id,
        t.experienced_at AS segment_start,
        LEAD(t.experienced_at, 1, b.date_to) OVER (
            ORDER BY t.experienced_at, t.id
        ) AS segment_end
    FROM timeline t
    CROSS JOIN bounds b
)
SELECT
    s.id AS state_id,
    s.name::varchar AS state_name,
    COALESCE(
        SUM(EXTRACT(EPOCH FROM (seg.segment_end - seg.segment_start))),
        0
    )::bigint AS seconds_in_state,
    ROUND(
        COALESCE(
            SUM(EXTRACT(EPOCH FROM (seg.segment_end - seg.segment_start))),
            0
        ) / 60.0,
        2
    ) AS minutes_in_state,
    ROUND(
        COALESCE(
            SUM(EXTRACT(EPOCH FROM (seg.segment_end - seg.segment_start))),
            0
        ) / 3600.0,
        2
    ) AS hours_in_state
FROM public.states s
LEFT JOIN segments seg
  ON seg.state_id = s.id
 AND seg.segment_end > seg.segment_start
GROUP BY s.id, s.name
HAVING COALESCE(SUM(EXTRACT(EPOCH FROM (seg.segment_end - seg.segment_start))), 0) > 0
ORDER BY seconds_in_state DESC, s.id ASC;
$$;

CREATE OR REPLACE FUNCTION public.get_user_task_rows() RETURNS TABLE(instance_id uuid, task_id uuid, instance_number integer, parent_instance_id uuid, list_id uuid, title text, description text, start_date timestamp with time zone, due_date timestamp with time zone, status public.task_status, rrule text, is_active boolean, is_routine boolean, size_id integer, consequence_id integer, friction_id integer, final_comments text, actual_duration integer, actual_friction_id integer, is_adaptive boolean, parent_task_id uuid)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS
$$
    SELECT
        ti.id AS instance_id,
        ti.task_id,
        ti.instance_number,
        ti.parent_instance_id,
        t.list_id,
        t.title,
        t.description,
        ti.start_date,
        ti.due_date,
        COALESCE(latest_status.new_status_id, 'ready'::task_status) AS status,
        t.rrule,
        t.is_active,
        t.is_routine,
        t.size_id,
        t.consequence_id,
        t.friction_id,
        ti.final_comments,
        ti.actual_duration,
        ti.actual_friction_id,
        t.is_adaptive,
        t.parent_task_id
    FROM public.task_instances ti
    JOIN public.tasks t
      ON t.id = ti.task_id
    LEFT JOIN LATERAL (
        SELECT tisl.new_status_id
        FROM public.task_instance_status_log tisl
        WHERE tisl.instance_changed_id = ti.id
        ORDER BY tisl.changed_at DESC, tisl.id DESC
        LIMIT 1
    ) latest_status ON TRUE
    WHERE t.user_id = auth.uid()
    ORDER BY ti.due_date DESC;
$$;

CREATE OR REPLACE FUNCTION public.is_worthy_instance_family(p_parent_instance_id uuid) RETURNS boolean
    LANGUAGE sql STABLE
    AS
$$
    SELECT
        is_worthy_task_instance(p_parent_instance_id)
        OR EXISTS (
            SELECT 1
            FROM task_instances child
            WHERE child.parent_instance_id = p_parent_instance_id
              AND is_worthy_task_instance(child.id)
        );
$$;

CREATE OR REPLACE FUNCTION public.is_worthy_task_instance(p_instance_id uuid) RETURNS boolean
    LANGUAGE sql STABLE
    AS
$$
    SELECT EXISTS (
        SELECT 1
        FROM public.task_instances
        WHERE id = p_instance_id
          AND public.get_current_task_instance_status(id) = 'completed'
          AND (
              actual_friction_id IS NOT NULL
              OR actual_duration IS NOT NULL
              OR final_comments IS NOT NULL
          )
    );
$$;

CREATE OR REPLACE FUNCTION public.rls_auto_enable() RETURNS event_trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS
$$
DECLARE
  cmd record;
BEGIN
  FOR cmd IN
    SELECT *
    FROM pg_event_trigger_ddl_commands()
    WHERE command_tag IN ('CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO')
      AND object_type IN ('table', 'partitioned table')
  LOOP
     IF cmd.schema_name IS NOT NULL
        AND cmd.schema_name IN ('public')
        AND cmd.schema_name NOT IN ('pg_catalog', 'information_schema')
        AND cmd.schema_name NOT LIKE 'pg_toast%'
        AND cmd.schema_name NOT LIKE 'pg_temp%' THEN
      BEGIN
        EXECUTE format(
            'alter table if exists %s enable row level security',
            cmd.object_identity
        );
        RAISE LOG 'rls_auto_enable: enabled RLS on %', cmd.object_identity;
      EXCEPTION
        WHEN OTHERS THEN
          RAISE LOG
              'rls_auto_enable: failed to enable RLS on %',
              cmd.object_identity;
      END;
     ELSE
        RAISE LOG
            'rls_auto_enable: skip % (either system schema or not in '
            'enforced list: %.)',
            cmd.object_identity,
            cmd.schema_name;
     END IF;
  END LOOP;
END;
$$;

CREATE OR REPLACE FUNCTION public.set_task_instance_status(p_instance_id uuid, p_new_status public.task_status, p_changed_at timestamp with time zone DEFAULT now()) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public'
    AS
$$
DECLARE
    v_log_id uuid;
    v_current_status task_status;
    v_user_id uuid;
BEGIN
    v_user_id := (
        SELECT ti.user_id
        FROM public.task_instances ti
        WHERE ti.id = p_instance_id
    );

    IF v_user_id IS NULL THEN
        RAISE EXCEPTION 'Task instance not found: %', p_instance_id;
    END IF;

    IF auth.uid() IS NOT NULL AND auth.uid() <> v_user_id THEN
        RAISE EXCEPTION 'Not allowed';
    END IF;

    v_current_status := public.get_current_task_instance_status(p_instance_id);

    IF v_current_status IS NOT NULL AND v_current_status = p_new_status THEN
        v_log_id := (
            SELECT tisl.id
            FROM public.task_instance_status_log tisl
            WHERE tisl.instance_changed_id = p_instance_id
            ORDER BY tisl.changed_at DESC, tisl.id DESC
            LIMIT 1
        );

        RETURN v_log_id;
    END IF;

    INSERT INTO public.task_instance_status_log (
        instance_changed_id,
        new_status_id,
        changed_at
    )
    VALUES (
        p_instance_id,
        p_new_status,
        COALESCE(p_changed_at, now())
    )
    RETURNING id INTO v_log_id;

    RETURN v_log_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.set_task_is_routine_from_rrule() RETURNS trigger
    LANGUAGE plpgsql
    AS
$$
DECLARE
    v_rrule TEXT := COALESCE(NEW.rrule, '');
    v_parent_is_routine BOOLEAN := FALSE;
BEGIN
    IF NEW.parent_task_id IS NOT NULL THEN
        v_parent_is_routine := COALESCE(
            (
                SELECT parent.is_routine
                FROM tasks parent
                WHERE parent.id = NEW.parent_task_id
            ),
            FALSE
        );
    END IF;

    NEW.is_routine := (
        v_parent_is_routine
        OR POSITION('FREQ=DAILY' IN UPPER(v_rrule)) > 0
        OR POSITION('FREQ=WEEKLY' IN UPPER(v_rrule)) > 0
    );
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.set_user_state(p_user_id uuid, p_state_id smallint, p_experienced_at timestamp with time zone DEFAULT now()) RETURNS uuid
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'public'
    AS
$$
WITH current_state AS (
    SELECT public.get_current_user_state_id(p_user_id) AS state_id
),
latest_row AS (
    SELECT usl.id
    FROM public.user_state_log usl
    WHERE usl.user_id = p_user_id
    ORDER BY usl.experienced_at DESC, usl.id DESC
    LIMIT 1
),
inserted_row AS (
    INSERT INTO public.user_state_log (user_id, state_id, experienced_at)
    SELECT
        p_user_id,
        p_state_id,
        COALESCE(p_experienced_at, now())
    FROM current_state
    WHERE current_state.state_id IS DISTINCT FROM p_state_id
    RETURNING id
)
SELECT id
FROM inserted_row
UNION ALL
SELECT id
FROM latest_row
WHERE NOT EXISTS (SELECT 1 FROM inserted_row)
LIMIT 1;
$$;

CREATE OR REPLACE FUNCTION public.sync_parent_task_from_latest_child_instances(p_task_id uuid, p_parent_instance_id uuid DEFAULT NULL::uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public'
    AS
$$
DECLARE
    v_parent_instance_id uuid;
    v_start_date timestamptz;
    v_due_date timestamptz;
    v_friction_id integer;
    v_consequence_id integer;
    v_size_id integer;
    v_max_size_dim_id integer;
    v_child_count integer := 0;
    v_mode text := 'latest';
BEGIN
    IF p_task_id IS NULL THEN
        RAISE EXCEPTION 'Parent task id is required.';
    END IF;

    SELECT COUNT(*)
    INTO v_child_count
    FROM public.tasks child_task
    WHERE child_task.parent_task_id = p_task_id;

    IF v_child_count = 0 THEN
        RETURN jsonb_build_object(
            'updated', false,
            'reason', 'no_children',
            'mode', v_mode
        );
    END IF;

    IF p_parent_instance_id IS NOT NULL THEN
        v_mode := 'specific_parent_instance';
        v_parent_instance_id := p_parent_instance_id;

        SELECT
            MIN(ti.start_date),
            MAX(ti.due_date)
        INTO
            v_start_date,
            v_due_date
        FROM public.task_instances ti
        JOIN public.tasks child_task
          ON child_task.id = ti.task_id
        WHERE child_task.parent_task_id = p_task_id
          AND ti.parent_instance_id = p_parent_instance_id;
    ELSE
        WITH latest_child_instances AS (
            SELECT DISTINCT ON (ti.task_id)
                ti.task_id,
                ti.start_date,
                ti.due_date
            FROM public.task_instances ti
            JOIN public.tasks child_task
              ON child_task.id = ti.task_id
            WHERE child_task.parent_task_id = p_task_id
            ORDER BY ti.task_id, ti.start_date DESC, ti.id DESC
        )
        SELECT
            MIN(lci.start_date),
            MAX(lci.due_date)
        INTO
            v_start_date,
            v_due_date
        FROM latest_child_instances lci;

        SELECT ti.id
        INTO v_parent_instance_id
        FROM public.task_instances ti
        WHERE ti.task_id = p_task_id
        ORDER BY ti.start_date DESC, ti.id DESC
        LIMIT 1;
    END IF;

    SELECT
        MAX(child_task.friction_id),
        MAX(child_task.consequence_id),
        MAX(child_task.size_id)
    INTO
        v_friction_id,
        v_consequence_id,
        v_size_id
    FROM public.tasks child_task
    WHERE child_task.parent_task_id = p_task_id;

    SELECT MAX(id)
    INTO v_max_size_dim_id
    FROM public.dim_task_sizes;

    IF v_size_id IS NOT NULL AND v_max_size_dim_id IS NOT NULL THEN
        v_size_id := LEAST(v_size_id + 1, v_max_size_dim_id);
    END IF;

    UPDATE public.tasks parent_task
    SET
        friction_id = COALESCE(v_friction_id, parent_task.friction_id),
        consequence_id = COALESCE(v_consequence_id, parent_task.consequence_id),
        size_id = COALESCE(v_size_id, parent_task.size_id),
        updated_at = now()
    WHERE parent_task.id = p_task_id;

    IF v_parent_instance_id IS NOT NULL AND v_start_date IS NOT NULL AND v_due_date IS NOT NULL THEN
        UPDATE public.task_instances parent_instance
        SET
            start_date = v_start_date,
            due_date = v_due_date,
            original_start_date = v_start_date,
            original_due_date = v_due_date
        WHERE parent_instance.id = v_parent_instance_id
          AND parent_instance.task_id = p_task_id;
    END IF;

    RETURN jsonb_build_object(
        'updated', true,
        'mode', v_mode,
        'parentTaskId', p_task_id,
        'parentInstanceId', v_parent_instance_id,
        'startDate', v_start_date,
        'dueDate', v_due_date,
        'frictionId', v_friction_id,
        'consequenceId', v_consequence_id,
        'sizeId', v_size_id
    );
END;
$$;

CREATE OR REPLACE FUNCTION public.sync_task_instance_status_from_due_date() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public'
    AS
$$
DECLARE
    v_current_status public.task_status;
    v_restore_status public.task_status;
BEGIN
    IF NEW.due_date IS NOT DISTINCT FROM OLD.due_date THEN
        RETURN NEW;
    END IF;

    v_current_status := public.get_current_task_instance_status(NEW.id);

    IF v_current_status IN (
        'ready'::public.task_status,
        'open'::public.task_status,
        'asleep'::public.task_status
    )
        AND NEW.due_date < now()
    THEN
        PERFORM public.set_task_instance_status(NEW.id, 'debt'::public.task_status);
    ELSIF v_current_status = 'debt'::public.task_status
        AND NEW.due_date >= now()
    THEN
        SELECT tisl.new_status_id
        INTO v_restore_status
        FROM public.task_instance_status_log tisl
        WHERE tisl.instance_changed_id = NEW.id
            AND tisl.new_status_id <> 'debt'::public.task_status
        ORDER BY tisl.changed_at DESC, tisl.id DESC
        LIMIT 1;

        PERFORM public.set_task_instance_status(
            NEW.id,
            COALESCE(v_restore_status, 'ready'::public.task_status)
        );
    END IF;

    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.update_task_series_from_instance(p_task_id uuid, p_instance_id uuid, p_list_id uuid, p_title text, p_description text DEFAULT NULL::text, p_rrule text DEFAULT NULL::text, p_size_id integer DEFAULT NULL::integer, p_consequence_id integer DEFAULT NULL::integer, p_friction_id integer DEFAULT NULL::integer, p_new_start_date timestamp with time zone DEFAULT NULL::timestamp with time zone, p_new_due_date timestamp with time zone DEFAULT NULL::timestamp with time zone) RETURNS void
    LANGUAGE plpgsql
    AS
$$
DECLARE
    v_user_id UUID := auth.uid();
    v_current_instance_id uuid;
    v_current_instance_number integer;
    v_current_instance_start_date timestamptz;
    v_current_instance_due_date timestamptz;
    v_current_instance_user_id uuid;
    v_current_instance_rrule text;
    v_start_delta INTERVAL;
    v_due_delta INTERVAL;
    v_instance RECORD;
BEGIN
    IF v_user_id IS NULL THEN
        RAISE EXCEPTION 'Not authenticated';
    END IF;

    v_current_instance_id := (
        SELECT ti.id
        FROM public.task_instances ti
        WHERE ti.id = p_instance_id
          AND ti.task_id = p_task_id
    );
    v_current_instance_number := (
        SELECT ti.instance_number
        FROM public.task_instances ti
        WHERE ti.id = p_instance_id
          AND ti.task_id = p_task_id
    );
    v_current_instance_start_date := (
        SELECT ti.start_date
        FROM public.task_instances ti
        WHERE ti.id = p_instance_id
          AND ti.task_id = p_task_id
    );
    v_current_instance_due_date := (
        SELECT ti.due_date
        FROM public.task_instances ti
        WHERE ti.id = p_instance_id
          AND ti.task_id = p_task_id
    );
    v_current_instance_user_id := (
        SELECT t.user_id
        FROM public.task_instances ti
        JOIN public.tasks t ON t.id = ti.task_id
        WHERE ti.id = p_instance_id
          AND ti.task_id = p_task_id
    );
    v_current_instance_rrule := (
        SELECT t.rrule
        FROM public.task_instances ti
        JOIN public.tasks t ON t.id = ti.task_id
        WHERE ti.id = p_instance_id
          AND ti.task_id = p_task_id
    );

    IF v_current_instance_id IS NULL THEN
        RAISE EXCEPTION 'Task instance not found';
    END IF;

    IF v_current_instance_user_id <> v_user_id THEN
        RAISE EXCEPTION 'Not allowed';
    END IF;

    IF v_current_instance_rrule IS NULL THEN
        RAISE EXCEPTION 'Series update requires a recurring task';
    END IF;

    IF p_new_start_date IS NULL OR p_new_due_date IS NULL THEN
        RAISE EXCEPTION 'New start and due dates are required';
    END IF;

    IF p_new_due_date < p_new_start_date THEN
        RAISE EXCEPTION 'Due date must be later than or equal to start date';
    END IF;

    UPDATE public.tasks
    SET
        list_id = p_list_id,
        title = p_title,
        description = p_description,
        rrule = p_rrule,
        size_id = p_size_id,
        consequence_id = p_consequence_id,
        friction_id = p_friction_id
    WHERE id = p_task_id
      AND user_id = v_user_id;

    v_start_delta := p_new_start_date - v_current_instance_start_date;
    v_due_delta := p_new_due_date - v_current_instance_due_date;

    FOR v_instance IN
        SELECT id, instance_number, start_date, due_date, is_exception
        FROM public.task_instances
        WHERE task_id = p_task_id
          AND instance_number >= v_current_instance_number
        ORDER BY instance_number
    LOOP
        IF public.get_current_task_instance_status(v_instance.id) IN ('completed', 'archived') THEN
            CONTINUE;
        END IF;

        IF v_instance.instance_number > v_current_instance_number
           AND COALESCE(v_instance.is_exception, FALSE) THEN
            CONTINUE;
        END IF;

        IF v_instance.id = p_instance_id THEN
            UPDATE public.task_instances
            SET
                start_date = p_new_start_date,
                due_date = p_new_due_date,
                original_start_date = p_new_start_date,
                original_due_date = p_new_due_date,
                is_exception = FALSE
            WHERE id = v_instance.id;
        ELSE
            UPDATE public.task_instances
            SET
                start_date = v_instance.start_date + v_start_delta,
                due_date = v_instance.due_date + v_due_delta,
                original_start_date = v_instance.start_date + v_start_delta,
                original_due_date = v_instance.due_date + v_due_delta,
                is_exception = FALSE
            WHERE id = v_instance.id;
        END IF;
    END LOOP;
END;
$$;

-- ============================================
-- Indexes
-- ============================================
CREATE INDEX idx_instances_due_date ON public.task_instances USING btree (due_date);

CREATE INDEX idx_instances_start_date ON public.task_instances USING btree (start_date);

CREATE INDEX idx_instances_task_id ON public.task_instances USING btree (task_id);

CREATE INDEX idx_logs_instance_chrono ON public.task_instance_status_log USING btree (instance_changed_id, changed_at DESC, id DESC);

CREATE INDEX idx_logs_status_changed ON public.task_instance_status_log USING btree (new_status_id, changed_at DESC, instance_changed_id, id DESC);

CREATE INDEX idx_task_instances_parent_id ON public.task_instances USING btree (parent_instance_id);

CREATE INDEX idx_tasks_parent_id ON public.tasks USING btree (parent_task_id);

CREATE INDEX idx_tasks_user_id ON public.tasks USING btree (user_id);

CREATE UNIQUE INDEX idx_unique_user_list_name_case_insensitive ON public.lists USING btree (user_id, lower((name)::text));

CREATE INDEX idx_user_state_log_user_experienced_at ON public.user_state_log USING btree (user_id, experienced_at DESC);

-- ============================================
-- Constraints
-- ============================================
ALTER TABLE ONLY public.dim_task_consequences
    ADD CONSTRAINT dim_task_consequences_label_key UNIQUE (label);

ALTER TABLE ONLY public.dim_task_consequences
    ADD CONSTRAINT dim_task_consequences_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.dim_task_frictions
    ADD CONSTRAINT dim_task_frictions_label_key UNIQUE (label);

ALTER TABLE ONLY public.dim_task_frictions
    ADD CONSTRAINT dim_task_frictions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.dim_task_sizes
    ADD CONSTRAINT dim_task_sizes_label_key UNIQUE (label);

ALTER TABLE ONLY public.dim_task_sizes
    ADD CONSTRAINT dim_task_sizes_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.lists
    ADD CONSTRAINT lists_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.personas
    ADD CONSTRAINT personas_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.profiles
    ADD CONSTRAINT profiles_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.states
    ADD CONSTRAINT states_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.task_instance_status_log
    ADD CONSTRAINT task_instance_status_log_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.task_instances
    ADD CONSTRAINT task_instances_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.user_state_log
    ADD CONSTRAINT user_state_log_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.lists
    ADD CONSTRAINT lists_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.profiles
    ADD CONSTRAINT profiles_id_fkey FOREIGN KEY (id) REFERENCES auth.users(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.profiles
    ADD CONSTRAINT profiles_persona_id_fkey FOREIGN KEY (persona_id) REFERENCES public.personas(id);

ALTER TABLE ONLY public.task_instance_status_log
    ADD CONSTRAINT task_instance_status_log_instance_changed_id_fkey FOREIGN KEY (instance_changed_id) REFERENCES public.task_instances(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.task_instances
    ADD CONSTRAINT task_instances_actual_friction_id_fkey FOREIGN KEY (actual_friction_id) REFERENCES public.dim_task_frictions(id);

ALTER TABLE ONLY public.task_instances
    ADD CONSTRAINT task_instances_parent_instance_id_fkey FOREIGN KEY (parent_instance_id) REFERENCES public.task_instances(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.task_instances
    ADD CONSTRAINT task_instances_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_consequence_id_fkey FOREIGN KEY (consequence_id) REFERENCES public.dim_task_consequences(id);

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_friction_id_fkey FOREIGN KEY (friction_id) REFERENCES public.dim_task_frictions(id);

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_list_id_fkey FOREIGN KEY (list_id) REFERENCES public.lists(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_parent_task_id_fkey FOREIGN KEY (parent_task_id) REFERENCES public.tasks(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_size_id_fkey FOREIGN KEY (size_id) REFERENCES public.dim_task_sizes(id);

ALTER TABLE ONLY public.user_state_log
    ADD CONSTRAINT user_state_log_state_id_fkey FOREIGN KEY (state_id) REFERENCES public.states(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.user_state_log
    ADD CONSTRAINT user_state_log_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;

-- ============================================
-- Triggers
-- ============================================
CREATE OR REPLACE TRIGGER trg_create_default_list AFTER INSERT ON public.profiles FOR EACH ROW EXECUTE FUNCTION public.fn_create_default_list_for_new_user();

CREATE OR REPLACE TRIGGER trg_set_task_is_routine_from_rrule BEFORE INSERT OR UPDATE OF rrule, parent_task_id ON public.tasks FOR EACH ROW EXECUTE FUNCTION public.set_task_is_routine_from_rrule();

CREATE OR REPLACE TRIGGER trg_sync_task_instance_status_from_due_date AFTER UPDATE OF due_date ON public.task_instances FOR EACH ROW EXECUTE FUNCTION public.sync_task_instance_status_from_due_date();

CREATE OR REPLACE TRIGGER trg_task_instances_inherit_user_id BEFORE INSERT ON public.task_instances FOR EACH ROW EXECUTE FUNCTION public.fn_set_instance_user_id_from_task();

CREATE OR REPLACE TRIGGER trg_task_instances_initial_status AFTER INSERT ON public.task_instances FOR EACH ROW EXECUTE FUNCTION public.ensure_initial_task_instance_status();

CREATE OR REPLACE TRIGGER trg_task_instances_updated_at BEFORE UPDATE ON public.task_instances FOR EACH ROW EXECUTE FUNCTION public.fn_update_timestamp();

CREATE OR REPLACE TRIGGER trg_tasks_inherit_user_id BEFORE INSERT ON public.tasks FOR EACH ROW EXECUTE FUNCTION public.fn_set_task_user_id_from_list();

CREATE OR REPLACE TRIGGER trg_tasks_updated_at BEFORE UPDATE ON public.tasks FOR EACH ROW EXECUTE FUNCTION public.fn_update_timestamp();

-- ============================================
-- Policies and RLS
-- ============================================
CREATE POLICY "Manage own profile" ON public.profiles USING ((auth.uid() = id)) WITH CHECK ((auth.uid() = id));

CREATE POLICY "Public read dim_task_consequences" ON public.dim_task_consequences FOR SELECT TO authenticated, anon USING (true);

CREATE POLICY "Public read dim_task_frictions" ON public.dim_task_frictions FOR SELECT TO authenticated, anon USING (true);

CREATE POLICY "Public read dim_task_sizes" ON public.dim_task_sizes FOR SELECT TO authenticated, anon USING (true);

CREATE POLICY "Public read personas" ON public.personas FOR SELECT TO authenticated, anon USING (true);

CREATE POLICY "Public read states" ON public.states FOR SELECT TO authenticated, anon USING (true);

CREATE POLICY "Service role can insert task status logs" ON public.task_instance_status_log FOR INSERT TO service_role WITH CHECK (true);

CREATE POLICY "Service role can read task status logs" ON public.task_instance_status_log FOR SELECT TO service_role USING (true);

CREATE POLICY "System can insert state logs" ON public.user_state_log FOR INSERT WITH CHECK (((auth.uid() = user_id) OR (auth.role() = 'service_role'::text)));

CREATE POLICY "Users allowed only to instances of their tasks" ON public.task_instances USING ((auth.uid() = user_id)) WITH CHECK ((auth.uid() = user_id));

CREATE POLICY "Users allowed only to their lists" ON public.lists USING ((auth.uid() = user_id));

CREATE POLICY "Users allowed only to their tasks" ON public.tasks USING ((auth.uid() = user_id)) WITH CHECK ((auth.uid() = user_id));

CREATE POLICY "Users can delete their own task status log" ON public.task_instance_status_log FOR DELETE USING ((EXISTS ( SELECT 1
   FROM public.task_instances ti
  WHERE ((ti.id = task_instance_status_log.instance_changed_id) AND (ti.user_id = auth.uid())))));

CREATE POLICY "Users can insert their own task status log" ON public.task_instance_status_log FOR INSERT WITH CHECK ((EXISTS ( SELECT 1
   FROM public.task_instances ti
  WHERE ((ti.id = task_instance_status_log.instance_changed_id) AND (ti.user_id = auth.uid())))));

CREATE POLICY "Users can view their own state log" ON public.user_state_log FOR SELECT USING ((auth.uid() = user_id));

CREATE POLICY "Users can view their own task status log" ON public.task_instance_status_log FOR SELECT USING ((EXISTS ( SELECT 1
   FROM public.task_instances ti
  WHERE ((ti.id = task_instance_status_log.instance_changed_id) AND (ti.user_id = auth.uid())))));

ALTER TABLE public.dim_task_consequences ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.dim_task_frictions ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.dim_task_sizes ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.lists ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.personas ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.states ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.task_instance_status_log ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.task_instances ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.tasks ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.user_state_log ENABLE ROW LEVEL SECURITY;

-- ============================================
-- Grants
-- ============================================
GRANT USAGE ON SCHEMA public TO anon;

GRANT USAGE ON SCHEMA public TO authenticated;

GRANT USAGE ON SCHEMA public TO service_role;

GRANT ALL ON FUNCTION public.age_task_instance_statuses(p_debt_days integer, p_stale_days integer, p_dry_run boolean) TO authenticated;

GRANT ALL ON FUNCTION public.age_task_instance_statuses(p_debt_days integer, p_stale_days integer, p_dry_run boolean) TO service_role;

GRANT ALL ON FUNCTION public.create_task_and_instances(p_list_id uuid, p_title text, p_description text, p_start_date timestamp with time zone, p_due_date timestamp with time zone, p_parent_task_id uuid, p_parent_instance_number integer, p_rrule text, p_size_id integer, p_consequence_id integer, p_friction_id integer, p_is_adaptive boolean) TO authenticated;

GRANT ALL ON FUNCTION public.create_task_and_instances(p_list_id uuid, p_title text, p_description text, p_start_date timestamp with time zone, p_due_date timestamp with time zone, p_parent_task_id uuid, p_parent_instance_number integer, p_rrule text, p_size_id integer, p_consequence_id integer, p_friction_id integer, p_is_adaptive boolean) TO service_role;

GRANT ALL ON FUNCTION public.default_preference_settings() TO authenticated;

GRANT ALL ON FUNCTION public.default_preference_settings() TO service_role;

GRANT ALL ON FUNCTION public.delete_task_by_policy(p_task_id uuid, p_instance_id uuid, p_scope text, p_keep_worthy boolean) TO authenticated;

GRANT ALL ON FUNCTION public.delete_task_by_policy(p_task_id uuid, p_instance_id uuid, p_scope text, p_keep_worthy boolean) TO service_role;

GRANT ALL ON FUNCTION public.ensure_initial_task_instance_status() TO authenticated;

GRANT ALL ON FUNCTION public.ensure_initial_task_instance_status() TO service_role;

GRANT ALL ON FUNCTION public.fn_create_default_list_for_new_user() TO authenticated;

GRANT ALL ON FUNCTION public.fn_create_default_list_for_new_user() TO service_role;

GRANT ALL ON FUNCTION public.fn_enforce_task_exclusivity() TO authenticated;

GRANT ALL ON FUNCTION public.fn_enforce_task_exclusivity() TO service_role;

GRANT ALL ON FUNCTION public.fn_set_instance_user_id_from_task() TO authenticated;

GRANT ALL ON FUNCTION public.fn_set_instance_user_id_from_task() TO service_role;

GRANT ALL ON FUNCTION public.fn_set_task_user_id_from_list() TO authenticated;

GRANT ALL ON FUNCTION public.fn_set_task_user_id_from_list() TO service_role;

GRANT ALL ON FUNCTION public.fn_update_timestamp() TO authenticated;

GRANT ALL ON FUNCTION public.fn_update_timestamp() TO service_role;

GRANT ALL ON FUNCTION public.gen_uuid_v7() TO authenticated;

GRANT ALL ON FUNCTION public.gen_uuid_v7() TO service_role;

GRANT ALL ON FUNCTION public.get_current_task_instance_status(p_instance_id uuid) TO authenticated;

GRANT ALL ON FUNCTION public.get_current_task_instance_status(p_instance_id uuid) TO service_role;

GRANT ALL ON FUNCTION public.get_current_user_state_id(p_user_id uuid) TO authenticated;

GRANT ALL ON FUNCTION public.get_current_user_state_id(p_user_id uuid) TO service_role;

GRANT ALL ON FUNCTION public.get_task_delete_context(p_task_id uuid, p_instance_id uuid) TO authenticated;

GRANT ALL ON FUNCTION public.get_task_delete_context(p_task_id uuid, p_instance_id uuid) TO service_role;

GRANT ALL ON FUNCTION public.get_tomorrow_midnight() TO authenticated;

GRANT ALL ON FUNCTION public.get_tomorrow_midnight() TO service_role;

GRANT ALL ON FUNCTION public.get_user_session_summaries(p_user_id uuid, p_limit integer) TO authenticated;

GRANT ALL ON FUNCTION public.get_user_session_summaries(p_user_id uuid, p_limit integer) TO service_role;

GRANT ALL ON FUNCTION public.get_user_state_time_summary(p_user_id uuid, p_date_from timestamp with time zone, p_date_to timestamp with time zone) TO authenticated;

GRANT ALL ON FUNCTION public.get_user_state_time_summary(p_user_id uuid, p_date_from timestamp with time zone, p_date_to timestamp with time zone) TO service_role;

GRANT ALL ON FUNCTION public.get_user_task_rows() TO authenticated;

GRANT ALL ON FUNCTION public.get_user_task_rows() TO service_role;

GRANT ALL ON FUNCTION public.is_worthy_instance_family(p_parent_instance_id uuid) TO authenticated;

GRANT ALL ON FUNCTION public.is_worthy_instance_family(p_parent_instance_id uuid) TO service_role;

GRANT ALL ON FUNCTION public.is_worthy_task_instance(p_instance_id uuid) TO authenticated;

GRANT ALL ON FUNCTION public.is_worthy_task_instance(p_instance_id uuid) TO service_role;

GRANT ALL ON FUNCTION public.rls_auto_enable() TO authenticated;

GRANT ALL ON FUNCTION public.rls_auto_enable() TO service_role;

GRANT ALL ON FUNCTION public.set_task_instance_status(p_instance_id uuid, p_new_status public.task_status, p_changed_at timestamp with time zone) TO authenticated;

GRANT ALL ON FUNCTION public.set_task_instance_status(p_instance_id uuid, p_new_status public.task_status, p_changed_at timestamp with time zone) TO service_role;

GRANT ALL ON FUNCTION public.set_task_is_routine_from_rrule() TO authenticated;

GRANT ALL ON FUNCTION public.set_task_is_routine_from_rrule() TO service_role;

GRANT ALL ON FUNCTION public.set_user_state(p_user_id uuid, p_state_id smallint, p_experienced_at timestamp with time zone) TO authenticated;

GRANT ALL ON FUNCTION public.set_user_state(p_user_id uuid, p_state_id smallint, p_experienced_at timestamp with time zone) TO service_role;

GRANT ALL ON FUNCTION public.sync_parent_task_from_latest_child_instances(p_task_id uuid, p_parent_instance_id uuid) TO authenticated;

GRANT ALL ON FUNCTION public.sync_parent_task_from_latest_child_instances(p_task_id uuid, p_parent_instance_id uuid) TO service_role;

GRANT ALL ON FUNCTION public.sync_task_instance_status_from_due_date() TO authenticated;

GRANT ALL ON FUNCTION public.sync_task_instance_status_from_due_date() TO service_role;

GRANT ALL ON FUNCTION public.update_task_series_from_instance(p_task_id uuid, p_instance_id uuid, p_list_id uuid, p_title text, p_description text, p_rrule text, p_size_id integer, p_consequence_id integer, p_friction_id integer, p_new_start_date timestamp with time zone, p_new_due_date timestamp with time zone) TO authenticated;

GRANT ALL ON FUNCTION public.update_task_series_from_instance(p_task_id uuid, p_instance_id uuid, p_list_id uuid, p_title text, p_description text, p_rrule text, p_size_id integer, p_consequence_id integer, p_friction_id integer, p_new_start_date timestamp with time zone, p_new_due_date timestamp with time zone) TO service_role;

GRANT ALL ON TABLE public.dim_task_consequences TO anon;

GRANT ALL ON TABLE public.dim_task_consequences TO authenticated;

GRANT ALL ON TABLE public.dim_task_consequences TO service_role;

GRANT ALL ON SEQUENCE public.dim_task_consequences_id_seq TO anon;

GRANT ALL ON SEQUENCE public.dim_task_consequences_id_seq TO authenticated;

GRANT ALL ON SEQUENCE public.dim_task_consequences_id_seq TO service_role;

GRANT ALL ON TABLE public.dim_task_frictions TO anon;

GRANT ALL ON TABLE public.dim_task_frictions TO authenticated;

GRANT ALL ON TABLE public.dim_task_frictions TO service_role;

GRANT ALL ON SEQUENCE public.dim_task_frictions_id_seq TO anon;

GRANT ALL ON SEQUENCE public.dim_task_frictions_id_seq TO authenticated;

GRANT ALL ON SEQUENCE public.dim_task_frictions_id_seq TO service_role;

GRANT ALL ON TABLE public.dim_task_sizes TO anon;

GRANT ALL ON TABLE public.dim_task_sizes TO authenticated;

GRANT ALL ON TABLE public.dim_task_sizes TO service_role;

GRANT ALL ON SEQUENCE public.dim_task_sizes_id_seq TO anon;

GRANT ALL ON SEQUENCE public.dim_task_sizes_id_seq TO authenticated;

GRANT ALL ON SEQUENCE public.dim_task_sizes_id_seq TO service_role;

GRANT ALL ON TABLE public.lists TO anon;

GRANT ALL ON TABLE public.lists TO authenticated;

GRANT ALL ON TABLE public.lists TO service_role;

GRANT ALL ON TABLE public.personas TO anon;

GRANT ALL ON TABLE public.personas TO authenticated;

GRANT ALL ON TABLE public.personas TO service_role;

GRANT ALL ON SEQUENCE public.personas_id_seq TO anon;

GRANT ALL ON SEQUENCE public.personas_id_seq TO authenticated;

GRANT ALL ON SEQUENCE public.personas_id_seq TO service_role;

GRANT ALL ON TABLE public.profiles TO anon;

GRANT ALL ON TABLE public.profiles TO authenticated;

GRANT ALL ON TABLE public.profiles TO service_role;

GRANT ALL ON TABLE public.states TO anon;

GRANT ALL ON TABLE public.states TO authenticated;

GRANT ALL ON TABLE public.states TO service_role;

GRANT ALL ON SEQUENCE public.states_id_seq TO anon;

GRANT ALL ON SEQUENCE public.states_id_seq TO authenticated;

GRANT ALL ON SEQUENCE public.states_id_seq TO service_role;

GRANT ALL ON TABLE public.task_instance_status_log TO anon;

GRANT ALL ON TABLE public.task_instance_status_log TO authenticated;

GRANT ALL ON TABLE public.task_instance_status_log TO service_role;

GRANT ALL ON TABLE public.task_instances TO anon;

GRANT ALL ON TABLE public.task_instances TO authenticated;

GRANT ALL ON TABLE public.task_instances TO service_role;

GRANT ALL ON TABLE public.tasks TO anon;

GRANT ALL ON TABLE public.tasks TO authenticated;

GRANT ALL ON TABLE public.tasks TO service_role;

GRANT ALL ON TABLE public.user_state_log TO anon;

GRANT ALL ON TABLE public.user_state_log TO authenticated;

GRANT ALL ON TABLE public.user_state_log TO service_role;
