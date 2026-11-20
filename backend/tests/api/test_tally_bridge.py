"""The bridge API (``/bridge/tally``, M16) and the Owner's Tally routes: pairing codes (one-time, expiring,
limited), device tokens (hashed, revocable, 401 when revoked), heartbeats, batches (validated, size-limited,
gzip-bombs refused, unknown fields dropped, idempotent, one company), completing a sync, and workspace isolation.

These routes are listed as ``DEVICE`` in the route matrix and tested here."""

from __future__ import annotations

import gzip
import json
import logging
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.models import AuditLog, Connection, TallyDevice, TallyRecord
from app.tally import pairing
from tests.conftest import FakeClock
from tests.tallybridge import PairedBridge, add_tally, bearer, pair
from tests.tenancy import World, build_world


@pytest.fixture
def world(db: DbSession, settings: Settings, clock: FakeClock) -> World:
    return build_world(db, settings, clock.now)


@pytest.fixture
def paired(client: TestClient, world: World, tmp_path: Path) -> PairedBridge:
    return pair(client, world, tmp_path / "bridge")


def batch(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "sync_id": str(uuid.UUID(int=1)),
        "seq": 0,
        "dataset": "ledgers",
        "mode": "full",
        "company": "UrbanCart Retail (demo)",
        "company_guid": "company-1",
        "records": [{"key": "L1", "fields": {"NAME": "Cash", "CLOSINGBALANCE": "-10.00"}}],
    }
    body.update(changes)
    return body


def complete_body(**datasets: int) -> dict[str, Any]:
    names = ("voucher_types", "groups", "ledgers", "stock_items", "vouchers")
    return {
        "mode": "full",
        "company": "UrbanCart Retail (demo)",
        "company_guid": "company-1",
        "alter_id_max": 10,
        "datasets": {
            n: {"batches": datasets.get(n, 0), "records": 0, "whole": True} for n in names
        },
    }


