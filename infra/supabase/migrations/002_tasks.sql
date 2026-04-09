-- 1. EXTENSIONS & CLEANNING

-- 2. TYPES
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'task_status') THEN
        CREATE TYPE task_status AS ENUM ('ready', 'open', 'asleep', 'completed', 'archived');
    END IF;
END$$;

-- 3. LOOKUP TABLES
CREATE TABLE IF NOT EXISTS lists (
    id UUID PRIMARY KEY DEFAULT gen_uuid_v7(),
    user_id UUID NOT NULL DEFAULT auth.uid() REFERENCES auth.users(id) ON DELETE CASCADE,
    name VARCHAR(50) NOT NULL,
    description TEXT
);
ALTER TABLE public.lists ENABLE ROW LEVEL SECURITY;
CREATE UNIQUE INDEX idx_unique_user_list_name_case_insensitive 
ON lists (user_id, LOWER(name));

CREATE TABLE IF NOT EXISTS dim_task_sizes (
    id SERIAL PRIMARY KEY,
    label VARCHAR(20) UNIQUE NOT NULL,
    self_describing varchar(256) not null,
    weight INT NOT NULL DEFAULT 1,
    ui_color CHAR(7)
);

CREATE TABLE IF NOT EXISTS dim_task_consequences (
    id SERIAL PRIMARY KEY,
    label VARCHAR(20) UNIQUE NOT NULL,
    self_describing varchar(256) not null,
    weight INT NOT NULL DEFAULT 1,
    ui_color CHAR(7)
);

CREATE TABLE IF NOT EXISTS dim_task_frictions (
    id SERIAL PRIMARY KEY,
    label VARCHAR(20) UNIQUE NOT NULL,
    self_describing varchar(256) not null,
    weight INT NOT NULL DEFAULT 1,
    ui_color CHAR(7)
);

CREATE TABLE IF NOT EXISTS dim_task_deadline_types (
    id SERIAL PRIMARY KEY,
    label VARCHAR(20) UNIQUE NOT NULL,
    self_describing varchar(256) not null,
    weight INT NOT NULL DEFAULT 0,
    ui_color CHAR(7)
);

