import os
import sys
from pathlib import Path
from unittest.mock import patch

from app.config import Settings


def test_effective_database_url_dev_clone(tmp_path):
    """Clean clone should resolve effective database URL to a local database folder."""
    data_dir = tmp_path / "custom_data"
    with patch.dict(os.environ, {"ALPHA_DATA_DIR": str(data_dir)}, clear=False):
        settings = Settings(database_url="")
        expected_dir = data_dir / "database"
        expected_url = f"sqlite:///{(expected_dir / 'wq.db').as_posix()}"
        assert settings.effective_database_url == expected_url


def test_effective_database_url_frozen_desktop(tmp_path):
    """When running inside a PyInstaller frozen desktop bundle, path resolves to ~/.alpha_research/."""
    home_dir = tmp_path / "fake_home"
    home_dir.mkdir()

    with patch.object(sys, "frozen", True, create=True), \
         patch.object(sys, "_MEIPASS", str(tmp_path / "bundle"), create=True), \
         patch.object(Path, "home", return_value=home_dir), \
         patch.dict(os.environ, {}, clear=True):

        expected_dir = home_dir / ".alpha_research" / "database"
        settings = Settings(database_url="")
        expected_url = f"sqlite:///{(expected_dir / 'wq.db').as_posix()}"
        assert settings.effective_database_url == expected_url
