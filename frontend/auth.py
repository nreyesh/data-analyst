"""Firebase Identity Toolkit authentication and server-side RBAC claim resolution."""

from __future__ import annotations

import hmac
import json
import logging
import os
from typing import Any
import requests

from agent.state import UserContext

logger = logging.getLogger("frontend.auth")

FIREBASE_SIGNIN_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
FIREBASE_LOOKUP_URL = "https://identitytoolkit.googleapis.com/v1/accounts:lookup"

# Default local development / smoke-test accounts (server-side only; never exposed to client override)
DEFAULT_DEV_ACCOUNTS: dict[str, dict[str, Any]] = {
    "admin@condominio.cl": {
        "uid": "uid_admin_001",
        "password": os.environ.get("DEV_ADMIN_PASSWORD", "admin123"),
        "role": "admin",
        "unit_code": None,
    },
    "residente504b@condominio.cl": {
        "uid": "uid_res_504b",
        "password": os.environ.get("DEV_RESIDENT_PASSWORD", "residente123"),
        "role": "resident",
        "unit_code": "B-504",
    },
    "residente102a@condominio.cl": {
        "uid": "uid_res_102a",
        "password": os.environ.get("DEV_RESIDENT_PASSWORD", "residente123"),
        "role": "resident",
        "unit_code": "A-102",
    },
}


def _load_server_role_directory() -> dict[str, dict[str, Any]]:
    """Load server-side user role and unit directory from environment or defaults."""
    directory = {k.lower(): dict(v) for k, v in DEFAULT_DEV_ACCOUNTS.items()}
    raw_json = os.environ.get("DEV_USERS_JSON")
    if raw_json:
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, dict):
                for email, info in parsed.items():
                    if isinstance(info, dict):
                        directory[email.strip().lower()] = info
        except Exception as exc:
            logger.warning("Failed to parse DEV_USERS_JSON: %s", exc)
    return directory


def verify_user_credentials(
    username: str,
    password: str,
    firebase_api_key: str | None = None,
    http_client: Any | None = None,
) -> UserContext | None:
    """Verify email/password via Firebase Auth (or server-side dev directory) and return immutable UserContext.

    Args:
        username: User email address.
        password: User password.
        firebase_api_key: Optional Firebase Web API Key override (defaults to FIREBASE_API_KEY env var).
        http_client: Optional HTTP client (requests or mock) for unit testing.

    Returns:
        Verified UserContext if credentials are valid and authorized, else None.
    """
    email = (username or "").strip().lower()
    if not email or not password:
        return None

    api_key = firebase_api_key if firebase_api_key is not None else os.environ.get("FIREBASE_API_KEY")
    role_directory = _load_server_role_directory()

    # 1. Firebase Identity Toolkit REST verification when FIREBASE_API_KEY is configured
    if api_key:
        http_post = http_client.post if http_client is not None else requests.post
        try:
            res = http_post(
                f"{FIREBASE_SIGNIN_URL}?key={api_key}",
                json={
                    "email": email,
                    "password": password,
                    "returnSecureToken": True,
                },
                timeout=5.0,
            )
            if res.status_code != 200:
                logger.warning("Firebase authentication rejected for email '%s' (HTTP %s)", email, res.status_code)
                return None

            auth_data = res.json()
            uid = auth_data.get("localId") or f"fb_{email}"
            id_token = auth_data.get("idToken")

            # Extract server-side custom claims from Firebase lookup or server role directory
            claims: dict[str, Any] = {}
            if isinstance(auth_data.get("customAttributes"), str):
                try:
                    claims = json.loads(auth_data["customAttributes"])
                except Exception:
                    claims = {}
            elif id_token:
                try:
                    lookup_res = http_post(
                        f"{FIREBASE_LOOKUP_URL}?key={api_key}",
                        json={"idToken": id_token},
                        timeout=5.0,
                    )
                    if lookup_res.status_code == 200:
                        users_list = lookup_res.json().get("users", [])
                        if users_list and isinstance(users_list[0].get("customAttributes"), str):
                            claims = json.loads(users_list[0]["customAttributes"])
                except Exception:
                    claims = {}

            dir_entry = role_directory.get(email, {})
            role = claims.get("role") or dir_entry.get("role")
            unit_code = claims.get("unit_code") or dir_entry.get("unit_code")

            if role not in ("admin", "resident"):
                logger.warning("User '%s' has no valid role claim assigned on server.", email)
                return None

            if role == "resident" and not unit_code:
                logger.warning("Resident '%s' is missing mandatory unit_code claim.", email)
                return None

            return UserContext(
                uid=uid,
                email=email,
                role=role,
                unit_code=unit_code if role == "resident" else None,
            )
        except Exception as exc:
            logger.error("Firebase authentication error for '%s': %s", email, exc)
            return None

    # 2. Offline / Local Dev verification against server-side directory
    account = role_directory.get(email)
    if not account:
        return None

    expected_password = str(account.get("password", ""))
    if not expected_password or not hmac.compare_digest(password, expected_password):
        return None

    role = account.get("role")
    unit_code = account.get("unit_code")
    if role not in ("admin", "resident"):
        return None

    try:
        return UserContext(
            uid=str(account.get("uid") or f"dev_{email}"),
            email=email,
            role=role,
            unit_code=unit_code if role == "resident" else None,
        )
    except ValueError as exc:
        logger.warning("Invalid account configuration for '%s': %s", email, exc)
        return None
