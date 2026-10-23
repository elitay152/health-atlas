import pytest


@pytest.fixture(autouse=True)
def isolated_request_coordination(tmp_path, monkeypatch):
    # Never spend a deployment's live request budget from synthetic tests.
    monkeypatch.setenv('HEALTH_ATLAS_COORDINATION_DIR', str(tmp_path / 'coordination'))
    monkeypatch.setenv('HEALTH_ATLAS_REFRESH_MODE', 'single-writer')
