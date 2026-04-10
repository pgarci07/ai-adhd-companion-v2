CREATE OR REPLACE FUNCTION update_task_series_from_instance(
    p_task_id UUID,
    p_instance_id UUID,
    p_list_id UUID,
    p_title TEXT,
    p_description TEXT DEFAULT NULL,
    p_rrule TEXT DEFAULT NULL,
    p_size_id INT DEFAULT NULL,
    p_consequence_id INT DEFAULT NULL,
    p_friction_id INT DEFAULT NULL,
    p_new_start_date TIMESTAMPTZ DEFAULT NULL,
    p_new_due_date TIMESTAMPTZ DEFAULT NULL
) RETURNS VOID AS $$
DECLARE
    v_user_id UUID := auth.uid();
    v_current_instance RECORD;
    v_start_delta INTERVAL;
    v_due_delta INTERVAL;
    v_instance RECORD;
BEGIN
    IF v_user_id IS NULL THEN
        RAISE EXCEPTION 'Not authenticated';
    END IF;

    SELECT
        ti.id,
        ti.task_id,
        ti.instance_number,
        ti.start_date,
        ti.due_date,
        t.user_id,
        t.rrule
    INTO v_current_instance
    FROM task_instances ti
    JOIN tasks t ON t.id = ti.task_id
    WHERE ti.id = p_instance_id
      AND ti.task_id = p_task_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Task instance not found';
    END IF;

    IF v_current_instance.user_id <> v_user_id THEN
        RAISE EXCEPTION 'Not allowed';
    END IF;

    IF v_current_instance.rrule IS NULL THEN
        RAISE EXCEPTION 'Series update requires a recurring task';
    END IF;

    IF p_new_start_date IS NULL OR p_new_due_date IS NULL THEN
        RAISE EXCEPTION 'New start and due dates are required';
    END IF;

    IF p_new_due_date < p_new_start_date THEN
        RAISE EXCEPTION 'Due date must be later than or equal to start date';
    END IF;

    UPDATE tasks
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

    v_start_delta := p_new_start_date - v_current_instance.start_date;
    v_due_delta := p_new_due_date - v_current_instance.due_date;

    FOR v_instance IN
        SELECT id, instance_number, start_date, due_date, status, is_exception
        FROM task_instances
        WHERE task_id = p_task_id
          AND instance_number >= v_current_instance.instance_number
        ORDER BY instance_number
    LOOP
        IF v_instance.status IN ('completed', 'archived') THEN
            CONTINUE;
        END IF;

        IF v_instance.instance_number > v_current_instance.instance_number
           AND COALESCE(v_instance.is_exception, FALSE) THEN
            CONTINUE;
        END IF;

        IF v_instance.id = p_instance_id THEN
            UPDATE task_instances
            SET
                start_date = p_new_start_date,
                due_date = p_new_due_date,
                original_start_date = p_new_start_date,
                original_due_date = p_new_due_date,
                is_exception = FALSE
            WHERE id = v_instance.id;
        ELSE
            UPDATE task_instances
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
$$ LANGUAGE plpgsql SECURITY INVOKER;
