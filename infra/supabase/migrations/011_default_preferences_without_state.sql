CREATE OR REPLACE FUNCTION public.default_preference_settings() RETURNS jsonb
    LANGUAGE sql IMMUTABLE
AS $$
    SELECT '{
        "language": "english",
        "average_session_time": 120,
        "custom_sizes": [15, 30, 60, 180, 720],
        "sprint": 30,
        "planner_minutes": 15,
        "time-mgmt": "Pomodoro",
        "first_day_of_week": "SU",
        "notifications": true,
        "enable_minute_chime": true,
        "keep_worthy": true
    }'::jsonb;
$$;

UPDATE public.profiles
SET preferences = (
    public.default_preference_settings()
    || COALESCE(preferences, '{}'::jsonb)
) - ARRAY[
    'state',
    'state_id',
    'current_state',
    'current_state_id'
];
