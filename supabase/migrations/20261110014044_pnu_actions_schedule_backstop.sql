-- A separate clock for the two existing workflows. The jobs start disabled.
create schema pnu_ops;
revoke all on schema pnu_ops from public, anon, authenticated, service_role;

create extension if not exists pg_cron;
create extension if not exists http with schema pnu_ops;

create table pnu_ops.actions_schedule_state (
  target text primary key check (target in ('feed', 'email')),
  checked_at timestamptz,
  outcome text,
  http_status integer,
  last_attempt_at timestamptz,
  last_accepted_run_id bigint,
  blocked_secret_version timestamptz
);

alter table pnu_ops.actions_schedule_state enable row level security;
revoke all on pnu_ops.actions_schedule_state from public, anon, authenticated, service_role;
insert into pnu_ops.actions_schedule_state (target) values ('feed'), ('email');

create function pnu_ops.check_actions_schedule(
  workflow_target text,
  observe_only boolean default false
) returns text
language plpgsql
security invoker
set search_path = pg_catalog
as $function$
declare
  observed_at timestamptz := clock_timestamp();
  state pnu_ops.actions_schedule_state%rowtype;
  endpoint text;
  credential text;
  secret_version timestamptz;
  request_headers pnu_ops.http_header[];
  response pnu_ops.http_response;
  history jsonb;
  decision text;
  status_code integer;
  attempted_at timestamptz;
  accepted_run_id bigint;
  reject_credential boolean := false;
begin
  endpoint := case workflow_target
    when 'feed' then 'https://api.github.com/repos/lofidonut3/pnu-public-notice-feed/actions/workflows/update-feed.yml'
    when 'email' then 'https://api.github.com/repos/lofidonut3/pnu-notice-agent-tools/actions/workflows/process-feed-events.yml'
  end;
  if endpoint is null then
    raise exception 'unknown notice scheduler target';
  end if;

  select * into state from pnu_ops.actions_schedule_state
  where target = workflow_target for update skip locked;
  if not found then
    return 'check_in_progress';
  end if;

  <<check_and_dispatch>>
  begin
    select btrim(decrypted_secret), updated_at into credential, secret_version
    from vault.decrypted_secrets where name = 'pnu_actions_scheduler_token';
    if credential is null then
      decision := 'missing_secret';
      exit check_and_dispatch;
    end if;
    if left(credential, 11) <> 'github_pat_' then
      decision := 'wrong_token_type';
      exit check_and_dispatch;
    end if;
    if state.blocked_secret_version = secret_version then
      decision := 'dispatch_blocked';
      exit check_and_dispatch;
    end if;

    request_headers := array[
      pnu_ops.http_header('Authorization', 'Bearer ' || credential),
      pnu_ops.http_header('Accept', 'application/vnd.github+json'),
      pnu_ops.http_header('X-GitHub-Api-Version', '2026-03-10'),
      pnu_ops.http_header('User-Agent', 'pnu-notice-schedule-backstop')
    ];

    begin
      response := pnu_ops.http(('GET', endpoint || '/runs?branch=main&per_page=10',
        request_headers, null, null)::pnu_ops.http_request);
      status_code := response.status;
      if status_code is distinct from 200 then
        decision := 'preflight_rejected';
        exit check_and_dispatch;
      end if;
      history := response.content::jsonb -> 'workflow_runs';
      if jsonb_typeof(history) is distinct from 'array' then
        decision := 'invalid_run_history';
        exit check_and_dispatch;
      end if;
      if exists (
        select from jsonb_array_elements(history) as run
        where (run->>'created_at')::timestamptz > observed_at - interval '45 minutes'
          and run->>'status' is distinct from 'completed'
      ) then
        decision := 'run_in_progress';
        exit check_and_dispatch;
      end if;
      if exists (
        select from jsonb_array_elements(history) as run
        where (run->>'created_at')::timestamptz > observed_at - interval '28 minutes'
          and run->>'conclusion' = 'success'
      ) then
        decision := 'recent_success';
        exit check_and_dispatch;
      end if;
      if (
        select count(*) = 3 and bool_and(conclusion in ('failure', 'timed_out', 'startup_failure'))
        from (
          select run->>'conclusion' as conclusion
          from jsonb_array_elements(history) as run
          order by (run->>'created_at')::timestamptz desc
          limit 3
        ) as latest_runs
      ) then
        decision := 'repeated_workflow_failures';
        exit check_and_dispatch;
      end if;
    exception when others then
      decision := 'preflight_error';
      exit check_and_dispatch;
    end;

    if state.last_attempt_at > observed_at - interval '28 minutes' then
      decision := 'dispatch_cooldown';
      exit check_and_dispatch;
    end if;
    if observe_only is distinct from false then
      return 'would_dispatch';
    end if;

    attempted_at := clock_timestamp();
    status_code := null;
    begin
      response := pnu_ops.http(('POST', endpoint || '/dispatches',
        request_headers, 'application/json', '{"ref":"main"}')::pnu_ops.http_request);
      status_code := response.status;
      if status_code = 200 then
        accepted_run_id := (response.content::jsonb ->> 'workflow_run_id')::bigint;
        if accepted_run_id > 0 then
          decision := 'accepted';
        else
          accepted_run_id := null;
          decision := 'dispatch_unconfirmed';
        end if;
      else
        decision := 'dispatch_rejected';
        reject_credential := status_code in (401, 403, 404, 422);
      end if;
    exception when others then
      -- POST may have reached GitHub. Never repeat it in the same check.
      decision := 'dispatch_unconfirmed';
    end;
  end check_and_dispatch;

  if observe_only is false then
    update pnu_ops.actions_schedule_state
    set checked_at = observed_at, outcome = decision, http_status = status_code,
        last_attempt_at = coalesce(attempted_at, last_attempt_at),
        last_accepted_run_id = coalesce(accepted_run_id, last_accepted_run_id),
        blocked_secret_version = case when reject_credential then secret_version
          else blocked_secret_version end
    where target = workflow_target;
  end if;
  return decision;
end;
$function$;

revoke all on function pnu_ops.check_actions_schedule(text, boolean)
from public, anon, authenticated, service_role;

select cron.alter_job(
  cron.schedule('pnu-feed-schedule-backstop', '2-59/5 * * * *',
    $job$select pnu_ops.check_actions_schedule('feed');$job$),
  active := false
);
select cron.alter_job(
  cron.schedule('pnu-email-schedule-backstop', '4-59/5 * * * *',
    $job$select pnu_ops.check_actions_schedule('email');$job$),
  active := false
);
