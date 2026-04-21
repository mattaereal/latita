from __future__ import annotations

import pytest

from latita.config import Config, reset_config, set_config


@pytest.fixture(autouse=True)
def isolated_config(tmp_path):
    """Provide an isolated latita config for every test."""
    cfg = Config.for_tests(tmp_path)
    set_config(cfg)
    cfg.ensure_dirs()
    yield cfg
    reset_config()
