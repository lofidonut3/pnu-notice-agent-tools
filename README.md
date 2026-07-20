# PNU Notice Agent Tools

PNU Notice Agent Tools is a local CLI package for consuming
[`pnu-public-notice-feed`](https://github.com/lofidonut3/pnu-public-notice-feed)
`events.json`, scanning it against compiled watch profiles, queueing candidate
notices for an agent, and materializing selected official notice materials.

It is not a real-time notification server and it does not crawl PNU websites
for feed generation. When run by cron, an agent runtime, or another automation
process, `check` reads `events.json`, selects only events after the local cursor,
and prints a JSON batch to stdout. If there are no new events, it exits quietly.
When an agent needs official materials for one selected notice, `resolve` can
fetch the official detail page and requested attachments into a local cache.
The `scan` command compares checked events with precompiled watch profiles
before an agent is invoked.

`run-watch-cycle` is the hosted-worker entry point. It scans the durable feed
cursor, processes queued candidates, resolves relevant official materials,
reuses each stored compiled intent, queues matched email in a durable outbox,
and retries due outbox deliveries.

This project and `pnu-public-notice-feed` are unofficial projects. They are not
operated by Pusan National University.

## Role

```text
pnu-public-notice-feed
  -> publishes public notice metadata as events.json

pnu-notice-agent-tools
  -> check prints only events after the local cursor
  -> scan matches new events against stored watch profiles before waking an agent
  -> check enriches compact events from monthly archive metadata
  -> check collapses same-notice duplicate groups
  -> queues matched candidates durably in local SQLite state
  -> resolve can materialize one selected notice's official page and attachments locally
  -> optionally compiles watch requests and performs grounded final analysis with NVIDIA endpoints
  -> parses text, PDF, XLSX, CSV, ZIP, and HWP when the optional readers are available
  -> transcribes image-only pages with the configured multimodal endpoint
  -> does not persist or mirror full notice bodies or attachment contents
  -> can optionally send matched plain-text results through operator-provided SMTP

AI agent / automation
  -> compiles natural-language watch requests into watch profiles
  -> invokes the candidate gate before waking expensive reader/model steps
  -> asks this tool to fetch official notice materials when needed
  -> reads fetched materials directly or through a separate reader skill
```

The goal is to let deterministic code reduce the feed to a small event batch
before an agent or automation step handles it.

## Intended UX Flow

```text
1. A scheduler or lightweight wrapper runs `pnu-notice scan`.
2. If stdout is empty, no candidate needs agent work and the run stops.
3. If candidates are printed, the wrapper invokes the agent.
4. The agent runs `pnu-notice resolve --candidate-id ...` for selected notices.
5. `resolve` saves the official detail page and requested attachments locally.
6. A model or separate reader skill reads the local materials when needed.
7. After successful handling, the agent runs `pnu-notice candidate complete`.
```

The deterministic scan and candidate gate remain LLM-free. Optional commands
can compile a natural-language request once, extract selected candidate
materials, call NVIDIA's hosted chat/embedding endpoints for grounded final
judgment, and send matched results through SMTP.

`events.json` events are compact routing records. By default, this helper follows
each event's `archive_file` and `archive_item_id` fields to enrich the output
with preview text, attachment metadata, and content access metadata from the
public monthly archive.

The resolver direction is documented in
[docs/content-resolver-decision.md](docs/content-resolver-decision.md). In
short: `check` stays simple, while `resolve` should become an on-demand
materials fetcher. It should fetch the official detail page and selected
attachments into a local cache, then print a manifest with paths, source URLs,
sizes, hashes, media types, and fetch statuses. Reading HWP/PDF/XLSX/HWPX
contents belongs in a separate reader skill or agent workflow.

The candidate gate direction is documented in
[docs/watch-profile-matcher-decision.md](docs/watch-profile-matcher-decision.md).
In short: natural-language watch requests should be compiled into deterministic
watch profiles when they are created or edited. Cron runs should use those
profiles to cheaply decide whether any checked event deserves agent attention.

The local state direction is documented in
[docs/local-alerting-state-decision.md](docs/local-alerting-state-decision.md).
In short: `scan` keeps a scan cursor and durable candidate queue in local
SQLite state, so unrelated notices stay quiet while matched candidates survive
until resolve, reading, and notification handling completes.

The optional cloud analysis direction is documented in
[docs/jules-cloud-analysis-decision.md](docs/jules-cloud-analysis-decision.md).
In short: a hosted watch gate may call Jules as an optional cloud worker for
selected candidate notices, but Jules should not own scheduling, state, feed
matching, or email delivery.

The Gemini subscription-based cloud-agent direction is documented in
[docs/gemini-scheduled-action-queue-decision.md](docs/gemini-scheduled-action-queue-decision.md).
In short: a user may create one Gemini Scheduled Action that reads hidden Gmail
queue emails from the hosted watch gate, uses the user's Gemini subscription for
final relevance analysis, and reports only truly relevant notices through
Gemini.

The lower-friction Gemini public-feed direction is documented in
[docs/gemini-public-feed-decision.md](docs/gemini-public-feed-decision.md).
In short: a shared setup Gem can collect the user's watch request and create one
Gemini Scheduled Action, while the public feed exposes Gemini-friendly latest
and evidence pages that Gemini can read without Antigravity, API keys, or
hosted per-user watch state.

The lower-level `check` and `match` commands remain useful for debugging and
composition.

## Scan Runtime Flow

Store a compiled watch profile:

```bash
python3 run.py profile upsert --profile-json watch.json
```

The course-cancellation example discussed during design is ready to register:

```bash
python3 run.py profile upsert \
  --profile-json examples/watches/summer-2026-database-001-cancelled.json \
  --db "$PNU_DATABASE_URL"
```

Run the quiet cron-facing scan:

```bash
python3 run.py scan
```

If no candidate matches, stdout is empty and the scan cursor still advances.
If candidates match, stdout contains a `pnu_notice_candidates` payload and the
candidates are stored in the local SQLite queue.

Inspect local state:

```bash
python3 run.py status --pretty
python3 run.py candidate list --status pending --pretty
```

Resolve and complete a candidate:

```bash
python3 run.py resolve --candidate-id cand_... --download-attachments --pretty
python3 run.py candidate complete --candidate-id cand_... --result-json result.json
```

## Run

```bash
python3 run.py
```

Run one complete watch cycle with local SQLite state:

```bash
python3 run.py run-watch-cycle \
  --db .pnu-notice-state.sqlite3 \
  --email-to student@example.com \
  --no-send --pretty
```

`--no-send` leaves matched notifications in the outbox. Remove it after the
`PNU_SMTP_*` variables are configured.

## Hosted Watch Runtime

The workflow at `.github/workflows/process-feed-events.yml` runs on:

- `repository_dispatch` type `pnu-feed-updated` after a successful feed deploy;
- an hourly fallback schedule at minute 17;
- manual `workflow_dispatch` for bootstrap and diagnostics.

GitHub-hosted runners are ephemeral, so hosted execution must use Postgres rather
than the default local SQLite file. Create a Supabase project, copy its Postgres
connection URI with TLS enabled, and configure these repository secrets:

| Secret | Purpose |
| --- | --- |
| `PNU_DATABASE_URL` | Supabase Postgres connection URI |
| `NVIDIA_API_KEY` | Watch compilation and final analysis |
| `PNU_EMAIL_TO` | Default recipient when a profile has no `delivery.email_to` |
| `PNU_SMTP_HOST` | SMTP server |
| `PNU_SMTP_FROM` | Sender address |
| `PNU_SMTP_USERNAME` | Optional SMTP username |
| `PNU_SMTP_PASSWORD` | Optional SMTP password |

Optional repository variables are `PNU_SMTP_PORT` (default `587`) and
`PNU_SMTP_STARTTLS` (default `true`). The worker creates and migrates its tables
on connection. The database role therefore needs schema/table creation and normal
read/write privileges.

The first scheduled or dispatched run establishes a baseline cursor and does not
analyze historical events. Use manual `workflow_dispatch` with
`include_baseline=true` only when intentionally backfilling the current event
window.

In the feed repository, set `WATCH_DISPATCH_TOKEN` to a token that can create
repository dispatches in this repository. The feed workflow already defaults
`WATCH_DISPATCH_REPOSITORY` to `lofidonut3/pnu-notice-agent-tools`; use that
optional variable only to override the target. A dispatch is only a wake hint;
the hourly scan and persisted Postgres cursor recover missed or duplicate hints.

On the first run, the helper stores the current latest event as a baseline and
does not print old events. Later runs print JSON only when new events are
available.

Default feed URL:

```text
https://lofidonut3.github.io/pnu-public-notice-feed/events.json
```

Use a local feed file:

```bash
python3 run.py \
  --events-url file:///path/to/pnu-public-notice-feed/public/events.json \
  --pretty
```

Print the current event window even on the first run:

```bash
python3 run.py check --include-baseline --limit 3 --pretty
```

## Output

If there are no new events, stdout is empty and the exit code is `0`.

If there are new events or cursor warnings, stdout contains a JSON payload:

```json
{
  "type": "pnu_feed_events",
  "events_url": "https://lofidonut3.github.io/pnu-public-notice-feed/events.json",
  "checked_at": "2026-06-05T13:30:00+09:00",
  "feed_generated_at": "2026-06-05T13:17:37+09:00",
  "feed_latest_event_id": "new-event-id",
  "cursor_status": "event_id",
  "warnings": [],
  "previous_cursor": {
    "last_seen_event_id": "previous-event-id",
    "last_seen_at": "2026-06-05T12:00:00+09:00"
  },
  "next_cursor": {
    "last_seen_event_id": "new-event-id",
    "last_seen_at": "2026-06-05T12:25:11+09:00"
  },
  "new_event_count": 1,
  "filtered_event_count": 1,
  "selected_event_count": 1,
  "dedupe_enabled": true,
  "suppressed_duplicate_count": 0,
  "archive_enrichment_enabled": true,
  "events": [
    {
      "event_id": "new-event-id",
      "event_type": "added",
      "notice_id": "pnu-main-notice:1500000",
      "source_id": "pnu-main-notice",
      "source_name": "PNU main notice",
      "source_category": "university_notice",
      "topics": ["academic"],
      "same_notice_group_id": null,
      "canonical_item_id": "pnu-main-notice:1500000",
      "is_canonical": true,
      "same_notice_source_ids": ["pnu-main-notice"],
      "title": "Notice title",
      "url": "https://www.pusan.ac.kr/...",
      "snippet": "Short preview text",
      "content_access": {
        "detail_url": "https://www.pusan.ac.kr/...",
        "requires_login": false,
        "content_mirrored": false,
        "attachments_mirrored": false
      },
      "attachments": [
        {
          "name": "attachment.pdf",
          "url": "https://www.pusan.ac.kr/...",
          "type": "pdf",
          "media_type": "application/pdf",
          "file_extension": "pdf"
        }
      ]
    }
  ]
}
```

By default, output events are compact records enriched with archive metadata.
To skip archive lookup and print only fields available in `events.json`:

```bash
python3 run.py check --no-archive --pretty
```

To print full event objects:

```bash
python3 run.py check --full --pretty
```

By default, same-notice duplicate groups are collapsed to one canonical event.
To print every matching event:

```bash
python3 run.py check --no-dedupe --pretty
```

## Resolve Official Materials

`resolve` outputs a materials manifest, not a parsed content bundle.

Resolve one selected event from a `pnu-notice check` payload:

```bash
python3 run.py resolve --event-json selected-event.json --pretty
```

Resolve the second event from a `check` payload that contains an `events` array:

```bash
python3 run.py resolve --event-json payload.json --event-index 1 --pretty
```

Resolve a direct official notice URL:

```bash
python3 run.py resolve --url "https://www.pusan.ac.kr/..." --pretty
```

Download original attachments as local materials when needed:

```bash
python3 run.py resolve --event-json selected-event.json --download-attachments --pretty
```

For notices with many attachments, resolve the detail page and manifest first,
then conservatively select files from the original watch request and attachment
names:

```bash
python3 run.py resolve \
  --event-json selected-event.json \
  --download-relevant-attachments \
  --watch-request "2026 여름계절수업에서 데이터베이스 001분반 폐강되면 알려줘" \
  --pretty
```

Use repeated `--attachment-index` flags when a caller or planner has already
selected exact manifest entries. Sets of three or fewer attachments are fetched
together; when filename evidence is inconclusive, relevant mode also falls back
to all attachments to avoid a silent false negative.

The target output is a JSON materials manifest:

```json
{
  "type": "pnu_notice_materials",
  "resolved_at": "2026-06-07T12:00:00+09:00",
  "notice": {
    "event_id": "event-id",
    "notice_id": "pnu-main-notice:1500000",
    "source_id": "pnu-main-notice",
    "title": "Notice title",
    "detail_url": "https://www.pusan.ac.kr/..."
  },
  "detail": {
    "url": "https://www.pusan.ac.kr/...",
    "local_path": ".pnu-notice-cache/materials/pnu-main-notice-1500000/detail.html",
    "media_type": "text/html",
    "bytes": 48291,
    "sha256": "...",
    "fetch_status": "ok",
    "text_preview": "Optional short visible text preview..."
  },
  "attachments": [
    {
      "index": 0,
      "name": "attachment.hwp",
      "url": "https://www.pusan.ac.kr/...",
      "local_path": ".pnu-notice-cache/materials/pnu-main-notice-1500000/attachments/00.hwp",
      "file_extension": "hwp",
      "media_type": "application/x-hwp",
      "bytes": 79360,
      "sha256": "...",
      "fetch_status": "ok"
    }
  ],
  "limits": {
    "max_file_bytes": 10000000,
    "max_total_bytes": 30000000
  },
  "warnings": []
}
```

`resolve` only fetches and records official local materials. The separate
`analyze` command consumes that manifest, extracts supported files, retrieves a
small evidence set, and asks the configured model for a citation-constrained
decision. When given only `--url`, `resolve` derives candidate attachment links
from the official detail page when possible.

## Optional NVIDIA Analysis

Install attachment readers and set an NVIDIA trial API key in the environment:

```bash
python3 -m pip install -e ".[analysis]"
export NVIDIA_API_KEY="..."
```

Compile and store a broad deterministic candidate profile:

```bash
python3 run.py profile compile \
  --watch-id summer-db-001 \
  --request "2026 여름계절수업에서 데이터베이스 001분반 폐강되면 알려줘" \
  --store --pretty
```

After `resolve --download-attachments`, analyze its manifest:

```bash
python3 run.py analyze \
  --request "2026 여름계절수업에서 데이터베이스 001분반 폐강되면 알려줘" \
  --materials-json materials.json --pretty
```

A compiled profile can be reused so the natural-language request is not compiled
again for every notice. Load the active revision directly from the SQLite store:

```bash
python3 run.py analyze \
  --watch-id summer-db-001 \
  --db .pnu-notice-state.sqlite3 \
  --materials-json materials.json --pretty
```

`--watch-profile-json watch-profile.json` remains available for file-based
pipelines, and `--revision` can select a non-active stored revision explicitly.

Extraction prefers native document text and tables, then local Tesseract OCR.
Only unresolved visual evidence selected from the request, compiled intent, and
available text is sent to the multimodal endpoint. The default visual limit is
eight pages and can be changed with `--max-visual-pages`.

Use `--dry-run` to inspect extracted evidence without an API call. Use
`--email-to` only after configuring `PNU_SMTP_HOST`, `PNU_SMTP_FROM`, and the
optional `PNU_SMTP_USERNAME`/`PNU_SMTP_PASSWORD` variables. The email contains
plain-text facts and source citations, not original attachments.

Run the six-case endpoint evaluation:

```bash
python3 scripts/evaluate_ai_flow.py --output evaluation/latest-report.json
```

Run the deterministic gate evaluation captured from the public feed on
2026-07-19:

```bash
python3 scripts/evaluate_watch_gate.py \
  --output evaluation/watch_gate_report.json
```

It contains 15 scholarship, dormitory, exchange, recruitment, internship,
outage, graduation, loan, and negative-control watch cases.

## Cursor Policy

The default `check` command does not advance the cursor after printing events.
This prevents event loss if a downstream agent or automation step fails after
receiving the batch.

After downstream handling succeeds, advance the cursor with `ack`.

When duplicate collapse is enabled, some duplicate events can be hidden from the
output. For that reason, it is safer to ack the payload's `next_cursor` values
instead of manually choosing the last printed event id.

```bash
python3 run.py ack \
  --event-id "$NEXT_CURSOR_EVENT_ID" \
  --seen-at "$NEXT_CURSOR_SEEN_AT"
```

For simple cron jobs where convenience matters more than explicit acking, use
`--advance`:

```bash
python3 run.py check --advance
```

## Cursor Status

- `no_cursor`: local state is empty. Without `--include-baseline`, the first run
  stores only the current feed baseline.
- `event_id`: `last_seen_event_id` was found in `events.json`; events after it
  were selected.
- `seen_at`: the event id was not found, but `last_seen_at` was used to select
  newer events.
- `archive_event_id`: the local cursor was older than the current `events.json`
  window, and monthly archives were used to catch up from `last_seen_event_id`.
- `archive_seen_at`: the local cursor was older than the current `events.json`
  window, and monthly archives were used to catch up from `last_seen_at`.
- `archive_required`: the local cursor was older than the current `events.json`
  window, but archive catch-up was not completed.
- `stale_cursor`: the event id was not found and `last_seen_at` is unavailable;
  output can include already handled events.

Disable archive catch-up and inspect only the current `events.json` window:

```bash
python3 run.py check --no-archive-catchup
```

## Filters

Include specific sources:

```bash
python3 run.py check --source pnu-main-notice --source pnu-onestop-scholarship
```

Include specific source categories:

```bash
python3 run.py check --source-category academic_unit_scholarship_notice
```

Include specific topic hints:

```bash
python3 run.py check --topic scholarship --topic contest
```

Include only new notice events:

```bash
python3 run.py check --event-type added
```

## State

Default state file:

```text
.pnu-notice-state.json
```

Use a custom state file:

```bash
python3 run.py check --state ~/.pnu-agent/pnu-notice-state.json
```

## Install Locally

```bash
python3 -m pip install -e .
pnu-notice check --pretty
```

The legacy `pnu-event-gate` command is kept as a compatibility alias.

## Test

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest -q
```
