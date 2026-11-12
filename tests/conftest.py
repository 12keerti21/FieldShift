import pytest

from rdss.data import load_registry


@pytest.fixture(scope="session")
def reg():
    return load_registry()
