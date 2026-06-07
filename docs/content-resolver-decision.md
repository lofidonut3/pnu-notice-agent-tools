# Resolve Materials Decision

Status: accepted and implemented in `resolve`

Date: 2026-06-07

## Context

`pnu-notice-agent-tools` is a small local CLI package for PNU notice agents. Its
`check` command should stay simple: read `events.json`, select events after the
local cursor, optionally apply explicit source/topic/type filters, collapse
duplicates, enrich from public archive metadata, and print a deterministic JSON
batch.

The personal agent still makes the relevance decision. `check` should not add
keyword search, semantic filtering, or "smart" output minimization, because
those features can create false negatives before the agent sees the event.

For selected notices, the agent needs official materials: the official detail
page and, often more importantly, the official attachments. Many PNU notices put
the key requirements, forms, lists, and schedules in attachments rather than in
the HTML page body.

Observed attachment distribution in `public-notice-feed/public/archive` during
the 2026-06 review:

```text
hwp: 208
pdf: 142
xlsx: 18
hwpx: 2
```

This makes HWP, PDF, XLSX, and HWPX important reading targets, but reading them
does not have to be this package's core responsibility.

## Decision

Keep `check` simple.

Rework `resolve` into an official materials fetcher/materializer, not a document
parser.

The target role for `resolve` is:

```text
selected event or official URL
  -> fetch official detail page
  -> optionally derive lightweight detail-page text from HTML
  -> fetch selected official attachments when requested
  -> write materials to a local cache directory
  -> record paths, source URLs, sizes, hashes, media types, and fetch statuses
  -> print a manifest JSON for the agent or a reader skill
```

`resolve` must not decide whether a notice matches a user request. It also must
not own HWP/PDF/XLSX semantic reading. It prepares official local materials with
provenance and bounded resource usage.

The reader strategy belongs in a separate skill or agent workflow.

## What `resolve` Fetches

`resolve` should fetch the official detail URL selected from the event payload or
from `--url`.

For the detail page:

- Save the raw response body locally, usually as HTML.
- Record source URL, local path, media type, byte size, SHA-256, and fetch
  status.
- It may include a lightweight `text_preview` or `detail_text` extracted from
  visible HTML text because this is cheap and deterministic, but this text is
  secondary to the saved source file.

For attachments:

- Prefer attachment metadata already present in the selected event or archive
  enrichment payload.
- For direct `--url` input, derive candidate attachment links from the fetched
  official detail page when possible.
- Do not crawl beyond the selected official detail page while discovering
  attachments.
- Download original attachment files only when requested.
- Preserve original attachment name, source URL, detected extension/media type,
  local path, byte size, SHA-256, and fetch status.
- Do not parse attachment contents in core `resolve`.
- Do not send attachments to external parser services.

Recommended default behavior:

```text
resolve
  -> fetch detail page
  -> list attachment metadata
  -> do not download attachments unless requested

resolve --download-attachments
  -> fetch detail page
  -> download attachments within configured limits
  -> print manifest with local paths
```

## Manifest Shape

The target output should be a materials manifest, not a content bundle:

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
      "name": "모집요강.hwp",
      "url": "https://www.pusan.ac.kr/...",
      "local_path": ".pnu-notice-cache/materials/pnu-main-notice-1500000/attachments/00.hwp",
      "file_extension": "hwp",
      "media_type": "application/x-hwp",
      "bytes": 79360,
      "sha256": "...",
      "fetch_status": "ok",
      "read_hints": {
        "gemini": "direct_file_input_candidate",
        "chatgpt": "try_direct_or_convert",
        "claude": "convert_first",
        "local": "libreoffice_or_tika_or_pyhwp"
      }
    }
  ],
  "limits": {
    "max_file_bytes": 10000000,
    "max_total_bytes": 30000000
  },
  "warnings": []
}
```

## Reader Skill Boundary

Attachment reading is delegated to a separate reader skill or agent workflow
that consumes `pnu_notice_materials`.

The reader skill can choose a strategy based on the user's actual tool:

```text
Gemini
  - Treat HWP/HWPX as direct file input candidates when the Gemini surface supports it.
  - Gemini API File Search documents support application/x-hwp and application/x-hwp-v5.

ChatGPT / GPT
  - Treat PDF, XLS/XLSX, DOCX, PPTX, TXT, and CSV as direct file input candidates.
  - Treat HWP/HWPX as uncertain unless the specific ChatGPT surface accepts them.
  - Convert first when direct upload fails or is unavailable.

Claude
  - Treat PDF, DOCX, CSV, TXT, HTML, ODT, RTF, EPUB, JSON, and XLSX as supported document candidates.
  - Convert HWP/HWPX first.

Codex / Claude Code / local agent
  - Use local tools when available.
  - Prefer soffice/libreoffice for HWP conversion in practical workflows.
  - Consider pyhwp, Apache Tika, or OpenHWP as optional alternatives.
```

This keeps the core CLI small and deterministic while still allowing stronger
document reading in environments that support it.

## LibreOffice/Soffice Position

LibreOffice/soffice is a good practical local backend for HWP reading, but it
should not be a required dependency of the base package.

Local benchmark on 2026-06-07 with recent PNU HWP samples and LibreOffice
24.2.7.2:

```text
single HWP conversion:
  elapsed: about 1.7s to 2.4s per file
  max RSS: about 250MB to 344MB

batch conversion of 8 HWP files in one soffice process:
  elapsed: about 3.2s total
  max RSS: about 341MB
```

This is acceptable for an on-demand reader skill after an agent has selected a
notice, but too heavy for `check` or for automatic parsing of every feed item.

Recommended constraints for any local reader skill using soffice:

```text
concurrency: 1
timeout_per_file: 10s to 20s
max_hwp_bytes: bounded, for example 10MB
max_total_bytes: bounded, for example 30MB
cache_by_sha256: true
use_separate_user_profile: true
```

## Resource And Safety Rules

`resolve` should enforce bounded fetching:

- maximum bytes per file
- maximum total bytes per notice
- timeout per request
- safe local filenames
- path traversal prevention
- SHA-256 for stable identity
- cache reuse where possible
- explicit fetch status for skipped, failed, and oversized resources

`resolve` may keep a local cache under a user-controlled path, but it must not
publish mirrored full notice bodies or attachment files.

## Consequences

Benefits:

- The base package stays small, deterministic, and dependency-light.
- The tool reliably obtains official materials and preserves provenance.
- Student workflows can use Gemini, ChatGPT, Claude, Codex, or local tools
  according to what each surface supports.
- HWP reading can improve independently in a reader skill without changing
  cursor/feed behavior.

Tradeoffs:

- `resolve` alone does not answer questions about attachment contents.
- Reader skill behavior can vary across model providers and local environments.
- The manifest must be explicit enough for downstream tools to decide whether
  to read, convert, skip, or ask the user.

## Implemented Shape

The current `resolve` implementation follows this materials-oriented shape:

```text
1. pnu_event_gate/content.py writes detail pages and requested attachments to a local cache.
2. The manifest includes local paths, source URLs, byte sizes, SHA-256 hashes, media types, and fetch statuses.
3. Attachment text extraction is not part of core `resolve`.
4. Detail pages may include a lightweight visible-text preview.
5. Tests assert local materialization instead of attachment text extraction.
```

The remaining separate work is to build the HWP/PDF/XLSX/HWPX reader skill that
consumes `pnu_notice_materials`.
