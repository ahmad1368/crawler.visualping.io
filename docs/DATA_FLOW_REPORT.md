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
