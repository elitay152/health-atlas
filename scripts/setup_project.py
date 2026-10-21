#!/usr/bin/env python3
"""Preview the roadmap; --apply creates GitHub planning objects using gh auth."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "docs" / "planning" / "roadmap.json"
COLORS = {"research": "D4C5F9", "data": "1D76DB", "gis": "0E8A16", "backend": "0052CC", "frontend": "C2E0C6", "testing": "FBCA04", "documentation": "0075CA", "enhancement": "A2EEEF"}


def validate(plan):
    phases = {m["phase"] for m in plan["milestones"]}
    seen = set()
    for issue in plan["issues"]:
        if issue["id"] in seen or issue["phase"] not in phases:
            raise ValueError("Duplicate issue ID or unknown phase")
        if not set(issue["depends_on"]).issubset(seen):
            raise ValueError("Dependencies must reference earlier issues")
        if not issue["acceptance"] or not issue["validation"]:
            raise ValueError("Each issue needs acceptance and validation")
        seen.add(issue["id"])


def command(args, payload=None, expect_json=True):
    result = subprocess.run(["gh", *args], input=json.dumps(payload) if payload is not None else None,
                            text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f"GitHub command failed ({' '.join(args[:3])}). Check API access and gh authentication/permissions; no credentials should be posted in chat.")
    if not expect_json:
        return result.stdout
    return json.loads(result.stdout) if result.stdout.strip() else None


def pages(endpoint):
    result, page = [], 1
    separator = "&" if "?" in endpoint else "?"
    while True:
        batch = command(["api", f"{endpoint}{separator}page={page}"])
        if not isinstance(batch, list):
            raise RuntimeError("Expected a paginated GitHub list")
        result.extend(batch)
        if len(batch) < 100:
            return result
        page += 1


def marker(issue):
    return f"<!-- health-atlas-roadmap:{issue['id']} -->"


def issue_body(issue, urls):
    dependencies = "\n".join(f"- {urls[dep]} ({dep})" for dep in issue["depends_on"]) or "None; ready to start."
    criteria = "\n".join(f"- [ ] {item}" for item in issue["acceptance"])
    references = "\n".join("- " + url for url in issue.get("sources", [])) or "See the linked dependencies for source decisions."
    return f"{marker(issue)}\n\n## Goal\n\n{issue['title']}\n\n## Acceptance criteria\n\n{criteria}\n\n## Dependencies\n\n{dependencies}\n\n## Candidate source references\n\n{references}\n\n## Validation\n\n{issue['validation']}\n\nPick a focused implementation, link its PR, and retain source/period/uncertainty metadata. Do not include credentials or private patient/client data.\n"


def apply_issues(plan):
    repo = plan["repository"]
    # Confirm access before creating anything. Existing content is never overwritten.
    info = command(["api", f"repos/{repo}"])
    if not info.get("has_issues"):
        raise RuntimeError("Enable GitHub Issues in repository settings before running setup.")
    labels = {x["name"] for x in pages(f"repos/{repo}/labels?per_page=100")}
    for label in sorted({label for i in plan["issues"] for label in i["labels"]}):
        if label not in labels:
            command(["api", "--method", "POST", f"repos/{repo}/labels", "--input", "-"], {"name": label, "color": COLORS[label]})
    milestones = pages(f"repos/{repo}/milestones?state=all&per_page=100")
    numbers = {}
    for milestone in plan["milestones"]:
        matches = [m for m in milestones if m["title"] == milestone["title"]]
        if len(matches) > 1:
            raise RuntimeError("Multiple milestones match a phase; resolve the ambiguity before retrying.")
        existing = matches[0] if matches else command(["api", "--method", "POST", f"repos/{repo}/milestones", "--input", "-"], {"title": milestone["title"], "description": milestone["description"]})
        numbers[milestone["phase"]] = existing["number"]
    existing_issues = [i for i in pages(f"repos/{repo}/issues?state=all&per_page=100") if "pull_request" not in i]
    urls = {}
    for issue in plan["issues"]:
        title = f"[{issue['id']}] {issue['title']}"
        matches = [i for i in existing_issues if marker(issue) in (i.get("body") or "") or i["title"] == title]
        if len(matches) > 1:
            raise RuntimeError(f"Multiple issues match {issue['id']}; resolve duplicates before retrying.")
        result = matches[0] if matches else command(["api", "--method", "POST", f"repos/{repo}/issues", "--input", "-"], {
            "title": title, "body": issue_body(issue, urls), "labels": issue["labels"], "milestone": numbers[issue["phase"]]})
        urls[issue["id"]] = result["html_url"]
        print(f"{issue['id']}: {result['html_url']}")
    return urls


def apply_project(plan, urls, project_number=None):
    owner = plan["repository"].split("/")[0]
    if project_number is not None:
        project = command(["project", "view", str(project_number), "--owner", owner, "--format", "json"])
        created = False
    else:
        listing = command(["project", "list", "--owner", owner, "--limit", "1000", "--format", "json"])
        projects = listing["projects"]
        if listing.get("totalCount", len(projects)) > len(projects):
            raise RuntimeError("Project discovery is incomplete; refusing to guess a matching board.")
        matches = [p for p in projects if p["title"] == plan["project_title"]]
        if len(matches) > 1:
            raise RuntimeError("Multiple expansion projects match; resolve the ambiguity before retrying.")
        created = not matches
        project = matches[0] if matches else command(["project", "create", "--owner", owner, "--title", plan["project_title"], "--format", "json"])
    number = str(project["number"])
    command(["project", "link", number, "--owner", owner, "--repo", plan["repository"]], expect_json=False)
    fields = command(["project", "field-list", number, "--owner", owner, "--format", "json"])["fields"]
    status = next(f for f in fields if f["name"] == "Status")
    if {x["name"] for x in status["options"]} != set(plan["statuses"]):
        if not created:
            raise RuntimeError("Existing project Status differs from the roadmap. Configure its five statuses in GitHub before retrying; existing field options were preserved.")
        mutation = "mutation($input: UpdateProjectV2FieldInput!) { updateProjectV2Field(input: $input) { projectV2Field { ... on ProjectV2SingleSelectField { id name options { id name } } } } }"
        colors = ["GRAY", "BLUE", "YELLOW", "PURPLE", "GREEN"]
        result = command(["api", "graphql", "--input", "-"], {"query": mutation, "variables": {"input": {"fieldId": status["id"], "singleSelectOptions": [dict(name=name, color=color, description=name) for name, color in zip(plan["statuses"], colors)]}}})
        if result.get("errors"):
            raise RuntimeError("Project status setup was rejected. Configure statuses in GitHub before retrying.")
        status = result["data"]["updateProjectV2Field"]["projectV2Field"]
    options = {x["name"]: x["id"] for x in status["options"]}
    listing = command(["project", "item-list", number, "--owner", owner, "--limit", "1000", "--format", "json"])
    items = listing["items"]
    if listing.get("totalCount", len(items)) > len(items):
        raise RuntimeError("Project item discovery is incomplete; refusing to create duplicate items.")
    existing_urls = {i.get("content", {}).get("url") for i in items}
    for issue in plan["issues"]:
        url = urls[issue["id"]]
        if url in existing_urls:
            continue  # Never reset a collaborator's workflow status.
        item = command(["project", "item-add", number, "--owner", owner, "--url", url, "--format", "json"])
        initial = "Ready" if issue["phase"] == 1 else "Backlog"
        command(["project", "item-edit", "--id", item["id"], "--project-id", project["id"], "--field-id", status["id"], "--single-select-option-id", options[initial]], expect_json=False)
    print(f"Project: {project.get('url', 'https://github.com/users/'+owner+'/projects/'+number)}")
    ensure_board_view(project)


def ensure_board_view(project):
    query = "query($id: ID!) { node(id:$id) { ... on ProjectV2 { views(first:100) { nodes { id name number layout groupByFields(first:20) { nodes { ... on ProjectV2SingleSelectField { name } } } } pageInfo { hasNextPage } } } } }"
    result = command(["api", "graphql", "--input", "-"], {"query": query, "variables": {"id": project["id"]}})
    views = result["data"]["node"]["views"]
    if views["pageInfo"]["hasNextPage"]:
        raise RuntimeError("View discovery is incomplete; refusing to create a duplicate board.")
    matches = [v for v in views["nodes"] if v["name"] == "Expansion board"]
    if len(matches) > 1:
        raise RuntimeError("Multiple Expansion board views match; resolve the ambiguity.")
    if matches:
        view = matches[0]
    else:
        mutation = "mutation($input: CreateProjectV2ViewInput!) { createProjectV2View(input:$input) { projectV2View { id name number layout groupByFields(first:20) { nodes { ... on ProjectV2SingleSelectField { name } } } } } }"
        result = command(["api", "graphql", "--input", "-"], {"query": mutation, "variables": {"input": {"projectId": project["id"], "name": "Expansion board", "layout": "BOARD_LAYOUT"}}})
        view = result["data"]["createProjectV2View"]["projectV2View"]
    if view["layout"] != "BOARD_LAYOUT" or not any(f.get("name") == "Status" for f in view["groupByFields"]["nodes"]):
        raise RuntimeError("Project items are populated, but the Expansion board view must be set to Board layout grouped by Status in GitHub. Existing view settings were preserved.")
    print(f"Verified Board view grouped by Status: {project['url']}/views/{view['number']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Create/reuse labels, milestones, issues and project on GitHub")
    parser.add_argument("--issues-only", action="store_true", help="Skip user-owned Projects when that permission is unavailable")
    parser.add_argument("--project-number", type=int, help="Use a known existing Project instead of listing user projects")
    args = parser.parse_args()
    plan = json.loads(PLAN.read_text())
    validate(plan)
    if not args.apply:
        print(f"Preview only: {len(plan['milestones'])} milestones, {len(plan['issues'])} issues, 1 project; no GitHub calls.")
        for phase in plan["milestones"]:
            print(phase["title"])
            for issue in plan["issues"]:
                if issue["phase"] == phase["phase"]:
                    print(f"  {issue['id']}: {issue['title']}")
        return
    if not shutil.which("gh"):
        raise SystemExit("Install GitHub CLI and authenticate with the intended account first.")
    urls = apply_issues(plan)
    if not args.issues_only:
        apply_project(plan, urls, args.project_number)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
