import pytest

from world import World


@pytest.fixture
def world(monkeypatch, tmp_path, caplog):
    context = World(monkeypatch, tmp_path, caplog)
    yield context
    context.close()


pytest_plugins = ["steps"]
