CREATE OR REPLACE FUNCTION public.delete_task_by_policy(
    "p_task_id" uuid,
    "p_instance_id" uuid,
    "p_scope" text,
    "p_keep_worthy" boolean DEFAULT false
) RETURNS void
    LANGUAGE plpgsql
    AS $$
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

GRANT EXECUTE ON FUNCTION public.delete_task_by_policy(
    "p_task_id" uuid,
    "p_instance_id" uuid,
    "p_scope" text,
    "p_keep_worthy" boolean
) TO authenticated;

GRANT EXECUTE ON FUNCTION public.delete_task_by_policy(
    "p_task_id" uuid,
    "p_instance_id" uuid,
    "p_scope" text,
    "p_keep_worthy" boolean
) TO service_role;


CREATE OR REPLACE FUNCTION public.get_task_delete_context(
    "p_task_id" uuid,
    "p_instance_id" uuid
) RETURNS jsonb
    LANGUAGE plpgsql
    AS $$
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

GRANT EXECUTE ON FUNCTION public.get_task_delete_context(
    "p_task_id" uuid,
    "p_instance_id" uuid
) TO authenticated;

GRANT EXECUTE ON FUNCTION public.get_task_delete_context(
    "p_task_id" uuid,
    "p_instance_id" uuid
) TO service_role;
