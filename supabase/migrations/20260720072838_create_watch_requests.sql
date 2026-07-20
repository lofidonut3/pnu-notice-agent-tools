create table public.watch_requests (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  request text not null check (char_length(request) between 5 and 1000),
  delivery_email text not null check (
    char_length(delivery_email) between 3 and 320
    and position('@' in delivery_email) > 1
  ),
  enabled boolean not null default true,
  status text not null default 'pending' check (
    status in ('pending', 'processing', 'active', 'failed')
  ),
  revision integer not null default 1 check (revision > 0),
  watch_id text unique,
  profile_revision text,
  compiled_intent_json text,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  processed_at timestamptz
);

create index watch_requests_user_updated_idx
  on public.watch_requests (user_id, updated_at desc);

create index watch_requests_pending_idx
  on public.watch_requests (created_at)
  where status = 'pending';

alter table public.watch_requests enable row level security;

revoke all on table public.watch_requests from public, anon, authenticated;
grant select on table public.watch_requests to authenticated;
grant insert (request, delivery_email, enabled)
  on table public.watch_requests to authenticated;
grant update (request, delivery_email, enabled)
  on table public.watch_requests to authenticated;
grant select, insert, update, delete
  on table public.watch_requests to pnu_notice_worker;
grant all on table public.watch_requests to service_role;

create policy watch_requests_select_own
  on public.watch_requests
  for select
  to authenticated
  using ((select auth.uid()) = user_id);

create policy watch_requests_insert_own
  on public.watch_requests
  for insert
  to authenticated
  with check ((select auth.uid()) = user_id);

create policy watch_requests_update_own
  on public.watch_requests
  for update
  to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

create policy pnu_notice_worker_all
  on public.watch_requests
  for all
  to pnu_notice_worker
  using (true)
  with check (true);

create function public.prepare_watch_request_update()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.request is distinct from old.request
     or new.delivery_email is distinct from old.delivery_email then
    new.revision := old.revision + 1;
    new.status := 'pending';
    new.profile_revision := null;
    new.compiled_intent_json := null;
    new.last_error := null;
    new.processed_at := null;
  end if;
  new.updated_at := now();
  return new;
end;
$$;

revoke all on function public.prepare_watch_request_update() from public, anon, authenticated;

create trigger prepare_watch_request_update
before update on public.watch_requests
for each row execute function public.prepare_watch_request_update();
