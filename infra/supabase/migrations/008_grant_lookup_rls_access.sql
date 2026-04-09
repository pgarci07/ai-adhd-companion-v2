alter table public.dim_task_sizes enable row level security;
alter table public.dim_task_consequences enable row level security;
alter table public.dim_task_frictions enable row level security;
alter table public.states enable row level security;

drop policy if exists "Public read dim_task_sizes" on public.dim_task_sizes;
create policy "Public read dim_task_sizes"
on public.dim_task_sizes
for select
to anon, authenticated
using (true);

drop policy if exists "Public read dim_task_consequences" on public.dim_task_consequences;
create policy "Public read dim_task_consequences"
on public.dim_task_consequences
for select
to anon, authenticated
using (true);

drop policy if exists "Public read dim_task_frictions" on public.dim_task_frictions;
create policy "Public read dim_task_frictions"
on public.dim_task_frictions
for select
to anon, authenticated
using (true);

drop policy if exists "Public read states" on public.states;
create policy "Public read states"
on public.states
for select
to anon, authenticated
using (true);

grant select on table public.dim_task_sizes to anon, authenticated;
grant select on table public.dim_task_consequences to anon, authenticated;
grant select on table public.dim_task_frictions to anon, authenticated;
grant select on table public.states to anon, authenticated;
