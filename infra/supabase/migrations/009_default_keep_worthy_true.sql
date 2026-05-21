CREATE OR REPLACE FUNCTION public.default_preference_settings() RETURNS jsonb
    LANGUAGE sql IMMUTABLE
AS $$
    SELECT '{
        "language": "english",
        "average_session_time": 120,
        "custom_sizes": [15, 30, 60, 180, 720],
        "sprint": 30,
        "time-mgmt": "Pomodoro",
        "first_day_of_week": "SU",
        "notifications": true,
        "keep_worthy": true
    }'::jsonb;
$$;
