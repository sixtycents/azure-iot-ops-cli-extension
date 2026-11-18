"""Live fixtures are deliberately outside the offline fixture tree."""

import pytest

from live_world import LiveWorld


@pytest.fixture
def live(tmp_path, request):
    world = LiveWorld(tmp_path)
    request.node.user_properties.append(("resource_prefix", world.prefix))
    try:
        yield world
    finally:
        world.cleanup()
