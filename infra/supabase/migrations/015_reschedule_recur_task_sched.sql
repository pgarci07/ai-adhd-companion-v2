-- Reschedule the recurrent task worker to run once a day at 04:00.
DO $$
DECLARE
  v_job_id bigint;
BEGIN
  SELECT jobid
  INTO v_job_id
  FROM cron.job
  WHERE jobname = 'rtask-sched-task';

  IF v_job_id IS NOT NULL THEN
    PERFORM cron.unschedule(v_job_id);
  END IF;
END;
$$;

SELECT cron.schedule(
  'rtask-sched-task',
  '0 4 * * *',
  $$
  SELECT net.http_post(
    url:='https://timnupswgnpbvmbbruxe.supabase.co/functions/v1/recurrent_task_sched',
    headers:=(SELECT jsonb_build_object(
      'Content-Type', 'application/json',
      'Authorization', 'Bearer ' || decrypted_secret
    ) FROM vault.decrypted_secrets WHERE name = 'adhd_companion_key'),
    body:='{"dry_run": false}'::jsonb
  );
  $$
);
