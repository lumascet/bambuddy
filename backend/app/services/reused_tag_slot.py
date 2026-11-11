"""Keep a reused-tag spool's own filament in its AMS slot.

A spool marked ``reused_tag`` carries an old Bambu Lab tag from another spool.
The AMS reads a tag when a spool goes in, at power-on and on a manual refresh,
and every read puts the tag's filament (the spool it came off) into the slot:
the printer screen and the slicer then show the wrong material and colour.

Whenever such a slot reports a filament other than its spool's, the spool's own
settings are sent to it again (``ams_filament_setting`` via
``apply_spool_to_slot_via_mqtt``). A send is given ``RETRY_SECONDS`` to show up
in the slot before it is repeated, and after ``MAX_ATTEMPTS`` sends that never
took, the slot is left alone until it reports the spool, empties or changes
spool -- a printer that refuses the setting is not sent it forever.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.models.spool import Spool
from backend.app.utils.color_utils import colors_similar
from backend.app.utils.filament_types import printer_filament_type

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_SECONDS = 60.0


@dataclass
class _SlotState:
    spool_id: int
    attempts: int = 0
    last_sent: float = 0.0
    gave_up: bool = False


_slots: dict[tuple[int, int, int], _SlotState] = {}


def forget_slot(printer_id: int, ams_id: int, tray_id: int) -> None:
    """Drop a slot's send history (slot emptied or assigned elsewhere)."""
    _slots.pop((printer_id, ams_id, tray_id), None)


def reset() -> None:
    """Clear all send history (tests)."""
    _slots.clear()


async def _spool_slot_types(db: AsyncSession, spool: Spool) -> set[str]:
    """The slot types an override of this spool can leave in the slot.

    Mirrors the assign path: the material reduced to a printer type, or the
    type of the spool's preset (by name, or a local preset's stored type).
    """
    types = {printer_filament_type(spool.material).upper()}
    if spool.slicer_filament_name:
        types.add(printer_filament_type(spool.slicer_filament_name).upper())
    lp_ref = (spool.slicer_filament or "").strip()
    if lp_ref.isdigit() and int(lp_ref) <= 2147483647:
        from backend.app.models.local_preset import LocalPreset

        lp_type = await db.scalar(select(LocalPreset.filament_type).where(LocalPreset.id == int(lp_ref)))
        if lp_type:
            types.add(printer_filament_type(lp_type).upper())
    types.discard("")
    return types


async def tray_shows_spool(db: AsyncSession, tray: dict, spool: Spool) -> bool:
    """Does the slot already report this spool's filament (type and colour)?"""
    cur_type = printer_filament_type(tray.get("tray_type") or "").upper()
    if not cur_type or cur_type not in await _spool_slot_types(db, spool):
        return False
    return colors_similar(tray.get("tray_color") or "", spool.rgba or "FFFFFFFF")


async def sync_reused_tag_slot(
    db: AsyncSession,
    *,
    printer_id: int,
    ams_id: int,
    tray_id: int,
    tray: dict,
    spool: Spool,
    now: float | None = None,
) -> bool:
    """Send the spool's own settings to its slot if the slot shows another
    filament. Returns True when the settings were sent."""
    key = (printer_id, ams_id, tray_id)
    if not spool.reused_tag:
        _slots.pop(key, None)
        return False
    state = _slots.get(key)
    if state is None or state.spool_id != spool.id:
        state = _SlotState(spool_id=spool.id)
        _slots[key] = state

    if await tray_shows_spool(db, tray, spool):
        # The override (or the user) put the spool's filament in the slot. A
        # later tag read that undoes it is sent again straight away.
        state.attempts = 0
        state.gave_up = False
        return False

    now = time.monotonic() if now is None else now
    if state.attempts and now - state.last_sent < RETRY_SECONDS:
        return False  # The last send may still be on its way.
    if state.attempts >= MAX_ATTEMPTS:
        if not state.gave_up:
            state.gave_up = True
            logger.warning(
                "Reused tag: printer %d AMS%d-T%d still shows %s/%s after %d sends of spool %d's settings; "
                "not sending again until the slot changes",
                printer_id,
                ams_id,
                tray_id,
                tray.get("tray_type"),
                tray.get("tray_color"),
                state.attempts,
                spool.id,
            )
        return False

    from backend.app.api.routes.inventory import apply_spool_to_slot_via_mqtt

    # The K-profiles are needed by the send; load them with the spool.
    loaded = await db.scalar(select(Spool).options(selectinload(Spool.k_profiles)).where(Spool.id == spool.id))
    if loaded is None:
        return False
    # No live-tray hints: the slot's filament id, type and sub-brand are the
    # tag's, and reusing them is exactly what this override is undoing.
    sent = await apply_spool_to_slot_via_mqtt(
        db=db,
        current_user=None,
        spool=loaded,
        printer_id=printer_id,
        ams_id=ams_id,
        tray_id=tray_id,
    )
    if sent:
        state.attempts += 1
        state.last_sent = now
        logger.info(
            "Reused tag: printer %d AMS%d-T%d showed %s/%s; sent spool %d's own filament (attempt %d)",
            printer_id,
            ams_id,
            tray_id,
            tray.get("tray_type"),
            tray.get("tray_color"),
            spool.id,
            state.attempts,
        )
    return bool(sent)


async def sync_spool_slots(db: AsyncSession, spool_id: int) -> int:
    """Run ``sync_reused_tag_slot`` for every slot a spool is assigned to,
    against the printers' live tray data. For the API paths that make a spool
    a reused-tag one (or change its filament): the printer sends no AMS update
    of its own then, so nothing else would correct the slot. Returns the
    number of slots the settings were sent to."""
    from backend.app.api.routes.inventory import _find_tray_in_ams_data
    from backend.app.models.spool_assignment import SpoolAssignment
    from backend.app.services.printer_manager import printer_manager

    spool = await db.get(Spool, spool_id)
    if spool is None:
        return 0
    rows = (
        (
            await db.execute(
                select(SpoolAssignment).where(SpoolAssignment.spool_id == spool_id, SpoolAssignment.ams_id != 255)
            )
        )
        .scalars()
        .all()
    )
    sent = 0
    for a in rows:
        # The user just changed the spool: earlier sends say nothing about
        # the settings it has now.
        forget_slot(a.printer_id, a.ams_id, a.tray_id)
        if not spool.reused_tag:
            continue
        state = printer_manager.get_status(a.printer_id)
        if not state or not state.raw_data:
            continue
        ams = state.raw_data.get("ams", [])
        if isinstance(ams, dict):
            ams = ams.get("ams", [])
        tray = _find_tray_in_ams_data(ams, a.ams_id, a.tray_id)
        if not tray or not tray.get("tray_type"):
            continue
        if (getattr(state, "state", "") or "").upper() in ("RUNNING", "PAUSE"):
            from backend.app.main import _slot_feeds_print

            if _slot_feeds_print(a.printer_id, a.ams_id, a.tray_id):
                continue  # Refused for the feeding slot; sent after the print.
        try:
            if await sync_reused_tag_slot(
                db, printer_id=a.printer_id, ams_id=a.ams_id, tray_id=a.tray_id, tray=tray, spool=spool
            ):
                sent += 1
        except Exception:
            logger.exception(
                "Reused tag: sending spool %d to printer %d AMS%d-T%d failed",
                spool_id,
                a.printer_id,
                a.ams_id,
                a.tray_id,
            )
    return sent
