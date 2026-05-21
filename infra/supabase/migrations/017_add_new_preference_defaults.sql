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
        "keep_worthy": true,
        "rest_duration": 2,
        "chunk_min_floor_minutes": 5
    }'::jsonb;
$$;

UPDATE public.profiles
SET preferences = jsonb_set(
    jsonb_set(
        COALESCE(preferences, '{}'::jsonb),
        '{rest_duration}',
        to_jsonb(2),
        true
    ),
    '{chunk_min_floor_minutes}',
    to_jsonb(5),
    true
)
WHERE preferences IS NULL
   OR NOT (preferences ? 'rest_duration')
   OR NOT (preferences ? 'chunk_min_floor_minutes');
