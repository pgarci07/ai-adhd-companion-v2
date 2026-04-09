DROP FUNCTION if exists update_user_state_log() CASCADE;
drop table if exists personas CASCADE;
drop table if exists states CASCADE;
drop table if exists profiles CASCADE;
drop table if exists user_state_log CASCADE;

create extension if not exists pgcrypto;

-- 1. Create a function for uuid v7 
CREATE OR REPLACE FUNCTION gen_uuid_v7() 
RETURNS uuid 
AS $$
DECLARE
  v_time bigint;
  v_uuid bytea;
BEGIN
  -- 1. Obtener el tiempo Unix en milisegundos (48 bits)
  v_time := (EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::bigint;

  -- 2. Construir el prefijo de tiempo y añadir bits aleatorios
  -- Usamos gen_random_bytes para la parte aleatoria (10 bytes)
  v_uuid := decode(lpad(to_hex(v_time), 12, '0'), 'hex') || gen_random_bytes(10);

  -- 3. Ajustar los bits de versión (v7) y variante (RFC 4122)
  v_uuid := set_byte(v_uuid, 6, (get_byte(v_uuid, 6) & 15) | 112); -- Versión 7 (0111)
  v_uuid := set_byte(v_uuid, 8, (get_byte(v_uuid, 8) & 63) | 128); -- Variante (10)

  RETURN encode(v_uuid, 'hex')::uuid;
END;
$$ LANGUAGE plpgsql VOLATILE;

-- 2. LOOKUP TABLES
create table if not exists personas (
    id smallserial primary key,
    name varchar(25) not null,
    description text not null,
    self_describing varchar(256) not null
);

create table if not exists states (
    id smallserial primary key,
    name varchar(25) not null,
    description text not null,
    self_describing varchar(256) not null
);

-- 2. APP USERS TABLE (en SUPABASE extiende la tabla auth.users y se llama generalmente profiles)
create table if not exists profiles (
    id uuid not null primary key references auth.users(id) on delete cascade,
    full_name text, 
    avatar_url text,

    -- app-oriented atributes
    role text default 'user',
    born date,
    preferences jsonb default '{}'::jsonb,
    persona_id smallint references personas(id),
    state_id smallint references states(id),

    created_at TIMESTAMP WITH TIME ZONE not null default now(),
    updated_at TIMESTAMP WITH TIME ZONE not null default now()
);
alter table public.profiles enable row level security;


-- 3. TABLE USER_STATE_LOG
create table if not exists user_state_log (
    id UUID NOT NULL PRIMARY KEY DEFAULT gen_uuid_v7(),
    user_id uuid not null references auth.users(id) on delete cascade,
    state_id smallint not null references states(id) on delete cascade,
    experienced_at TIMESTAMP WITH TIME ZONE not null default now()
);

-- Update state_log when profiles(state_id) is updated
create function update_user_state_log()
returns trigger
language plpgsql
as $$
begin
    if new.state_id is not null and (old.state_id is null or new.state_id != old.state_id) then
        insert into user_state_log(user_id, state_id)
        values (new.user_id, new.state_id);
        return new;
    end if;
    return null;
end;
$$;
create trigger user_state_update_trigger
after update on profiles
for each row
execute function update_user_state_log();
-- Si se piensa que un usuario puede cambiar de persona (lo creo posible) habria que hacer otro log

-- RLS Policies
CREATE POLICY "Manage own profile" ON public.profiles FOR ALL USING (auth.uid() = id) WITH CHECK (auth.uid() = id);
