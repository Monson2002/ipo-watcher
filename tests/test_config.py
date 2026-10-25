from ipo_watcher import config


def test_env_treats_empty_string_as_unset(monkeypatch):
    monkeypatch.setenv("TEST_ENV_VAR_EMPTY", "")
    assert config._env("TEST_ENV_VAR_EMPTY", "fallback") == "fallback"


def test_env_returns_real_value_when_set(monkeypatch):
    monkeypatch.setenv("TEST_ENV_VAR_REAL", "value")
    assert config._env("TEST_ENV_VAR_REAL", "fallback") == "value"


def test_env_returns_default_when_truly_unset(monkeypatch):
    monkeypatch.delenv("TEST_ENV_VAR_UNSET", raising=False)
    assert config._env("TEST_ENV_VAR_UNSET") == ""
