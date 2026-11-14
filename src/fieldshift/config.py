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


# Placeholders people paste verbatim from instructions. A file containing one of
# these looks structurally valid and is useless, which is exactly the failure this
# module exists to catch early.
PLACEHOLDER_SECRETS = (
    "YOUR_PASSWORD", "YOUR_USERNAME", "your_password", "changeme",
    "PASSWORD", "xxxx", "<password>",
)


def earthdata_ready() -> tuple[bool, str]:
    """Whether ~/.netrc carries a plausible Earthdata entry, and what to do if not.

    Deliberately does not read or return the password. It does check for known
    placeholder strings, because a netrc containing a literal 'YOUR_PASSWORD' is
    structurally perfect and completely useless - an earlier version of this
    function reported exactly that state as READY.

    Structural validity is not authentication. Use `earthdata_authenticated()` for
    that; it asks the server.
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
    text = NETRC_FILE.read_text(encoding="utf-8")
    for placeholder in PLACEHOLDER_SECRETS:
        if placeholder in text:
            return False, (
                f"{NETRC_FILE} still contains the literal placeholder "
                f"{placeholder!r}. The file is structurally valid but will not "
                "authenticate. Replace it with the real value."
            )
    return True, "Earthdata entry present in ~/.netrc, permissions correct."


def earthdata_authenticated(timeout_s: float = 25.0) -> tuple[bool, str]:
    """Ask Earthdata whether the stored credentials actually work.

    The password is never read by this process: urllib's netrc-aware opener and
    curl both read ~/.netrc directly. This is the only check that distinguishes
    'the file looks right' from 'the credentials are valid', which matters because
    the difference is invisible until a download fails hours later.
    """
    ok, message = earthdata_ready()
    if not ok:
        return False, message

    import netrc
    import urllib.error
    import urllib.request

    try:
        auth = netrc.netrc(str(NETRC_FILE)).authenticators(EARTHDATA_HOST)
    except (netrc.NetrcParseError, FileNotFoundError) as exc:
        return False, f"could not parse {NETRC_FILE}: {exc}"
    if not auth:
        return False, f"no {EARTHDATA_HOST} entry in {NETRC_FILE}"
    login, _, password = auth

    # Basic auth must be sent PREEMPTIVELY. urllib's HTTPBasicAuthHandler only
    # sends credentials after receiving a 401 with a recognised realm, and URS does
    # not issue a standard challenge - so the handler never sends them and the 401
    # is returned verbatim. An earlier version of this function used that handler
    # and reported perfectly valid credentials as rejected, which is just as
    # misleading as the placeholder bug it was written to catch.
    import base64

    token = base64.b64encode(f"{login}:{password or ''}".encode()).decode()
    request = urllib.request.Request(
        f"https://{EARTHDATA_HOST}/api/users/tokens",
        headers={"Authorization": f"Basic {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return True, (
                f"authenticated with Earthdata as {login!r} (HTTP {response.status})"
            )
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return False, (
                f"Earthdata rejected the credentials for {login!r} (HTTP {exc.code}). "
                "Check the username at https://urs.earthdata.nasa.gov/profile - it is "
                "a short handle, not your email address."
            )
        return False, f"Earthdata returned HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return False, f"could not reach {EARTHDATA_HOST}: {exc.reason}"


def status() -> str:
    """Human-readable credential inventory. Never prints a secret."""
    lines = ["Credential status", "-" * 46]
    ok, msg = earthdata_ready()
    lines.append(f"  {'EARTHDATA (~/.netrc)':28s} {'present' if ok else 'MISSING'}")
    if not ok:
        lines.append(f"      {msg.splitlines()[0]}")
    for name in WHERE_TO_GET:
        present = has_api_key(name)
        lines.append(f"  {name:28s} {'READY' if present else 'missing'}")
        if not present:
            lines.append(f"      get: {WHERE_TO_GET[name]}")
    return "\n".join(lines)
