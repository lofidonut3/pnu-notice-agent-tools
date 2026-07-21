# AI-Assisted Notice Analysis Architecture Decision

Status: accepted as the target architecture; the current implementation is a transitional MVP

Date: 2026-07-19

## Context

The service accepts natural-language watch requests such as:

> Notify me if Database section 001 is cancelled during the 2026 summer session.

It must monitor public Pusan National University notices, inspect attached PDF,
spreadsheet, HWP, and image files, decide whether a request has been satisfied,
and send an evidence-backed notification.

The first implementation can perform this as a mostly linear pipeline:

```text
feed
  -> candidate gate
  -> attachment extraction
  -> evidence reduction
  -> AI judgment
  -> citation validation
  -> email
```

This is a suitable MVP skeleton, but applying the full pipeline independently for
every user and every notice would repeat downloads, extraction, OCR, and model
calls. It would also ask a language model to perform exact comparisons that are
more reliable and cheaper in deterministic code.

The architecture therefore needs to preserve the early candidate gate while
sharing expensive notice processing across all watches.

PNU source boards do not provide a reliable common webhook. The upstream source
boundary therefore remains polling-based. Event-driven execution begins after the
feed generator has durably observed and published a new event.

## Decision Summary

The system will use a two-phase architecture:

1. Compile each natural-language watch request once into a persisted `WatchSpec`.
2. Process each plausible notice once, cache its extracted material, and evaluate
   all matching watches against that shared representation.
3. Emit a wake signal after a successful feed publication so the watch runtime can
   process its durable cursor immediately instead of waiting for another schedule.

AI is required, but it is not the default implementation for every step. It is
responsible for natural-language compilation, difficult visual extraction, and
ambiguous semantic judgment. Deterministic code remains responsible for feed
ingestion, exact filtering, document parsing, structured comparisons, validation,
idempotency, and delivery.

## Target Flow

```text
Watch registration
  user request
    -> AI WatchSpec compiler
    -> schema validation
    -> persist original request + WatchSpec
    -> optional user confirmation

Notice runtime
  feed generator observes added/updated notice
    -> publish feed and durable event cursor
    -> emit feed_updated wake signal
  watch runtime wakes
    -> replay events after its own durable cursor
    -> union metadata gate across active watches
    -> stop if no watch can plausibly match
    -> fetch detail page and attachment manifest once
    -> plan required attachment downloads
    -> fetch selected attachments once
    -> cache by notice ID and content hash
    -> extract and chunk once
    -> evaluate candidate watches
         -> deterministic predicate evaluator where available
         -> AI semantic judge only where needed
    -> citation and predicate validation
    -> durable notification outbox
    -> email delivery with retries and receipts
```

The unit of expensive processing is the notice, not the user. The unit of final
decision remains the watch-notice pair.

The wake-signal payload is never the source of truth. It may contain the latest
event ID as a hint, but the consumer always replays from its persisted cursor. This
makes duplicate or lost dispatches recoverable. A periodic fallback scan remains
enabled because GitHub schedules and outbound dispatches are best-effort.

## Feed Event Origin and Recovery

The feed generator polls each official source according to its configured interval.
It does not run AI and it does not wait for an AI agent to inspect GitHub Actions.
When a generated run contains added or updated durable events, the successful
publication emits a `pnu-feed-updated` wake signal to the watch runtime.

`repository_dispatch`, an authenticated webhook, or an equivalent queue may carry
the signal. Dispatch happens only after the new feed is durable. A downstream run
that wakes successfully still reads `events.json` and archive files from its own
cursor.

Each source maintains independent recovery state:

- last successful check time;
- recent known notice IDs, not only a numeric maximum ID;
- error count, backoff deadline, and last error;
- recovery or catch-up status;
- the last known-good source items.

After an outage, a paginated adapter starts from the newest page and continues
until it finds a previously known, non-pinned notice ID. A pinned notice does not
count as the catch-up boundary because it may appear on every page. If the boundary
cannot be found within a bounded number of pages, the source fails closed with a
catch-up error and does not advance its success state.

Maintenance pages, unexpectedly empty lists after prior data existed, duplicate
IDs, invalid source IDs, excessive text, and excessive attachment counts are source
errors. They must not replace the last known-good cache or be interpreted as mass
deletion.

Source freshness degradation does not block valid events from other sources from
being published. Structural corruption and publish-size violations remain blocking.
Freshness health is reported and alerted separately from publish invariants.

## Watch Registration

The original user request must always be retained. AI compiles it into a versioned,
machine-readable `WatchSpec` only when the watch is created or explicitly edited.
It must not be recompiled for every incoming notice.

