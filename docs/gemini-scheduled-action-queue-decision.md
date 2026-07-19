# Gemini Scheduled Action Queue Decision

Status: accepted as the best current subscription-based cloud-agent direction;
PoC required before product commitment

Date: 2026-06-08

## Context

The product needs a cloud-first watch flow for ordinary PNU students.

The original local-agent design is powerful but has a major UX cost:

```text
local scheduler / local agent
  -> pnu-notice scan
  -> local reader/model checks notice materials
  -> Discord/Telegram/email notification
```

This requires the user's computer or local runner to be available. That is not
a good default for students who expect a notice alert service to work in the
background.

The ideal target is:

```text
user says what notice they want
  -> the system watches PNU notices in the cloud
  -> the first user-visible alert is already the final analyzed result
  -> the flow uses the user's existing Gemini subscription when possible
  -> no Jules API key, Gemini API key, Cloud Billing setup, or always-on PC
```

Research and discussion showed that each available route has a gap:

- Jules API can run cloud analysis, but requires an API key and has no confirmed
  Google-login delegation flow for a hosted service.
- Apps Script can run in the cloud and send email, but AI analysis from Apps
  Script uses Gemini API or Vertex AI, not the user's Gemini app subscription.
- Hosted watch-gate AI is the simplest product UX, but it does not use the
  user's Gemini subscription.
- Gemini Scheduled Actions run inside Gemini Apps and can use the user's Gemini
  subscription, but external services do not appear to have a public API for
  creating or triggering them.

The most promising subscription-based route is to treat Gemini Scheduled Action
as one user-created scheduled analyst, not as the product's state store or
watch manager.

References:

- Gemini Scheduled Actions: https://support.google.com/gemini/answer/16316416
- Gemini connected apps: https://support.google.com/gemini/answer/15229592
- Gmail filters: https://support.google.com/mail/answer/6579
- Gemini Spark availability and background task direction:
  https://support.google.com/gemini/answer/17094507

## Decision

Adopt the following direction as the best current Gemini-subscription-based
cloud flow:

```text
one-time setup
  -> user opens a shared setup Gem or onboarding prompt
  -> setup Gem creates one Gemini Scheduled Action when the UI supports it
  -> copy/paste prompt remains the fallback

watch creation
  -> user tells an AI agent such as Antigravity what notice they want
  -> the agent reads the PNU Notice Agent skill
  -> the agent compiles the request into a structured watch profile
  -> the agent registers that profile with the hosted watch gate

hosted watch runtime
  -> hosted watch gate checks public-notice-feed on schedule
  -> hosted watch gate applies deterministic candidate matching
  -> when a plausible candidate appears, hosted watch gate resolves the notice
     detail page and attachments into a bounded evidence bundle
  -> hosted watch gate sends that evidence bundle to the user's Gmail as a
     hidden queue email

Gmail queue
  -> Gmail filter applies Skip Inbox, Mark Read, and a label such as
     PNU-Agent-Queue
  -> candidate emails are not intended to be user-visible alerts

Gemini Scheduled Action
  -> runs daily or on another user-selected schedule
  -> reads the PNU-Agent-Queue Gmail label
  -> compares queued candidate evidence against the user's watch profiles
  -> reports only truly relevant notices to the user through Gemini
  -> ignores unrelated candidates
```

This keeps user request management in the product, while using Gemini Apps as
the final cloud analyst.

## UX Contract

The user-facing setup should be:

```text
1. User opens the hosted watch gate or asks an agent to set up PNU notice watch.
2. User opens the shared setup Gem or receives a generated prompt.
3. The setup Gem creates one Gemini Scheduled Action when available.
4. User creates watch requests through the agent or hosted UI.
5. User never edits the Scheduled Action for each new watch.
6. User receives final relevant results through Gemini notifications or chat.
```

PoC update on 2026-06-08: a Gemini Gem chat can create a scheduled action from
the Gem conversation. This improves onboarding, but copy/paste prompt fallback
should remain because the behavior is not yet documented as clearly as the
separate Gems and Scheduled Actions features.

The Scheduled Action prompt should be stable. It should not hard-code every
watch request. It should instruct Gemini to read the latest watch profiles and
queued candidate emails.

Watch request changes should happen in the hosted watch gate:

```text
add watch request
edit watch request
disable watch request
delete watch request
```

The next Scheduled Action run should read the updated state.

## Important Limitation

Gemini Scheduled Action is time-based, not event-triggered.

Therefore this product must not promise:

```text
exact notice appears
  -> Gemini immediately wakes
```

The realistic behavior is:

```text
candidate does not exist
  -> hosted watch gate sends no queue email
  -> Scheduled Action still runs on its schedule
  -> Gemini should find no queue work and do nothing useful

candidate exists
  -> hosted watch gate sends hidden queue email
  -> Scheduled Action reads it on the next scheduled run
  -> Gemini decides whether it is truly relevant
```

We cannot currently guarantee that a regular Gemini Scheduled Action will
produce no notification at all when there is no matching candidate. The prompt
can request quiet no-op behavior, but this needs PoC validation.

## Layer Responsibilities

### public-notice-feed

Publishes the public PNU notice feed, archive metadata, event stream, and
attachment metadata.