# ---------------------------------------------------------------------------------------------- pairing
class TestPairing:
    def test_add_tally_gives_a_one_time_code(self, client: TestClient, world: World) -> None:
        body = add_tally(client, world)
        code = body["pairing"]["code"]
        assert len(code) == 9 and code[4] == "-"
        assert body["pairing"]["server_url"] == "http://localhost:8000"
        assert body["connection"]["tally"]["state"] == "not_paired"

    def test_a_code_works_once(self, client: TestClient, world: World) -> None:
        code = add_tally(client, world)["pairing"]["code"]
        first = client.post(
            "/bridge/tally/pair",
            json={"code": code, "device_name": "PC", "bridge_version": "1.0.0"},
        )
        assert first.status_code == 201
        assert first.json()["token"].startswith("tlyd_")
        again = client.post(
            "/bridge/tally/pair",
            json={"code": code, "device_name": "PC", "bridge_version": "1.0.0"},
        )
        assert again.status_code == 400
        assert again.json()["error"]["code"] == "pairing_code_invalid"

    def test_lowercase_and_spaces_are_fine(self, client: TestClient, world: World) -> None:
        code = add_tally(client, world)["pairing"]["code"]
        messy = f" {code[:4].lower()} {code[5:].lower()} "
        response = client.post(
            "/bridge/tally/pair",
            json={"code": messy, "device_name": "PC", "bridge_version": "1.0.0"},
        )
        assert response.status_code == 201

    def test_an_expired_code_is_refused(
        self, client: TestClient, world: World, clock: FakeClock
    ) -> None:
        code = add_tally(client, world)["pairing"]["code"]
        clock.advance(minutes=11)
        response = client.post(
            "/bridge/tally/pair",
            json={"code": code, "device_name": "PC", "bridge_version": "1.0.0"},
        )
        assert response.status_code == 400

    def test_a_new_code_cancels_the_previous_one(self, client: TestClient, world: World) -> None:
        added = add_tally(client, world)
        old = added["pairing"]["code"]
        response = client.post(
            f"/connections/{added['connection']['id']}/tally/pairing-code",
            headers=world.headers("owner_a"),
        )
        assert response.status_code == 200
        refused = client.post(
            "/bridge/tally/pair",
            json={"code": old, "device_name": "PC", "bridge_version": "1.0.0"},
        )
        assert refused.status_code == 400
        accepted = client.post(
            "/bridge/tally/pair",
            json={"code": response.json()["code"], "device_name": "PC", "bridge_version": "1.0.0"},
        )
        assert accepted.status_code == 201

    def test_guessing_is_limited_per_address(self, client: TestClient) -> None:
        statuses = [
            client.post(
                "/bridge/tally/pair",
                json={"code": "AAAA-AAAA", "device_name": "PC", "bridge_version": "1.0.0"},
            ).status_code
            for _ in range(pairing.PAIR_ATTEMPTS + 1)
        ]
        assert statuses[:-1] == [400] * pairing.PAIR_ATTEMPTS
        assert statuses[-1] == 429

    def test_validation(self, client: TestClient) -> None:
        response = client.post("/bridge/tally/pair", json={"code": "X", "device_name": ""})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    def test_only_a_hash_of_the_token_is_stored_and_it_is_never_logged_or_audited(
        self,
        client: TestClient,
        world: World,
        db: DbSession,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.DEBUG)
        code = add_tally(client, world)["pairing"]["code"]
        token = client.post(
            "/bridge/tally/pair",
            json={"code": code, "device_name": "PC", "bridge_version": "1.0.0"},
        ).json()["token"]
        device = db.scalars(select(TallyDevice)).one()
        assert device.token_hash == pairing.token_hash(token)
        assert token not in json.dumps(device.status_json)
        assert token not in caplog.text and code not in caplog.text
        audit = [e for e in db.scalars(select(AuditLog)) if e.action == "connection.tally_paired"]
        assert len(audit) == 1
        assert token not in json.dumps(audit[0].details)

    def test_pairing_another_pc_revokes_the_first(
        self, client: TestClient, world: World, paired: PairedBridge
    ) -> None:
        code = client.post(
            f"/connections/{paired.connection_id}/tally/pairing-code",
            headers=world.headers("owner_a"),
        ).json()["code"]
        client.post(
            "/bridge/tally/pair",
            json={"code": code, "device_name": "NEW-PC", "bridge_version": "1.0.0"},
        )
        old = client.post("/bridge/tally/poll", json={}, headers=bearer(paired.token))
        assert old.status_code == 401


