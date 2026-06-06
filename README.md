# PNU Notice Event Gate

PNU Notice Event Gate는 `pnu-public-notice-feed`의 `events.json`을 소비하는 로컬 cursor helper다.

이 프로젝트는 실시간 알림 서버가 아니다. 부산대 사이트를 직접 크롤링하지도 않는다.
cron이나 agent runtime이 실행할 때 `events.json`을 읽고, 로컬 cursor 이후의 event batch만 잘라서 stdout으로 출력한다.
새 event가 없으면 조용히 종료한다.

이 프로젝트와 `pnu-public-notice-feed`는 부산대학교가 운영하는 공식 서비스가 아니다.

## Role

```text
pnu-public-notice-feed
  -> public notice metadata를 events.json으로 제공

pnu-notice-event-gate
  -> local cursor 이후 event batch만 출력
  -> archive metadata enrichment
  -> same-notice duplicate collapse
  -> LLM 호출 안 함
  -> 원문/첨부파일 판단 안 함
  -> 실시간 push delivery 안 함

AI agent / automation
  -> 출력된 event batch를 사용자 관심 조건과 대조
  -> 필요하면 공식 원문과 첨부파일 확인
```

이 helper의 목적은 agent나 automation이 `events.json` 전체를 직접 처리하기 전에 deterministic code가 먼저 새 event만 잘라내게 하는 것이다.
`events.json`의 event는 compact routing record로 취급한다.
기본 출력은 event의 `archive_file`/`archive_item_id`로 public archive를 조회해서 snippet, 첨부파일 metadata, content access 정보를 보강한다.

## Run

```bash
python3 run.py
```

첫 실행은 과거 이벤트를 전부 보내지 않고 현재 최신 이벤트를 baseline으로 저장한 뒤 조용히 종료한다.
이후 실행부터 새 이벤트가 있으면 stdout에 JSON을 출력한다.

기본 feed URL:

```text
https://lofidonut3.github.io/pnu-public-notice-feed/events.json
```

로컬 feed 파일로 테스트하려면:

```bash
python3 run.py \
  --events-url file:///path/to/pnu-public-notice-feed/public/events.json \
  --pretty
```

첫 실행에서도 현재 이벤트를 보고 싶으면:

```bash
python3 run.py check --include-baseline --limit 3 --pretty
```

## Output

새 이벤트가 없으면 stdout은 비어 있고 exit code는 `0`이다.

새 이벤트가 있거나 cursor 경고가 있으면 JSON을 출력한다.

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
      "source_name": "부산대 대학공지",
      "source_category": "university_notice",
      "topics": ["academic"],
      "same_notice_group_id": null,
      "canonical_item_id": "pnu-main-notice:1500000",
      "is_canonical": true,
      "same_notice_source_ids": ["pnu-main-notice"],
      "title": "공지 제목",
      "url": "https://www.pusan.ac.kr/...",
      "snippet": "본문 일부",
      "attachments": [
        {
          "name": "첨부파일.pdf",
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

기본 출력은 에이전트 판단에 필요한 compact event에 archive metadata를 붙인 형태다.
Archive 조회 없이 `events.json`에 있는 compact event만 출력하려면:

```bash
python3 run.py check --no-archive --pretty
```

전체 원본 event가 필요하면:

```bash
python3 run.py check --full --pretty
```

기본적으로 같은 공지로 판단된 duplicate group은 canonical event 하나만 출력한다.
중복 event까지 모두 보고 싶으면:

```bash
python3 run.py check --no-dedupe --pretty
```

## Cursor Policy

기본 `check`는 이벤트를 출력해도 커서를 자동으로 전진시키지 않는다.
에이전트나 downstream automation이 처리에 실패했는데 이벤트가 사라지는 상황을 막기 위해서다.

다운스트림 처리가 성공하면 `ack`로 커서를 전진시킨다.
`--no-dedupe`를 쓰지 않는 경우 출력에서 숨겨진 duplicate event가 있을 수 있으므로, 직접 event id를 골라 ack하기보다 payload의 `next_cursor` 값을 그대로 쓰는 것이 안전하다.

```bash
python3 run.py ack \
  --event-id "$NEXT_CURSOR_EVENT_ID" \
  --seen-at "$NEXT_CURSOR_SEEN_AT"
```

단순 cron에서 중복 출력보다 간편함이 중요하면 `--advance`를 쓸 수 있다.

```bash
python3 run.py check --advance
```

## Cursor Status

- `no_cursor`: local state가 비어 있다. `--include-baseline` 실행이 아니면 첫 실행에서 baseline만 저장한다.
- `event_id`: `last_seen_event_id`를 `events.json`에서 찾았고, 그 뒤 event만 출력했다.
- `seen_at`: event id를 못 찾았지만 `last_seen_at` 기준으로 새 event를 잘라냈다.
- `archive_event_id`: local cursor가 현재 `events.json` window보다 오래됐고, monthly archive에서 `last_seen_event_id`를 찾아 catch-up했다.
- `archive_seen_at`: local cursor가 현재 `events.json` window보다 오래됐고, monthly archive에서 `last_seen_at` 기준으로 catch-up했다.
- `archive_required`: local cursor가 현재 `events.json` window보다 오래됐지만 archive catch-up을 하지 못했다.
- `stale_cursor`: event id를 못 찾았고 `seen_at`도 없어 중복 출력 가능성이 있다.

Archive catch-up을 끄고 현재 `events.json` window만 확인하려면:

```bash
python3 run.py check --no-archive-catchup
```

## Filters

특정 source만 넘기기:

```bash
python3 run.py check --source pnu-main-notice --source pnu-onestop-scholarship
```

특정 source category만 넘기기:

```bash
python3 run.py check --source-category academic_unit_scholarship_notice
```

특정 topic만 넘기기:

```bash
python3 run.py check --topic scholarship --topic contest
```

새 글만 넘기기:

```bash
python3 run.py check --event-type added
```

## State

기본 state 파일:

```text
.event-gate-state.json
```

다른 위치를 쓰려면:

```bash
python3 run.py check --state ~/.pnu-agent/event-gate-state.json
```

## Install Locally

```bash
python3 -m pip install -e .
pnu-event-gate check --pretty
```

## Test

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest -q
```
