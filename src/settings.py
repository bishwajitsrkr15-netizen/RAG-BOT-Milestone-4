"""Read application settings from process, Streamlit secrets, or local .env."""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError


def read_setting(key_name: str, project_root: Path) -> str | None:
    environment_value = os.environ.get(key_name)
    if environment_value:
        return environment_value

    try:
        secret_value = st.secrets.get(key_name)
    except StreamlitSecretNotFoundError:
        secret_value = None
    if isinstance(secret_value, str) and secret_value:
        return secret_value

    try:
        lines = (project_root / ".env").read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if name.strip() == key_name:
            value = value.strip().strip("\"'")
            return value or None
    return None
