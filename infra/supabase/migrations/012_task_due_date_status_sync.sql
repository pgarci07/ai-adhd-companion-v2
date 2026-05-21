CREATE OR REPLACE FUNCTION public.sync_task_instance_status_from_due_date()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public'
AS $$
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

DROP TRIGGER IF EXISTS trg_sync_task_instance_status_from_due_date
ON public.task_instances;

CREATE TRIGGER trg_sync_task_instance_status_from_due_date
AFTER UPDATE OF due_date
ON public.task_instances
FOR EACH ROW
EXECUTE FUNCTION public.sync_task_instance_status_from_due_date();
