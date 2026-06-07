# Local Alerting State Decision

Status: accepted and initial implementation added; open decisions remain

Date: 2026-06-07

Initial implementation note: the first implementation uses SQLite for stored
profiles, scan state, candidate queue, candidate status, and notification
receipt recording. It follows the later addendum direction: `scan_state` plus a
durable candidate queue, not full no-match history by default.

## Context

The added `pnu-notice match` and `pnu-notice scan` commands change the tool from
a simple cursor helper into a small local alerting pipeline.

The simple flow is:

```text
pnu-notice check
  -> pnu-notice match
  -> if no candidates, exit quietly
  -> if candidates exist, resolve/read/notify
  -> pnu-notice ack
```

This flow has a hidden state problem.

If no candidates match and the cursor is not advanced, the same new events will
be checked again on the next cron run. If the cursor is advanced too early while
some candidate still needs `resolve`, reading, or notification, the system can
lose an event after a downstream failure.

Therefore the tool needs local processing state beyond a single feed cursor.

## Research Patterns

The direction follows established event, alerting, and queue patterns.

- Event routers such as AWS EventBridge and Google Pub/Sub subscription filters
  filter events before invoking downstream consumers. Pub/Sub explicitly treats
  messages that do not match a subscription filter as acknowledged by the
  service.
- Reverse-search systems such as Elasticsearch/OpenSearch percolator store
  queries and match incoming documents against them. This is the same shape as
  matching new notice events against saved watch profiles.
- Alerting systems such as Prometheus Alertmanager separate alert routing from
  grouping, deduplication, silencing, and notification delivery.
- Queue systems such as SQS and Pub/Sub separate delivery, retry, visibility,
  and dead-letter handling so failed work can be retried or isolated.
- Durable execution systems emphasize idempotency because retries can repeat
  side effects such as notification sends.
- Alert rules are often testable against samples or history, as in Prometheus
  rule tests and EventBridge event-pattern testing.
- Quiet cron systems need a heartbeat or status surface so "nothing matched" is
  distinguishable from "the job did not run."

Reference examples:

- AWS EventBridge event patterns: https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-event-patterns.html
- Google Pub/Sub subscription filters: https://cloud.google.com/pubsub/docs/subscription-message-filter
- OpenSearch percolate query: https://docs.opensearch.org/docs/latest/query-dsl/specialized/percolate/
- Elasticsearch percolator: https://www.elastic.co/guide/en/elasticsearch/reference/current/percolator.html
- Prometheus Alertmanager: https://prometheus.io/docs/alerting/latest/alertmanager/
- AWS SQS visibility timeout: https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html
- Google Pub/Sub dead-letter topics: https://cloud.google.com/pubsub/docs/dead-letter-topics
- Prometheus rule testing: https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/

## Decision

`pnu-notice-agent-tools` should own deterministic local processing state for the
notice alerting pipeline.

The local state must track more than the feed cursor:

- which events were checked,
- which watch profile revision saw each event,
- whether a pair was a no-match,
- which pairs became candidates,
- whether candidates were resolved,
- whether notification was sent,
- which failures are retryable,
- which failures are terminal or need user attention,
- whether the feed/check/match cron is healthy.

This is still local-first deterministic tooling. The package must not become a
hosted notification service and must not call an LLM.

## Processing Ledger

The tool should keep a local processing ledger keyed by:

```text
event_id + watch_id + watch_profile_revision
```

Recommended statuses:

```text
unchecked
no_match
candidate_pending
resolved
read_checked
notified
completed
failed_retryable
failed_terminal
needs_attention
```

`no_match` is a handled state. It means the candidate gate evaluated that event
against that watch profile revision and decided not to wake the agent.

`completed` is a handled state. It means downstream processing for that
event/watch pair reached a final successful outcome.

`failed_terminal` is handled for cursor movement but should remain inspectable.

`failed_retryable` and `candidate_pending` are not handled for cursor movement.

## Cursor Advancement Rule

The cursor should advance only when all event/profile work up to the candidate
checkpoint is handled.

Handled states:

```text
no_match
completed
failed_terminal
```

Unfinished states:

```text
candidate_pending
resolved
read_checked
failed_retryable
needs_attention
```

This prevents two failure modes:

- repeated quiet no-match events when the cursor never advances;
- lost candidate events when the cursor advances before downstream work
  succeeds.

The current `ack` command can remain as a low-level explicit cursor command.
The future alerting pipeline should add a higher-level ack/finalize command that
advances only to the highest safe checkpoint based on the ledger.

## Profile Baselines And Revisions

Each watch profile needs its own baseline and revision.

Profile baseline answers:

```text
Should this watch start from now, or should it backtest/replay recent archive items?
```

Profile revision answers:

```text
Which version of the compiled watch profile produced this match/no-match result?
```

When a user edits a watch request, the compiler should create a new profile
revision. Old ledger entries should remain tied to the old revision so the tool
can explain past decisions.

## Backtest, Dry Run, And Explain

The matcher should support inspection before cron use.

Planned surfaces:

