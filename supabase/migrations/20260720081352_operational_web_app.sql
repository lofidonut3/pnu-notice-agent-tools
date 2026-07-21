create schema if not exists private;
revoke all on schema private from public, anon, authenticated;

create table private.watch_request_activity (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  action text not null check (action in ('create', 'compile')),
  created_at timestamptz not null default now()
);

create index watch_request_activity_user_created_idx
  on private.watch_request_activity (user_id, created_at desc);

revoke all on table private.watch_request_activity from public, anon, authenticated;

create function private.enforce_watch_request_guard()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  caller_id uuid := (select auth.uid());
  account_email text;
  enabled_count integer;
  recent_compile_count integer;
  is_compile boolean;
  is_enabling boolean;
begin
  if caller_id is null then
    return new;
  end if;

  if new.user_id is distinct from caller_id then
    raise exception 'watch request owner does not match authenticated user'
      using errcode = '42501';
  end if;

  select email into account_email
  from auth.users
  where id = caller_id;

  if account_email is null
     or lower(trim(new.delivery_email)) is distinct from lower(trim(account_email)) then
    raise exception 'notification email must match the signed-in account'
      using errcode = '23514';
  end if;

  if tg_op = 'INSERT' then
    is_enabling := new.enabled;
  else
    is_enabling := new.enabled and not old.enabled;
  end if;

  if is_enabling then
    select count(*) into enabled_count
    from public.watch_requests
    where user_id = caller_id
      and enabled
      and (tg_op = 'INSERT' or id <> new.id);

    if enabled_count >= 10 then
      raise exception 'at most 10 active watches are allowed per account'
        using errcode = '23514';
    end if;
  end if;

  if tg_op = 'INSERT' then
    is_compile := true;
  else
    is_compile := new.request is distinct from old.request
      or new.delivery_email is distinct from old.delivery_email;
  end if;

  if is_compile then
    select count(*) into recent_compile_count
    from private.watch_request_activity
    where user_id = caller_id
      and action = 'compile'
      and created_at >= now() - interval '1 hour';

    if recent_compile_count >= 10 then
      raise exception 'at most 10 watch compilations are allowed per hour'
        using errcode = '42900';
    end if;

    if tg_op = 'INSERT' then
      insert into private.watch_request_activity (user_id, action)
      values (caller_id, 'create');
    end if;
    insert into private.watch_request_activity (user_id, action)
    values (caller_id, 'compile');
  end if;

  return new;
end;
$$;

revoke all on function private.enforce_watch_request_guard() from public, anon, authenticated;

create trigger enforce_watch_request_guard
before insert or update on public.watch_requests
for each row execute function private.enforce_watch_request_guard();

create table public.user_notifications (
  id text primary key,
  outbox_id text unique,
  user_id uuid not null references auth.users(id) on delete cascade,
  watch_request_id uuid not null references public.watch_requests(id) on delete cascade,
  watch_id text not null,
  candidate_id text not null unique,
  event_id text not null,
  notice_id text,
  classification text not null check (classification in ('matched', 'uncertain')),
  delivery_status text not null check (
    delivery_status in ('not_applicable', 'queued', 'retry', 'sent', 'needs_attention')
  ),
  title text not null,
  summary text not null,
  notice_url text,
  facts_json text not null default '[]',
  evidence_json text not null default '[]',
  last_error text,
  read_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  sent_at timestamptz
);

create index user_notifications_user_created_idx
  on public.user_notifications (user_id, created_at desc);
create index user_notifications_unread_idx
  on public.user_notifications (user_id, created_at desc)
  where read_at is null;

alter table public.user_notifications enable row level security;
revoke all on table public.user_notifications from public, anon, authenticated;
grant select on table public.user_notifications to authenticated;
grant update (read_at) on table public.user_notifications to authenticated;
grant select, insert, update, delete on table public.user_notifications to pnu_notice_worker;
grant all on table public.user_notifications to service_role;

create policy user_notifications_select_own
  on public.user_notifications
  for select
  to authenticated
  using ((select auth.uid()) = user_id);

create policy user_notifications_mark_own_read
  on public.user_notifications
  for update
  to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

create policy pnu_notice_worker_user_notifications_all
  on public.user_notifications
  for all
  to pnu_notice_worker
  using (true)
  with check (true);

create table public.service_health (
  id text primary key,
  status text not null check (status in ('healthy', 'degraded', 'unhealthy')),
  checked_at timestamptz not null,
  feed_generated_at timestamptz,
  latest_cycle_at timestamptz,
  open_incident_count integer not null default 0,
  summary text not null,
  details_json text not null default '{}'
);

alter table public.service_health enable row level security;
revoke all on table public.service_health from public, anon, authenticated;
grant select on table public.service_health to authenticated;
grant select, insert, update, delete on table public.service_health to pnu_notice_worker;
grant all on table public.service_health to service_role;

create policy service_health_authenticated_read
  on public.service_health
  for select
  to authenticated
  using (true);

create policy pnu_notice_worker_service_health_all
  on public.service_health
  for all
  to pnu_notice_worker
  using (true)
  with check (true);

create table public.operator_incidents (
  id text primary key,
  fingerprint text not null unique,
  component text not null,
  severity text not null check (severity in ('warning', 'critical')),
  status text not null check (status in ('open', 'resolved')),
  message text not null,
  first_seen_at timestamptz not null,
  last_seen_at timestamptz not null,
  notified_at timestamptz,
  resolved_at timestamptz
);

create index operator_incidents_open_idx
  on public.operator_incidents (last_seen_at desc)
  where status = 'open';

alter table public.operator_incidents enable row level security;
revoke all on table public.operator_incidents from public, anon, authenticated;
grant select, insert, update, delete on table public.operator_incidents to pnu_notice_worker;
grant all on table public.operator_incidents to service_role;

create policy pnu_notice_worker_operator_incidents_all
  on public.operator_incidents
  for all
  to pnu_notice_worker
  using (true)
  with check (true);
