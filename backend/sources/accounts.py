"""Alias module re-exporting read_account and read_account_history."""

from __future__ import annotations

from backend.sources.account import read_account, read_account_history

__all__ = ["read_account", "read_account_history"]

