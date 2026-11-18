"""Strict completeness gate, activated only by --secretsync-features."""

from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path

import pytest

from .inventory import load_inventory
from .reporting import write_report


def pytest_addoption(parser):
    group = parser.getgroup("secretsync")
    group.addoption("--secretsync-features")
    group.addoption("--secretsync-report")
    group.addoption("--secretsync-e2e", action="store_true")


def pytest_configure(config):
    config.addinivalue_line("markers", "e2e: real service acceptance")
    config.pluginmanager.register(Tags(config), "secretsync-tags")
    root = config.getoption("--secretsync-features")
    if root:
        config.pluginmanager.register(Completeness(config), "secretsync-completeness")


class Tags:
    """Tag registration also applies to focused runs without the completeness gate."""

    def __init__(self, config):
        self.config = config

    @pytest.hookimpl(optionalhook=True, tryfirst=True)
    def pytest_bdd_apply_tag(self, tag, function):
        self.config.addinivalue_line("markers", f"{tag}: specification tag")
        pytest.mark.__getattr__(tag)(function)
        return True


class Completeness:
    def __init__(self, config):
        self.config = config
        self.errors = []
        self.cases = []
        self.nodes = {}
        self.phases = defaultdict(list)
        self.collected = Counter()
        try:
            inventory = load_inventory(Path(config.getoption("--secretsync-features")))
            for case in inventory:
                for tag in case.tags:
                    config.addinivalue_line("markers", f"{tag}: specification tag")
            self.cases = [case for case in inventory
                          if not config.getoption("--secretsync-e2e") or "e2e" in case.tags]
        except (OSError, ValueError, KeyError) as error:
            self.errors.append(f"Inventory invalid: {error}")
        self.index = defaultdict(list)
        for case in self.cases:
            self.index[(case.scenario_id, case.example_name)].append(case)

    def match(self, item):
        ids = [m.name[9:] for m in item.iter_markers() if m.name.startswith("scenario-")]
        if not ids:
            # Ordinary unit tests have no scenario marker. Unmarked BDD bindings cannot disappear.
            if getattr(getattr(item, "obj", None), "__scenario__", None) is not None:
                self.errors.append(f"Unidentified BDD binding: {item.nodeid}")
            return None
        example = getattr(item, "callspec", None)
        row = example.params.get("_pytest_bdd_example", {}) if example else {}
        matches = self.index.get((ids[0], row.get("case")), [])
        if len(ids) != 1 or len(matches) != 1:
            self.errors.append(f"Undefined or ambiguous BDD binding: {item.nodeid}")
            return None
        case = matches[0]
        template = getattr(getattr(item, "obj", None), "__scenario__", None)
        if template is None:
            self.errors.append(f"Missing BDD scenario metadata: {item.nodeid}")
            return None
        rendered = template.render(row)
        expected_path = Path(self.config.getoption("--secretsync-features")) / case.feature_path
        if (Path(template.feature.filename).resolve() != expected_path.resolve()
                or template.line_number != case.scenario_line or rendered.name != case.name
                or tuple(step.name for step in rendered.steps) != case.steps):
            self.errors.append(f"BDD source or expanded text mismatch: {item.nodeid}")
            return None
        return case.key

    def pytest_collection_finish(self, session):
        for item in session.items:
            key = self.match(item)
            if key:
                self.nodes[item.nodeid] = key
                self.collected[key] += 1

    def pytest_deselected(self, items):
        for item in items:
            # -m e2e intentionally discards untagged whitebox-independent scenarios.
            if self.config.getoption("--secretsync-e2e") and not list(item.iter_markers(name="e2e")):
                continue
            if self.match(item):
                self.errors.append(f"Deselected BDD binding: {item.nodeid}")

    def pytest_collectreport(self, report):
        if report.failed or report.skipped:
            self.errors.append(f"Collection {report.outcome}: {report.nodeid}")

    def pytest_runtest_logreport(self, report):
        self.phases[report.nodeid].append((report.when, report.outcome, hasattr(report, "wasxfail")))

    def pytest_sessionfinish(self, session, exitstatus):
        outcomes = {}
        for node, key in self.nodes.items():
            phases = self.phases[node]
            good = (len(phases) == 3 and {p[0] for p in phases} == {"setup", "call", "teardown"}
                    and all(p[1] == "passed" and not p[2] for p in phases))
            outcomes[key] = "passed" if good else "failed"
        for case in self.cases:
            count = self.collected[case.key]
            if count != 1:
                self.errors.append(f"{case.key}: expected one binding, collected {count}")
            if outcomes.get(case.key) != "passed":
                self.errors.append(f"{case.key}: not successfully executed")
        if not self.cases:
            self.errors.append("Empty required inventory")
        # Exit 1 may be pytest-cov's independent coverage gate or a non-BDD unit failure.
        # Neither changes whether the required BDD cases ran successfully.
        if int(exitstatus) not in (0, 1):
            self.errors.append(f"pytest exit status: {int(exitstatus)}")
        passed = not self.errors
        write_report(Path(self.config.getoption("--secretsync-report") or "secretsync-report"), "cases", {
            "status": "passed" if passed else "failed",
            "pytest_exit_status": int(exitstatus),
            "expected": len(self.cases), "collected": sum(self.collected.values()),
            "executed": sum(bool(self.phases[node]) for node in self.nodes),
            "outcomes": dict(Counter(outcomes.get(c.key, "not_run") for c in self.cases)), "errors": self.errors,
            "cases": [{**asdict(c), "outcome": outcomes.get(c.key, "not_run"),
                       "nodes": [n for n, k in self.nodes.items() if k == c.key]} for c in self.cases],
        })
        if not passed:
            session.exitstatus = 1
