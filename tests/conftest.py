"""Every test runs against a throwaway database and backup folder, so no test can touch the real data/ folder."""

import dataclasses

import pytest

from ar_scout import config, db, scheduler, service


@pytest.fixture(autouse=True)
def _isolated_data(tmp_path, monkeypatch):
    s = dataclasses.replace(config.settings, db_path=tmp_path / "test.db", backup_dir=tmp_path / "backups")
    for mod in (config, db, service, scheduler):
        monkeypatch.setattr(mod, "settings", s)
