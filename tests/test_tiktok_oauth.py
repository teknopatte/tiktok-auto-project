import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from src.tiktok_oauth import (
    TikTokOAuthConfig,
    build_authorization_url,
    create_state,
    exchange_code_for_token,
    public_status,
    verify_state,
)


class TikTokOAuthTests(unittest.TestCase):
    def test_build_authorization_url_uses_v2_endpoint_and_scopes(self):
        config = TikTokOAuthConfig(
            client_key="client-key",
            client_secret="secret",
            redirect_uri="https://tiktok.aemour.com/tiktok/callback/",
            scopes="user.info.basic, video.upload",
            local_callback_url="http://127.0.0.1:8787/tiktok/callback",
        )
        url = build_authorization_url(config, state="csrf-state")
        parsed = urlparse(url)
        query = parse_qs(parsed.query)

        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "www.tiktok.com")
        self.assertEqual(parsed.path, "/v2/auth/authorize/")
        self.assertEqual(query["client_key"], ["client-key"])
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["scope"], ["user.info.basic,video.upload"])
        self.assertEqual(query["redirect_uri"], ["https://tiktok.aemour.com/tiktok/callback/"])
        self.assertEqual(query["state"], ["csrf-state"])

    def test_state_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "oauth_state.json"
            state = create_state(state_file)

            self.assertTrue(verify_state(state, state_file))
            self.assertFalse(verify_state("wrong", state_file))

    def test_exchange_code_persists_enriched_token(self):
        config = TikTokOAuthConfig(
            client_key="client-key",
            client_secret="secret",
            redirect_uri="https://tiktok.aemour.com/tiktok/callback/",
            scopes="user.info.basic",
            local_callback_url="http://127.0.0.1:8787/tiktok/callback",
        )
        response = {
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_in": 86400,
            "refresh_expires_in": 31536000,
            "open_id": "open-id",
            "scope": "user.info.basic",
            "token_type": "Bearer",
        }
        with tempfile.TemporaryDirectory() as tmp:
            token_file = Path(tmp) / "token.json"
            with patch("src.tiktok_oauth.form_post", return_value=response):
                token = exchange_code_for_token("code", config, token_file)

            self.assertEqual(token["access_token"], "access")
            self.assertEqual(token["refresh_token"], "refresh")
            self.assertTrue(token_file.exists())
            self.assertIn("access_token_expires_at", token)

    def test_public_status_reports_missing_config(self):
        with patch.dict("os.environ", {}, clear=True), patch("src.tiktok_oauth.load_token", return_value={}):
            status = public_status()

        self.assertFalse(status["configured"])
        self.assertFalse(status["connected"])
        self.assertFalse(status["client_key_configured"])


if __name__ == "__main__":
    unittest.main()