-- 4. MASTER TASKS TABLE
-- user_id denormalized for performance reasons in Supabase RLS
CREATE TABLE IF NOT EXISTS tasks (
    id UUID PRIMARY KEY DEFAULT gen_uuid_v7(),
    list_id UUID NOT NULL REFERENCES lists(id) ON DELETE RESTRICT,
    user_id UUID NOT NULL,
    title TEXT NOT NULL,
    description TEXT,

    -- Task duration, needed if due_date becomes important
    duration INTERVAL,
    
    -- Hierarchy & Recurrence
    parent_task_id UUID REFERENCES tasks(id) ON DELETE CASCADE,
    is_recurring BOOLEAN NOT NULL DEFAULT FALSE,
    rrule TEXT,
    
    -- Bounds for the Recurrence Series
    series_start_date TIMESTAMP WITH TIME ZONE,
    series_end_date TIMESTAMP WITH TIME ZONE, -- NULL for "Never end"
    
    -- Attributes with Weights
    size_id INT REFERENCES dim_task_sizes(id),
    consequence_id INT REFERENCES dim_task_consequences(id),
    friction_id INT REFERENCES dim_task_frictions(id),
    deadline_type_id INT REFERENCES dim_task_deadline_types(id),
    
    -- adaptive_mode: whether the owner opts for adaptive support from application (true by default)
    is_adaptive BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
alter table public.tasks enable row level security;

-- 5. TASKS TRIGGERS
-- User is the owner of the list
CREATE OR REPLACE FUNCTION fn_set_task_user_id_from_list()
RETURNS TRIGGER AS $$
BEGIN
    SELECT user_id INTO NEW.user_id FROM lists WHERE id = NEW.list_id;
    IF NEW.user_id IS NULL THEN
        RAISE EXCEPTION 'Error: List % does not have a valid owner', NEW.list_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_tasks_inherit_user_id
BEFORE INSERT ON tasks
FOR EACH ROW
EXECUTE FUNCTION fn_set_task_user_id_from_list();

-- Validates that a task cannot be a parent AND recurring simultaneously.
CREATE OR REPLACE FUNCTION fn_enforce_task_exclusivity()
RETURNS TRIGGER AS $$
BEGIN
    -- Condition 1: If becoming recurring, must not have children
    IF NEW.is_recurring IS TRUE THEN
        IF EXISTS (SELECT 1 FROM tasks WHERE parent_task_id = NEW.id) THEN
            RAISE EXCEPTION 'Constraint Violation: A task with subtasks cannot be made recurring.';
        END IF;
    END IF;

    -- Condition 2: If becoming a subtask, the parent must not be recurring
    IF NEW.parent_task_id IS NOT NULL THEN
        IF EXISTS (SELECT 1 FROM tasks WHERE id = NEW.parent_task_id AND is_recurring IS TRUE) THEN
            RAISE EXCEPTION 'Constraint Violation: Cannot add subtasks to a recurring task.';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_task_exclusivity
BEFORE INSERT OR UPDATE ON tasks
FOR EACH ROW EXECUTE FUNCTION fn_enforce_task_exclusivity();

-- 6. INSTANCES TABLE
-- user_id denormalized for performance reasons in Supabase RLS
CREATE TABLE task_instances (
    id UUID PRIMARY KEY DEFAULT gen_uuid_v7(),
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE RESTRICT,
    user_id UUID NOT NULL,
    occurrence_index INTEGER NOT NULL DEFAULT 1,

    start_date TIMESTAMP WITH TIME ZONE NOT NULL,
    due_date TIMESTAMP WITH TIME ZONE,
    status task_status NOT NULL DEFAULT 'ready',
    actual_friction_id INT REFERENCES dim_task_frictions(id),
    actual_duration INTERVAL,

    -- Metadata
    is_exception BOOLEAN DEFAULT FALSE,
    original_start_date TIMESTAMP WITH TIME ZONE,
    original_due_date TIMESTAMP WITH TIME ZONE,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
alter table public.task_instances enable row level security;

-- 7. INSTANCES TRIGGERS
-- check integrity
CREATE OR REPLACE FUNCTION fn_set_instance_user_id_from_task()
RETURNS TRIGGER AS $$
BEGIN
    SELECT user_id INTO NEW.user_id FROM tasks WHERE id = NEW.task_id;
    IF NEW.user_id IS NULL THEN
        RAISE EXCEPTION 'Integrity error: parent task % does not exist or has no owner', NEW.task_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_task_instances_inherit_user_id
BEFORE INSERT ON task_instances
FOR EACH ROW
EXECUTE FUNCTION fn_set_instance_user_id_from_task();


-- 8. PERFORMANCE INDEXES
CREATE INDEX idx_tasks_user_id ON tasks(user_id);
CREATE INDEX idx_instances_user_id ON task_instances(user_id);
CREATE INDEX idx_tasks_parent_id ON tasks(parent_task_id);
CREATE INDEX idx_instances_start_date ON task_instances(start_date);
CREATE INDEX idx_instances_due_date ON task_instances(due_date);

-- 9. HOUSKEEPING FOR TIMESTAMPING UPDATES 
CREATE OR REPLACE FUNCTION fn_update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
-- Trigger para la tabla maestra
CREATE TRIGGER trg_tasks_updated_at
BEFORE UPDATE ON tasks
FOR EACH ROW
EXECUTE FUNCTION fn_update_timestamp();

-- Trigger para la tabla de instancias
CREATE TRIGGER trg_task_instances_updated_at
BEFORE UPDATE ON task_instances
FOR EACH ROW
EXECUTE FUNCTION fn_update_timestamp();

-- 10. CREATE TASK STORED PROCEDURE
-- A safe mechanism to create a new task with first instance
CREATE OR REPLACE PROCEDURE sp_create_task_with_instance(
    p_list_id UUID,
    p_title TEXT,
    p_start_date TIMESTAMP WITH TIME ZONE,
    p_description TEXT DEFAULT NULL,
    p_duration INTERVAL DEFAULT NULL,
    p_due_date TIMESTAMP WITH TIME ZONE DEFAULT NULL,
    p_parent_task_id UUID DEFAULT NULL,
    p_is_recurring BOOLEAN DEFAULT FALSE,
    p_rrule TEXT DEFAULT NULL,
    p_serie_start_date TIMESTAMP WITH TIME ZONE DEFAULT NULL,
    p_serie_end_date TIMESTAMP WITH TIME ZONE DEFAULT NULL,
    p_size_id INT DEFAULT NULL,
    p_consequence_id INT DEFAULT NULL,
    p_friction_id INT DEFAULT NULL,
    p_deadline_type_id INT DEFAULT NULL,
    p_is_adaptive BOOLEAN DEFAULT TRUE
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_new_task_id UUID;
BEGIN
    INSERT INTO tasks (
        list_id,
        title, 
        description,
        duration,
        parent_task_id,
        is_recurring, 
        rrule, 
        series_start_date, 
        series_end_date,
        size_id,
        consequence_id,
        friction_id,
        deadline_type_id,
        is_adaptive
    ) VALUES (
        p_list_id,
        p_title, 
        p_description, 
        p_duration,
        p_parent_task_id,
        p_is_recurring, 
        p_rrule, 
        p_serie_start_date, 
        p_serie_end_date,
        p_size_id,
        p_consequence_id,
        p_friction_id,
        p_deadline_type_id,
        p_is_adaptive
    )
    RETURNING id INTO v_new_task_id;
    INSERT INTO task_instances (
        task_id,
        start_date,
        due_date,
        original_start_date, 
        original_due_date
    ) VALUES (
        v_new_task_id,
        p_start_date,
        p_due_date,
        p_start_date,
        p_due_date
    );
    -- la aplicación se encargará de generar el resto de las instancias usando la libreria 
    -- dateutil que entiende el formato rrule
    COMMIT;
END;
$$;

-- 11. Log of status changes in a task instance
-- Here is a good part of the BRAIN of the APP
CREATE TABLE IF NOT EXISTS task_instance_status_log (
    id UUID PRIMARY KEY DEFAULT gen_uuid_v7(),
    instance_changed_id UUID NOT NULL REFERENCES task_instances(id) ON DELETE CASCADE,
    new_status_id task_status NOT NULL,
    changed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
-- Compound index to speed up in retreiving an instance change log
CREATE INDEX idx_logs_instance_chrono ON task_instance_status_log (instance_changed_id, changed_at DESC);

-- Update task_status_log when tasks(status_id) is updated
CREATE FUNCTION update_task_instance_status_log()
RETURNS trigger
language plpgsql
as $$
begin
    if new.status_id != old.status_id then
        insert into task_instance_status_log(instance_changed_id, new_status_id)
        values (new.id, new.status_id);
        return new;
    end if;
    return null;
end;
$$;
CREATE TRIGGER task_instance_status_update_trigger
AFTER UPDATE ON task_instances
for each row
execute function update_task_instance_status_log();


-- 10. RLS
CREATE POLICY "Users allowed only to their lists" ON lists FOR ALL USING (auth.uid() = user_id);
CREATE POLICY "Users allowed only to their tasks" ON tasks FOR ALL USING (auth.uid() = user_id);
CREATE POLICY "Users allowed only to instances of their tasks" ON task_instances FOR ALL USING (auth.uid() = user_id);
