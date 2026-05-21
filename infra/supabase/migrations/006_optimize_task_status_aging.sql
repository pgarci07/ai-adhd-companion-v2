CREATE OR REPLACE FUNCTION public.age_task_instance_statuses(
    "p_debt_days" integer,
    "p_stale_days" integer,
    "p_dry_run" boolean DEFAULT false
) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET "search_path" TO 'public'
    AS $$
DECLARE
    v_now timestamptz := now();
    v_debt_cutoff timestamptz := v_now - make_interval(days => p_debt_days);
    v_stale_cutoff timestamptz := v_now - make_interval(days => p_stale_days);
    v_debt_candidates jsonb := '[]'::jsonb;
    v_stale_candidates jsonb := '[]'::jsonb;
    v_moved_to_debt integer := 0;
    v_moved_to_stale integer := 0;
    v_candidate record;
BEGIN
    WITH debt_candidates AS (
        SELECT
            ti.id AS instance_id,
            ti.due_date,
            latest.new_status_id AS current_status
        FROM public.task_instances ti
        JOIN LATERAL (
            SELECT tisl.new_status_id, tisl.changed_at, tisl.id
            FROM public.task_instance_status_log tisl
            WHERE tisl.instance_changed_id = ti.id
            ORDER BY tisl.changed_at DESC, tisl.id DESC
            LIMIT 1
        ) latest ON true
        WHERE latest.new_status_id IN ('ready', 'open', 'asleep')
          AND ti.due_date < v_debt_cutoff
    )
    SELECT
        COALESCE(
            jsonb_agg(
                jsonb_build_object(
                    'instanceId', dc.instance_id,
                    'dueDate', dc.due_date,
                    'currentStatus', dc.current_status
                )
                ORDER BY dc.due_date, dc.instance_id
            ),
            '[]'::jsonb
        ),
        COUNT(*)
    INTO v_debt_candidates, v_moved_to_debt
    FROM debt_candidates dc;

    WITH latest_statuses AS (
        SELECT DISTINCT ON (tisl.instance_changed_id)
            tisl.instance_changed_id AS instance_id,
            tisl.new_status_id AS current_status,
            tisl.changed_at
        FROM public.task_instance_status_log tisl
        ORDER BY
            tisl.instance_changed_id,
            tisl.changed_at DESC,
            tisl.id DESC
    ),
    stale_candidates AS (
        SELECT
            ls.instance_id,
            ls.changed_at AS debt_since,
            ls.current_status
        FROM latest_statuses ls
        WHERE ls.current_status = 'debt'
          AND ls.changed_at < v_stale_cutoff
    )
    SELECT
        COALESCE(
            jsonb_agg(
                jsonb_build_object(
                    'instanceId', sc.instance_id,
                    'debtSince', sc.debt_since,
                    'currentStatus', sc.current_status
                )
                ORDER BY sc.debt_since, sc.instance_id
            ),
            '[]'::jsonb
        ),
        COUNT(*)
    INTO v_stale_candidates, v_moved_to_stale
    FROM stale_candidates sc;

    IF NOT p_dry_run THEN
        FOR v_candidate IN
            SELECT (value->>'instanceId')::uuid AS instance_id
            FROM jsonb_array_elements(v_debt_candidates)
        LOOP
            PERFORM public.set_task_instance_status(
                v_candidate.instance_id,
                'debt',
                v_now
            );
        END LOOP;

        FOR v_candidate IN
            SELECT (value->>'instanceId')::uuid AS instance_id
            FROM jsonb_array_elements(v_stale_candidates)
        LOOP
            PERFORM public.set_task_instance_status(
                v_candidate.instance_id,
                'stale',
                v_now
            );
        END LOOP;
    END IF;

    RETURN jsonb_build_object(
        'movedToDebt', v_moved_to_debt,
        'movedToStale', v_moved_to_stale,
        'debtCandidates', v_debt_candidates,
        'staleCandidates', v_stale_candidates
    );
END;
$$;

ALTER FUNCTION public.age_task_instance_statuses(
    "p_debt_days" integer,
    "p_stale_days" integer,
    "p_dry_run" boolean
) OWNER TO postgres;

DROP INDEX IF EXISTS public.idx_logs_instance_chrono;

CREATE INDEX "idx_logs_instance_chrono"
    ON public.task_instance_status_log
    USING btree ("instance_changed_id", "changed_at" DESC, "id" DESC);

CREATE INDEX IF NOT EXISTS "idx_logs_status_changed"
    ON public.task_instance_status_log
    USING btree (
        "new_status_id",
        "changed_at" DESC,
        "instance_changed_id",
        "id" DESC
    );

GRANT EXECUTE ON FUNCTION public.age_task_instance_statuses(
    "p_debt_days" integer,
    "p_stale_days" integer,
    "p_dry_run" boolean
) TO service_role;
