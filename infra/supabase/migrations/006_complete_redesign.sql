DROP TABLE IF EXISTS dim_task_deadline_types CASCADE;
DROP TABLE IF EXISTS tasks CASCADE;
DROP TABLE IF EXISTS task_instances CASCADE;
DROP PROCEDURE IF EXISTS sp_create_task_with_instance(UUID,TEXT,TIMESTAMPTZ,TEXT,INTERVAL,TIMESTAMPTZ,UUID,BOOLEAN,TEXT,TIMESTAMPTZ,TIMESTAMPTZ,INT,INT,INT,INT,BOOLEAN);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'task_status') THEN
        ALTER TYPE task_status ADD VALUE 'stale';
        ALTER TYPE task_status ADD VALUE 'debt';
    END IF;
END$$;

-- 1. RLS POLICY FOR PERSONAS
-- Access to table is granted to unauthenticated, so that the app can read it before login
alter table public.personas enable row level security;

drop policy if exists "Public read personas" on public.personas;
create policy "Public read personas"
on public.personas
for select
to anon, authenticated
using (true);

grant select on table public.personas to anon, authenticated;

-- 4. MASTER TASKS TABLE
-- user_id denormalized for performance reasons in Supabase RLS
CREATE TABLE IF NOT EXISTS tasks (
    id UUID PRIMARY KEY DEFAULT gen_uuid_v7(),
    list_id UUID NOT NULL REFERENCES lists(id) ON DELETE RESTRICT,
    user_id UUID NOT NULL,
    title TEXT NOT NULL,
    description TEXT,

    -- Hierarchy & Recurrence
    parent_task_id UUID REFERENCES tasks(id) ON DELETE CASCADE,
    rrule TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    -- Attributes with Weights
    size_id INT REFERENCES dim_task_sizes(id),
    consequence_id INT REFERENCES dim_task_consequences(id),
    friction_id INT REFERENCES dim_task_frictions(id),
    
    -- adaptive_mode: whether the owner opts for adaptive support from application (true by default)
    is_adaptive BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
alter table public.tasks enable row level security;

-- 5. TASKS TRIGGERS & CONSTRAINTS
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

-- Validates that a task cannot be a child AND recurring simultaneously.
ALTER TABLE tasks 
ADD CONSTRAINT subtask_no_recurrence 
CHECK (parent_task_id IS NULL OR rrule IS NULL);

-- Check for circular parenting
ALTER TABLE tasks
ADD CONSTRAINT task_not_own_parent
CHECK (id != parent_task_id);

-- 6. INSTANCES TABLE
-- Function for default deadline being tomorrow @ 00:00h
CREATE OR REPLACE FUNCTION get_tomorrow_midnight() 
RETURNS timestamptz AS $$
  SELECT date_trunc('day', now() + interval '1 day');
$$ LANGUAGE SQL;

-- user_id denormalized for performance reasons in Supabase RLS
-- parent_instance_number & instance_nnumber is an artifact to handle recurrent tasks with subtasks
CREATE TABLE task_instances (
    id UUID PRIMARY KEY DEFAULT gen_uuid_v7(),
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE RESTRICT,
    user_id UUID NOT NULL,
    instance_number INTEGER NOT NULL DEFAULT 1,
    parent_instance_id UUID REFERENCES task_instances(id) ON DELETE CASCADE,

    start_date TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    due_date TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT get_tomorrow_midnight(),
    status task_status NOT NULL DEFAULT 'ready',
    actual_friction_id INT REFERENCES dim_task_frictions(id),
    actual_duration INT,
    final_comments TEXT,

    -- Metadata
    is_exception BOOLEAN DEFAULT FALSE,
    original_start_date TIMESTAMP WITH TIME ZONE,
    original_due_date TIMESTAMP WITH TIME ZONE,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
ALTER TABLE public.task_instances enable row level security;

-- 7. INSTANCES TRIGGERS & CONSTRAINTS
-- check dates consistency
ALTER TABLE task_instances
ADD CONSTRAINT chk_deadline_after_start 
CHECK (due_date >= start_date);

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

-- check start date in not in the past
CREATE OR REPLACE FUNCTION ensure_start_date_is_future()
RETURNS TRIGGER AS $$
BEGIN
    -- Allow a small 1-minute buffer for network latency/server clock drift
    IF NEW.start_date < (now() - interval '1 minute') THEN
        RAISE EXCEPTION 'You cannot plan a task in the past. Your brain is here in the present!';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_check_start_date_future
BEFORE INSERT OR UPDATE ON task_instances
FOR EACH ROW
EXECUTE FUNCTION ensure_start_date_is_future();

-- 8. PERFORMANCE INDEXES
-- ? CREATE INDEX idx_instances_user_id ON task_instances(user_id);
CREATE INDEX idx_tasks_user_id ON tasks(user_id);
CREATE INDEX idx_instances_task_id ON task_instances(task_id);
CREATE INDEX idx_tasks_parent_id ON tasks(parent_task_id);
CREATE INDEX idx_instances_start_date ON task_instances(start_date);
CREATE INDEX idx_instances_due_date ON task_instances(due_date);
CREATE INDEX idx_task_instances_parent_id ON task_instances(parent_instance_id);

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

-- Update task_status_log when task_instances(status) is updated
CREATE OR REPLACE FUNCTION update_task_instance_status_log()
RETURNS trigger
language plpgsql
as $$
begin
    if new.status != old.status then
        insert into task_instance_status_log(instance_changed_id, new_status_id)
        values (new.id, new.status);
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
CREATE POLICY "Users allowed only to their tasks" ON tasks FOR ALL USING (auth.uid() = user_id);
CREATE POLICY "Users allowed only to instances of their tasks" ON task_instances FOR ALL USING (auth.uid() = user_id);
