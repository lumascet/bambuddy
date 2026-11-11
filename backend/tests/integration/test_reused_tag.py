"""Spools carrying a reused Bambu Lab tag.

An old Bambu Lab tag stuck on a third-party spool keeps reporting the filament
it was written for (here: Bambu PLA red, 30 % left). With ``reused_tag`` set,
the tag only identifies the spool: the slot is assigned to the spool the tag
is linked to, no phantom spool is created from the tag's data, the assignment
survives the mismatching material/colour, and the AMS remain% -- the tag's,
not this spool's -- is never synced into the spool's weight.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.services.usage_tracker import _active_sessions

TAG_UID = "A1B2C3D4E5F60718"
TRAY_UUID = "0123456789ABCDEF0123456789ABCDEF"

# What the reused tag says: Bambu PLA Basic red, 30 % left.
TAG_TRAY = {
    "id": 0,
    "tray_type": "PLA",
    "tray_color": "FF0000FF",
    "tray_info_idx": "GFA00",
    "tray_sub_brands": "PLA Basic",
    "tag_uid": TAG_UID,
    "tray_uuid": TRAY_UUID,
    "remain": 30,
}


@pytest.fixture(autouse=True)
def _no_sessions():
    _active_sessions.clear()
    yield
    _active_sessions.clear()


async def _reused_tag_spool(db_session, *, reused: bool = True) -> Spool:
    """The real spool: black PETG from another brand, 100 g used."""
    spool = Spool(
        material="PETG",
        brand="Sunlu",
        rgba="000000FF",
        label_weight=1000,
        weight_used=100,
        tag_uid=TAG_UID,
        tray_uuid=TRAY_UUID,
        reused_tag=reused,
    )
    db_session.add(spool)
    await db_session.commit()
    return spool


async def _push(test_engine, printer_id: int, ams_data: list) -> list[dict]:
    """Feed one AMS push through on_ams_change; returns what was broadcast."""
    from backend.app.main import _unknown_tag_last_broadcast, on_ams_change

    _unknown_tag_last_broadcast.clear()

    status = MagicMock()
    status.raw_data = {"ams": {"ams": ams_data}}
    status.state = "IDLE"
    status.connected = True

    real_db = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    with (
        patch("backend.app.main.async_session", real_db),
        patch("backend.app.core.database.async_session", real_db),
        patch("backend.app.main.printer_manager") as mock_pm,
        patch("backend.app.main.mqtt_relay") as mock_relay,
        patch("backend.app.main.ws_manager") as mock_ws,
    ):
        mock_pm.get_printer.return_value = MagicMock(name="X1C", serial_number="REUSEDTAG")
        mock_pm.get_status.return_value = status
        mock_pm.get_client.return_value = MagicMock()
        mock_pm.get_model.return_value = "X1C"
        mock_relay.on_ams_change = AsyncMock()
        mock_ws.send_printer_status = AsyncMock()
        mock_ws.broadcast = AsyncMock()
        await on_ams_change(printer_id, ams_data)
    return [c.args[0] for c in mock_ws.broadcast.call_args_list]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_tag_assigns_the_linked_spool_and_creates_none(test_engine, db_session, printer_factory):
    printer = await printer_factory(name="X1C")
    spool_id = (await _reused_tag_spool(db_session)).id
    printer_id = printer.id

    await _push(test_engine, printer_id, [{"id": 0, "tray": [dict(TAG_TRAY)]}])

    db_session.expire_all()
    assignment = await db_session.scalar(select(SpoolAssignment).where(SpoolAssignment.printer_id == printer_id))
    assert assignment is not None and assignment.spool_id == spool_id
    assert await db_session.scalar(select(func.count()).select_from(Spool)) == 1


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("reused, expected_used", [(True, 100), (False, 700)])
async def test_the_tags_remain_is_not_synced_into_the_spool(
    test_engine, db_session, printer_factory, reused, expected_used
):
    """The control case (``reused=False``) shows the push does sync otherwise."""
    printer = await printer_factory(name="X1C")
    spool = await _reused_tag_spool(db_session, reused=reused)
    db_session.add(
        SpoolAssignment(
            spool_id=spool.id,
            printer_id=printer.id,
            ams_id=0,
            tray_id=0,
            fingerprint_color="FF0000FF",
            fingerprint_type="PLA",
        )
    )
    await db_session.commit()
    spool_id, printer_id = spool.id, printer.id

    await _push(test_engine, printer_id, [{"id": 0, "tray": [dict(TAG_TRAY)]}])

    db_session.expire_all()
    assert (await db_session.get(Spool, spool_id)).weight_used == expected_used
    # The tag's PLA/red never unlinks the PETG/black spool it identifies.
    assignment = await db_session.scalar(select(SpoolAssignment).where(SpoolAssignment.printer_id == printer_id))
    assert assignment is not None and assignment.spool_id == spool_id


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sync_weights_from_ams_skips_a_reused_tag_spool(async_client: AsyncClient, db_session, printer_factory):
    printer = await printer_factory()
    spool = await _reused_tag_spool(db_session)
    db_session.add(SpoolAssignment(spool_id=spool.id, printer_id=printer.id, ams_id=0, tray_id=0))
    await db_session.commit()

    state = SimpleNamespace(state="IDLE", connected=True, raw_data={"ams": [{"id": 0, "tray": [dict(TAG_TRAY)]}]})
    with patch("backend.app.services.printer_manager.printer_manager") as pm:
        pm.get_status = MagicMock(return_value=state)
        response = await async_client.post("/api/v1/inventory/sync-ams-weights")

    assert response.json()["synced"] == 0
    await db_session.refresh(spool)
    assert spool.weight_used == 100


@pytest.mark.asyncio
@pytest.mark.integration
async def test_link_tag_can_mark_the_tag_as_reused(async_client: AsyncClient, db_session):
    spool = Spool(material="PETG", label_weight=1000)
    db_session.add(spool)
    await db_session.commit()

    resp = await async_client.patch(
        f"/api/v1/inventory/spools/{spool.id}/link-tag",
        json={"tag_uid": TAG_UID, "tray_uuid": TRAY_UUID, "reused_tag": True},
    )

    assert resp.status_code == 200
    assert resp.json()["reused_tag"] is True


@pytest.mark.asyncio
@pytest.mark.integration
async def test_clearing_the_tag_clears_the_flag(async_client: AsyncClient, db_session):
    spool = await _reused_tag_spool(db_session)

    resp = await async_client.patch(
        f"/api/v1/inventory/spools/{spool.id}",
        json={"tag_uid": None, "tray_uuid": None, "tag_type": None, "data_origin": None},
    )

    assert resp.status_code == 200
    assert resp.json()["reused_tag"] is False


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_flag_can_be_toggled_on_a_tagged_spool(async_client: AsyncClient, db_session):
    spool = await _reused_tag_spool(db_session, reused=False)

    resp = await async_client.patch(f"/api/v1/inventory/spools/{spool.id}", json={"reused_tag": True})

    assert resp.status_code == 200
    assert resp.json()["reused_tag"] is True


# --- Unknown tags are asked about, not added ---------------------------------


def _unknown_tag_events(sent: list[dict]) -> list[dict]:
    return [m for m in sent if m.get("type") == "unknown_tag"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_unknown_tag_is_asked_about_not_added(test_engine, db_session, printer_factory):
    """Off by default: the tag may be authentic or reused, so nothing is created."""
    printer_id = (await printer_factory(name="X1C")).id

    sent = await _push(test_engine, printer_id, [{"id": 0, "tray": [dict(TAG_TRAY)]}])

    assert await db_session.scalar(select(func.count()).select_from(Spool)) == 0
    [event] = _unknown_tag_events(sent)
    assert (event["reason"], event["remain"], event["tray_uuid"]) == ("unknown", 30, TRAY_UUID)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_unknown_tag_is_not_matched_to_an_untagged_spool_either(test_engine, db_session, printer_factory):
    """Matching on the tag's material and colour is taking its word too."""
    printer_id = (await printer_factory(name="X1C")).id
    db_session.add(Spool(material="PLA", rgba="FF0000FF", label_weight=1000))
    await db_session.commit()

    await _push(test_engine, printer_id, [{"id": 0, "tray": [dict(TAG_TRAY)]}])

    db_session.expire_all()
    assert await db_session.scalar(select(func.count()).select_from(SpoolAssignment)) == 0
    assert await db_session.scalar(select(func.count()).select_from(Spool).where(Spool.tray_uuid.is_not(None))) == 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_auto_add_still_adds_when_switched_on(test_engine, db_session, printer_factory):
    from backend.app.models.settings import Settings

    printer_id = (await printer_factory(name="X1C")).id
    db_session.add(Settings(key="auto_add_unknown_rfid", value="true"))
    await db_session.commit()

    await _push(test_engine, printer_id, [{"id": 0, "tray": [dict(TAG_TRAY)]}])

    assert await db_session.scalar(select(func.count()).select_from(Spool)) == 1


