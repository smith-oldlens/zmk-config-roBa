"""OAuth token management for the Pinterest API v5.

A Pinterest access token expires (~30 days), but a refresh token does not
(unless revoked). This module keeps a small cache file with the current
access token + refresh token, refreshing automatically whenever the access
token is missing/expired so the collector never needs a human to paste in
a fresh token again.

One-time setup (to obtain the initial refresh token) is done via
`run_interactive_setup`, wired up as `python -m collector --setup-auth`.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

TOKEN_URL = "https://api.pinterest.com/v5/oauth/token"
AUTH_URL = "https://www.pinterest.com/oauth/"
DEFAULT_SCOPE = "pins:read,boards:read,pins:write,boards:write"

# Refresh a bit before actual expiry so a request never races token expiry.
_SAFETY_MARGIN_SECONDS = 300


class PinterestAuth:
    """Produces a valid access token, refreshing and persisting it as needed."""

    def __init__(
        self,
        cache_path: str | Path,
        client_id: str | None = None,
        client_secret: str | None = None,
        initial_access_token: str | None = None,
        initial_refresh_token: str | None = None,
    ):
        self.cache_path = Path(cache_path)
        self.client_id = client_id
        self.client_secret = client_secret
        self._data = self._load()

        # Env-provided values only seed an empty cache; once the cache file
        # holds a (possibly rotated) refresh token, that's the source of truth.
        if not self._data.get("access_token") and initial_access_token:
            self._data["access_token"] = initial_access_token
        if not self._data.get("refresh_token") and initial_refresh_token:
            self._data["refresh_token"] = initial_refresh_token
        self._data.setdefault("expires_at", 0)

    def _load(self) -> dict:
        if self.cache_path.exists():
            try:
                return json.loads(self.cache_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                log.warning("Could not parse token cache at %s; starting fresh.", self.cache_path)
        return {}

    def _save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def _refresh(self) -> None:
        refresh_token = self._data.get("refresh_token")
        if not refresh_token:
            raise RuntimeError(
                "No Pinterest refresh token available. Run "
                "`python -m collector --setup-auth` once to obtain one, or set "
                "PINTEREST_ACCESS_TOKEN directly if you don't need auto-refresh."
            )
        if not self.client_id or not self.client_secret:
            raise RuntimeError(
                "Refreshing the Pinterest token requires PINTEREST_CLIENT_ID and "
                "PINTEREST_CLIENT_SECRET to be set."
            )

        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        resp = requests.post(
            TOKEN_URL,
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()

        self._data["access_token"] = payload["access_token"]
        self._data["expires_at"] = time.time() + float(payload.get("expires_in", 0))
        # Pinterest may rotate the refresh token on refresh; keep the new one if so.
        if payload.get("refresh_token"):
            self._data["refresh_token"] = payload["refresh_token"]
        self._save()
        log.info("Pinterest access token refreshed (valid for %ss).", payload.get("expires_in"))

    def get_access_token(self, force_refresh: bool = False) -> str:
        expired = time.time() >= self._data.get("expires_at", 0) - _SAFETY_MARGIN_SECONDS
        if force_refresh or expired:
            self._refresh()
        token = self._data.get("access_token")
        if not token:
            raise RuntimeError("No Pinterest access token available.")
        return token


def authed_request(auth: PinterestAuth, method: str, url: str, **kwargs) -> requests.Response:
    """requests.request() that transparently retries once on a 401 with a forced refresh."""
    headers = dict(kwargs.pop("headers", None) or {})
    headers["Authorization"] = f"Bearer {auth.get_access_token()}"
    resp = requests.request(method, url, headers=headers, **kwargs)
    if resp.status_code == 401:
        headers["Authorization"] = f"Bearer {auth.get_access_token(force_refresh=True)}"
        resp = requests.request(method, url, headers=headers, **kwargs)
    return resp


def run_interactive_setup(cfg: dict, client_id: str | None, client_secret: str | None) -> int:
    """One-time interactive OAuth authorization-code flow to obtain a refresh token."""
    if not client_id or not client_secret:
        print("Set PINTEREST_CLIENT_ID and PINTEREST_CLIENT_SECRET first, then rerun --setup-auth.")
        return 1

    redirect_uri = "https://developers.pinterest.com/apps/callback"
    auth_url = (
        f"{AUTH_URL}?client_id={client_id}&redirect_uri={redirect_uri}"
        f"&response_type=code&scope={DEFAULT_SCOPE}"
    )
    print("1. Open this URL in a browser and authorize the app:")
    print(f"   {auth_url}")
    print("2. After approving, you'll be redirected to a URL containing '?code=...'.")
    pasted = input("3. Paste that full redirect URL (or just the code) here: ").strip()

    match = re.search(r"[?&]code=([^&]+)", pasted)
    code = match.group(1) if match else pasted
    if not code:
        print("No code provided; aborting.")
        return 1

    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
        timeout=30,
    )
    try:
        resp.raise_for_status()
    except requests.RequestException:
        print(f"Token exchange failed: {resp.status_code} {resp.text}")
        return 1
    payload = resp.json()

    cache_path = Path(cfg["token_cache_file"])
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(
            {
                "access_token": payload["access_token"],
                "refresh_token": payload.get("refresh_token"),
                "expires_at": time.time() + float(payload.get("expires_in", 0)),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"\nSaved tokens to {cache_path}. Auto-refresh is now set up for local runs.")
    if payload.get("refresh_token"):
        print("\nFor GitHub Actions, add these as repository secrets:")
        print(f"  PINTEREST_CLIENT_ID={client_id}")
        print("  PINTEREST_CLIENT_SECRET=(already known to you)")
        print(f"  PINTEREST_REFRESH_TOKEN={payload['refresh_token']}")
    return 0
