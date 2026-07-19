# Gemini Public Feed Decision

Status: accepted as the lowest-friction Gemini-subscription UX direction;
PoC required before replacing the hosted watch-profile flow

Date: 2026-06-08

## Context

The previous Gemini Scheduled Action queue direction assumed this flow:

```text
user tells an agent a watch request
  -> agent compiles it into a structured watch profile
  -> hosted watch gate stores the profile
  -> hosted watch gate sends hidden Gmail queue emails for candidates
  -> Gemini Scheduled Action reads the queue and performs final analysis
```

That is coherent, but it still assumes the user has an AI agent surface such as
Antigravity, Codex, Claude Code, or another local/IDE agent available during
setup. Many ordinary students may not use those tools.

The product should also support a lower-friction path where the user only needs
Gemini web or the Gemini mobile app:

```text
user opens a shared setup Gem
  -> user says what notices they care about
  -> the Gem creates one Gemini Scheduled Action
  -> user's watch request lives inside the Scheduled Action prompt
  -> Gemini reads a public PNU notice feed prepared for LLM use
  -> Gemini compares the public feed with the user's request
  -> Gemini reports only relevant notices
```

This avoids API keys, Cloud Billing, local runners, and agent-specific setup.
It also keeps the user's watch request private from our server.

References:

- Gemini Scheduled Actions: https://support.google.com/gemini/answer/16316416
- Gemini connected apps: https://support.google.com/gemini/answer/13695044
- Gemini in Gmail: https://support.google.com/mail/answer/14355636
- Work/school connected app limits:
  https://support.google.com/gemini/answer/14959807

Observed PoC update on 2026-06-08:

- A Gemini Gem chat can create a scheduled action from the Gem conversation.
- This makes a shared setup Gem viable as the default onboarding surface.
- Official documentation is still clearer about Gems and Scheduled Actions as
  separate features, so this behavior should be validated again during product
  PoC and onboarding testing.

## Decision

Add a Gemini-only public-feed direction as the simplest subscription-based UX.

The core idea is:

```text
user state
  -> collected by a shared setup Gem
  -> lives in the user's Gemini Scheduled Action prompt

our public product
  -> publishes Gemini-friendly PNU notice feed and evidence pages

setup Gem
  -> asks the user what notices they want
  -> writes the scheduled-action instruction
  -> creates or guides creation of the Scheduled Action

Gemini Scheduled Action
  -> reads the public feed on a schedule
  -> applies the user's natural-language watch request
  -> reports only truly relevant notices
```

In this mode, our server does not store per-user watch requests and does not
perform user-specific candidate matching.

## Target UX

Initial setup:

```text
1. User opens our onboarding page.
2. User chooses "Use with Gemini".
3. The page opens or links to the shared PNU Notice Watch Setup Gem.
4. User tells the Gem what notices they care about.
5. The Gem asks clarifying questions only if needed.
6. The Gem creates one Gemini Scheduled Action for the user.
7. The user confirms the scheduled action.
```

Daily use:

```text
1. public-notice-feed updates public PNU notice data.
2. Gemini-friendly latest feed and evidence pages update.
3. Gemini Scheduled Action runs on the user's schedule.
4. Gemini reads the public feed.
5. Gemini compares recent notices with the user's prompt.
6. If relevant notices exist, Gemini notifies the user.
7. If no relevant notices exist, Gemini should stay quiet or produce a no-op.
```

Watch edits:

```text
user tells the Scheduled Action chat or the setup Gem:
  "앞으로 교환학생 모집 공지도 추가해줘."
  "기숙사 공지는 이제 빼줘."
  "국가장학금은 대학원생 대상이면 제외해줘."
```

Gemini Scheduled Actions can be edited from the scheduled action chat, so the
user does not need to revisit our service for every request change.

## Setup Gem Contract

The setup Gem is not the runtime. It is the onboarding assistant.

It should:

- ask for the user's notice interests in plain Korean,
- convert broad requests into a clear watch instruction,
- ask a short clarification when the request is too vague,
- include the public feed URL and unofficial-service disclaimer,
- create one Scheduled Action from the Gem chat when available,
- fall back to showing a copyable Scheduled Action prompt if direct creation
  fails or the UI changes.