async def _archived_spool(db_session, *, weight_used: float) -> int:
    spool = Spool(
        material="PETG",
        brand="Sunlu",
        color_name="Black",
        rgba="000000FF",
        label_weight=1000,
        weight_used=weight_used,
        tag_uid=TAG_UID,
        tray_uuid=TRAY_UUID,
        reused_tag=True,
        archived_at=datetime(2026, 10, 1),
    )
    db_session.add(spool)
    await db_session.commit()
    return spool.id


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_empty_archived_spools_tag_coming_back_full_suggests_re_adding(
    test_engine, db_session, printer_factory
):
    printer_id = (await printer_factory(name="X1C")).id
    archived_id = await _archived_spool(db_session, weight_used=980)  # 2 % left
    tray = dict(TAG_TRAY, remain=95)

    sent = await _push(test_engine, printer_id, [{"id": 0, "tray": [tray]}])

    [event] = _unknown_tag_events(sent)
    assert event["reason"] == "refilled_archived"
    assert event["archived_spool"]["id"] == archived_id
    assert (event["archived_spool"]["material"], event["archived_spool"]["remaining_pct"]) == ("PETG", 2)
    # Only suggested: nothing is created or assigned until the user answers.
    assert await db_session.scalar(select(func.count()).select_from(Spool)) == 1
    assert await db_session.scalar(select(func.count()).select_from(SpoolAssignment)) == 0


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize(
    "weight_used, remain",
    [
        (980, 20),  # Empty when archived, but not much fuller now (18 points).
        (500, 95),  # Archived half full: the same spool, not a refill.
    ],
)
async def test_an_archived_spools_tag_otherwise_stays_quiet(
    test_engine, db_session, printer_factory, weight_used, remain
):
    printer_id = (await printer_factory(name="X1C")).id
    await _archived_spool(db_session, weight_used=weight_used)

    sent = await _push(test_engine, printer_id, [{"id": 0, "tray": [dict(TAG_TRAY, remain=remain)]}])

    assert _unknown_tag_events(sent) == []


