# Jules Cloud Analysis Decision

Status: accepted as an optional enhanced-mode direction

Date: 2026-06-08

## Context

The original local-agent flow assumed that a user's computer, scheduler, or
agent runtime would stay available:

```text
local scheduler
  -> pnu-notice scan
  -> agent resolves and reads official materials
  -> agent sends Discord/Telegram/email
```

That creates a major UX limit for ordinary students: the watch only works while
the user's machine or local runner is available.

The product direction is shifting toward a hosted watch gate:

```text
public-notice-feed
  -> hosted watch gate
  -> deterministic candidate matching
  -> email notification
```

The remaining question is whether deep final analysis of selected notice
details and attachments can also happen in the cloud without the user's
computer.

Jules is a candidate for that optional cloud analysis worker because:

- Jules exposes a REST API for programmatic session creation.
- The REST API supports repoless sessions.
- Repoless sessions run in an ephemeral cloud development environment.
- Jules can access public web URLs from its cloud environment.

References:

- Jules API overview: https://developers.google.com/jules/api
- Jules sessions API: https://jules.google/docs/api/reference/sessions/
- Jules repoless API changelog: https://jules.google/docs/changelog/2026-01-26-4
- Jules environment: https://jules.google/docs/environment

## Decision

Use Jules only as an optional cloud deep-analysis worker after the hosted watch
gate has already selected a plausible candidate notice.

Jules should not be the default scheduler, state store, feed scanner, email
sender, or only notification path.

Preferred hosted flow:

```text
watch creation
  -> user enters a natural-language watch request
  -> hosted watch gate compiles or asks clarifying questions
  -> hosted watch gate stores a deterministic watch profile

scheduled hosted scan
  -> hosted watch gate reads public-notice-feed events
  -> deterministic candidate gate filters by source, topic, title, snippet,
     attachment names, negative terms, and same-notice groups

candidate with no deep analysis needed
  -> hosted watch gate sends an email with title, source, reason, official URL,
     and attachment links

candidate requiring detail or attachment judgment
  -> if the user connected Jules, hosted watch gate creates a Jules repoless
     session with bounded public notice context
  -> Jules checks the official detail page and attachments
  -> hosted watch gate polls Jules results
  -> hosted watch gate sends email with the final analysis

Jules unavailable, slow, or failed
  -> hosted watch gate sends a fallback email with candidate details, official
     links, attachment links, and a manual Gemini/Jules prompt
```

This keeps the MVP valuable without Jules, while making Jules a strong
cloud-first enhancement for users willing to connect it.

## Why Jules Is Not The Scheduler

The hosted watch gate should own scheduling because it has the product state:

- users
- watch profiles
- scan cursors
- candidate queues
- duplicate suppression
- notification receipts
- retry and timeout policy
- email delivery state

Jules should receive only a bounded task after candidate selection. Making Jules
poll the feed directly would duplicate state, weaken dedupe, and make retries
harder to reason about.

## User UX

Initial setup:

```text
1. User signs in to the hosted watch gate.
2. User enters a watch request.
3. User optionally connects Jules by pasting a Jules API key.
4. The hosted watch gate stores the API key in encrypted secret storage.
5. The hosted watch gate confirms that cloud deep analysis is enabled.
```

Daily use:

```text
1. User does not keep a local computer on.
2. Hosted watch gate scans the public feed on schedule.
3. If no candidate matches, nothing is sent.
4. If a candidate matches, the user gets email.
5. If Jules is enabled and useful for that candidate, the email can include
   final detail/attachment analysis instead of only links.
```

Important UX constraints:

- Jules API key connection is optional, not required for the MVP.
- Users who do not connect Jules still get useful notice alerts by email.
- The user should not need Antigravity, a local agent, or a local scheduler after
  watch creation.
- Antigravity or any other AI surface can help the user create the initial watch
  request, but it is not part of the hosted runtime.

## Security Boundary

The hosted watch gate may store a Jules API key only if the user explicitly
connects it.

Rules:

- Never put the Jules API key in a Jules prompt.
- Never store the key in logs, email, candidate records, or activity payloads.
- Use the key only as an HTTP authentication secret.
- Store it encrypted at rest.
- Allow the user to delete or rotate the key.
- Treat any chat- or log-exposed key as compromised and rotate it.
- Do not send the user's email address to Jules unless absolutely needed.
- Send only the minimum watch context needed for the decision.

The content sent to Jules should normally be limited to:

- notice title
- source name and source category
- official detail URL
- attachment names and public download URLs
- deterministic match reasons
- the user's relevant watch criteria, minimized and redacted

## PoC Results

PoC date: 2026-06-08

Observed results:

