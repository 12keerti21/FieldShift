"""Credential and configuration loading.

One rule: no credential is ever hard-coded, logged, or passed on a command line.
API keys come from a gitignored `.env`; Earthdata comes from `~/.netrc`, which
every NASA download tool reads directly so the password never transits a shell
command or an environment dump.

Missing keys raise a message that names the file to edit and the URL to get the
key from, because "KeyError: NASS_QUICKSTATS_KEY" at hour 30 of a hackathon is
not a helpful error.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"
NETRC_FILE = Path.home() / ".netrc"
EARTHDATA_HOST = "urs.earthdata.nasa.gov"

WHERE_TO_GET = {
    "NASS_QUICKSTATS_KEY": "https://quickstats.nass.usda.gov/api/",
    "USGS_WATERDATA_KEY": "https://api.waterdata.usgs.gov/signup/",
    "AMS_MARKET_NEWS_KEY": "https://mymarketnews.ams.usda.gov/",
    "OPENET_KEY": "https://openetdata.org/",
    "CDS_API_KEY": "https://cds.climate.copernicus.eu/how-to-api",
    "COPERNICUS_CLIENT_ID": "https://dataspace.copernicus.eu/",
    "COPERNICUS_CLIENT_SECRET": "https://dataspace.copernicus.eu/",
}


class MissingCredential(RuntimeError):
    pass


@lru_cache(maxsize=1)
def load_env(path: Path | None = None) -> dict[str, str]:
    """Parse .env. Process environment wins, so CI can inject without a file."""
    env_path = path or ENV_FILE
    values: dict[str, str] = {}
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip().strip("'\"")
            if value:
                values[key.strip()] = value
    for key in list(WHERE_TO_GET) + ["CDS_API_URL"]:
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def api_key(name: str) -> str:
    """Fetch a key, or explain exactly how to obtain and install it."""
    value = load_env().get(name)
    if value:
        return value
    where = WHERE_TO_GET.get(name, "(no registration URL recorded)")
    raise MissingCredential(
        f"{name} is not set.\n"
        f"  1. Get a key: {where}\n"
        f"  2. Add it to {ENV_FILE} as  {name}=your_key_here\n"
        f"  (copy .env.example to .env first; .env is gitignored)"
    )


def has_api_key(name: str) -> bool:
    """For skipping optional work without raising."""
    return bool(load_env().get(name))


def earthdata_ready() -> tuple[bool, str]:
    """Whether ~/.netrc carries an Earthdata entry, and what to do if not.

    Deliberately does not read or return the password - only whether the host
    appears in the file.
    """
    if not NETRC_FILE.exists():
        return False, (
            f"{NETRC_FILE} does not exist. Create it with:\n"
            f"  printf 'machine {EARTHDATA_HOST} login YOUR_USERNAME "
            f"password YOUR_PASSWORD\\n' >> ~/.netrc\n"
            f"  chmod 600 ~/.netrc\n"
            f"Your username (not your email) is at https://urs.earthdata.nasa.gov/profile"
        )
    if EARTHDATA_HOST not in NETRC_FILE.read_text(encoding="utf-8"):
        return False, f"{NETRC_FILE} exists but has no {EARTHDATA_HOST} entry."
    mode = NETRC_FILE.stat().st_mode & 0o777
    if mode & 0o077:
        return False, (
            f"{NETRC_FILE} is mode {mode:o}; curl and earthaccess refuse a "
            f"world-readable netrc. Run: chmod 600 ~/.netrc"
        )
    return True, "Earthdata entry present in ~/.netrc, permissions correct."


def status() -> str:
    """Human-readable credential inventory. Never prints a secret."""
    lines = ["Credential status", "-" * 46]
    ok, msg = earthdata_ready()
    lines.append(f"  {'EARTHDATA (~/.netrc)':28s} {'READY' if ok else 'MISSING'}")
    if not ok:
        lines.append(f"      {msg.splitlines()[0]}")
    for name in WHERE_TO_GET:
        present = has_api_key(name)
        lines.append(f"  {name:28s} {'READY' if present else 'missing'}")
        if not present:
            lines.append(f"      get: {WHERE_TO_GET[name]}")
    return "\n".join(lines)