```text
pnu-notice match --profile watch.json --event-json batch.json --explain
pnu-notice match --profile watch.json --backtest archive/*.json
pnu-notice history --watch WATCH_ID
pnu-notice explain --watch WATCH_ID --event-id EVENT_ID
```

Backtest and explain are not LLM features. They should expose deterministic
signals:

- matched title terms,
- matched snippets,
- matched attachment names,
- source/topic/category hits,
- negative-term suppression,
- score and threshold,
- action chosen.

## Notification Idempotency

Notification sending must be idempotent.

Retries can repeat reader/model steps or notification steps. The tool should
record notification receipts keyed by a stable id such as:

```text
watch_id + event_id + result_classification + result_hash
```

Before sending a notification, the runner should check whether the same receipt
was already sent.

This is especially important when a crash happens after the message is sent but
before the cursor or ledger is updated.

## Failure Handling

Failures should be classified.

Retryable examples:

- temporary network failure while fetching `events.json`,
- temporary official detail-page or attachment download failure,
- temporary Discord/Telegram send failure,
- model/runtime timeout.

Terminal or user-attention examples:

- attachment exceeds configured limits,
- official attachment URL is forbidden or gone,
- unsupported file format after configured reader attempts,
- repeated retryable failure over the retry budget,
- user profile is too broad and wakes the agent too often.

The tool should keep enough state to support retry budgets and a future status
command.

## Heartbeat And Status

Quiet cron behavior needs a status surface.

The system should distinguish:

```text
ran successfully, no new events
ran successfully, events existed but no watch matched
ran successfully, candidates are pending
feed was stale or degraded
cron did not run
downstream handling failed
```

Planned surfaces:

```text
pnu-notice status
pnu-notice watches
pnu-notice history
```

The default notification UX can remain quiet, but local inspection should not be
quiet.

## Notification Dedupe, Throttle, Pause, And Expiry

Feed-level duplicate collapse is not enough.

The alerting pipeline also needs watch-level controls:

- do not notify the same watch/event pair twice,
- collapse same-notice groups per watch,
- optionally throttle noisy recurring watches,
- allow a watch to be paused,
- allow one-shot watches to complete,
- allow watch expiry by date or manual stop.

These controls belong to local state and profile metadata, not to
`public-notice-feed`.

## Reader Escalation Policy

The candidate gate should not automatically send every candidate to the most
expensive reader/model path.

Preferred escalation:

```text
event metadata
  -> resolved detail HTML and attachment list
  -> local/lightweight parsers when available
  -> direct runtime file input when supported
  -> stronger model or manual confirmation only when needed
```

This preserves the same principle as the candidate gate: cheap deterministic
steps first, expensive semantic steps only when justified.

## Open Decisions

These are the decisions still needed before implementation.

### 1. State Backend

Options:

- JSON state file: simple and consistent with current `.pnu-notice-state.json`.
- SQLite: safer for ledger/history queries, retries, and future status commands.

Current leaning: start with JSON only if the first implementation remains small.
Move to SQLite if `history`, retry budgets, or multiple profiles make the JSON
shape awkward.

### 2. Command Surface

Possible commands:

```text
pnu-notice match
pnu-notice finalize
pnu-notice status
pnu-notice watches
pnu-notice history
pnu-notice explain
```

Need to decide whether `match` should update ledger state by default or require
an explicit `--record` flag.

### 3. Cursor And Ledger Coupling

Need to decide whether cursor advancement remains only `ack`, or whether a new
high-level command such as `finalize` computes the safe cursor checkpoint from
the ledger.

Current leaning: keep low-level `ack` for compatibility, add higher-level
finalization for profile-aware flows.

### 4. No-Candidate Output

Need to decide default output when events exist but no watch matches.

Options:

- stdout empty, ledger/heartbeat updated;
- JSON empty payload only with `--verbose` or `--empty-json`.

Current leaning: stdout empty by default for cron UX.

### 5. Watch Profile Storage

Need to decide whether tools store profiles under a default local path or only
consume explicit profile files from the runner.

Current leaning: support explicit profile files first. Add managed profile
storage only when the watcher skill UX needs it.

### 6. Profile Revision Semantics

Need to define what changes create a new profile revision:

- natural-language request edit,
- compiler version change,
- threshold change,
- added/removed positive or negative terms,
- source/topic hint changes.

### 7. Retry Budget

Need to define retry counts, backoff, and terminal transition for:

- feed fetch,
- archive enrichment,
- resolve detail page,
- resolve attachments,
- reader/model step,
- notification send.

### 8. Notification Receipt Boundary

Need to decide whether notification receipts live in `pnu-notice-agent-tools`
state or in the runtime/messaging integration.

Current leaning: tool should provide receipt storage primitives; the actual
Discord/Telegram send remains outside the package.

### 9. Status UX

Need to define the minimum useful `status` output:

- last check time,
- last feed generated time,
- last cursor,
- pending candidates,
- retrying failures,
- terminal failures,
- watch count,
- stale feed warning.

### 10. Backtest Scope

Need to decide whether backtest reads:

