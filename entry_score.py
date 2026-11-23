"""Shared inclusive evidence threshold for processes reading the same deployment config."""
import os
from pathlib import Path

from dotenv import dotenv_values

def entry_threshold():
    value = os.getenv("MIN_ENTRY_SCORE")
    if value is None:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        if get_script_run_ctx(suppress_warning=True) is not None:
            import streamlit as st
            try:
                value = st.secrets.get("MIN_ENTRY_SCORE")
            except FileNotFoundError:
                pass
    if value is None:
        value = dotenv_values(Path(__file__).with_name(".env")).get("MIN_ENTRY_SCORE", "60")
    try:
        score = int(value)
        if 1 <= score <= 100:
            return score
    except (ValueError, TypeError):
        pass
    raise ValueError("MIN_ENTRY_SCORE must be an integer from 1 to 100.")

MIN_ENTRY_SCORE = entry_threshold()
