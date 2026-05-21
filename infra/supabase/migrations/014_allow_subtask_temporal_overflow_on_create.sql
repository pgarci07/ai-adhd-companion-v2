CREATE OR REPLACE FUNCTION public.create_task_and_instances(
    "p_list_id" uuid,
    "p_title" text,
    "p_description" text DEFAULT NULL::text,
    "p_start_date" timestamp with time zone DEFAULT NULL::timestamp with time zone,
    "p_due_date" timestamp with time zone DEFAULT NULL::timestamp with time zone,
    "p_parent_task_id" uuid DEFAULT NULL::uuid,
    "p_parent_instance_number" integer DEFAULT 1,
    "p_rrule" text DEFAULT NULL::text,
    "p_size_id" integer DEFAULT NULL::integer,
    "p_consequence_id" integer DEFAULT NULL::integer,
    "p_friction_id" integer DEFAULT NULL::integer,
    "p_is_adaptive" boolean DEFAULT true
) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
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

ALTER FUNCTION public.create_task_and_instances(
    "p_list_id" uuid,
    "p_title" text,
    "p_description" text,
    "p_start_date" timestamp with time zone,
    "p_due_date" timestamp with time zone,
    "p_parent_task_id" uuid,
    "p_parent_instance_number" integer,
    "p_rrule" text,
    "p_size_id" integer,
    "p_consequence_id" integer,
    "p_friction_id" integer,
    "p_is_adaptive" boolean
) OWNER TO postgres;

GRANT EXECUTE ON FUNCTION public.create_task_and_instances(
    uuid,
    text,
    text,
    timestamp with time zone,
    timestamp with time zone,
    uuid,
    integer,
    text,
    integer,
    integer,
    integer,
    boolean
) TO authenticated;
GRANT EXECUTE ON FUNCTION public.create_task_and_instances(
    uuid,
    text,
    text,
    timestamp with time zone,
    timestamp with time zone,
    uuid,
    integer,
    text,
    integer,
    integer,
    integer,
    boolean
) TO service_role;
