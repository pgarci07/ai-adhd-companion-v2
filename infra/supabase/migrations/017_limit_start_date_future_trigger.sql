-- The start-date guard should not block unrelated updates such as changing
-- task_instances.status from ready to open.
DROP TRIGGER IF EXISTS trg_check_start_date_future ON task_instances;

CREATE TRIGGER trg_check_start_date_future
BEFORE INSERT OR UPDATE OF start_date ON task_instances
FOR EACH ROW
EXECUTE FUNCTION ensure_start_date_is_future();
