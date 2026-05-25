"""Root conftest.py — shared pytest configuration and markers."""

import pytest


def pytest_configure(config):
    """Register custom markers to avoid unknown marker warnings."""
    config.addinivalue_line(
        "markers", "integration: marks tests that require external services (network, API keys)"
    )


@pytest.fixture(autouse=True, scope="session")
def _block_real_ddgs():
    """Prevent real DDGS calls in all non-integration tests.

    Patches ddgs.DDGS at the module level so any code that lazily imports
    DDGS (like WebSearchTool._ddgs_search) gets a no-op fake instead of
    hitting the real DuckDuckGo API. Per-test monkeypatch on ddgs.DDGS
    still takes precedence (last write wins).
    """
    try:
        import ddgs as _ddgs_module
    except ModuleNotFoundError:
        yield
        return

    class _FakeDDGS:
        """Fake DDGS context manager that returns empty results."""
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def text(self, *, query, max_results):
            return iter([])

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_ddgs_module, "DDGS", _FakeDDGS)
        yield