A `WatchSpec` should contain at least:

- original request text;
- normalized entities and aliases;
- required and excluded terms;
- source or category constraints;
- applicable date or academic-term constraints;
- predicate type and predicate parameters;
- notification condition;
- uncertainty policy;
- compiler model, prompt, and schema versions.

For the example request, the durable representation would identify the summer
2026 term, course name `Database`, section `001`, and the `state_change` predicate
with target state `cancelled`.

Schema validation is mandatory. If compilation produces missing or contradictory
fields, registration must request correction instead of silently creating a broad
watch.

## Candidate Gating

The first runtime stage is a deterministic union gate over all active watches.
It uses cheap feed and notice metadata such as title, body preview, source,
department, publication date, category, and known attachment names.

The gate answers only:

> Could at least one active watch plausibly match this notice?

It must favor recall over precision. A negative result stops processing. A positive
result identifies candidate watches and permits attachment download and extraction.
It is not a final relevance decision.

Exact terms, aliases, source constraints, date windows, and lexical retrieval are
evaluated before embeddings. Embeddings are a fallback for synonymy and weak
lexical overlap, not a replacement for exact matching.

## Shared Notice Processing

When at least one watch passes the union gate, the service first downloads the
official detail page and resolves the attachment manifest. It then applies an
attachment plan. Small attachment sets are normally downloaded together. Larger
sets are selected using exact terms, filenames, media types, known predicate needs,
and an optional AI planner when deterministic signals are ambiguous.

The attachment planner may prioritize or request more material, but metadata-only
AI judgment must not reject a notice whose decisive evidence may be inside an
unread attachment. Selected attachments are downloaded once. Reusable artifacts
are persisted using:

- notice source and notice ID for logical identity;
- attachment URL and SHA-256 for content identity;
- extractor and OCR versions for derived-artifact identity.

The shared notice record may contain:

- normalized notice text and metadata;
- attachment inventory and hashes;
- extracted page, sheet, row, and cell content;
- table-preserving structured records;
- OCR text and confidence;
- reusable text chunks with stable evidence IDs;
- optional chunk embeddings;
- extraction warnings and provenance.

An unchanged selected attachment must not be downloaded, parsed, embedded, or OCRed
again for another user. If content changes under the same URL, the hash creates a
new material version and triggers reevaluation where required. Attachments omitted
by an initial plan remain discoverable in the manifest and can be fetched later
without resolving the notice again.

## Extraction Policy

Extraction uses the cheapest reliable representation in this order:

1. Compile or load the persisted WatchSpec.
2. Native text and table extraction.
3. Local OCR for pages or images without usable text.
4. Lexical and structured selection of relevant visual pages.
5. Searchable OCR or alternate parser recovery when local extraction is weak.
6. Vision-language model analysis only for selected, low-confidence visual regions.

A vision model must not receive every page of a long document by default. Page
selection is based on attachment metadata, lexical hits, table headers, OCR
confidence, and neighboring context.

Tables must retain row and column relationships. Flattened text is insufficient
when the decision depends on values occurring in the same row, such as course,
section, status, date, or eligibility category.

## Evidence Retrieval and Token Budget

Evidence reduction is retrieval, not free-form summarization. It selects source
fragments that may prove or disprove the watch predicate and preserves enough
surrounding context to interpret them.

Default model input budgets are:

| Case | Target evidence budget |
| --- | ---: |
| Typical notice | 1,500-4,000 tokens |
| Complex multi-document notice | 4,000-8,000 tokens |
| Exceptional hard limit | 12,000 tokens |

The previously discussed 5,000-15,000-token range is not a target. Sending more
material can reduce judgment quality as well as increase cost and latency.

Each evidence item has a stable ID and source location, for example notice body,
attachment, page, sheet, row, or cell range. Neighboring rows or paragraphs may be
included conservatively, but unrelated full attachments are not sent merely because
they are available.

Watch embeddings are computed once at registration. Chunk embeddings, when used,
are computed once after extraction. Full-text search, BM25, exact identifiers, and
structured field matching run before vector retrieval.

## Predicate Evaluators

The system will classify watches into predicate families and use specialized
evaluators where possible.

| Predicate | Preferred evaluator |
| --- | --- |
| Announcement occurrence | Metadata and exact/lexical matching |
| Eligibility or attribute inclusion | Structured row/field comparison |
| State change | Versioned before/after comparison |
| Deadline trigger | Parsed date plus clock logic |
| Result announcement | Identifier and result-field comparison |
| Open-ended relevance | AI semantic judgment with cited evidence |