It should remain an independent public product. It does not know users, watch
profiles, Gmail, Gemini, or agent runtimes.

### pnu-notice-agent-tools

Provides deterministic tooling and contracts:

- compiled watch profile shape,
- candidate matching concepts,
- candidate queue/status concepts,
- resolver/materialization concepts,
- evidence bundle shape that a downstream agent can read.

It should not call Gemini, Jules, Gmail, or hosted services by default.

### PNU Notice Agent skill

Guides an AI agent such as Antigravity to:

- understand the user's freeform watch request,
- ask clarifying questions when the request is too vague,
- compile the request into a structured watch profile,
- register or update that profile with the hosted watch gate,
- avoid making the user edit low-level JSON or Scheduled Action prompts.

### hosted watch gate

Owns product state and cloud runtime:

- users,
- watch profiles,
- feed polling,
- deterministic candidate matching,
- candidate dedupe,
- official notice resolving,
- bounded evidence bundle creation,
- hidden Gmail queue email delivery,
- queue acknowledgements or expiry.

It should not require a Jules API key or Gemini API key for the default
subscription-based flow.

### Gmail

Acts as the user-owned queue surface that Gemini can read through connected
apps.

Candidate queue emails should be hidden from the user's inbox with a Gmail
filter:

```text
from: pnu notice service sender
subject prefix: [PNU-Agent-Queue]
actions: Skip Inbox, Mark Read, Apply Label: PNU-Agent-Queue
```

### Gemini Scheduled Action

Acts as the final subscription-based cloud analyst.

It should:

- run on a predictable schedule,
- read only the queue label and latest watch profile instructions,
- compare candidate evidence against the user's original intent,
- report only relevant notices,
- include deadlines, target audience, required action, official links, and
  uncertainty,
- ignore irrelevant candidates.

## Queue Email Shape

Candidate queue emails should be machine-readable enough for Gemini but still
human-readable for debugging.

Recommended sections:

```text
PNU Notice Agent Candidate

candidate_id:
watch_profile_ids:
created_at:
expires_at:

Watch profiles:
- id:
- original_request:
- structured criteria:
- negative criteria:

Notice:
- title:
- source:
- source category:
- published/updated date:
- official detail URL:

Deterministic candidate reasons:
- matched title terms:
- matched source/topic hints:
- matched attachment names:
- score:

Resolved evidence:
- official body text excerpt or normalized body text
- attachment list
- attachment extracted text summaries
- deadlines found by resolver when deterministic
- file size / media type / fetch status warnings

Gemini instruction:
- Decide whether this candidate truly satisfies the watch request.
- If relevant, tell the user what changed, why it matters, who it applies to,
  what action is required, and what the official source is.
- If unrelated, do not report it.
- If uncertain, say what is uncertain and include the official URL.
```

The hosted watch gate should bound email size. If the resolved evidence is too
large, it should send summaries, excerpts, and official links rather than
unbounded full text.

## Why Candidate Email Is Acceptable

The candidate email is not the alert UX. It is a queue item.

The user's Gmail filter should hide it:

```text
Skip Inbox
Mark Read
Apply Label: PNU-Agent-Queue
```

That preserves the desired UX:

```text
first user-visible result
  -> final Gemini notification or Gemini chat result
```

The PoC must verify whether Gemini Scheduled Action can reliably read archived,
read, labelled Gmail messages.

## Rejected As Default

### Jules API key as the default cloud analyst

Rejected for default onboarding because users must create and trust the service
with a Jules API key. Keep as an optional technical-user enhancement only.

### Apps Script AI analysis with Gemini API

Rejected for this subscription-based direction because it uses Gemini API or
Vertex AI, not the user's Gemini app subscription. It remains a possible
developer-style automation route, but not the "use my Gemini subscription"
route.

### Hosted watch gate performs all AI analysis

Rejected for this specific direction because it does not use the user's Gemini
subscription. It remains the simplest fallback if Gemini Scheduled Action cannot
read the queue or stay quiet enough.

### Local agent as the default runtime

Rejected as the default UX because it requires the user's computer or runner to
stay available.

## PoC Checklist

This direction is accepted only as the best current path. Product commitment
requires PoC results for:

- Create a Gmail filter that hides `[PNU-Agent-Queue]` emails with Skip Inbox,
  Mark Read, and Apply Label.
- Confirm Gemini Scheduled Action can read archived/read labelled Gmail messages
  from that label.
- Confirm the Scheduled Action can compare a queue email against a watch request
  and produce a useful final notice.
- Confirm no-candidate days are acceptably quiet, or document the exact
  notification behavior.
- Confirm long evidence emails fit within practical Gemini context behavior.
- Confirm Korean watch requests and Korean notice evidence are handled well.
- Confirm attachments converted to resolver text are sufficient for common PNU
  HWP/PDF/XLSX/HWPX notices.
- Confirm work/school PNU accounts and personal Gmail accounts have different
  limits, especially around Gmail connected apps and attachments.

## Fallback Plan

If the PoC fails, keep the same hosted watch gate and agent skill, but change
the final delivery mode:

```text
hosted watch gate
  -> deterministic candidate matching
  -> resolver evidence bundle
  -> hosted final email alert
```

In that fallback, Gemini Scheduled Action becomes optional user-side review, not
the primary alert mechanism.
