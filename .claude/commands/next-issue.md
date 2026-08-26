---
description: Work the next open GitHub issue end-to-end - branch, implement, PR into staging, review loop, hand off the merge command.
---

Before doing anything, read your memory files `next-issue-workflow`, `crawler-data-flow-watchlist`, and `next-issue-progress` (project memory dir). Follow those rules; don't re-derive them. Keep all chat output terse throughout - no full backlog dumps, no repo-wide re-summaries.

## 1. Resume check

Read `next-issue-progress`. If it names an issue already in "awaiting-merge" or "awaiting-review" state:
- `gh pr view <PR#> --json state,mergedAt,reviews,comments` (one targeted call, not a broad scan).
- If merged: `gh issue close <n> --comment "Merged into staging via #<PR#>."`, update `next-issue-progress` (state=merged-closed, advance "next up"), then continue to step 2 for the next issue in the same run.
- If still open with no new human feedback: tell the user in one line that PR #<PR#> for issue #<n> is still open and waiting on them (review + the merge command from last time), and stop. Don't start a new issue while a prior one is unmerged - issues are dependency-ordered.
- If open with new review comments/requested changes: skip to step 5 (apply changes) for that same issue/branch instead of starting a new one.

## 2. Pick the next issue

`gh issue list --state open --json number,title,body --limit 100`, pick the lowest issue number. (Skip this query entirely if `next-issue-progress` already names the next issue - just `gh issue view <n>` it directly.)

## 3. Bootstrap staging (one-time only, first run)

Only if `git ls-remote --heads origin staging` is empty:
- If `main` has zero commits, create one minimal commit (e.g. `.gitignore` only - no issue content) and push it.
- Branch `staging` from `main`, push it.
This is infrastructure, not an issue - don't open a PR for it.

## 4. Branch + implement

- `git checkout -b issue-<n>-<kebab-title> staging` (branch from latest `staging`, pulled first).
- Implement only what issue #<n>'s acceptance criteria ask for - no scope creep into later issues.
- Append this issue's section to `docs/DATA_FLOW_REPORT.md` (inputs -> transformation -> outputs), per the issue's own acceptance criteria.
- Update the "Data flow tree" ASCII overview near the top of `docs/DATA_FLOW_REPORT.md` to reflect this issue's new/changed components (mark not-yet-built downstream nodes `(planned)`).
- Check the change against `crawler-data-flow-watchlist` - note any hits (credentials, password matches, snapshots, new sensitive DB columns, outbound calls) to report to the user.
- Run whatever test/lint tooling already exists in the repo for touched code; skip silently if none exists yet for this early an issue.
- Commit (`<type>: <title> (Refs #<n>)`), push the branch.
- `gh pr create --base staging --title "<issue title>" --body "Refs #<n>\n\n<2-4 line summary of what was implemented>"`.
- Update `next-issue-progress`: issue #<n>, branch, PR#, state=awaiting-review.

## 5. Report and review loop

Show the user, briefly: PR link, a short bullet list of what changed, and any data-flow/security concerns found in step 4 (even if none - say "no new concerns" explicitly, don't omit it).

Then use AskUserQuestion: "PR #<PR#> is ready - anything you want changed?" with options like "Looks good, ready to merge" and "I want changes" (free text via Other for specifics).

- If changes requested: make them on the same branch, push (updates the existing PR), then repeat this question. Loop until approved.
- If approved: update `next-issue-progress` to state=awaiting-merge, then tell the user to run:
  `gh pr merge <PR#> --squash --delete-branch`
  Make clear this merges into `staging` (the PR's base) and that Claude will not run it - the user runs it, then invokes `/next-issue` again to close this issue and pick up the next one.

Stop here. Do not merge, and do not start another issue in the same run once a PR is open and awaiting review/merge.
