"""Optional Firebase Realtime Database integration.

The backend runs without Firebase credentials. When FIREBASE_CREDENTIALS_JSON
and FIREBASE_DATABASE_URL are configured, alerts and route decisions are pushed
to Realtime Database for hosted dashboards or mobile dispatch clients.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_enabled = False
_app: Any = None


def initialize_firebase() -> bool:
    """Initialize Firebase Admin SDK if credentials are configured."""
    global _enabled, _app
    if _enabled:
        return True

    database_url = os.getenv("FIREBASE_DATABASE_URL", "").strip()
    credentials_json = os.getenv("FIREBASE_CREDENTIALS_JSON", "").strip()
    credentials_path = os.getenv("FIREBASE_CREDENTIALS_PATH", "").strip()

    if not database_url or not (credentials_json or credentials_path):
        logger.info("Firebase disabled; set FIREBASE_DATABASE_URL and credentials to enable.")
        return False

    try:
        import firebase_admin
        from firebase_admin import credentials

        try:
            _app = firebase_admin.get_app()
        except ValueError:
            if credentials_json:
                cred_dict = json.loads(credentials_json)
                cred = credentials.Certificate(cred_dict)
            else:
                path = Path(credentials_path).expanduser()
                cred = credentials.Certificate(str(path))
            _app = firebase_admin.initialize_app(cred, {"databaseURL": database_url})
        _enabled = True
        logger.info("Firebase Realtime Database enabled.")
        return True
    except Exception as exc:
        logger.warning("Firebase initialization skipped: %s", exc)
        _enabled = False
        return False


def firebase_enabled() -> bool:
    return _enabled or initialize_firebase()


def push_json(path: str, payload: Dict[str, Any]) -> Optional[str]:
    """Push a JSON payload to Firebase and return the generated key."""
    if not firebase_enabled():
        return None
    try:
        from firebase_admin import db

        ref = db.reference(path)
        result = ref.push(payload)
        return getattr(result, "key", None)
    except Exception as exc:
        logger.warning("Firebase push failed for %s: %s", path, exc)
        return None


async def push_json_async(path: str, payload: Dict[str, Any]) -> Optional[str]:
    """Async-compatible wrapper used by the risk scheduler."""
    return push_json(path, payload)
