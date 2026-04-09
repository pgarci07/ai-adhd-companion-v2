-- 10. CREATE TASK FUNCTION (STORED PROCEDURE)
-- A safe mechanism to create a new task with first instance
CREATE OR REPLACE FUNCTION create_task_and_instances(
    p_list_id UUID,
    p_title TEXT,
    p_description TEXT DEFAULT NULL,
    p_start_date TIMESTAMPTZ DEFAULT NULL,
    p_due_date TIMESTAMPTZ DEFAULT NULL,
    p_parent_task_id UUID DEFAULT NULL,
    p_parent_instance_number INT DEFAULT 1,
    p_rrule TEXT DEFAULT NULL,
    p_size_id INT DEFAULT NULL,
    p_consequence_id INT DEFAULT NULL,
    p_friction_id INT DEFAULT NULL,
    p_is_adaptive BOOLEAN DEFAULT TRUE
) RETURNS UUID AS $$
DECLARE
    v_user_id UUID := auth.uid();
    v_new_task_id UUID;
    v_exec_period INTERVAL;
    v_start_delay INTERVAL;
    v_first_parent_start TIMESTAMPTZ;
    v_current_instance RECORD;
    v_calc_start TIMESTAMPTZ;
    v_calc_due TIMESTAMPTZ;
    v_is_first BOOLEAN := TRUE;
BEGIN
    -- 1. INITIAL VALIDATION
    IF v_user_id IS NULL THEN RAISE EXCEPTION 'Not authenticated'; END IF;

    -- Nesting check: No sub-subtasks allowed
    IF p_parent_task_id IS NOT NULL THEN
        IF EXISTS (SELECT 1
            FROM tasks WHERE id = p_parent_task_id AND parent_task_id IS NOT NULL) THEN
            RAISE EXCEPTION 'Nesting Error: Subtasks cannot have their own subtasks.';
        END IF;
    END IF;

    -- 2. INSERT TASK TEMPLATE
    INSERT INTO tasks (
        user_id, list_id, title, description, parent_task_id, 
        rrule, is_active, size_id, consequence_id, friction_id, is_adaptive
    ) VALUES (
        v_user_id, p_list_id, p_title, p_description, p_parent_task_id, 
        p_rrule, TRUE, p_size_id, p_consequence_id, p_friction_id, p_is_adaptive
    ) RETURNING id INTO v_new_task_id;

    -- 3. INSTANCE SPAWNING LOGIC
    IF p_parent_task_id IS NOT NULL THEN
        -- WORKFLOW: SUBTASK CATCH-UP WITH RELATIVE OFFSETS
        
        -- Calculate the duration of the work stint as defined by the UI
        v_exec_period := p_due_date - p_start_date;

        -- We need the start_date of the 'earliest' active parent instance to calculate the delay
        SELECT start_date INTO v_first_parent_start 
        FROM task_instances 
        WHERE task_id = p_parent_task_id 
          AND instance_number = p_parent_instance_number;

        -- Calculate how far into the parent's window this subtask starts
        v_start_delay := p_start_date - v_first_parent_start;

        -- Loop through all active parent instances to create synchronized children
        FOR v_current_instance IN 
            SELECT id, start_date, due_date, instance_number 
            FROM task_instances 
            WHERE task_id = p_parent_task_id
              AND instance_number >= p_parent_instance_number
              AND status IN ('ready', 'open', 'asleep', 'debt')
            ORDER BY instance_number ASC
        LOOP
            IF v_is_first THEN
                -- The first instance uses exactly what the UI sent
                v_calc_start := p_start_date;
                v_calc_due := p_due_date;
                v_is_first := FALSE;
            ELSE
                -- Future instances use the calculated relative offset and period
                v_calc_start := v_current_instance.start_date + v_start_delay;
                v_calc_due := v_calc_start + v_exec_period;
            END IF;

            -- SAFETY CHECK: Ensure the subtask actually fits inside the parent window
            IF v_calc_start < v_current_instance.start_date OR v_calc_due > v_current_instance.due_date THEN
                RAISE EXCEPTION 'Temporal Overflow: Subtask instance % falls outside parent window (% to %)', 
                                v_current_instance.instance_number, v_calc_start, v_calc_due;
            END IF;

            INSERT INTO task_instances (
                task_id, user_id, parent_instance_id, start_date, due_date, 
                instance_number, original_start_date, original_due_date, status
            ) VALUES (
                v_new_task_id, v_user_id, v_current_instance.id, v_calc_start, v_calc_due, 
                v_current_instance.instance_number, v_calc_start, v_calc_due, 'ready'
            );
        END LOOP;

    ELSE
        -- WORKFLOW: MASTER TASK (Recursive or Single)
        -- Just create the first instance. An edge function spawn by cron will handle future rrule slots.
        INSERT INTO task_instances (
            task_id, user_id, start_date, due_date, instance_number, 
            original_start_date, original_due_date, status
        ) VALUES (
            v_new_task_id, v_user_id, p_start_date, p_due_date, 1, 
            p_start_date, p_due_date, 'ready'
        );
    END IF;

    RETURN v_new_task_id;
END;
$$ LANGUAGE plpgsql;