For a course cancellation, title similarity alone is not proof. The evaluator must
show that the course-section pair previously existed and is later explicitly marked
cancelled or absent from an authoritative replacement list under a defined diff
policy. Exact spreadsheet and table diffs belong in deterministic code.

AI may interpret ambiguous wording, but it must not invent a state transition from
a single unversioned document.

## AI Responsibility Boundary

AI is used for:

- compiling a natural-language request into a `WatchSpec`;
- resolving semantic ambiguity not covered by deterministic predicates;
- interpreting selected low-confidence images or complex visual tables;
- producing a final structured judgment for open-ended watches.

AI is not used for:

- polling feeds or detecting new IDs;
- downloading or deduplicating files;
- exact date, amount, count, course, section, or status comparisons;
- citation existence checks;
- retry, idempotency, or delivery state;
- rewriting the final email when structured decision data already exists.

Provider access is behind an internal interface. Model and provider changes must
not alter the `WatchSpec`, evidence, or decision schemas. Free endpoints may be used
for personal evaluation, but the architecture must not depend on undocumented
capacity or quota evasion.

## Decision and Validation Contract

Every evaluator returns a structured result such as:

```json
{
  "status": "matched | not_matched | uncertain",
  "predicate": "state_change",
  "reason": "short machine-auditable explanation",
  "evidence_ids": ["notice:123:attachment:abc:sheet:1:row:42"],
  "facts": {
    "course": "Database",
    "section": "001",
    "state": "cancelled"
  }
}
```

Validation is stronger than checking whether cited IDs exist. It verifies that:

- each claim is supported by the cited source fragment;
- coupled values occur in the same row or logically linked record;
- dates, amounts, counts, course names, and sections match parsed values;
- all evidence required by the predicate is present;
- state-change claims include the required version history;
- evidence belongs to the evaluated notice and material version.

An unsupported `matched` decision is downgraded to `uncertain` and is not sent as
a confirmed alert. Validation failure must be observable and retained for review.

## Notification Delivery

Email content is rendered from validated structured data using a fixed template.
No additional model call is needed solely to compose the message.

The message contains:

- the original watch request;
- the decision and concise reason;
- the relevant extracted evidence with source labels;
- links to the official notice and attachments;
- an explicit uncertainty label when applicable.

Validated notifications enter a durable outbox before delivery. The delivery layer
supports retries, receipts, and a terminal `needs_attention` or dead-letter state.
The idempotency key is derived from:

```text
watch_id + event_id + decision_hash
```

This prevents duplicate email while allowing a materially changed decision to be
sent again.

## Failure Policy

The system fails closed for confirmed alerts:

- extraction failure does not imply no match;
- model failure does not imply no match;
- missing predicate evidence does not permit `matched`;
- delivery failure does not discard the validated decision;
- one provider outage does not corrupt persisted watch or evidence state.

Recoverable extraction and model failures are retried with bounded policies. Cases
that remain unresolved become `uncertain` or `needs_attention` according to the
watch policy.

## Transition from the MVP

The current linear implementation remains useful for end-to-end quality validation,
but it is a transitional state. Migration will preserve existing feed and extraction
behavior while changing ownership of cached artifacts and decisions.

### Implemented through the 2026-07-21 iteration

- The feed publishes valid healthy-source changes even when non-critical sources
  are temporarily degraded, while size and structural checks remain blocking.
- K2Web polling uses known-ID pagination catch-up, excludes pinned notices as a
  boundary, preserves cached data on implausible responses, and validates source
  payload limits.
- A successful feed deployment with added or updated notices can emit a
  `pnu-feed-updated` repository-dispatch wake hint.
- Material resolution always produces the detail page and attachment manifest,
  and supports all, conservative relevant, or explicit-index attachment plans.
- Compiled watch intent can be persisted in SQLite and loaded by `analyze
  --watch-id`, avoiding repeated request compilation.
- Native extraction and local OCR precede targeted visual-model transcription;
  unresolved visual input defaults to at most eight selected pages.
- The same state API supports local SQLite and Supabase-compatible Postgres.
- `run-watch-cycle` performs scanning, candidate processing, relevant attachment
  extraction, stored-intent analysis, outbox enqueue, and due-delivery retries.
- A GitHub Actions receiver handles `pnu-feed-updated` and retains an hourly
  cursor-based fallback schedule with non-overlapping concurrency.
- The durable email outbox uses watch, event, decision, channel, and recipient
  identity to suppress duplicate delivery and retain retry state.
