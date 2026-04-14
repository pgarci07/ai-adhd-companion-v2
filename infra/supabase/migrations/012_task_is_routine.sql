ALTER TABLE tasks
ADD COLUMN IF NOT EXISTS is_routine BOOLEAN NOT NULL DEFAULT FALSE;


CREATE OR REPLACE FUNCTION set_task_is_routine_from_rrule()
RETURNS TRIGGER AS $$
DECLARE
    v_rrule TEXT := COALESCE(NEW.rrule, '');
BEGIN
    NEW.is_routine := (
        POSITION('FREQ=DAILY' IN UPPER(v_rrule)) > 0
        OR POSITION('FREQ=WEEKLY' IN UPPER(v_rrule)) > 0
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


DROP TRIGGER IF EXISTS trg_set_task_is_routine_from_rrule ON tasks;
CREATE TRIGGER trg_set_task_is_routine_from_rrule
BEFORE INSERT OR UPDATE OF rrule ON tasks
FOR EACH ROW
EXECUTE FUNCTION set_task_is_routine_from_rrule();


UPDATE tasks
SET is_routine = (
    POSITION('FREQ=DAILY' IN UPPER(COALESCE(rrule, ''))) > 0
    OR POSITION('FREQ=WEEKLY' IN UPPER(COALESCE(rrule, ''))) > 0
);
