from __future__ import annotations

import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from youtube_recent_downloader import PROJECT_ROOT
except ModuleNotFoundError:  # pragma: no cover
    from .youtube_recent_downloader import PROJECT_ROOT


AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
REVOKE_URL = "https://open.tiktokapis.com/v2/oauth/revoke/"
DEFAULT_TOKEN_FILE = PROJECT_ROOT / ".state" / "tiktok_token.json"
DEFAULT_OAUTH_STATE_FILE = PROJECT_ROOT / ".state" / "tiktok_oauth_state.json"
DEFAULT_REDIRECT_URI = "https://tiktok.aemour.com/tiktok/callback/"
DEFAULT_LOCAL_CALLBACK_URL = "http://127.0.0.1:8787/tiktok/callback"
DEFAULT_SCOPES = "user.info.basic,video.upload,video.publish,video.list"
TOKEN_REFRESH_MARGIN_SECONDS = 10 * 60


@dataclass(frozen=True)
class TikTokOAuthConfig:
    client_key: str
    client_secret: str
    redirect_uri: str
    scopes: str
    local_callback_url: str

    @property
    def configured(self) -> bool:
        return bool(self.client_key and self.client_secret and self.redirect_uri)


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def oauth_config() -> TikTokOAuthConfig:
    return TikTokOAuthConfig(
        client_key=os.getenv("TIKTOK_CLIENT_KEY", "").strip(),
        client_secret=os.getenv("TIKTOK_CLIENT_SECRET", "").strip(),
        redirect_uri=os.getenv("TIKTOK_REDIRECT_URI", DEFAULT_REDIRECT_URI).strip(),
        scopes=os.getenv("TIKTOK_SCOPES", DEFAULT_SCOPES).strip(),
        local_callback_url=os.getenv("TIKTOK_LOCAL_CALLBACK_URL", DEFAULT_LOCAL_CALLBACK_URL).strip(),
    )


def normalize_scope(scope: str) -> str:
    return ",".join(part.strip() for part in scope.replace(" ", ",").split(",") if part.strip())


def create_state(state_file: Path = DEFAULT_OAUTH_STATE_FILE) -> str:
    state = secrets.token_urlsafe(32)
    save_json(state_file, {"state": state, "created_at": int(time.time())})
    return state


def verify_state(returned_state: str, state_file: Path = DEFAULT_OAUTH_STATE_FILE) -> bool:
    stored = load_json(state_file, {})
    if not returned_state or returned_state != stored.get("state"):
        return False
    created_at = int(stored.get("created_at") or 0)
    return created_at > 0 and time.time() - created_at < 15 * 60


def build_authorization_url(config: TikTokOAuthConfig | None = None, *, state: str | None = None) -> str:
    config = config or oauth_config()
    if not config.client_key:
        raise ValueError("TIKTOK_CLIENT_KEY manquant dans .env")
    state = state or create_state()
    params = {
        "client_key": config.client_key,
        "response_type": "code",
        "scope": normalize_scope(config.scopes),
        "redirect_uri": config.redirect_uri,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def form_post(url: str, payload: dict[str, str]) -> dict[str, Any]:
    body = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Cache-Control": "no-cache",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"TikTok OAuth HTTP {exc.code}: {raw}") from exc
    return json.loads(raw or "{}")


def token_expiry(now: int, seconds: Any) -> int:
    try:
        return now + int(seconds)
    except (TypeError, ValueError):
        return now


def enrich_token_payload(payload: dict[str, Any]) -> dict[str, Any]:
    now = int(time.time())
    enriched = dict(payload)
    enriched["created_at"] = now
    enriched["access_token_expires_at"] = token_expiry(now, enriched.get("expires_in"))
    enriched["refresh_token_expires_at"] = token_expiry(now, enriched.get("refresh_expires_in"))
    return enriched


def exchange_code_for_token(
    code: str,
    config: TikTokOAuthConfig | None = None,
    token_file: Path = DEFAULT_TOKEN_FILE,
) -> dict[str, Any]:
    config = config or oauth_config()
    if not config.configured:
        raise ValueError("TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET manquants dans .env")
    response = form_post(
        TOKEN_URL,
        {
            "client_key": config.client_key,
            "client_secret": config.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": config.redirect_uri,
        },
    )
    if response.get("error"):
        raise RuntimeError(f"TikTok OAuth error: {response.get('error_description') or response.get('error')}")
    if not response.get("access_token"):
        raise RuntimeError(f"Reponse OAuth TikTok incomplete: {response}")
    token = enrich_token_payload(response)
    save_json(token_file, token)
    return token


def refresh_access_token(
    config: TikTokOAuthConfig | None = None,
    token_file: Path = DEFAULT_TOKEN_FILE,
) -> dict[str, Any]:
    config = config or oauth_config()
    token = load_json(token_file, {})
    refresh_token = str(token.get("refresh_token") or "")
    if not refresh_token:
        raise RuntimeError("Aucun refresh_token TikTok stocke.")
    response = form_post(
        TOKEN_URL,
        {
            "client_key": config.client_key,
            "client_secret": config.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
    )
    if response.get("error"):
        raise RuntimeError(f"TikTok refresh error: {response.get('error_description') or response.get('error')}")
    refreshed = enrich_token_payload({**token, **response})
    save_json(token_file, refreshed)
    return refreshed


def load_token(token_file: Path = DEFAULT_TOKEN_FILE) -> dict[str, Any]:
    token = load_json(token_file, {})
    return token if isinstance(token, dict) else {}


def token_connected(token: dict[str, Any] | None = None) -> bool:
    token = token if token is not None else load_token()
    return bool(token.get("access_token") and token.get("refresh_token"))


def token_needs_refresh(token: dict[str, Any], now: int | None = None) -> bool:
    now = int(time.time()) if now is None else now
    expires_at = int(token.get("access_token_expires_at") or 0)
    return bool(expires_at and expires_at - now <= TOKEN_REFRESH_MARGIN_SECONDS)


def get_valid_access_token(token_file: Path = DEFAULT_TOKEN_FILE) -> str:
    token = load_token(token_file)
    if not token_connected(token):
        env_token = os.getenv("TIKTOK_ACCESS_TOKEN", "").strip()
        if env_token:
            return env_token
        raise RuntimeError("TikTok non connecte.")
    if token_needs_refresh(token):
        token = refresh_access_token(token_file=token_file)
    return str(token.get("access_token") or "")


def disconnect(token_file: Path = DEFAULT_TOKEN_FILE) -> None:
    try:
        token_file.unlink()
    except FileNotFoundError:
        return


def public_status() -> dict[str, Any]:
    config = oauth_config()
    token = load_token()
    return {
        "configured": config.configured,
        "connected": token_connected(token),
        "client_key_configured": bool(config.client_key),
        "client_secret_configured": bool(config.client_secret),
        "redirect_uri": config.redirect_uri,
        "local_callback_url": config.local_callback_url,
        "scopes": normalize_scope(config.scopes),
        "open_id": token.get("open_id"),
        "scope": token.get("scope"),
        "access_token_expires_at": token.get("access_token_expires_at"),
        "refresh_token_expires_at": token.get("refresh_token_expires_at"),
    }
