import json
from types import SimpleNamespace as NS

import pytest

from secretsync_bdd import plugin
from secretsync_bdd.inventory import Case


CASE = Case("SS-1", "f:SS-1:None:None", "f.feature", 3, None, None, "<safe>", ("scenario-SS-1",), ("done",))


class Config:
    def __init__(self, path, live=False, enabled=True):
        self.options = {"--secretsync-features": str(path) if enabled else None,
                        "--secretsync-report": str(path), "--secretsync-e2e": live}
        self.markers = []
        self.registered = []
        self.pluginmanager = NS(register=lambda *args: self.registered.append(args))

    def getoption(self, name):
        return self.options[name]

    def addinivalue_line(self, kind, value):
        self.markers.append(value)


def item(node="node", ids=("scenario-SS-1",), row=None, bdd=False):
    rendered = NS(name=CASE.name, steps=[NS(name=text) for text in CASE.steps])
    template = NS(feature=NS(filename=CASE.feature_path), line_number=CASE.scenario_line,
                  render=lambda context: rendered)
    obj = NS(__scenario__=template) if bdd or ids else NS()
    markers = [NS(name=name) for name in ids]
    return NS(nodeid=node, obj=obj, callspec=NS(params={"_pytest_bdd_example": row}) if row else None,
              iter_markers=lambda name=None: iter([m for m in markers if name is None or m.name == name]))


@pytest.fixture
def gate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(plugin, "load_inventory", lambda *args: [CASE])
    return plugin.Completeness(Config(tmp_path))


def phases(gate, node="node", outcome="passed", xfail=False):
    for when in ("setup", "call", "teardown"):
        report = NS(nodeid=node, when=when, outcome=outcome)
        if xfail:
            report.wasxfail = "expected"
        gate.pytest_runtest_logreport(report)


def test_success_and_escaped_report(gate, tmp_path):
    gate.pytest_collection_finish(NS(items=[item(), item("ordinary", ids=())]))
    phases(gate)
    session = NS(exitstatus=0)
    gate.pytest_sessionfinish(session, 0)
    report = json.loads((tmp_path / "cases.json").read_text())
    assert session.exitstatus == 0
    assert report["status"] == "passed"
    assert report["cases"][0]["nodes"] == ["node"]
    assert report["outcomes"] == {"passed": 1}
    assert "&lt;safe&gt;" in (tmp_path / "cases.html").read_text()


@pytest.mark.parametrize("mode", ["missing", "duplicate", "skip", "xfail", "failed", "incomplete", "pytest"])
def test_no_false_success(gate, tmp_path, mode):
    items = [] if mode == "missing" else [item()]
    if mode == "duplicate":
        items.append(item("second"))
    gate.pytest_collection_finish(NS(items=items))
    if mode != "incomplete":
        phases(gate, outcome={"skip": "skipped", "failed": "failed"}.get(mode, "passed"),
               xfail=mode == "xfail")
    session = NS(exitstatus=0)
    gate.pytest_sessionfinish(session, 2 if mode == "pytest" else 0)
    assert session.exitstatus == 1
    assert json.loads((tmp_path / "cases.json").read_text())["errors"]


def test_binding_identity_is_marker_and_case_not_node_name(gate):
    assert gate.match(item("arbitrary renamed node")) == CASE.key
    assert gate.match(item(ids=(), bdd=True)) is None
    assert gate.match(item(ids=("scenario-SS-999",))) is None
    assert gate.match(item(ids=("scenario-SS-1", "scenario-SS-2"))) is None
    assert gate.match(item(row={"case": "unknown"})) is None
    gate.index[("SS-1", "row")] = [CASE]
    assert gate.match(item(row={"case": "row"})) == CASE.key
    gate.index[("SS-1", "row")].append(CASE)
    assert gate.match(item(row={"case": "row"})) is None
    assert len(gate.errors) == 5


@pytest.mark.parametrize("mismatch", ["path", "line", "name", "steps", "metadata"])
def test_copied_or_changed_scenario_cannot_qualify_by_tags_alone(gate, mismatch):
    binding = item()
    template = binding.obj.__scenario__
    if mismatch == "path":
        template.feature.filename = "copied.feature"
    elif mismatch == "line":
        template.line_number += 1
    elif mismatch == "name":
        template.render({}).name = "copied scenario"
    elif mismatch == "steps":
        template.render({}).steps = [NS(name="different behavior")]
    else:
        del binding.obj.__scenario__
    assert gate.match(binding) is None
    assert gate.errors


