"""What the bridge keeps on the PC, in one folder (``%LOCALAPPDATA%\\TallyBridge`` on Windows,
``~/.tally-bridge`` elsewhere, or ``TALLY_BRIDGE_HOME``):

* ``config.json``: the platform address, the device id, the Tally address and company. Not secret.
* ``token.bin``: the device token, protected with Windows DPAPI (only this Windows user can read it); elsewhere a
  file only the owner can read.
* ``state.json``: sync progress, saved after every accepted batch, so a restart, sleep or lost network continues
  where it stopped.
* ``outbox.json``: the one batch being sent. It is written before sending and removed after the platform accepted
  it, so a batch is re-sent exactly as it was (the platform ignores a batch it already has).

Every write goes to a temporary file first and then replaces the old one, so a crash never leaves half a file.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def default_home() -> Path:
    configured = os.environ.get("TALLY_BRIDGE_HOME")
    if configured:
        return Path(configured)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "TallyBridge"
    return Path.home() / ".tally-bridge"


def _write_atomic(path: Path, data: bytes, *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    fd = os.open(tmp, flags, 0o600 if private else 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


# ------------------------------------------------------------------------------------- the device token
def _dpapi(data: bytes, *, protect: bool) -> bytes:  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(data, len(data))
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    target = Blob()
    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = function(
        ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(target)
    )  # 0x1: CRYPTPROTECT_UI_FORBIDDEN
    if not ok:
        raise OSError("Windows could not protect the device token (DPAPI)")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel32.LocalFree(target.pbData)


def protect(token: str) -> bytes:
    raw = token.encode("utf-8")
    if sys.platform == "win32":  # pragma: no cover - Windows only
        return b"dpapi:" + base64.b64encode(_dpapi(raw, protect=True))
    return b"plain:" + base64.b64encode(raw)


def unprotect(data: bytes) -> str:
    kind, _, body = data.partition(b":")
    raw = base64.b64decode(body)
    if kind == b"dpapi":  # pragma: no cover - Windows only
        return _dpapi(raw, protect=False).decode("utf-8")
    if kind == b"plain":
        return raw.decode("utf-8")
    raise ValueError("unknown token format")


# ------------------------------------------------------------------------------------------ the files
@dataclass
class BridgeConfig:
    server: str
    device_id: str
    tally_url: str
    company: str | None = None  # None: the company Tally shows
    device_name: str = ""
    connection_name: str = ""
    workspace_name: str = ""


@dataclass
class DatasetProgress:
    batches: int = 0  # batches the platform accepted (the next one gets this number)
    records: int = 0
    finished: bool = False
    next_from: str | None = None  # vouchers of a full read: the next month to read (YYYY-MM-DD)


@dataclass
class PendingSync:
    sync_id: str
    mode: str  # "full" or "incremental"
    started_at: str
    company: str
    company_guid: str
    alter_floor: int | None = None  # incremental: vouchers altered after this alter id
    alter_id_max: int = 0
    progress: dict[str, DatasetProgress] = field(default_factory=dict)

    def dataset(self, name: str) -> DatasetProgress:
        return self.progress.setdefault(name, DatasetProgress())


@dataclass
class SyncState:
    company_guid: str | None = None
    last_alter_id: int | None = None
    last_sync_at: str | None = None
    last_full_sync_at: str | None = None
    pending: PendingSync | None = None

    def as_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> SyncState:
        pending = raw.get("pending")
        state = cls(
            company_guid=raw.get("company_guid"),
            last_alter_id=raw.get("last_alter_id"),
            last_sync_at=raw.get("last_sync_at"),
            last_full_sync_at=raw.get("last_full_sync_at"),
        )
        if isinstance(pending, dict):
            progress = {
                str(k): DatasetProgress(**v)
                for k, v in (pending.get("progress") or {}).items()
                if isinstance(v, dict)
            }
            state.pending = PendingSync(
                **{k: v for k, v in pending.items() if k != "progress"}, progress=progress
            )
        return state


class Store:
    def __init__(self, home: Path | None = None) -> None:
        self.home = home or default_home()

    @property
    def _config(self) -> Path:
        return self.home / "config.json"

    @property
    def _token(self) -> Path:
        return self.home / "token.bin"

    @property
    def _state(self) -> Path:
        return self.home / "state.json"

    @property
    def _outbox(self) -> Path:
        return self.home / "outbox.json"

    @property
    def log_file(self) -> Path:
        return self.home / "bridge.log"

    @property
    def lock_file(self) -> Path:
        return self.home / "bridge.lock"

    # -------------------------------------------------------------------------------------- pairing
    def paired(self) -> bool:
        return self._config.exists() and self._token.exists()

    def save_pairing(self, config: BridgeConfig, token: str) -> None:
        _write_atomic(self._token, protect(token), private=True)
        self.save_config(config)
        self.save_state(SyncState())
        self.clear_outbox()

    def save_config(self, config: BridgeConfig) -> None:
        _write_atomic(self._config, json.dumps(asdict(config), indent=2).encode("utf-8"))

    def config(self) -> BridgeConfig:
        return BridgeConfig(**json.loads(self._config.read_text("utf-8")))

    def token(self) -> str:
        return unprotect(self._token.read_bytes())

    def forget(self) -> None:
        for path in (self._token, self._config, self._state, self._outbox):
            with contextlib.suppress(FileNotFoundError):
                path.unlink()

    # ---------------------------------------------------------------------------------- progress
    def state(self) -> SyncState:
        try:
            return SyncState.from_json(json.loads(self._state.read_text("utf-8")))
        except (FileNotFoundError, ValueError, TypeError):
            return SyncState()

    def save_state(self, state: SyncState) -> None:
        _write_atomic(self._state, json.dumps(state.as_json(), indent=2).encode("utf-8"))

    def outbox(self) -> dict[str, Any] | None:
        try:
            raw = json.loads(self._outbox.read_text("utf-8"))
        except (FileNotFoundError, ValueError):
            return None
        return raw if isinstance(raw, dict) else None

    def save_outbox(self, batch: dict[str, Any]) -> None:
        _write_atomic(self._outbox, json.dumps(batch, ensure_ascii=False).encode("utf-8"))

    def clear_outbox(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self._outbox.unlink()