The setup Gem should not require the user to understand JSON, feed URLs, watch
profiles, Gmail filters, API keys, or agent runtimes.

## Example Scheduled Action Prompt

The setup Gem should generate or create a scheduled action equivalent to:

```text
매일 오전 9시에 실행해.

아래 PNU Notice Agent feed를 확인해서 내가 원하는 공지가 새로 올라왔는지
판단해줘.

Feed:
https://example.com/gemini/latest.html

내가 원하는 공지:
- 국가장학금 2차 신청
- 기숙사 추가모집 또는 결원 모집
- 컴퓨터공학부 졸업요건 변경

판단 방식:
- 제목만 보지 말고 본문 요약, 첨부파일명, evidence 링크까지 확인해.
- 진짜 관련 있는 공지만 알려줘.
- 관련 공지가 없으면 아무 내용도 만들지 말고 조용히 넘어가.
- 관련 공지가 있으면 신청 기간, 대상, 해야 할 일, 공식 링크를 알려줘.
- 애매하면 애매한 이유와 공식 링크를 같이 알려줘.
```

The real prompt should use the production public feed URL and should explain
that the service is unofficial.

## Public Feed Contract

Gemini should not need to parse raw `events.json` unless it wants to. The public
product should expose an LLM-readable page.

Recommended surfaces:

```text
/gemini/latest.html
  Recent 24-72 hour notice digest for Gemini Scheduled Actions.

/gemini/latest.md
  Plain Markdown equivalent when easier for models to read.

/gemini/notices/{notice_id}.html
  One notice evidence page with body text, attachment metadata, extracted text,
  warnings, and official links.
```

`latest` should be bounded and scannable:

- generated timestamp,
- coverage window,
- unofficial-project disclaimer,
- notice title,
- source and source category,
- published or updated date,
- short body preview,
- attachment names and file types,
- same-notice duplicate grouping,
- evidence-page link,
- official detail-page link.

Evidence pages should provide enough material for final judgment:

- official detail URL,
- normalized body text,
- attachment list,
- extracted attachment text or bounded summary,
- deadlines when deterministically detected,
- audience/eligibility phrases when deterministically detected,
- fetch and conversion warnings,
- official download links.

Evidence pages must be bounded. Large attachments should be summarized or
truncated with clear warnings and official links.

## Alternative Delivery: Hidden Gmail Digest

If Gemini Scheduled Action cannot reliably read a public URL, the same
user-private request model can use Gmail as the feed surface:

```text
our service
  -> sends a daily PNU notice digest email
  -> user Gmail filter hides it with Skip Inbox, Mark Read, and a label

Gemini Scheduled Action
  -> reads the hidden digest label
  -> applies the watch request stored in the Gemini prompt
  -> reports only relevant notices
```

In this variant, our service still does not store the user's watch request. It
only manages an email subscription to a general PNU notice digest.

This is less clean than a public URL because it requires an email subscription
and a Gmail filter, but it may be more reliable if Gemini handles Gmail better
than arbitrary public pages.

## Layer Responsibilities

### public-notice-feed

Publishes official-public notice metadata and, if adopted, Gemini-friendly
latest/evidence pages.

It should remain independent and public. It should not store users or private
watch requests.

### pnu-notice-agent-tools

Provides deterministic local or hosted concepts that can help generate evidence
pages:

- candidate and notice identifiers,
- resolver manifests,
- attachment fetch/conversion policies,
- evidence bundle shape,
- deterministic warnings and limits.

It should not be required for the user's Gemini-only setup.

### hosted watch gate

Not required for the default Gemini-only public-feed mode.

It remains useful for advanced modes:

- user-specific watch profiles,
- stronger deterministic prefiltering,
- hidden candidate queue emails,
- email fallback alerts,
- enterprise or non-Gemini users.

### Gemini Scheduled Action

Owns the user's private watch request in this mode.

It should:

