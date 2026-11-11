"""Retry pacing of the reused-tag slot override (services.reused_tag_slot).

A send is given RETRY_SECONDS to show in the slot before it is repeated, and
after MAX_ATTEMPTS sends that never took the slot is left alone until it shows
the spool -- a printer that refuses the setting is not sent it forever.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.services import reused_tag_slot as rts

TAG_TRAY = {"tray_type": "PLA", "tray_color": "FF0000FF"}
SPOOL_TRAY = {"tray_type": "PETG", "tray_color": "000000FF"}


@pytest.fixture(autouse=True)
def _fresh():
    rts.reset()
    yield
    rts.reset()


def _spool(**kw):
    base = {
        "id": 7,
        "reused_tag": True,
        "material": "PETG",
        "rgba": "000000FF",
        "slicer_filament": None,
        "slicer_filament_name": None,
    }
    return SimpleNamespace(**{**base, **kw})


async def _sync(tray, now, spool=None, sent=True):
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=spool or _spool())
    with patch("backend.app.api.routes.inventory.apply_spool_to_slot_via_mqtt", AsyncMock(return_value=sent)) as apply:
        result = await rts.sync_reused_tag_slot(
            db, printer_id=1, ams_id=0, tray_id=0, tray=dict(tray), spool=spool or _spool(), now=now
        )
    return result, apply.await_count


@pytest.mark.asyncio
async def test_a_send_is_not_repeated_before_it_had_time_to_show():
    assert await _sync(TAG_TRAY, now=0) == (True, 1)
    assert await _sync(TAG_TRAY, now=rts.RETRY_SECONDS - 1) == (False, 0)
    assert await _sync(TAG_TRAY, now=rts.RETRY_SECONDS + 1) == (True, 1)


@pytest.mark.asyncio
async def test_sends_stop_after_max_attempts_until_the_slot_shows_the_spool():
    for i in range(rts.MAX_ATTEMPTS):
        assert (await _sync(TAG_TRAY, now=i * rts.RETRY_SECONDS * 2))[0] is True
    assert await _sync(TAG_TRAY, now=1e6) == (False, 0)
    # Once it took, the next tag read is sent again at once.
    assert await _sync(SPOOL_TRAY, now=1e6 + 1) == (False, 0)
    assert await _sync(TAG_TRAY, now=1e6 + 2) == (True, 1)


@pytest.mark.asyncio
async def test_a_failed_send_counts_for_nothing():
    """No MQTT client (printer reconnecting): the next change tries again."""
    assert await _sync(TAG_TRAY, now=0, sent=False) == (False, 1)
    assert await _sync(TAG_TRAY, now=1, sent=True) == (True, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tray, spool",
    [
        (SPOOL_TRAY, _spool()),
        # The preset's type is what the assign path writes (PLA-AERO here).
        (
            {"tray_type": "PLA-AERO", "tray_color": "000000FF"},
            _spool(material="PLA", slicer_filament_name="Bambu PLA Aero"),
        ),
        # Colour readback differs a little from what was sent.
        ({"tray_type": "PETG", "tray_color": "0A0A0AFF"}, _spool()),
    ],
)
async def test_a_slot_showing_the_spool_is_not_sent_anything(tray, spool):
    assert await _sync(tray, now=0, spool=spool) == (False, 0)


@pytest.mark.asyncio
async def test_a_spool_that_is_not_reused_is_never_sent():
    assert await _sync(TAG_TRAY, now=0, spool=_spool(reused_tag=False)) == (False, 0)
