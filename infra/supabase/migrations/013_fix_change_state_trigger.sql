-- Fix update_user_state_log trigger to use correct column name (id instead of user_id)
DROP FUNCTION IF EXISTS update_user_state_log() CASCADE;

CREATE FUNCTION update_user_state_log()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    IF new.state_id IS NOT NULL AND (old.state_id IS NULL OR new.state_id != old.state_id) THEN
        INSERT INTO user_state_log(user_id, state_id)
        VALUES (new.id, new.state_id);
        RETURN new;
    END IF;
    RETURN NULL;
END;
$$;

CREATE TRIGGER user_state_update_trigger
AFTER UPDATE ON profiles
FOR EACH ROW
EXECUTE FUNCTION update_user_state_log();

-- Enable RLS on user_state_log if not already enabled
ALTER TABLE public.user_state_log ENABLE ROW LEVEL SECURITY;

-- Add RLS policies for user_state_log
DROP POLICY IF EXISTS "Users can view their own state log" ON public.user_state_log;
CREATE POLICY "Users can view their own state log"
ON public.user_state_log
FOR SELECT
USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "System can insert state logs" ON public.user_state_log;
CREATE POLICY "System can insert state logs"
ON public.user_state_log
FOR INSERT
WITH CHECK (true);
