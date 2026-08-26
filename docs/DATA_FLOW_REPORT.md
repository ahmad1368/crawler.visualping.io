# Data Flow Report

This document tracks how data moves through the system, issue by issue. Each
issue that touches the crawl, extraction, storage, or API layers appends its
own section below describing: inputs -> transformation -> outputs, with a
focus on where credentials or extracted secrets travel.

## Issue #1: scaffold project structure & tooling

- **Inputs:** none (infra-only issue, no runtime data flow).
- **Transformation:** created the empty package skeleton (`app/crawler`,
  `app/extractors`, `app/storage`, `app/api`, `app/models.py`), pinned base
  dependencies, and set up `.gitignore` to exclude `.env`, `__pycache__/`,
  `.venv/`, `*.db`, and crawl snapshot output directories from version
  control.
- **Outputs:** none. No code here handles credentials, extracted matches, or
  network I/O yet.

## Issue #2: environment & secrets configuration

- **Inputs:** environment variables / a local `.env` file: `TARGET_URL`,
  `AUTH_USERNAME`, `AUTH_PASSWORD` (required, no defaults), `CONTEXT_CHARS`,
  `CONCURRENCY`, `MAX_PAGES` (optional, sane defaults).
- **Transformation:** `app/settings.py` defines a `pydantic-settings`
  `Settings` class that loads and validates these values at process start.
  Missing required vars raise a `ValidationError` immediately rather than
  the app running with blank credentials. `.env.example` documents the
  expected keys with placeholder values only.
- **Outputs:** an in-memory `Settings` instance held by the process,
  including the plaintext Basic Auth credentials (`auth_username`,
  `auth_password`) for the target site. This is the point where those
  credentials first enter the system. Nothing here logs, persists, or
  serializes `Settings` — the real `.env` stays local and gitignored, and
  `.env.example` never contains real values.

## Issue #3: core data models (PasswordMatch, PageResult, CrawlSummary)

- **Inputs:** none directly -- this issue defines the pydantic schemas that
  later crawler/extractor/storage/API issues will construct and pass
  around; no runtime data flows through it yet.
- **Transformation:** `app/models.py` now defines `SourceType` (the enum of
  password-hiding techniques extractors can find: html, css, js,
  http_header, cookie, image_metadata, binary), `PasswordMatch` (the
  extracted secret plus its context and locator), `PageResult` (one
  crawled page and its matches), and `CrawlSummary` (aggregate crawl
  stats). Required fields are validated at construction time.
- **Outputs:** none yet -- these are shapes, not a live data path. Flagging
  ahead: `PasswordMatch.value`, `context_before`, and `context_after` are
  the plaintext secret and its surrounding text. Every later issue that
  constructs, stores, or serializes a `PasswordMatch` must be checked
  against how far that value travels (DB row, log line, API/WebSocket
  payload) per the project's data-flow watchlist.
