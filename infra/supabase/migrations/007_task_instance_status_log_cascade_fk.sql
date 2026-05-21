-- Keep task status logs aligned with task-instance lifecycle.
-- If an instance is deleted, its status log must disappear automatically too.

-- First remove any orphan log rows left behind by the previous non-cascading FK.
DELETE FROM public.task_instance_status_log tisl
WHERE NOT EXISTS (
    SELECT 1
    FROM public.task_instances ti
    WHERE ti.id = tisl.instance_changed_id
);

-- Recreate the FK with ON DELETE CASCADE so deleting one instance cannot leave
-- historical status rows pointing at a missing task instance.
ALTER TABLE public.task_instance_status_log
DROP CONSTRAINT IF EXISTS task_instance_status_log_instance_changed_id_fkey;

ALTER TABLE public.task_instance_status_log
ADD CONSTRAINT task_instance_status_log_instance_changed_id_fkey
FOREIGN KEY (instance_changed_id)
REFERENCES public.task_instances(id)
ON DELETE CASCADE;
