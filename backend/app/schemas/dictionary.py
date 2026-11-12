"""Request and response bodies for the business dictionary (M7)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.dictionary.model import Dictionary
from app.schemas.auth import _Strict


class VersionOut(BaseModel):
    id: uuid.UUID
    version: int
    status: Literal["draft", "confirmed", "superseded"]
    origin: Literal["initial", "schema_change", "replacement", "redraft"]
    created_at: datetime
    confirmed_at: datetime | None
    confirmed_by_name: str | None
    # Items still waiting for the Owner (columns, metrics, joins).
    review_items: int
    # What changed against the dictionary this one continues (new/missing columns and tables, ...).
    diff: list[dict[str, Any]]
    # How the draft was made: the AI's status, suggestions dropped and why.
    notes: dict[str, Any]


class VersionContentOut(VersionOut):
    content: Dictionary


class DictionaryOut(BaseModel):
    # False until an Owner has confirmed a version: answers then say "dictionary not confirmed".
    confirmed: bool
    drafting: bool
    # The version answers use: the newest confirmed one (Owners); before any confirmation, the draft (others).
    active: VersionContentOut | None
    # The Owner's working copy (Owners only).
    draft: VersionContentOut | None
    versions: list[VersionOut]


class DraftRequest(_Strict):
    # Ask the AI again even if the same question was answered before.
    fresh_ai: bool = False


class DraftQueuedOut(BaseModel):
    drafting: bool = True


class ReplaceRequest(_Strict):
    from_connection_id: uuid.UUID
    to_connection_id: uuid.UUID
    # Delete the old connection (its stored copies and credentials) once the mappings have moved.
    remove_old: bool = True


class ReplaceOut(BaseModel):
    matched: list[tuple[str, str]] = Field(description="(old table, new table) pairs")
    unmatched: list[str] = Field(description="Old tables with no partner in the new connection")
    removed_old: bool
    draft: VersionContentOut
