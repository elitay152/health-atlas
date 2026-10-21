# Health Atlas expansion plan

Use GitHub Issues for tasks, milestones for phases, and one repository-linked GitHub Project for workflow. Each ticket has acceptance criteria, dependencies and validation. No dates or contributor assignments are invented.

| Milestone | Outcome | Tickets |
| --- | --- | --- |
| Phase 1 — Source research | Confirm CMS, HRSA and tract data; define reusable data contracts | 4 |
| Phase 2 — Oregon healthcare access | Import hospitals/shortage areas, map/search, report quality | 5 |
| Phase 3 — Local health exploration | Tract imports, boundaries, drill-down and interpretation | 4 |
| Phase 4 — Service-area analysis | Reproducible areas, defensible summaries and comparisons | 3 |
| Phase 5 — National expansion | Region configuration, scale and two-state verification | 3 |

[roadmap.json](roadmap.json) contains the full 19-ticket backlog. The source-research tasks are ready to pick up; downstream tasks remain blocked by their listed dependencies. Check acceptance criteria before moving an issue to Done.

## Workflow

**Backlog → Ready → In progress → In review → Done**

Move a ticket to Ready after its dependencies and source decisions are resolved. Assign one owner, work on a feature branch, and link a PR. Use `Closes #123` only when the PR completes the ticket. Closing an issue is separate from the Project status unless GitHub automation is configured.

Feature, bug and research forms are under `.github/ISSUE_TEMPLATE/`; the PR template asks for relevant validation and data considerations. Update source/period/uncertainty metadata when a change touches data. Keep credentials and private patient/client data out of tickets and screenshots.

## Publish the planning objects

Preview is read-only and does not contact GitHub:

```sh
python scripts/setup_project.py
```

After reviewing the plan, an authorized operator with GitHub CLI authentication can run:

```sh
python scripts/setup_project.py --apply
```

The command creates/reuses labels, five milestones, 19 issues, and a linked user-owned Project named **Health Atlas — Expansion**. It adds dependencies as issue links. Existing matching issues, descriptions and workflow progress are preserved; stable roadmap markers avoid duplicates on normal reruns. Run one setup operator at a time. A partial failure may leave already-created objects; correct the reported access/configuration issue and rerun. Review any partly added Project items manually if their status update failed.

Repository Issues/milestones require write access; user-owned Projects need separate Project permissions. An unset token variable does not establish missing access: use existing GitHub CLI authentication first. If it lacks access, authenticate securely with GitHub CLI's browser/device flow; never paste tokens in chat, tickets, code or command arguments. A classic OAuth/PAT flow generally needs appropriate repository scope and `project`; fine-grained permissions differ. Use `--apply --issues-only` when repository access works but user Projects are unavailable.

If a matching Project already exists with different statuses, setup stops rather than replacing its workflow options. Configure **Backlog, Ready, In progress, In review, Done** in GitHub and rerun. Existing item statuses are never reset.

### Board view setup

The setup attempts to create an **Expansion board** view using the current GitHub API and verifies Board layout grouped by Status. Existing views are preserved. If GitHub requires a manual grouping change, setup reports it rather than claiming the board is configured: open the Project, choose Board layout, group by **Status**, and save. Optionally enable built-in workflows to mark closed issues Done. Repository collaborators may also need Project access, especially for a private user-owned Project.

If listing the owner's Projects is unavailable but a particular Project is accessible, pass `--project-number NUMBER` to target that known Project instead of listing all projects.

## Current publication state

The five milestones and all 19 issues have been created and verified:

- [Milestones](https://github.com/elitay152/health-atlas/milestones)
- [Phase 1 research](https://github.com/elitay152/health-atlas/milestone/1): issues #15–#18
- Phase 2: #19–#23; Phase 3: #24–#27; Phase 4: #28–#30; Phase 5: #31–#33

Repository API access works after the network update. **The Project board remains blocked:** GitHub returns `Resource not accessible by integration` for the `createProjectV2` mutation. A filtered lookup confirmed no matching Health Atlas project before the rejected creation attempt. Repository admin permission does not establish user-Project permission. No Project or Board creation is claimed. Grant the GitHub integration the required Projects access, or use securely authenticated local GitHub CLI access, then rerun `--apply`. Existing issues/milestones will be reused rather than duplicated.

Alternatively, create the Board from [your GitHub Projects page](https://github.com/users/elitay152/projects), configure the five statuses, and bulk-add these repository issues. The roadmap/milestones are usable in GitHub Issues while the board access is resolved.
