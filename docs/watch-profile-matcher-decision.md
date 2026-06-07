# Watch Profile Candidate Gate Decision

Status: accepted and initial implementation added

Date: 2026-06-07

Initial implementation note: the first implementation provides stored watch
profiles, deterministic `match`, runtime-facing `scan`, and durable candidate
queueing. The lower-level matcher remains deterministic and LLM-free.

## Context

`pnu-notice check` currently emits every new event batch after the local cursor.
The downstream agent then compares that batch with the user's watch request.

That is correct for a first working flow, but it becomes inefficient when:

- the public feed covers many PNU sources,
- several unrelated notices appear every day,
- user watch requests are narrow, and
- the expensive step is waking an LLM agent to inspect events that obviously do
  not match any watch.

The product still should not become a simple keyword notification bot. The final
answer must come from reading the official notice page and, when needed,
attachments. But the system needs a cheap deterministic gate before invoking the
agent.

## Decision

Add a deterministic watch-profile candidate gate to `pnu-notice-agent-tools`.

This must be a separate layer after `check`, not extra "smart" behavior inside
`check`.

Target flow:

```text
Hermes cron
  -> pnu-notice check
  -> pnu-notice match --watch-profile watches.json
  -> if no candidates, exit quietly
  -> if candidates exist, invoke agent
  -> pnu-notice resolve for selected candidates
  -> reader/model verifies official page and attachments
  -> notify
  -> pnu-notice ack
```

`check` remains responsible only for cursoring, archive enrichment, duplicate
collapse, explicit event/source/topic filters, and deterministic JSON output.

The new candidate gate is responsible for comparing new event metadata with
precompiled watch profiles and returning only plausible candidates.

## Responsibility Split

### Agent Or Skill

An agent or skill compiles a natural-language user watch request into a
deterministic watch profile.

It may use:

- the user's original request,
- recent and historical `public-notice-feed` archive items,
- source/topic distribution,
- common PNU notice vocabulary,
- known synonyms and administrative phrases,
- user-provided constraints.

This is allowed to use LLM reasoning because it runs when a watch is created or
edited, not on every cron tick.

### pnu-notice-agent-tools

The tool consumes an already compiled profile.

It may:

- match positive terms and phrases,
- apply source/category/topic hints,
- apply negative terms,
- score title, snippet, source metadata, tags, and attachment names,
- emit match reasons and a numeric score,
- keep matching deterministic and inspectable.

It must not:

- call an LLM,
- compile freeform natural language into a profile by itself,
- decide the final user-facing answer,
- read HWP/PDF/XLSX/HWPX attachment contents during candidate matching,
- silently advance the cursor before downstream handling succeeds.

## Watch Profile Shape

The profile should preserve the user's request and expose deterministic matching
fields.

Example:

```json
{
  "id": "watch-national-scholarship-round-2",
  "type": "recurring",
  "request": "국가장학금 2차 신청 공지가 뜨면 알려줘",
  "positive_terms": [
    "국가장학금",
    "한국장학재단",
    "학자금지원",
    "지원구간"
  ],
  "phrases": [
    "국가장학금 2차",
    "2차 신청"
  ],
  "source_hints": [
    "pnu-onestop-scholarship",
    "scholarship"
  ],
  "topic_hints": [
    "scholarship"
  ],
  "negative_terms": [
    "근로장학",
    "교내장학",
    "대학원"
  ],
  "attachment_hints": [
    "신청",
    "제출서류",
    "매뉴얼"
  ],
  "candidate_threshold": 5,
  "resolve_threshold": 8
}
```

Fields are intentionally simple. A future version may add weighted terms or
embeddings, but the first version should be debuggable with plain JSON.

## Candidate Match Output

The candidate gate should return structured reasons, not only a boolean.

Target shape:

```json
{
  "type": "pnu_notice_candidates",
  "checked_at": "2026-06-07T12:00:00+09:00",
  "watch_count": 1,
  "input_event_count": 12,
  "candidate_count": 1,
  "candidates": [
    {
      "watch_id": "watch-national-scholarship-round-2",
      "event_id": "event-id",
      "notice_id": "pnu-onestop-scholarship:12345",
      "score": 9,
      "action": "invoke_agent",
      "matched": {
        "positive_terms": ["국가장학금", "한국장학재단"],
        "phrases": ["2차 신청"],
        "source_hints": ["scholarship"],
        "attachment_hints": ["신청"]
      },
      "warnings": []
    }
  ]
}
```

If no candidates match, stdout should be empty or a machine-readable empty
payload should be opt-in. The default cron UX should stay quiet.

## Matching Policy

The first implementation should prefer a conservative deterministic score over
semantic complexity.

Recommended signals:

- title match: strongest cheap signal
- source id/category/topic match: strong routing signal
- snippet match: medium signal
- attachment filename match: strong signal when the user's request often depends
  on attached lists or forms
- negative term match: subtract or suppress
- same-notice duplicate metadata: preserve canonical handling from `check`

The matcher should not require every term. PNU notices often use alternate names
for the same concept. It should also avoid broad OR-only matching that wakes the
agent too often.

## Backtest Requirement

Before relying on a profile, the profile compiler or a separate validation
command should be able to replay it against historical archive items.

Backtest goals:

- estimate how noisy the profile is,
- catch obvious missed terms,
- identify source/topic hints,
- adjust thresholds before cron use.

This can be implemented later, but the profile format should allow backtesting
from the start.

## Relationship To `resolve`

`match` decides whether a new event is a plausible candidate.

`resolve` materializes official local materials for candidates that deserve deep
inspection.

Neither command performs the final user-facing semantic judgment. The final
decision still belongs to the reader/model step that checks the official detail
page and attachments.

## Relationship To Local State

Adding `match` creates local alerting-state requirements beyond the existing
feed cursor.

If no candidates match, the tool still needs to remember that the checked
events were handled for the relevant watch profile revision; otherwise the same
events will wake the matcher again on the next cron run. If candidates do match,
the cursor must not move past them until downstream resolve/read/notify work is
handled.

The local state and ack semantics are documented in
`docs/local-alerting-state-decision.md`.

## Why This Belongs In This Package

The candidate gate is deterministic feed tooling:

- it consumes `pnu-notice check` output,
- it works on feed event/archive metadata,
- it can run quietly inside cron,
- it avoids unnecessary agent invocations,
- it does not need LLM access.

That makes it part of `pnu-notice-agent-tools`, not the user-facing watcher
skill itself.

The watcher skill should own natural-language UX and profile generation. The
tool should own repeatable matching against feed events.

## Addendum: Prefer `scan` For Runtime Flow

After revisiting the layer boundaries, the preferred runtime-facing abstraction
is not:

```text
pnu-notice check
  -> pnu-notice match
  -> pnu-notice finalize
```

The preferred abstraction is:

```text
pnu-notice scan
```

`scan` should combine the deterministic pieces needed before an agent is woken:

```text
read new feed events
  -> match active watch profiles
  -> enqueue matched candidates durably
  -> advance the scan cursor
  -> print candidate payload only when agent work exists
```

This keeps `check` and `match` available as lower-level debug/composition
commands, while giving Hermes or another wrapper a single command that matches
the real UX contract:

```text
If nothing needs agent judgment, stay quiet.
If candidate notices exist, wake the agent.
```

Under this model, the matcher does not need to store every no-match pair by
default. No-match events are handled by scan cursor advancement. Matched events
are protected by the durable candidate queue.

The durable queue and revised state model are documented in
`docs/local-alerting-state-decision.md`.
