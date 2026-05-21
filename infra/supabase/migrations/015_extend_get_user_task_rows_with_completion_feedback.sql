DROP FUNCTION get_user_task_rows();
CREATE OR REPLACE FUNCTION public.get_user_task_rows()
RETURNS TABLE(
    "instance_id" uuid,
    "task_id" uuid,
    "instance_number" integer,
    "parent_instance_id" uuid,
    "list_id" uuid,
    "title" text,
    "description" text,
    "start_date" timestamp with time zone,
    "due_date" timestamp with time zone,
    "status" public.task_status,
    "rrule" text,
    "is_active" boolean,
    "is_routine" boolean,
    "size_id" integer,
    "consequence_id" integer,
    "friction_id" integer,
    "final_comments" text,
    "actual_duration" integer,
    "actual_friction_id" integer,
    "is_adaptive" boolean,
    "parent_task_id" uuid
)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET "search_path" TO 'public'
    AS $$
    SELECT
        ti.id AS instance_id,
        ti.task_id,
        ti.instance_number,
        ti.parent_instance_id,
        t.list_id,
        t.title,
        t.description,
        ti.start_date,
        ti.due_date,
        COALESCE(latest_status.new_status_id, 'ready'::task_status) AS status,
        t.rrule,
        t.is_active,
        t.is_routine,
        t.size_id,
        t.consequence_id,
        t.friction_id,
        ti.final_comments,
        ti.actual_duration,
        ti.actual_friction_id,
        t.is_adaptive,
        t.parent_task_id
    FROM public.task_instances ti
    JOIN public.tasks t
      ON t.id = ti.task_id
    LEFT JOIN LATERAL (
        SELECT tisl.new_status_id
        FROM public.task_instance_status_log tisl
        WHERE tisl.instance_changed_id = ti.id
        ORDER BY tisl.changed_at DESC, tisl.id DESC
        LIMIT 1
    ) latest_status ON TRUE
    WHERE t.user_id = auth.uid()
    ORDER BY ti.due_date DESC;
$$;

ALTER FUNCTION public.get_user_task_rows() OWNER TO postgres;

GRANT EXECUTE ON FUNCTION public.get_user_task_rows() TO authenticated;
GRANT EXECUTE ON FUNCTION public.get_user_task_rows() TO service_role;