# --- Answering the prompt -----------------------------------------------------


async def _from_slot(async_client: AsyncClient, printer_id: int, body: dict):
    state = SimpleNamespace(state="IDLE", connected=True, raw_data={"ams": [{"id": 0, "tray": [dict(TAG_TRAY)]}]})
    with patch("backend.app.services.printer_manager.printer_manager") as pm:
        pm.get_status = MagicMock(return_value=state)
        pm.get_client = MagicMock(return_value=MagicMock())
        pm.get_model = MagicMock(return_value="X1C")
        return await async_client.post(
            "/api/v1/inventory/spools/from-slot",
            json={"printer_id": printer_id, "ams_id": 0, "tray_id": 0, **body},
        )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_answering_reused_creates_the_real_spool(async_client: AsyncClient, db_session, printer_factory):
    printer_id = (await printer_factory()).id

    resp = await _from_slot(
        async_client,
        printer_id,
        {"reused_tag": True, "material": "PETG", "brand": "Sunlu", "color_name": "Black", "rgba": "000000ff"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["material"], body["brand"], body["rgba"], body["reused_tag"]) == ("PETG", "Sunlu", "000000FF", True)
    # Starts full: the 30 % the tag reports is the old spool's.
    assert (body["weight_used"], body["tray_uuid"]) == (0, TRAY_UUID)
    assignment = await db_session.scalar(select(SpoolAssignment).where(SpoolAssignment.printer_id == printer_id))
    assert assignment.spool_id == body["id"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_answering_authentic_adds_the_tags_filament(async_client: AsyncClient, printer_factory):
    printer_id = (await printer_factory()).id

    resp = await _from_slot(async_client, printer_id, {"reused_tag": False})

    assert resp.status_code == 200, resp.text
    assert (resp.json()["material"], resp.json()["reused_tag"]) == ("PLA", False)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_re_adding_moves_the_tag_off_the_archived_spool(async_client: AsyncClient, db_session, printer_factory):
    printer_id = (await printer_factory()).id
    archived_id = await _archived_spool(db_session, weight_used=980)

    resp = await _from_slot(async_client, printer_id, {"reused_tag": True, "material": "PETG", "brand": "Sunlu"})

    assert resp.status_code == 200, resp.text
    archived = await db_session.get(Spool, archived_id)
    await db_session.refresh(archived)
    assert (archived.tag_uid, archived.tray_uuid, archived.archived_at is not None) == (None, None, True)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_tag_on_an_active_spool_is_refused(async_client: AsyncClient, db_session, printer_factory):
    printer_id = (await printer_factory()).id
    holder = await _reused_tag_spool(db_session)

    resp = await _from_slot(async_client, printer_id, {"reused_tag": True, "material": "PLA"})

    assert resp.status_code == 409
    assert resp.json()["detail"]["spool_id"] == holder.id


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_reused_answer_needs_a_material(async_client: AsyncClient, printer_factory):
    printer_id = (await printer_factory()).id

    resp = await _from_slot(async_client, printer_id, {"reused_tag": True})

    assert resp.status_code == 400
