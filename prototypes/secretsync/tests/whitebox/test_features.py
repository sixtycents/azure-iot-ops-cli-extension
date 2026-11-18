"""Every accepted feature must execute offline, including scenarios also selected for live tests."""

from pathlib import Path

from pytest_bdd import scenarios


scenarios(str(Path(__file__).resolve().parents[4] / "features" / "secretsync"))
