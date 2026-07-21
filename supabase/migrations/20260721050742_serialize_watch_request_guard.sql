create function private.serialize_watch_request_guard()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  caller_id uuid := (select auth.uid());
begin
  if caller_id is not null then
    perform pg_catalog.pg_advisory_xact_lock(
      pg_catalog.hashtextextended(caller_id::text, 0)
    );
  end if;
  return new;
end;
$$;

revoke all on function private.serialize_watch_request_guard()
  from public, anon, authenticated;

create trigger a_serialize_watch_request_guard
before insert or update on public.watch_requests
for each row execute function private.serialize_watch_request_guard();