def test_pytest_failure_does_not_conflate_independent_completeness(gate, tmp_path):
    gate.pytest_collection_finish(NS(items=[item()]))
    phases(gate)
    session = NS(exitstatus=1)
    gate.pytest_sessionfinish(session, 1)
    report = json.loads((tmp_path / "cases.json").read_text())
    assert report["status"] == "passed"
    assert report["pytest_exit_status"] == 1
    assert session.exitstatus == 1


def test_deselection_and_collection_errors(gate):
    gate.pytest_deselected([item()])
    gate.pytest_collectreport(NS(failed=False, skipped=False))
    gate.pytest_collectreport(NS(failed=True, skipped=False, outcome="failed", nodeid="collect"))
    gate.pytest_collectreport(NS(failed=False, skipped=True, outcome="skipped", nodeid="collect"))
    assert len(gate.errors) == 3
    gate.config.options["--secretsync-e2e"] = True
    gate.pytest_deselected([item(), item(ids=("scenario-SS-1", "e2e"))])
    assert len(gate.errors) == 4


def test_invalid_empty_inventory_reports(tmp_path, monkeypatch):
    def invalid(*args):
        raise ValueError("broken")
    monkeypatch.setattr(plugin, "load_inventory", invalid)
    gate = plugin.Completeness(Config(tmp_path))
    gate.pytest_sessionfinish(NS(exitstatus=0), 0)
    report = json.loads((tmp_path / "cases.json").read_text())
    assert report["status"] == "failed"
    assert "Empty required inventory" in report["errors"]


def test_live_inventory_inherits_selection_but_registers_all_tags(tmp_path, monkeypatch):
    from dataclasses import replace
    live = replace(CASE, scenario_id="SS-2", key="live", tags=("e2e", "scenario-SS-2"))
    monkeypatch.setattr(plugin, "load_inventory", lambda *args: [CASE, live])
    config = Config(tmp_path, live=True)
    gate = plugin.Completeness(config)
    assert gate.cases == [live]
    assert any("scenario-SS-1:" in tag for tag in config.markers)


def test_hook_registration(tmp_path, monkeypatch):
    import importlib
    # pytest loads explicit plugins before coverage starts: test their startup under measurement.
    importlib.reload(plugin)
    groups = []
    group = NS(addoption=lambda *a, **k: groups.append((a, k)))
    plugin.pytest_addoption(NS(getgroup=lambda name: group))
    assert len(groups) == 3
    monkeypatch.setattr(plugin, "load_inventory", lambda *a: [CASE])
    config = Config(tmp_path)
    plugin.pytest_configure(config)
    assert [name for _, name in config.registered] == ["secretsync-tags", "secretsync-completeness"]
    config = Config(tmp_path, enabled=False)
    plugin.pytest_configure(config)
    assert [name for _, name in config.registered] == ["secretsync-tags"]


def test_focused_strict_markers_are_registered_before_application(pytestconfig, monkeypatch):
    monkeypatch.setattr(pytestconfig.option, "strict_markers", True)
    tags = plugin.Tags(pytestconfig)

    def binding():
        pass
    assert tags.pytest_bdd_apply_tag("scenario-SS-FOCUSED-REGRESSION", binding)
    assert tags.pytest_bdd_apply_tag("secretsync-focused-regression", binding)
    assert [mark.name for mark in binding.pytestmark] == [
        "scenario-SS-FOCUSED-REGRESSION", "secretsync-focused-regression"]
    assert "scenario-SS-FOCUSED-REGRESSION: specification tag" in pytestconfig.getini("markers")


def test_default_report_destination(gate, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    gate.config.options["--secretsync-report"] = None
    gate.pytest_sessionfinish(NS(exitstatus=0), 0)
    assert (tmp_path / "secretsync-report" / "cases.json").exists()


def test_report_startup_and_json_html_roundtrip(tmp_path):
    import importlib
    from secretsync_bdd import reporting
    module = importlib.reload(reporting)
    payload = {"status": "blocked", "errors": ['<script>"secret-free"&</script>'], "expected": 0}
    module.write_report(tmp_path, "blocked", payload)
    assert json.loads((tmp_path / "blocked.json").read_text()) == {"schema_version": 1, **payload}
    html = (tmp_path / "blocked.html").read_text()
    assert "<script>" not in html
    assert "&lt;script&gt;" in html and "&amp;" in html
