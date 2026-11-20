"""Test helpers for the Tally bridge (M16): a Tally connection with a pairing code, and the **real** bridge code
(``bridge/tally/tally_bridge``) talking to the API through the test client and reading the Tally stand-in."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient
from tally_bridge.dev.stand_in import StandInTally, StandInTallyTransport, build_company
from tally_bridge.platform_client import PlatformClient, Settings
from tally_bridge.service import Bridge
from tally_bridge.store import BridgeConfig, Store
from tally_bridge.sync import SyncEngine
from tally_bridge.tally_client import TallyClient

from tests.tenancy import World

SERVER = "http://localhost:8000"


class AppTransport(httpx.BaseTransport):
    """The bridge's own HTTP client reaching the app in-process, through the test client."""

    def __init__(self, client: TestClient) -> None:
        self._client = client

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self._client.request(
            request.method,
            str(request.url),
            content=request.read(),
            headers={k: v for k, v in request.headers.items() if k.lower() != "host"},
        )
        return httpx.Response(
            response.status_code,
            headers={k: v for k, v in response.headers.items() if k.lower() != "content-length"},
            content=response.content,
        )


def transport(client: TestClient) -> httpx.BaseTransport:
    return AppTransport(client)


def add_tally(
    client: TestClient, world: World, who: str = "owner_a", name: str = "Tally"
) -> dict[str, Any]:
    response = client.post("/connections/tally", json={"name": name}, headers=world.headers(who))
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


@dataclass
class PairedBridge:
    connection_id: uuid.UUID
    token: str
    store: Store
    tally: StandInTally
    platform: PlatformClient
    tally_client: TallyClient

    def engine(self, **kwargs: Any) -> SyncEngine:
        settings = kwargs.pop("settings", Settings(max_batch_rows=200))
        return SyncEngine(
            self.tally_client,
            self.platform,
            self.store,
            settings=settings,
            sleep=lambda _: None,
            **kwargs,
        )

    def bridge(self, **kwargs: Any) -> Bridge:
        return Bridge(self.tally_client, self.platform, self.store, **kwargs)


def pair(
    client: TestClient,
    world: World,
    home: Path,
    *,
    who: str = "owner_a",
    stand_in: StandInTally | None = None,
    name: str = "Tally",
) -> PairedBridge:
    added = add_tally(client, world, who, name)
    tally = stand_in or StandInTally(build_company(None))
    platform = PlatformClient(SERVER, transport=transport(client))
    paired = platform.pair(added["pairing"]["code"], device_name="ACCOUNTS-PC")
    platform.close()
    store = Store(home)
    store.save_pairing(
        BridgeConfig(server=SERVER, device_id=paired.device_id, tally_url="http://127.0.0.1:9000"),
        paired.token,
    )
    return PairedBridge(
        connection_id=uuid.UUID(added["connection"]["id"]),
        token=paired.token,
        store=store,
        tally=tally,
        platform=PlatformClient(SERVER, paired.token, transport=transport(client)),
        tally_client=TallyClient(transport=StandInTallyTransport(tally)),
    )


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
