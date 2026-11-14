"""Credential handling.

The rule this enforces: a credential file that is structurally perfect but contains
a pasted placeholder must NOT report as ready. An earlier version of this module
reported a netrc containing the literal string 'YOUR_PASSWORD' as READY, which is
the most misleading possible answer - it sends you off to debug a download instead
of the one line that is actually wrong.

No test here reads or asserts on real secret material.
"""

import os

import pytest

from rdss import config


@pytest.fixture
def netrc(tmp_path, monkeypatch):
    path = tmp_path / ".netrc"
    monkeypatch.setattr(config, "NETRC_FILE", path)
    return path


def _write(path, body, mode=0o600):
    path.write_text(body)
    os.chmod(path, mode)
    return path


def test_missing_netrc_explains_how_to_create_it(netrc):
    ok, msg = config.earthdata_ready()
    assert not ok
    assert "does not exist" in msg
    assert "chmod 600" in msg
    assert "urs.earthdata.nasa.gov" in msg


def test_netrc_without_an_earthdata_entry_is_not_ready(netrc):
    _write(netrc, "machine example.com login a password b\n")
    ok, msg = config.earthdata_ready()
    assert not ok
    assert "no urs.earthdata.nasa.gov entry" in msg


def test_world_readable_netrc_is_refused(netrc):
    _write(netrc, "machine urs.earthdata.nasa.gov login u password p\n", mode=0o644)
    ok, msg = config.earthdata_ready()
    assert not ok
    assert "chmod 600" in msg


@pytest.mark.parametrize("placeholder", ["YOUR_PASSWORD", "YOUR_USERNAME", "changeme"])
def test_pasted_placeholders_are_caught(netrc, placeholder):
    """The actual failure: the instruction was run verbatim."""
    _write(netrc, f"machine urs.earthdata.nasa.gov login u password {placeholder}\n")
    ok, msg = config.earthdata_ready()
    assert not ok
    assert placeholder in msg
    assert "structurally valid" in msg


def test_a_well_formed_entry_reports_ready(netrc):
    _write(netrc, "machine urs.earthdata.nasa.gov login keerti_c password s3cret!\n")
    ok, msg = config.earthdata_ready()
    assert ok
    assert "permissions correct" in msg


def test_ready_does_not_claim_authentication(netrc):
    """Structural validity and working credentials are different claims."""
    _write(netrc, "machine urs.earthdata.nasa.gov login u password p\n")
    _, msg = config.earthdata_ready()
    assert "authenticat" not in msg.lower()


def test_live_probe_short_circuits_when_the_file_is_not_ready(netrc):
    """No network call should be attempted on a placeholder."""
    _write(netrc, "machine urs.earthdata.nasa.gov login u password YOUR_PASSWORD\n")
    ok, msg = config.earthdata_authenticated(timeout_s=0.01)
    assert not ok
    assert "YOUR_PASSWORD" in msg


# --- API keys ---------------------------------------------------------------------


def test_missing_api_key_names_the_file_and_the_url(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("NASS_QUICKSTATS_KEY=\n")
    config.load_env.cache_clear()
    monkeypatch.setattr(config, "ENV_FILE", env)
    monkeypatch.delenv("NASS_QUICKSTATS_KEY", raising=False)
    with pytest.raises(config.MissingCredential) as exc:
        config.api_key("NASS_QUICKSTATS_KEY")
    text = str(exc.value)
    assert "quickstats.nass.usda.gov/api" in text
    assert ".env" in text
    config.load_env.cache_clear()


def test_env_file_is_parsed_and_quotes_stripped(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("# a comment\nNASS_QUICKSTATS_KEY='abc123'\nEMPTY=\n")
    config.load_env.cache_clear()
    monkeypatch.setattr(config, "ENV_FILE", env)
    monkeypatch.delenv("NASS_QUICKSTATS_KEY", raising=False)
    assert config.load_env()["NASS_QUICKSTATS_KEY"] == "abc123"
    assert "EMPTY" not in config.load_env()
    config.load_env.cache_clear()


def test_process_environment_overrides_the_file(monkeypatch, tmp_path):
    """So CI can inject keys without writing a file."""
    env = tmp_path / ".env"
    env.write_text("NASS_QUICKSTATS_KEY=from_file\n")
    config.load_env.cache_clear()
    monkeypatch.setattr(config, "ENV_FILE", env)
    monkeypatch.setenv("NASS_QUICKSTATS_KEY", "from_environment")
    assert config.api_key("NASS_QUICKSTATS_KEY") == "from_environment"
    config.load_env.cache_clear()


def test_every_known_key_has_a_registration_url():
    for name, url in config.WHERE_TO_GET.items():
        assert url.startswith("https://"), name


def test_status_never_prints_secret_material(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("NASS_QUICKSTATS_KEY=SUPERSECRETVALUE\n")
    config.load_env.cache_clear()
    monkeypatch.setattr(config, "ENV_FILE", env)
    monkeypatch.setattr(config, "NETRC_FILE", tmp_path / "nonexistent")
    monkeypatch.delenv("NASS_QUICKSTATS_KEY", raising=False)
    out = config.status()
    assert "SUPERSECRETVALUE" not in out
    assert "READY" in out  # it reports presence, not the value
    config.load_env.cache_clear()
