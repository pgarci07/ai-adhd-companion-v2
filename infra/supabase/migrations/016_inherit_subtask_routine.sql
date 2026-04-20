CREATE OR REPLACE FUNCTION set_task_is_routine_from_rrule()
RETURNS TRIGGER AS $$
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
$$ LANGUAGE plpgsql;


DROP TRIGGER IF EXISTS trg_set_task_is_routine_from_rrule ON tasks;
CREATE TRIGGER trg_set_task_is_routine_from_rrule
BEFORE INSERT OR UPDATE OF rrule, parent_task_id ON tasks
FOR EACH ROW
EXECUTE FUNCTION set_task_is_routine_from_rrule();


UPDATE tasks child
SET is_routine = parent.is_routine
FROM tasks parent
WHERE child.parent_task_id = parent.id
  AND parent.is_routine IS TRUE
  AND child.is_routine IS FALSE;
