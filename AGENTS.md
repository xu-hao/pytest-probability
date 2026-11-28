# pytest-probability contributor instructions

See [README.md](README.md) and [docs/index.md](docs/index.md) (including [docs/contributing.md](docs/contributing.md) and the rest of [docs/](docs/)) for project overview, usage, and documentation.

- Keep statistical reference and test-only dependencies (`scipy`, `statsmodels`, `pytest-xdist`) strictly in `[project.optional-dependencies].test` in `pyproject.toml`, never in runtime `dependencies`.

<!-- gardenia:begin -->
- Use the installed `agrein` skills: `ticket-delivery` before changing code, docs,
  config, infrastructure, or live state; `epic-queue` when decomposing multi-ticket
  work or coordinating subagent workers; `bug-investigation` before fixing defects or
  failing checks; and `owner-escalation` for unsettled product/policy rulings, live
  operation approvals, and decision handoffs.
- Coordinate ticket work by delegating each authorized ticket to a worker selected with
  the installed `agroute` and `howhard` skills; an assigned leaf worker implements its
  ticket directly without recursively delegating it (report a capability gap first if
  `agroute` or worker delegation is unavailable on the host):
  1. Inspect or refresh live agent quota state with `agr usage`.
  2. Emit eligible provider-qualified candidates for this host with
     `agr candidates --host <codex|claude|agy> --output /tmp/TASK-candidates.json --json`.
     If `price_request` is non-null, look up official first-party provider pricing (or
     classify internal harness models as `"alias"` or `"unlisted"`), run
     `agr prices resolve --file /tmp/TASK-prices.json --json`, and re-emit candidates once.
  3. Select a route with `howhard` (`context --host <codex|claude|agy> --auto-decide`, or
     `context`/`retrieve`/`decide` evaluating `baseline_request` in an independent stateless
     context only when non-null, or `pick --host <codex|claude|agy>`), then resolve the
     execution plan with
     `agr plan --host <codex|claude|agy> --decision /tmp/TASK-decision.json --json`.
  4. Honor `needs_review`. Execute native plans (`target_agent == host_agent`) with the
     host's native subagent tool and cross-agent plans (`target_agent != host_agent`) with
     the returned non-interactive CLI command.
  5. Record the actual outcome and measured usage in `howhard` following the plan's
     `feedback` block (`matched_receipt` or `raw_native`), then update `howhard` memory.
- Track every code, test, documentation, or packaging change with a descriptive issue
  (`#N — one sentence describing the work`). Do not file an issue solely for
  machine-local installation, deployment, configuration, service lifecycle, or other
  operator state unless it also requires a repository change or the user requests one.
- Develop and validate only in an issue-specific Git worktree branched from
  `origin/main`. Preserve unrelated work and keep the primary checkout read-only during
  development.
- Verify behavior at public boundaries. Start bug fixes with a failing behavioral test;
  accompany runtime changes with focused tests, affected canonical documentation, and a
  `CHANGELOG.md` entry when the change is user-facing.
- A documentation-only change needs no tests or AGCoord gate. It qualifies only when
  every changed path is `README.md`, `CHANGELOG.md`, `AGENTS.md`, Markdown below
  `docs/`, or a `.png` or `.svg` image below `docs/assets/` whose provenance is
  recorded in `docs/assets/README.md`. Verify the exact pull-request file list and
  diff, then merge it through the forge; the issue, worktree, branch, pull-request,
  and no-direct-target-push rules still apply.
- On Linux hosts with `agcoord` (`agc`), route checks and pull-request landing through
  the local coordinator with integer byte memory claims:
  - Check: `agc run --label "<short plain-English summary>" --no-overflow --resource cpu=4 --resource memory=4294967296 -- python -m pytest -q`
  - Land: `agc land <pr> --label "land #<pr> <short summary>" --no-overflow --resource cpu=4 --resource memory=4294967296 -- python -m pytest -q`
  Give every job a short plain-English `--label` for humans in `agc list` and `agc tui`
  (lead with what is checked or landed; omit commit SHAs, branch names, `"pre-land"` tags,
  and file paths).
  Never submit coordinated work recursively inside an admitted job, and never bypass or
  separate the gate verdict from publication. On hosts without `agc`, run
  `python -m pytest -q` directly and merge through the forge after checks pass.
- Tests must own and stop every process, repository, and temporary directory they
  create, and must never inspect or mutate real host state (`~/.codex`, `~/.claude`,
  `~/.gemini`, `~/.config`, or `~/.local`).
- Never upload a distribution to PyPI or modify live host/production state unless the
  owner explicitly authorizes that exact operation. Permission to implement, test,
  commit, push, merge, tag, or create a GitHub release does not authorize a PyPI upload.
<!-- gardenia:end -->
