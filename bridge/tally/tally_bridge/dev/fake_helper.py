"""The fake helper: the real bridge, reading the Tally stand-in in-process, for trying M16 without Windows.

    python -m tally_bridge.dev.fake_helper --server http://localhost:8000 --code K7QM-3XRP
        [--sample] [--live 60] [--flaky 0.3] [--offline-after 300] [--tally-closed-after 120]

* ``--code``: the pairing code from Connections > Add data > Connect Tally (pairs, then runs). Without it the
  helper uses the pairing saved in ``--home`` (default ``~/.tally-bridge-dev``).
* ``--live N``: the stand-in adds a sale every N seconds (incremental syncs then send something new).
* ``--flaky P``: each request to the platform fails with probability P (backoff and resuming in action).
* ``--offline-after N``: after N seconds the helper goes silent, like a PC that went to sleep. The Connections
  screen then shows "Tally PC offline since ...".
* ``--tally-closed-after N``: after N seconds the stand-in stops answering, like Tally being closed.
"""

from __future__ import annotations

import argparse
import logging
import random
import threading
import time
from pathlib import Path

import httpx

from tally_bridge.cli import _setup_logging
from tally_bridge.dev.stand_in import (
    FIXTURES,
    StandInTally,
    StandInTallyTransport,
    build_company,
    live,
)
from tally_bridge.platform_client import PlatformClient, PlatformRefused
from tally_bridge.service import Bridge
from tally_bridge.store import BridgeConfig, Store
from tally_bridge.tally_client import DEFAULT_URL, TallyClient

logger = logging.getLogger("tally_bridge")


class FlakyTransport(httpx.BaseTransport):
    """Real HTTP to the platform, except that some requests fail as if the network dropped."""

    def __init__(self, failure_rate: float, offline: threading.Event) -> None:
        self._inner = httpx.HTTPTransport()
        self._rate = failure_rate
        self._offline = offline
        self._random = random.Random(5)  # noqa: S311 - simulation only

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if self._offline.is_set():
            raise httpx.ConnectError("offline (simulated)", request=request)
        if self._random.random() < self._rate:
            raise httpx.ConnectError("network dropped (simulated)", request=request)
        return self._inner.handle_request(request)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the real bridge against the Tally stand-in.")
    parser.add_argument("--server", default="http://localhost:8000")
    parser.add_argument("--code", help="a pairing code from the Connections screen")
    parser.add_argument("--home", default=str(Path.home() / ".tally-bridge-dev"))
    parser.add_argument(
        "--sample", action="store_true", help="a small company instead of the demo one"
    )
    parser.add_argument("--live", type=float, default=0)
    parser.add_argument("--flaky", type=float, default=0.0)
    parser.add_argument("--offline-after", type=float, default=0)
    parser.add_argument("--tally-closed-after", type=float, default=0)
    parser.add_argument(
        "--interval", type=int, default=0, help="minutes between syncs (default 15)"
    )
    args = parser.parse_args(argv)

    store = Store(Path(args.home))
    _setup_logging(store, verbose=False)
    tally_stand_in = StandInTally(build_company(None if args.sample else FIXTURES))
    offline = threading.Event()
    transport = FlakyTransport(args.flaky, offline)
    if args.code:
        client = PlatformClient(args.server, transport=httpx.HTTPTransport())
        try:
            paired = client.pair(
                args.code.strip().upper(), device_name="Fake Tally PC (development)"
            )
        except PlatformRefused as error:
            print(f"Pairing failed: {error.message}")
            return 2
        finally:
            client.close()
        store.save_pairing(
            BridgeConfig(
                server=client.server,
                device_id=paired.device_id,
                tally_url=DEFAULT_URL,
                device_name="Fake Tally PC (development)",
                connection_name=paired.connection_name,
                workspace_name=paired.workspace_name,
            ),
            paired.token,
        )
        print(f"Paired with {paired.workspace_name} ({paired.connection_name}).")
    if not store.paired():
        print("Not paired: pass --code with a pairing code from the Connections screen.")
        return 1
    config = store.config()
    tally = TallyClient(DEFAULT_URL, transport=StandInTallyTransport(tally_stand_in))
    platform = PlatformClient(config.server, store.token(), transport=transport)
    bridge = Bridge(tally, platform, store)
    if args.interval:
        from dataclasses import replace

        bridge.settings = replace(bridge.settings, interval_minutes=args.interval)
    stop = threading.Event()
    if args.live > 0:
        threading.Thread(target=live, args=(tally_stand_in, args.live, stop), daemon=True).start()
    started = time.monotonic()

    def watcher() -> None:
        while not stop.wait(1.0):
            elapsed = time.monotonic() - started
            if args.offline_after and elapsed >= args.offline_after and not offline.is_set():
                logger.warning("Simulating a PC that went offline (nothing is sent any more).")
                offline.set()
            if args.tally_closed_after and elapsed >= args.tally_closed_after:
                if not tally_stand_in.offline:
                    logger.warning("Simulating Tally being closed.")
                tally_stand_in.offline = True

    threading.Thread(target=watcher, daemon=True).start()
    print(
        f"Fake helper running for {config.workspace_name}: {tally_stand_in.company.name}. Ctrl+C stops."
    )
    try:
        bridge.run(stop)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        tally.close()
        platform.close()
    if tally_stand_in.refused:
        print(
            f"WARNING: {len(tally_stand_in.refused)} request(s) that would change Tally were refused."
        )
        return 5
    return 4 if bridge.unpaired else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