# ------------------------------------------------------------------------------------------ device tokens
class TestDeviceAuth:
    @pytest.mark.parametrize(
        "headers",
        [
            {},
            {"Authorization": "Bearer nope"},
            {"Authorization": "Basic dGVzdA=="},
            bearer("tlyd_" + "x" * 43),
        ],
    )
    def test_without_a_valid_token_every_route_is_401(
        self, client: TestClient, headers: dict[str, str]
    ) -> None:
        for path, body in (
            ("/bridge/tally/poll", {}),
            ("/bridge/tally/batches", batch()),
            (f"/bridge/tally/syncs/{uuid.UUID(int=1)}/complete", complete_body()),
        ):
            response = client.post(path, json=body, headers=headers)
            assert response.status_code == 401, path
            assert response.json()["error"]["code"] == "not_paired"

    def test_unpairing_revokes_at_once(
        self, client: TestClient, world: World, paired: PairedBridge, db: DbSession
    ) -> None:
        assert (
            client.post("/bridge/tally/poll", json={}, headers=bearer(paired.token)).status_code
            == 200
        )
        response = client.post(
            f"/connections/{paired.connection_id}/tally/unpair", headers=world.headers("owner_a")
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "needs_reauth"
        assert body["tally"]["state"] == "not_paired"
        assert (
            client.post("/bridge/tally/poll", json={}, headers=bearer(paired.token)).status_code
            == 401
        )
        assert (
            client.post(
                "/bridge/tally/batches", json=batch(), headers=bearer(paired.token)
            ).status_code
            == 401
        )

    def test_removing_the_connection_removes_the_device_and_its_rows(
        self, client: TestClient, world: World, paired: PairedBridge, db: DbSession
    ) -> None:
        paired.engine().sync()
        response = client.delete(
            f"/connections/{paired.connection_id}", headers=world.headers("owner_a")
        )
        assert response.status_code == 204
        db.expire_all()
        assert db.scalars(select(TallyDevice)).all() == []
        assert db.scalars(select(TallyRecord)).all() == []
        assert (
            client.post("/bridge/tally/poll", json={}, headers=bearer(paired.token)).status_code
            == 401
        )


# ------------------------------------------------------------------------------------------- heartbeat
class TestPoll:
    def test_the_heartbeat_is_stored_with_known_fields_only(
        self, client: TestClient, paired: PairedBridge, db: DbSession, clock: FakeClock
    ) -> None:
        clock.advance(minutes=3)
        response = client.post(
            "/bridge/tally/poll",
            json={
                "wait_seconds": 0,
                "status": {
                    "tally": "unreachable",
                    "company": "UrbanCart\x07",
                    "bridge_version": "1.0.1",
                    "anything_else": "dropped",
                },
            },
            headers=bearer(paired.token),
        )
        assert response.status_code == 200
        assert response.json() == {
            "refresh": False,
            "full": False,
            "settings": {
                "interval_minutes": 15,
                "full_sync_hours": 24,
                "max_batch_rows": 2000,
                "max_batch_bytes": 2_000_000,
                "history_months": 36,
                "poll_wait_seconds": 25,
            },
        }
        device = db.scalars(select(TallyDevice)).one()
        db.refresh(device)
        assert device.last_seen_at == clock.now
        assert device.bridge_version == "1.0.1"
        assert device.status_json["company"] == "UrbanCart"
        assert "anything_else" not in device.status_json

    def test_the_answer_can_only_say_read_again(
        self, client: TestClient, paired: PairedBridge
    ) -> None:
        pairing.request_refresh(client.app.state.redis, paired.connection_id, full=True)  # type: ignore[attr-defined]
        answer = client.post("/bridge/tally/poll", json={}, headers=bearer(paired.token)).json()
        assert set(answer) == {"refresh", "full", "settings"}
        assert answer["refresh"] is True and answer["full"] is True

    def test_validation(self, client: TestClient, paired: PairedBridge) -> None:
        response = client.post(
            "/bridge/tally/poll", json={"wait_seconds": 999}, headers=bearer(paired.token)
        )
        assert response.status_code == 422


# -------------------------------------------------------------------------------------------- batches
class TestBatches:
    def send(self, client: TestClient, paired: PairedBridge, body: dict[str, Any]) -> Any:
        return client.post("/bridge/tally/batches", json=body, headers=bearer(paired.token))

    def test_a_batch_is_stored_once(
        self, client: TestClient, paired: PairedBridge, db: DbSession
    ) -> None:
        first = self.send(client, paired, batch())
        assert first.json() == {"accepted": 1, "duplicate": False}
        again = self.send(client, paired, batch())
        assert again.json() == {"accepted": 0, "duplicate": True}
        assert len(db.scalars(select(TallyRecord)).all()) == 1

    def test_unknown_fields_are_dropped(
        self, client: TestClient, paired: PairedBridge, db: DbSession
    ) -> None:
        body = batch(
            records=[
                {
                    "key": "L1",
                    "fields": {"NAME": "Asha Rao", "LEDGERPHONE": "98450 00000", "EMAIL": "a@b.in"},
                }
            ]
        )
        assert self.send(client, paired, body).status_code == 200
        record = db.scalars(select(TallyRecord)).one()
        assert record.fields == {"NAME": "Asha Rao"}

    def test_rows_are_in_the_devices_workspace(
        self, client: TestClient, world: World, paired: PairedBridge, db: DbSession
    ) -> None:
        self.send(client, paired, batch())
        record = db.scalars(select(TallyRecord)).one()
        assert record.org_id == world.org_a.id
        assert record.connection_id == paired.connection_id

    @pytest.mark.parametrize(
        "change",
        [
            {"dataset": "users"},
            {"mode": "replace"},
            {"seq": -1},
            {"records": [{"key": "", "fields": {}}]},
            {"records": [{"key": "L1", "fields": {"name": "lowercase"}}]},
            {"records": [{"key": "L1", "fields": {"NAME": "x" * 513}}]},
            {"records": [{"key": "L1", "fields": {"NAME": 5}}]},
            {"extra": True},
        ],
    )
    def test_validation(
        self, client: TestClient, paired: PairedBridge, change: dict[str, Any]
    ) -> None:
        response = self.send(client, paired, batch(**change))
        assert response.status_code == 422, response.text

    def test_too_many_rows(self, client: TestClient, paired: PairedBridge) -> None:
        rows = [{"key": f"L{i}", "fields": {"NAME": f"L{i}"}} for i in range(2001)]
        response = self.send(client, paired, batch(records=rows))
        assert response.status_code == 413

    def test_too_many_bytes(self, client: TestClient, paired: PairedBridge) -> None:
        data = json.dumps(batch(records=[{"key": "L", "fields": {"NAME": "x" * 500}}] * 5000))
        response = client.post(
            "/bridge/tally/batches",
            content=data,
            headers={**bearer(paired.token), "Content-Type": "application/json"},
        )
        assert response.status_code == 413

    def test_gzip_is_accepted_and_a_gzip_bomb_is_not(
        self, client: TestClient, paired: PairedBridge
    ) -> None:
        packed = gzip.compress(json.dumps(batch()).encode())
        ok = client.post(
            "/bridge/tally/batches",
            content=packed,
            headers={**bearer(paired.token), "Content-Encoding": "gzip"},
        )
        assert ok.status_code == 200
        bomb = gzip.compress(b"{" + b" " * 50_000_000 + b"}")
        refused = client.post(
            "/bridge/tally/batches",
            content=bomb,
            headers={**bearer(paired.token), "Content-Encoding": "gzip"},
        )
        assert refused.status_code == 413
        odd = client.post(
            "/bridge/tally/batches",
            content=b"x",
            headers={**bearer(paired.token), "Content-Encoding": "br"},
        )
        assert odd.status_code == 415

    def test_another_company_is_refused(self, client: TestClient, paired: PairedBridge) -> None:
        assert self.send(client, paired, batch()).status_code == 200
        response = self.send(
            client,
            paired,
            batch(sync_id=str(uuid.UUID(int=2)), company="Other Books", company_guid="company-2"),
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "company_mismatch"

    def test_a_paused_connection_takes_nothing(
        self, client: TestClient, world: World, paired: PairedBridge
    ) -> None:
        client.patch(
            f"/connections/{paired.connection_id}",
            json={"paused": True},
            headers=world.headers("owner_a"),
        )
        response = self.send(client, paired, batch())
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "connection_paused"


# ------------------------------------------------------------------------------------------- complete
class TestComplete:
    def url(self) -> str:
        return f"/bridge/tally/syncs/{uuid.UUID(int=1)}/complete"

    def test_missing_batches_are_named(self, client: TestClient, paired: PairedBridge) -> None:
        client.post("/bridge/tally/batches", json=batch(), headers=bearer(paired.token))
        response = client.post(
            self.url(), json=complete_body(ledgers=3), headers=bearer(paired.token)
        )
        assert response.status_code == 409
        assert response.json()["error"]["fields"] == {"ledgers": "1,2"}

    def test_complete_twice_is_harmless(self, client: TestClient, paired: PairedBridge) -> None:
        client.post("/bridge/tally/batches", json=batch(), headers=bearer(paired.token))
        first = client.post(self.url(), json=complete_body(ledgers=1), headers=bearer(paired.token))
        assert first.json()["status"] == "complete"
        again = client.post(self.url(), json=complete_body(ledgers=1), headers=bearer(paired.token))
        assert again.json() == {"status": "already_complete", "removed": 0}
        late = client.post("/bridge/tally/batches", json=batch(seq=1), headers=bearer(paired.token))
        assert late.status_code == 409

    def test_vouchers_are_never_removed_after_an_incremental_read(
        self, client: TestClient, paired: PairedBridge, db: DbSession
    ) -> None:
        voucher = batch(
            dataset="vouchers",
            records=[{"key": "V1", "fields": {"GUID": "V1", "DATE": "20260701"}}],
        )
        client.post("/bridge/tally/batches", json=voucher, headers=bearer(paired.token))
        client.post(self.url(), json=complete_body(vouchers=1), headers=bearer(paired.token))
        later = str(uuid.UUID(int=3))
        body = complete_body()
        body["mode"] = "incremental"
        response = client.post(
            f"/bridge/tally/syncs/{later}/complete", json=body, headers=bearer(paired.token)
        )
        assert response.json()["removed"] == 0
        assert len(db.scalars(select(TallyRecord)).all()) == 1


# ---------------------------------------------------------------------------------------- Owner routes
class TestOwnerRoutes:
    def test_analysts_see_but_cannot_pair_or_unpair(
        self, client: TestClient, world: World, paired: PairedBridge
    ) -> None:
        listing = client.get("/connections", headers=world.headers("analyst_a")).json()
        tally = next(c for c in listing["connections"] if c["type"] == "tally")
        assert tally["tally"]["device_name"] == "ACCOUNTS-PC"
        for path in (
            f"/connections/{paired.connection_id}/tally/pairing-code",
            f"/connections/{paired.connection_id}/tally/unpair",
        ):
            assert client.post(path, headers=world.headers("analyst_a")).status_code == 403
        assert (
            client.post(
                "/connections/tally", json={}, headers=world.headers("analyst_a")
            ).status_code
            == 403
        )

    def test_tally_routes_refuse_other_kinds_of_connection(
        self, client: TestClient, world: World, db: DbSession
    ) -> None:
        other = Connection(
            org_id=world.org_a.id, type="razorpay", name="R", status="active", config={}
        )
        db.add(other)
        db.commit()
        response = client.post(
            f"/connections/{other.id}/tally/pairing-code", headers=world.headers("owner_a")
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "not_tally"

    def test_codes_are_limited(self, client: TestClient, world: World) -> None:
        connection_id = add_tally(client, world)["connection"]["id"]
        statuses = [
            client.post(
                f"/connections/{connection_id}/tally/pairing-code", headers=world.headers("owner_a")
            ).status_code
            for _ in range(20)
        ]
        assert statuses[-1] == 429

    def test_test_reports_the_pc_and_never_marks_the_connection_failed(
        self, client: TestClient, world: World, paired: PairedBridge, clock: FakeClock
    ) -> None:
        clock.advance(
            minutes=6
        )  # past the 5 minutes without a heartbeat (and within the token's 10)
        response = client.post(
            f"/connections/{paired.connection_id}/test", headers=world.headers("owner_a")
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "offline" in body["message"]
        detail = client.get(
            f"/connections/{paired.connection_id}", headers=world.headers("owner_a")
        ).json()
        assert detail["status"] == "active"

    def test_another_workspace_cannot_see_or_touch_it(
        self, client: TestClient, world: World, paired: PairedBridge
    ) -> None:
        for method, path in (
            ("GET", f"/connections/{paired.connection_id}"),
            ("POST", f"/connections/{paired.connection_id}/tally/pairing-code"),
            ("POST", f"/connections/{paired.connection_id}/tally/unpair"),
            ("POST", f"/connections/{paired.connection_id}/refresh"),
        ):
            response = client.request(method, path, headers=world.headers("owner_b"))
            assert response.status_code == 404, path
        listing = client.get("/connections", headers=world.headers("owner_b")).text
        assert "ACCOUNTS-PC" not in listing
