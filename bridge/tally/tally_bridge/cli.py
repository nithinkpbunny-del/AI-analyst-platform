"""Command line: ``tally-bridge pair|run|sync-now|status|companies|forget``.

tally-bridge pair K7QM-3XRP --server https://app.example.in
tally-bridge run                       (keep it running; start it at log-on, docs/SETUP_TALLY.md)
tally-bridge sync-now [--full]         (one sync, then exit)
tally-bridge status
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import logging.handlers
import os
import platform
import signal
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import IO

from tally_bridge import __version__
from tally_bridge.platform_client import (
    PlatformClient,
    PlatformRefused,
    PlatformUnavailable,
    Unauthorized,
)
from tally_bridge.service import Bridge
from tally_bridge.store import BridgeConfig, Store
from tally_bridge.tally_client import DEFAULT_URL, TallyClient, TallyUnavailable, check_tally_url
from tally_bridge.tally_xml import TallyError


def _setup_logging(store: Store, verbose: bool) -> None:
    store.home.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        store.log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler, console]
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    logging.getLogger("httpx").setLevel(
        logging.WARNING
    )  # request lines would show the address only


@contextlib.contextmanager
def single_instance(path: Path) -> Iterator[None]:
    """Only one bridge runs per PC user: two would send the same batches twice for nothing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle: IO[str] = path.open("a+")
    try:
        try:
            if sys.platform == "win32":  # pragma: no cover - Windows only
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise SystemExit("The Tally bridge is already running on this PC.") from error
        yield
    finally:
        handle.close()


def _clients(store: Store) -> tuple[BridgeConfig, TallyClient, PlatformClient]:
    if not store.paired():
        raise SystemExit(
            "This PC is not paired yet. Run: tally-bridge pair <CODE> --server <address>"
        )
    config = store.config()
    return config, TallyClient(config.tally_url), PlatformClient(config.server, store.token())


def cmd_pair(args: argparse.Namespace, store: Store) -> int:
    tally_url = check_tally_url(args.tally)
    device_name = (args.name or platform.node() or "Tally PC")[:80]
    client = PlatformClient(args.server)
    try:
        paired = client.pair(args.code.strip().upper(), device_name=device_name)
    except PlatformRefused as error:
        print(f"Pairing failed: {error.message}")
        return 2
    except PlatformUnavailable:
        print("The platform could not be reached. Check the address and the internet connection.")
        return 3
    finally:
        client.close()
    store.save_pairing(
        BridgeConfig(
            server=client.server,
            device_id=paired.device_id,
            tally_url=tally_url,
            company=args.company,
            device_name=device_name,
            connection_name=paired.connection_name,
            workspace_name=paired.workspace_name,
        ),
        paired.token,
    )
    print(f"Paired with {paired.workspace_name} ({paired.connection_name}).")
    print("Now run: tally-bridge run")
    return 0


def cmd_run(args: argparse.Namespace, store: Store) -> int:
    config, tally, platform_client = _clients(store)
    stop = threading.Event()
    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_: stop.set())
    with single_instance(store.lock_file):
        bridge = Bridge(tally, platform_client, store, company=config.company)
        logging.getLogger("tally_bridge").info(
            "Tally bridge %s running: Tally at %s, sending to %s",
            __version__,
            tally.url,
            config.server,
        )
        try:
            bridge.run(stop)
        finally:
            tally.close()
            platform_client.close()
    return 4 if bridge.unpaired else 0


def cmd_sync_now(args: argparse.Namespace, store: Store) -> int:
    config, tally, platform_client = _clients(store)
    with single_instance(store.lock_file):
        bridge = Bridge(tally, platform_client, store, company=config.company)
        try:
            done = bridge.sync_once(full=args.full, sleep=threading.Event().wait)
        except Unauthorized:
            print("The platform no longer accepts this PC. Pair it again.")
            return 4
        finally:
            tally.close()
            platform_client.close()
    if done:
        print(f"Sync finished ({bridge.status.last_sync_mode}): {bridge.status.records}")
        return 0
    print(f"Sync did not finish: {bridge.status.last_error}. It will continue next time.")
    return 1


def cmd_status(args: argparse.Namespace, store: Store) -> int:
    if not store.paired():
        print("Not paired.")
        return 1
    config = store.config()
    state = store.state()
    print(f"Paired with {config.workspace_name} ({config.connection_name}) at {config.server}")
    print(f"This PC: {config.device_name}; Tally at {config.tally_url}")
    print(f"Company: {config.company or 'the one open in Tally'}")
    print(
        f"Last sync: {state.last_sync_at or 'never'}; last full read: {state.last_full_sync_at or 'never'}"
    )
    if state.pending is not None:
        print(f"A {state.pending.mode} sync is in progress and continues on the next run.")
    return 0


def cmd_companies(args: argparse.Namespace, store: Store) -> int:
    tally = TallyClient(store.config().tally_url if store.paired() else args.tally)
    try:
        companies = tally.companies()
    except TallyUnavailable:
        print(
            "Tally is not reachable. Open Tally and turn on its XML server (docs/SETUP_TALLY.md)."
        )
        return 3
    except TallyError as error:
        print(f"Tally answered with an error: {error.message}")
        return 3
    finally:
        tally.close()
    for company in companies:
        print(company.name)
    return 0


def cmd_forget(args: argparse.Namespace, store: Store) -> int:
    store.forget()
    print("This PC forgot its pairing. Also unpair it on the Connections screen.")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="tally-bridge", description="Sends Tally data to your workspace."
    )
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--verbose", action="store_true")
    root.add_argument("--home", help="folder for the bridge's settings (default: per user)")
    sub = root.add_subparsers(dest="command", required=True)
    pair = sub.add_parser(
        "pair", help="pair this PC with a workspace using the code from Connections"
    )
    pair.add_argument("code")
    pair.add_argument("--server", required=True, help="the platform address, https://...")
    pair.add_argument(
        "--tally", default=DEFAULT_URL, help=f"Tally's XML server (default {DEFAULT_URL})"
    )
    pair.add_argument("--company", help="read this company (default: the one open in Tally)")
    pair.add_argument("--name", help="a name for this PC (default: the computer name)")
    sub.add_parser("run", help="keep sending Tally data (every 15 minutes and when asked)")
    sync_now = sub.add_parser("sync-now", help="one sync, then exit")
    sync_now.add_argument("--full", action="store_true", help="read everything again")
    sub.add_parser("status", help="show the pairing and the last sync")
    companies = sub.add_parser("companies", help="list the companies open in Tally")
    companies.add_argument("--tally", default=DEFAULT_URL)
    sub.add_parser("forget", help="remove the pairing from this PC")
    return root


COMMANDS = {
    "pair": cmd_pair,
    "run": cmd_run,
    "sync-now": cmd_sync_now,
    "status": cmd_status,
    "companies": cmd_companies,
    "forget": cmd_forget,
}


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    store = Store(Path(args.home) if args.home else None)
    _setup_logging(store, args.verbose)
    try:
        return COMMANDS[args.command](args, store)
    except ValueError as error:
        print(str(error))
        return 2


if __name__ == "__main__":  # pragma: no cover
    os.environ.setdefault("PYTHONUTF8", "1")
    sys.exit(main())