- run on the user's chosen schedule,
- read the public feed or hidden digest,
- apply the user's natural-language criteria,
- report relevant notices with official links and clear action items,
- stay quiet when nothing matches if Gemini behavior allows it.

### Setup Gem

Owns the first-run UX.

It should:

- receive the user's freeform request,
- create the scheduled action in Gemini when the current UI supports it,
- make the final scheduled instruction inspectable by the user,
- provide a manual copy/paste fallback,
- explain how to edit the watch request later.

## Why This Is Attractive

This is the lowest-friction path for ordinary Gemini users:

- no local computer required after setup,
- no Antigravity/Codex/Claude/Jules requirement,
- no manual long prompt writing if the setup Gem can create the scheduled
  action directly,
- no API key,
- no Cloud Billing,
- no hosted storage of the user's watch request,
- user can edit interests by talking to Gemini,
- the first visible result can be the Gemini final analysis.

It also gives `public-notice-feed` a stronger standalone product role:

```text
public-notice-feed
  -> not only machine-readable feed data
  -> also an LLM-readable PNU notice intelligence surface
```

## Important Limitations

Gemini Scheduled Action is still time-based. This mode must not promise
event-triggered immediacy.

Do not promise:

```text
notice appears
  -> Gemini wakes immediately
```

Promise only:

```text
Gemini checks on your schedule.
```

Also unresolved:

- Gemini may still create a notification for no-op scheduled runs.
- Gemini's public URL reading behavior needs PoC validation.
- Gemini may not reliably follow many evidence links in one run.
- Work/school Google accounts may have connected-app restrictions.
- Korean prompt and Korean notice handling needs validation.
- User-level dedupe is weaker because we do not store per-user state.

The prompt should tell Gemini to use the latest generated timestamp and avoid
reporting old items repeatedly, but this is less robust than server-side
per-user dedupe.

## Relationship To The Hidden Queue Direction

This public-feed direction is simpler than the hidden queue direction:

```text
public-feed mode
  -> no user watch state on our server
  -> no agent setup required
  -> no per-user candidate queue
  -> Gemini does more filtering work

hidden-queue mode
  -> user watch state exists on hosted watch gate
  -> agent or UI compiles watch profiles
  -> hosted gate sends only candidate evidence
  -> Gemini does less filtering work
```

Recommended product tiers:

```text
Basic Gemini mode
  shared setup Gem + Gemini Scheduled Action + public Gemini-friendly feed.

Stable Gemini mode
  shared setup Gem + Gemini Scheduled Action + hidden daily Gmail digest.

Advanced agent mode
  Antigravity or another agent + hosted watch gate structured profiles.

Fallback non-Gemini mode
  hosted watch gate sends final email alerts directly.
```

## PoC Checklist

Before committing this as the default UX, validate:

- A shared setup Gem can create a Scheduled Action from the Gem chat across the
  target account types and devices.
- The Gem can fall back to a copyable prompt when direct Scheduled Action
  creation is unavailable.
- Gemini Scheduled Action can read a public `latest.html` URL.
- Gemini can follow one or more evidence-page links when needed.
- Gemini can compare Korean natural-language watch requests against Korean
  notice evidence.
- Gemini avoids repeating the same notice across daily runs when instructed to
  use dates and generated timestamps.
- No-match days are acceptably quiet.
- A bounded latest feed does not exceed practical context limits.
- Evidence pages with HWP/PDF/XLSX/HWPX extracted text are useful enough for
  final judgment.
- The same prompt works in personal Google AI Pro/Ultra accounts and identify
  what breaks in PNU/work/school accounts.
- The Gem-driven onboarding copy is simple enough for a non-technical student to
  complete.

## Fallback Plan

If public URL reading is unreliable, try hidden Gmail digest while keeping the
user's watch request in Gemini.

If no-op notifications or repeated notices make the UX poor, move the default
back to hosted watch profiles and server-side email alerts.

If Gemini Scheduled Action cannot provide reliable final notifications, keep
Gemini support as an optional "review my daily digest" integration rather than
the primary alert product.
