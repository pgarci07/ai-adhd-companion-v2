CREATE OR REPLACE FUNCTION is_worthy_task_instance(p_instance_id UUID)
RETURNS BOOLEAN AS $$
    SELECT EXISTS (
        SELECT 1
        FROM task_instances
        WHERE id = p_instance_id
          AND status = 'completed'
          AND (
              actual_friction_id IS NOT NULL
              OR actual_duration IS NOT NULL
              OR final_comments IS NOT NULL
          )
    );
$$ LANGUAGE sql STABLE;


CREATE OR REPLACE FUNCTION is_worthy_instance_family(p_parent_instance_id UUID)
RETURNS BOOLEAN AS $$
    SELECT
        is_worthy_task_instance(p_parent_instance_id)
        OR EXISTS (
            SELECT 1
            FROM task_instances child
            WHERE child.parent_instance_id = p_parent_instance_id
              AND is_worthy_task_instance(child.id)
        );
$$ LANGUAGE sql STABLE;


CREATE OR REPLACE FUNCTION get_task_delete_context(
    p_task_id UUID,
    p_instance_id UUID
) RETURNS jsonb AS $$
DECLARE
    v_user_id UUID := auth.uid();
    v_task RECORD;
    v_instance RECORD;
    v_current_worthy BOOLEAN := FALSE;
    v_current_family_worthy BOOLEAN := FALSE;
    v_all_worthy_count INTEGER := 0;
    v_future_worthy_count INTEGER := 0;
BEGIN
    IF v_user_id IS NULL THEN
        RAISE EXCEPTION 'Not authenticated';
    END IF;

    SELECT
        t.id,
        t.user_id,
        t.rrule,
        t.parent_task_id,
        EXISTS (
            SELECT 1
            FROM tasks child_task
            WHERE child_task.parent_task_id = t.id
        ) AS has_subtasks
    INTO v_task
    FROM tasks t
    WHERE t.id = p_task_id;

    IF NOT FOUND OR v_task.user_id <> v_user_id THEN
        RAISE EXCEPTION 'Task not found or not allowed';
    END IF;

    SELECT
        ti.id,
        ti.instance_number,
        ti.parent_instance_id
    INTO v_instance
    FROM task_instances ti
    WHERE ti.id = p_instance_id
      AND ti.task_id = p_task_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Task instance not found';
    END IF;

    IF v_task.has_subtasks THEN
        v_current_family_worthy := is_worthy_instance_family(v_instance.id);

        SELECT COUNT(*)
        INTO v_all_worthy_count
        FROM task_instances parent_instance
        WHERE parent_instance.task_id = p_task_id
          AND is_worthy_instance_family(parent_instance.id);

        SELECT COUNT(*)
        INTO v_future_worthy_count
        FROM task_instances parent_instance
        WHERE parent_instance.task_id = p_task_id
          AND parent_instance.instance_number >= v_instance.instance_number
          AND is_worthy_instance_family(parent_instance.id);
    ELSE
        v_current_worthy := is_worthy_task_instance(v_instance.id);
        v_current_family_worthy := v_current_worthy;

        SELECT COUNT(*)
        INTO v_all_worthy_count
        FROM task_instances ti
        WHERE ti.task_id = p_task_id
          AND is_worthy_task_instance(ti.id);

        SELECT COUNT(*)
        INTO v_future_worthy_count
        FROM task_instances ti
        WHERE ti.task_id = p_task_id
          AND ti.instance_number >= v_instance.instance_number
          AND is_worthy_task_instance(ti.id);
    END IF;

    RETURN jsonb_build_object(
        'is_recurring', v_task.rrule IS NOT NULL,
        'has_subtasks', v_task.has_subtasks,
        'instance_number', v_instance.instance_number,
        'allow_all', v_instance.instance_number > 1,
        'current_worthy', v_current_worthy,
        'current_family_worthy', v_current_family_worthy,
        'all_worthy_count', v_all_worthy_count,
        'future_worthy_count', v_future_worthy_count
    );
END;
$$ LANGUAGE plpgsql SECURITY INVOKER;


CREATE OR REPLACE FUNCTION delete_task_by_policy(
    p_task_id UUID,
    p_instance_id UUID,
    p_scope TEXT,
    p_keep_worthy BOOLEAN DEFAULT FALSE
) RETURNS VOID AS $$
DECLARE
    v_user_id UUID := auth.uid();
    v_task RECORD;
    v_instance RECORD;
BEGIN
    IF v_user_id IS NULL THEN
        RAISE EXCEPTION 'Not authenticated';
    END IF;

    SELECT
        t.id,
        t.user_id,
        t.rrule,
        t.parent_task_id,
        EXISTS (
            SELECT 1
            FROM tasks child_task
            WHERE child_task.parent_task_id = t.id
        ) AS has_subtasks
    INTO v_task
    FROM tasks t
    WHERE t.id = p_task_id;

    IF NOT FOUND OR v_task.user_id <> v_user_id THEN
        RAISE EXCEPTION 'Task not found or not allowed';
    END IF;

    SELECT
        ti.id,
        ti.instance_number
    INTO v_instance
    FROM task_instances ti
    WHERE ti.id = p_instance_id
      AND ti.task_id = p_task_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Task instance not found';
    END IF;

    IF v_task.rrule IS NULL THEN
        DELETE FROM task_instances WHERE task_id = p_task_id;
        DELETE FROM tasks WHERE id = p_task_id;
        RETURN;
    END IF;

    IF p_scope = 'current' THEN
        DELETE FROM task_instances WHERE id = p_instance_id;
        RETURN;
    END IF;

    IF p_scope = 'future' THEN
        DELETE FROM task_instances
        WHERE task_id = p_task_id
          AND instance_number >= v_instance.instance_number;

        UPDATE tasks
        SET is_active = FALSE
        WHERE id = p_task_id;
        RETURN;
    END IF;

    IF p_scope = 'all' THEN
        IF p_keep_worthy THEN
            IF v_task.has_subtasks THEN
                DELETE FROM task_instances
                WHERE task_id = p_task_id
                  AND NOT is_worthy_instance_family(id);
            ELSE
                DELETE FROM task_instances
                WHERE task_id = p_task_id
                  AND NOT is_worthy_task_instance(id);
            END IF;

            UPDATE tasks
            SET is_active = FALSE
            WHERE id = p_task_id;
        ELSE
            DELETE FROM task_instances WHERE task_id = p_task_id;
            DELETE FROM tasks WHERE id = p_task_id;
        END IF;
        RETURN;
    END IF;

    RAISE EXCEPTION 'Unsupported delete scope: %', p_scope;
END;
$$ LANGUAGE plpgsql SECURITY INVOKER;
