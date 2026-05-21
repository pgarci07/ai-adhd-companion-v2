CREATE OR REPLACE FUNCTION public.sync_parent_task_from_latest_child_instances(
    p_task_id uuid
) RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO public
AS $$
DECLARE
    v_parent_instance_id uuid;
    v_start_date timestamptz;
    v_due_date timestamptz;
    v_friction_id integer;
    v_consequence_id integer;
    v_size_id integer;
    v_max_size_dim_id integer;
    v_child_count integer := 0;
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
            'reason', 'no_children'
        );
    END IF;

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

    SELECT ti.id
    INTO v_parent_instance_id
    FROM public.task_instances ti
    WHERE ti.task_id = p_task_id
    ORDER BY ti.start_date DESC, ti.id DESC
    LIMIT 1;

    IF v_parent_instance_id IS NOT NULL AND v_start_date IS NOT NULL AND v_due_date IS NOT NULL THEN
        -- The parent task window shown in the UI comes from task_instances, so
        -- for edit/delete recalculations we refresh the latest parent instance.
        UPDATE public.task_instances parent_instance
        SET
            start_date = v_start_date,
            due_date = v_due_date,
            original_start_date = v_start_date,
            original_due_date = v_due_date
        WHERE parent_instance.id = v_parent_instance_id;
    END IF;

    RETURN jsonb_build_object(
        'updated', true,
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

GRANT EXECUTE ON FUNCTION public.sync_parent_task_from_latest_child_instances(uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION public.sync_parent_task_from_latest_child_instances(uuid) TO service_role;
