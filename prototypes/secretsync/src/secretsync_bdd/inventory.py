"""Canonical inventory; parsing and substitution belong to official Gherkin."""

from dataclasses import dataclass
from pathlib import Path

from gherkin.parser import Parser
from gherkin.pickles.compiler import Compiler
from gherkin.errors import CompositeParserException


@dataclass(frozen=True)
class Case:
    scenario_id: str
    key: str
    feature_path: str
    scenario_line: int
    example_line: int | None
    example_name: str | None
    name: str
    tags: tuple[str, ...]
    steps: tuple[str, ...]


def _scenarios(children):
    for child in children:
        if "scenario" in child:
            yield child["scenario"]
        elif "rule" in child:
            yield from _scenarios(child["rule"]["children"])


def load_inventory(feature_root: Path, e2e: bool = False) -> list[Case]:
    result, seen = [], set()
    paths = sorted(feature_root.rglob("*.feature"))
    if not paths:
        raise ValueError("No feature files found")
    for path in paths:
        relative = path.relative_to(feature_root).as_posix()
        try:
            document = Parser().parse(path.read_text(encoding="utf-8"))
        except CompositeParserException as error:
            raise ValueError(f"{relative}: invalid Gherkin") from error
        scenarios, rows = {}, {}
        for scenario in _scenarios(document["feature"]["children"]):
            ids = [t["name"][10:] for t in scenario["tags"] if t["name"].startswith("@scenario-")]
            if len(ids) != 1 or not ids[0].startswith("SS-") or not ids[0][3:].isdigit():
                raise ValueError(f"{relative}: require one @scenario-SS-* ID")
            identity = ids[0]
            if identity in seen:
                raise ValueError(f"Duplicate scenario ID: {identity}")
            seen.add(identity)
            scenarios[scenario["id"]] = (identity, scenario["location"]["line"])
            if "Outline" in scenario["keyword"] and not scenario["examples"]:
                raise ValueError(f"{identity}: empty outline Examples")
            for examples in scenario["examples"]:
                if not examples.get("tableBody"):
                    raise ValueError(f"{identity}: empty Examples")
                headers = [c["value"] for c in examples["tableHeader"]["cells"]]
                for row in examples["tableBody"]:
                    values = dict(zip(headers, (c["value"] for c in row["cells"])))
                    rows[row["id"]] = (examples["location"]["line"], row["location"]["line"], values.get("case"))
        # The Python compiler accepts the AST document itself, not a messages envelope.
        for pickle in Compiler().compile({**document, "uri": relative}):
            scenario_id, line = scenarios[pickle["astNodeIds"][0]]
            block, row, name = (None, None, None)
            if len(pickle["astNodeIds"]) > 1:
                block, row, name = rows[pickle["astNodeIds"][1]]
            tags = tuple(sorted(t["name"].lstrip("@") for t in pickle["tags"]))
            if not e2e or "e2e" in tags:
                result.append(Case(scenario_id, f"{relative}:{scenario_id}:{block}:{row}", relative,
                                   line, row, name, pickle["name"], tags,
                                   tuple(s["text"] for s in pickle["steps"])))
    return result