- only local archive files,
- published archive URLs,
- `public-notice-feed` source metadata,
- resolved detail pages and attachment names only.

Current leaning: start with local or published archive metadata only. Do not
resolve historical attachments during default backtest.

## Implementation Order

Recommended order:

1. Define state/ledger schema.
2. Implement `match` in stateless dry-run mode.
3. Add `match --record` or equivalent ledger recording.
4. Define safe cursor finalization.
5. Add `status` and `history`.
6. Add backtest/explain.
7. Add notification receipt support.

The state semantics should be decided before implementing a cron-facing
candidate matcher. Otherwise the matcher can reduce LLM calls but still create
duplicate processing or event-loss edge cases.

## Addendum: Revised Simpler Direction

After reviewing the layer boundaries again, the preferred direction is simpler
than a full per-event no-match ledger.

The user-facing UX is:

```text
User asks the agent:
  "When this notice appears, read the details and attachments and tell me."
```

The user does not run `pnu-notice` commands directly. The CLI is an internal
tooling surface for an agent runtime, scheduler, or wrapper.

That changes the state design. The tool should still provide deterministic local
state, but it does not need to record every no-match event/profile pair by
default. It can use:

```text
scan cursor
  -> remembers which feed events were already scanned

durable candidate queue
  -> stores only event/watch pairs that passed deterministic matching
```

With this model, a no-match event is handled by advancing the scan cursor after
the scan completes. A matched event is not lost because it is copied into a
durable candidate queue before the scan cursor advances.

Revised target flow:

```text
watch creation
  -> agent compiles natural-language request into a watch profile
  -> pnu-notice stores the profile

cron wrapper
  -> pnu-notice scan

scan with no candidates
  -> update scan cursor
  -> update heartbeat/run summary
  -> stdout empty
  -> wrapper exits without waking the agent

scan with candidates
  -> write candidate records to durable local queue
  -> update scan cursor
  -> print candidate payload
  -> wrapper invokes the agent

agent handling
  -> pnu-notice resolve candidate
  -> reader/model checks official detail page and attachments
  -> Discord/Telegram send happens outside the tool
  -> pnu-notice marks candidate completed and records notification receipt
```

This keeps the important safety properties:

- unrelated events do not wake the agent repeatedly;
- candidates survive crashes after scan because they are queued locally;
- the feed scan cursor can advance independently from candidate processing;
- notification idempotency can be handled with candidate status and receipts.

The revised minimum state model is:

```text
profiles
  compiled watch profiles and profile revisions

scan_state
  last scanned event cursor, last run time, feed generated_at, warnings

candidates
  watch_id, profile_revision, event_id, match score/reasons, status

notification_receipts
  stable ids for sent notifications
```

Candidate statuses can be simpler than the earlier ledger:

```text
pending
resolving
resolved
notified
completed
failed_retryable
failed_terminal
needs_attention
```

The earlier `event_id + watch_id + watch_profile_revision` ledger remains a
useful conceptual model for auditability, but the implementation should start
from `scan_state + durable candidate queue` unless debugging needs prove that
full no-match history is worth storing.

## Addendum: Revised Command Surface

The cron-facing command should be a higher-level command:

```text
pnu-notice scan
```

`scan` should internally perform:

```text
check feed events
  -> match active profiles
  -> enqueue candidates
  -> advance scan cursor
  -> print candidates only when agent attention is needed
```

Lower-level commands can remain useful for debugging and composition:

```text
pnu-notice check
pnu-notice match
pnu-notice resolve
pnu-notice ack
```

But the Hermes flow should prefer `scan`, because it reflects the real internal
contract:

```text
run quietly unless candidate agent work exists
```

The previous `finalize` idea is less central under this model. Candidate
completion should be expressed on candidate records instead:

```text
pnu-notice candidate complete CANDIDATE_ID
pnu-notice candidate fail CANDIDATE_ID
```

The feed scan cursor does not need to wait for every candidate to finish,
because queued candidates are durable work items.

## Addendum: Layer Boundaries

The revised direction keeps these boundaries:

```text
public-notice-feed
  Publishes public notice metadata, event stream, and archive.
  Does not know users, watches, candidates, or notifications.

pnu-notice-agent-tools
  Owns deterministic local state:
    profiles
    scan cursor
    candidate queue
    resolve cache
    candidate status
    notification receipts
    status/explain/backtest surfaces
  Does not call an LLM.
  Does not write final user-facing message text.

agent skill
  Understands natural-language user requests.
  Compiles and revises watch profiles.
  Uses tools to scan, resolve, inspect status, and mark candidates.
  Produces the final answer to the user.

reader skill or runtime
  Reads or converts PDFs, HWP/HWPX, XLSX, and other attachments.
  Escalates from local parsing to model/file input when needed.

Hermes or wrapper
  Runs cron.
  Calls `pnu-notice scan` before waking the agent.
  Invokes the agent only when candidate payload exists.
  Handles Discord/Telegram transport integration.
```

This is the current preferred design because it keeps the visible UX simple
while avoiding unnecessary LLM wakeups, repeated no-match processing, and lost
candidate events.
