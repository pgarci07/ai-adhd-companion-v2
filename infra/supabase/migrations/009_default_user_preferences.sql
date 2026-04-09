CREATE OR REPLACE FUNCTION default_preference_settings() 
RETURNS jsonb AS $$
    SELECT '{
        "language": "english", 
        "average_session_time": 120, 
        "custom_sizes": [15, 30, 60, 180, 720],
        "sprint": 30,
        "time-mgmt": "Pomodoro",
        "notifications": true
    }'::jsonb;
$$ LANGUAGE sql IMMUTABLE;

-- Then use it in your table:
ALTER TABLE profiles 
ALTER COLUMN preferences SET DEFAULT default_preference_settings();
