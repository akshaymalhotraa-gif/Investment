"""OAuth 2.0 for a Desktop-app client, with no google-auth dependency.

Two flows, same token file:

  * loopback  - opens a browser, catches the redirect on 127.0.0.1. Default.
  * manual    - prints a URL, you paste the code back. For headless boxes.

Tokens land in ~/.config/healthlog/token.json with 0600 permissions.
"""

from __future__ import annotations

import http.server
import json
import secrets
import socket
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from typing import Any

import requests

from . import config


class AuthError(RuntimeError):
    pass


@dataclass
class ClientSecret:
    client_id: str
    client_secret: str

    @classmethod
    def load(cls) -> "ClientSecret":
        if not config.CLIENT_SECRET_PATH.exists():
            raise AuthError(
                f"No OAuth client at {config.CLIENT_SECRET_PATH}.\n"
                "Download the Desktop-app credentials JSON from Google Cloud "
                "console and save it there. See README step 1."
            )
        raw = json.loads(config.CLIENT_SECRET_PATH.read_text())
        # Desktop credentials nest under "installed"; tolerate "web" too.
        blob = raw.get("installed") or raw.get("web") or raw
        try:
            return cls(blob["client_id"], blob["client_secret"])
        except KeyError as exc:
            raise AuthError(
                f"{config.CLIENT_SECRET_PATH} is missing {exc}. Re-download it "
                "as an OAuth client ID of type 'Desktop app'."
            ) from exc


def _save_token(token: dict[str, Any]) -> None:
    config.ensure_config_dir()
    # expires_in is relative; store an absolute deadline so restarts are safe.
    if "expires_in" in token:
        token["expires_at"] = time.time() + float(token.pop("expires_in")) - 60
    config.TOKEN_PATH.write_text(json.dumps(token, indent=2))
    config.TOKEN_PATH.chmod(0o600)


def _load_token() -> dict[str, Any]:
    if not config.TOKEN_PATH.exists():
        raise AuthError("Not authenticated. Run: healthlog auth login")
    return json.loads(config.TOKEN_PATH.read_text())


def _exchange(payload: dict[str, str]) -> dict[str, Any]:
    resp = requests.post(config.OAUTH_TOKEN_URI, data=payload, timeout=30)
    if resp.status_code != 200:
        raise AuthError(f"Token endpoint returned {resp.status_code}: {resp.text}")
    return resp.json()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(config.SCOPES),
        "state": state,
        # offline + consent is what actually mints a refresh token. Without
        # prompt=consent Google silently omits it on re-authorisation.
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{config.OAUTH_AUTH_URI}?{urllib.parse.urlencode(params)}"


def login_loopback(open_browser: bool = True) -> None:
    """Full browser round-trip against a throwaway localhost listener."""
    client = ClientSecret.load()
    port = _free_port()
    redirect_uri = f"http://127.0.0.1:{port}"
    state = secrets.token_urlsafe(16)
    caught: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib naming
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            caught.update({k: v[0] for k, v in query.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            ok = "code" in caught and caught.get("state") == state
            body = (
                "<h2>Authorised.</h2><p>Close this tab and return to the terminal.</p>"
                if ok
                else f"<h2>Authorisation failed.</h2><pre>{caught}</pre>"
            )
            self.wfile.write(body.encode())

        def log_message(self, *args: Any) -> None:
            pass  # keep the terminal clean

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()

    url = _auth_url(client.client_id, redirect_uri, state)
    print(f"Opening browser for Google consent...\n  {url}\n")
    if open_browser:
        webbrowser.open(url)

    deadline = time.time() + 300
    while "code" not in caught and "error" not in caught and time.time() < deadline:
        time.sleep(0.25)
    server.server_close()

    if "error" in caught:
        raise AuthError(f"Google returned: {caught['error']}")
    if "code" not in caught:
        raise AuthError("Timed out waiting for the redirect (5 min).")
    if caught.get("state") != state:
        raise AuthError("State mismatch - possible CSRF, aborting.")

    token = _exchange(
        {
            "code": caught["code"],
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    )
    _verify_refresh_token(token)
    _save_token(token)
    print(f"Authenticated. Token stored at {config.TOKEN_PATH}")


def login_manual_url() -> str:
    """Step 1 of the headless flow: the URL to open on any machine."""
    client = ClientSecret.load()
    return _auth_url(client.client_id, "urn:ietf:wg:oauth:2.0:oob", "manual")


def login_manual_complete(code: str) -> None:
    """Step 2 of the headless flow: redeem the pasted code."""
    client = ClientSecret.load()
    token = _exchange(
        {
            "code": code.strip(),
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
            "grant_type": "authorization_code",
        }
    )
    _verify_refresh_token(token)
    _save_token(token)
    print(f"Authenticated. Token stored at {config.TOKEN_PATH}")


def _verify_refresh_token(token: dict[str, Any]) -> None:
    if "refresh_token" not in token:
        raise AuthError(
            "Google issued an access token but no refresh token, so this login "
            "would die in an hour.\n"
            "Revoke the app at https://myaccount.google.com/permissions and run "
            "`healthlog auth login` again - the consent prompt must be shown "
            "fresh for a refresh token to be minted."
        )


def access_token() -> str:
    """A valid bearer token, refreshing on the fly when the old one lapsed."""
    token = _load_token()
    if token.get("expires_at", 0) > time.time():
        return token["access_token"]

    client = ClientSecret.load()
    try:
        refreshed = _exchange(
            {
                "refresh_token": token["refresh_token"],
                "client_id": client.client_id,
                "client_secret": client.client_secret,
                "grant_type": "refresh_token",
            }
        )
    except AuthError as exc:
        if "invalid_grant" in str(exc):
            raise AuthError(
                "Refresh token rejected (invalid_grant).\n\n"
                "The usual cause: your OAuth consent screen is still in "
                "'Testing', where Google caps refresh tokens at 7 days.\n"
                "Fix it permanently - Google Cloud console > APIs & Services > "
                "OAuth consent screen > Publish app - then run "
                "`healthlog auth login` once more."
            ) from exc
        raise

    # A refresh response carries no new refresh_token; keep the existing one.
    refreshed.setdefault("refresh_token", token["refresh_token"])
    _save_token(refreshed)
    return refreshed["access_token"]


def status() -> dict[str, Any]:
    token = _load_token()
    remaining = token.get("expires_at", 0) - time.time()
    return {
        "token_file": str(config.TOKEN_PATH),
        "has_refresh_token": "refresh_token" in token,
        "access_token_valid_for_seconds": max(0, int(remaining)),
        "scopes": token.get("scope", "").split(),
    }