```text
Jules API key creation:
  easy enough for a technical user to complete manually

GET /v1alpha/sessions with Jules API key:
  HTTP 200
  empty session list returned

GET /v1alpha/sources with the same key:
  HTTP 401 in this environment
  source listing is not required for repoless sessions

POST /v1alpha/sessions without sourceContext:
  HTTP 200
  repoless session created successfully

Jules cloud execution:
  public PNU detail URL reachable
  public PNU attachment URL reachable
  attachment Content-Disposition header visible
  tiny download test succeeded
```

The Jules web UI reported this final JSON for the PNU connectivity test:

```json
{
  "detail_reachable": true,
  "detail_status": "200",
  "attachment_reachable": true,
  "attachment_status": "200",
  "attachment_content_disposition_seen": true,
  "tiny_download_ok": true,
  "notes": "Both URLs are reachable and returned HTTP 200 OK. The attachment URL successfully provided a Content-Disposition header with the filename. A small range download was requested, though the server ignored the Range header and delivered the full file (137 KiB), which downloaded successfully."
}
```

Implications:

- The API-key UX is less painful than expected, but still too heavy for default
  onboarding.
- Jules can be called from a hosted service to run cloud work without the user's
  computer.
- PNU public detail and attachment URLs are reachable from Jules cloud, at least
  for the tested sample.
- Some PNU servers may ignore HTTP Range requests and return the full file, so
  the hosted gate must enforce size limits before and during downloads.
- Jules latency is measured in minutes rather than seconds, so it fits
  "analyze and email soon" better than real-time alerting.

## Runtime Contract

The hosted watch gate should ask Jules for machine-readable output. A good
target response shape is:

```json
{
  "matched": true,
  "confidence": "high",
  "user_should_care": true,
  "reason": "The notice is a course cancellation notice and includes an attachment list relevant to the watch criteria.",
  "summary": "Short user-facing summary.",
  "deadlines": [
    {
      "label": "Application deadline",
      "date": "2026-06-14",
      "source": "official detail page"
    }
  ],
  "attachments_checked": [
    {
      "name": "attachment.xlsx",
      "status": "checked",
      "finding": "Relevant row found"
    }
  ],
  "warnings": []
}
```

The gate should treat the output as untrusted model output:

- validate JSON shape,
- keep the official URL and attachment links in the email,
- include uncertainty when Jules is unsure,
- fall back to candidate-only email when parsing fails.

## Attachment Policy

Jules should only be used after candidate matching. It must not parse every feed
item.

Recommended hosted limits before creating a Jules task:

```text
max_attachments_per_notice: small bounded number
max_single_attachment_bytes: configured threshold
max_total_attachment_bytes: configured threshold
allowed_sources: public official PNU URLs only
timeout_per_jules_session: configured minutes
fallback_on_timeout: send candidate-only email
```

The PoC showed that a server can ignore Range and return a full file. Therefore
the hosted gate should prefer metadata size when present, and any direct hosted
download proxy must stop reading after the configured byte limit.

## Relationship To Existing Tools

`pnu-notice-agent-tools` remains deterministic feed tooling:

```text
check
  Read feed events after a cursor.

scan
  Match active watch profiles and enqueue candidate notices.

resolve
  Materialize official detail page and attachments.
```

Jules cloud analysis is a downstream consumer of the same candidate/materials
contract. It does not change the core package boundary:

- `pnu-notice-agent-tools` does not call Jules.
- `pnu-notice-agent-tools` does not send email.
- A hosted watch gate may reuse the profile, match, candidate, and resolve
  concepts.
- The hosted watch gate owns Jules API integration and email delivery.

## Recommended Product Position

Default product:

```text
hosted watch gate
  -> deterministic candidate matching
  -> email notice alert with official links
```

Enhanced mode:

```text
user connects Jules API key
  -> hosted watch gate invokes Jules for selected candidates
  -> email includes detail/attachment analysis
```

Rejected as default:

```text
Jules as mandatory runtime
  -> too much setup for ordinary students

Jules as feed scheduler
  -> wrong owner for watch state, dedupe, retries, and email delivery

local agent as the only runtime
  -> requires the user's computer to be on
```

## Remaining Questions

- Can Jules reliably read HWP and HWPX content, or does it need conversion
  instructions and fallback tooling?
- How often does Jules return machine-readable final output through the API
  activities stream versus only the web UI?
- What are realistic latency, timeout, and retry policies for hosted email UX?
- How should a non-technical user be guided through Jules API key connection?
- Does a PNU Workspace/Gemini Education account get usable Jules API access, or
  is a personal Gmail Google AI plan required?
- What are the per-user task limits and failure modes under real notice volume?
