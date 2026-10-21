import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("setup_project", ROOT/"scripts/setup_project.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def plan():
    return json.loads(setup.PLAN.read_text())


def test_roadmap_dependencies_and_readiness():
    roadmap = plan()
    setup.validate(roadmap)
    assert len(roadmap["milestones"]) == 5
    assert len(roadmap["issues"]) == 19
    assert [i["id"] for i in roadmap["issues"] if not i["depends_on"]] == ["SRC-CMS", "SRC-HRSA", "SRC-LOCAL", "SRC-CONTRACT"]
    broken = copy.deepcopy(roadmap)
    broken["issues"][0]["depends_on"] = ["NATIONAL-VERIFY"]
    with pytest.raises(ValueError):
        setup.validate(broken)


def test_preview_never_calls_github(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["setup_project.py"])
    def unexpected(*args, **kwargs):
        raise AssertionError("Preview must never call GitHub")
    monkeypatch.setattr(setup, "command", unexpected)
    setup.main()
    assert "Preview only: 5 milestones, 19 issues" in capsys.readouterr().out


def test_issue_setup_reruns_without_duplicate_writes(monkeypatch):
    roadmap = plan()
    repository = roadmap["repository"]
    state = {"labels": [], "milestones": [], "issues": []}
    writes = []
    def pages(endpoint):
        if "/labels?" in endpoint:
            return state["labels"]
        if "/milestones?" in endpoint:
            return state["milestones"]
        return state["issues"]
    def command(args, payload=None, **kwargs):
        if args == ["api", f"repos/{repository}"]:
            return {"has_issues": True}
        writes.append(args)
        if "/labels" in args[3]:
            state["labels"].append(dict(payload))
            return payload
        if "/milestones" in args[3]:
            row = dict(payload, number=len(state["milestones"])+1)
            state["milestones"].append(row)
            return row
        row = dict(payload, html_url=f"https://github.com/{repository}/issues/{len(state['issues'])+1}")
        state["issues"].append(row)
        return row
    monkeypatch.setattr(setup, "pages", pages)
    monkeypatch.setattr(setup, "command", command)
    first = setup.apply_issues(roadmap)
    count = len(writes)
    second = setup.apply_issues(roadmap)
    assert first == second
    assert len(writes) == count
    assert len(state["issues"]) == 19
    facility = next(i for i in state["issues"] if "[ACCESS-CMS]" in i["title"])
    assert first["SRC-CMS"] in facility["body"]


def test_existing_project_status_options_are_not_overwritten(monkeypatch):
    roadmap = plan()
    calls = []
    def command(args, payload=None, **kwargs):
        calls.append(args)
        if args[:2] == ["project", "list"]:
            return {"projects": [{"title": roadmap["project_title"], "number": 1, "id": "project"}]}
        if args[:2] == ["project", "field-list"]:
            return {"fields": [{"name": "Status", "id": "status", "options": [{"name": "Custom workflow", "id": "option"}]}]}
        return None
    monkeypatch.setattr(setup, "command", command)
    with pytest.raises(RuntimeError, match="preserved"):
        setup.apply_project(roadmap, {})
    assert not any(c[:2] == ["api", "graphql"] for c in calls)


def test_rest_pagination_works_with_older_github_cli(monkeypatch):
    seen = []
    def command(args, payload=None, **kwargs):
        seen.append(args)
        return list(range(100)) if args[1].endswith("&page=1") else [100]
    monkeypatch.setattr(setup, "command", command)
    assert setup.pages("repos/example/project/issues?per_page=100") == list(range(101))
    assert len(seen) == 2
    assert all("--slurp" not in c for c in seen)


def test_existing_board_is_verified_without_overwriting_view(monkeypatch):
    calls = []
    view = dict(name="Expansion board", id="view", layout="BOARD_LAYOUT", number=2,
                groupByFields={"nodes": [{"name": "Status"}]})
    def command(args, payload=None, **kwargs):
        calls.append(payload)
        return {"data": {"node": {"views": {"nodes": [view], "pageInfo": {"hasNextPage": False}}}}}
    monkeypatch.setattr(setup, "command", command)
    setup.ensure_board_view({"id": "project", "url": "https://github.com/users/example/projects/1"})
    assert len(calls) == 1
    view["layout"] = "TABLE_LAYOUT"
    with pytest.raises(RuntimeError, match="preserved"):
        setup.ensure_board_view({"id": "project", "url": "https://github.com/users/example/projects/1"})
    assert all(p["query"].startswith("query") for p in calls)