- The hosted runtime is provisioned with Supabase Postgres, Gemini as the default
  analysis provider, NVIDIA as an optional fallback, Gmail SMTP delivery, and the
  feed repository's dispatch credential. SMTP and repository-dispatch smoke tests
  have both completed successfully.
- Every scan and complete watch cycle persists a durable run record with command,
  timestamps, outcome, event and candidate counts, and warnings. Uncaught failures
  after database initialization are recorded before the workflow exits, and
  `status` exposes aggregate and latest run information.
- The PNU Watch web app uses Supabase email authentication and an RLS-protected
  `watch_requests` queue. Browser clients can create, edit, pause, and resume only
  their own requests; a separate lightweight worker compiles pending requests into
  private versioned watch profiles without exposing runtime credentials or internal
  state to the browser.
- Watch create, edit, pause, and resume actions invoke an authenticated Edge
  Function that emits a `pnu-watch-requested` wake hint. The ten-minute compiler
  schedule remains the durable recovery path when dispatch is unavailable.
- Per-account database guards bind delivery addresses to the authenticated account,
  cap enabled watches, and rate-limit create/edit compilation requests. These checks
  run in Postgres so they cannot be bypassed by a modified browser client.
- Final decisions are persisted as a user-owned notification projection containing
  the result, delivery state, normalized facts, and compact evidence locations. The
  web app exposes this history without granting browser access to internal runtime
  tables or full extracted document content.
- Matched decisions must cite evidence containing required entities and predicate
  markers. Course and section identifiers must occur in the same cited row or
  paragraph, while state and deadline predicates require corresponding status or
  date evidence. Unsupported matches are downgraded to `uncertain`.
- An hourly health monitor evaluates feed and worker freshness, degraded runs,
  failed requests, and exhausted delivery retries. It stores a public service-health
  projection, deduplicates private operator incidents, and emails only newly opened
  or reopened incidents.

### Still required for the target architecture

- Share extracted notice artifacts and embeddings across all users by content hash,
  rather than only reusing downloaded files in a local materials directory.
- Add complete predicate-specific evaluators beyond the current conservative
  evidence guard, especially versioned course-list comparisons and exact capacity
  changes.
- Add detailed metrics for model cost, latency, extraction quality, gate recall,
  and validation failures. The current health monitor covers availability and stuck
  work, not product-quality trends.
- Define notification retention, pagination, and account deletion behavior before
  exposing more than the current latest 100 records.

### P0: Required before broader use

- Add shared notice and attachment material caching by content hash.
- Introduce specialized evaluators for predicates that require exact version or
  numeric comparisons.
- Run recurring production fixtures and incident drills against real notice formats,
  including large spreadsheets, scanned PDFs, and temporary source outages.

### P1: Scale and resilience

- Build a shared chunk and optional embedding index.
- Evaluate the union metadata gate across all active watches.
- Add provider abstraction, fallback, and quota controls.
- Add metrics for gate recall, extraction quality, model decisions, validation
  failures, cost, latency, and delivery outcomes.

## Consequences

Benefits:

- Expensive processing is shared across users.
- Most notices are rejected before attachment work.
- Exact predicates become cheaper, more testable, and more reliable.
- AI usage is concentrated where it adds semantic value.
- Every alert remains traceable to official source evidence.
- Provider changes do not require redesigning the pipeline.

Costs and tradeoffs:

- Persisted material and versioning add storage and lifecycle complexity.
- Predicate-specific evaluators require domain modeling and fixtures.
- High-recall gating intentionally permits some unnecessary extraction.
- Conservative validation can produce `uncertain` instead of an immediate alert.
- OCR and table extraction quality still require monitoring and targeted fallback.

These costs are accepted because silent false negatives and unsupported confirmed
alerts are more harmful than bounded additional processing or explicit uncertainty.

## Non-Goals

This decision does not support:

- sending every feed event to a language model;
- using a language model for exact spreadsheet diffs;
- summarizing the entire notice corpus separately for each user;
- processing every page with a vision model by default;
- bypassing or evading free-provider quotas;
- emailing claims that fail evidence validation.

## Relationship to Existing Decisions

This document extends, rather than replaces:

- `watch-profile-matcher-decision.md` for deterministic candidate matching;
- `content-resolver-decision.md` for official content retrieval and extraction;
- `local-alerting-state-decision.md` for durable alert state and delivery behavior.

When implementation details conflict, this document governs the AI-assisted
analysis boundary and shared notice-processing architecture. The more specific
existing decision governs its own subsystem unless it is explicitly superseded.
