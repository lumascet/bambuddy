"""The auto_add_unknown_rfid setting, read one way everywhere.

Off by default: a tag Bambuddy has never seen may be an authentic Bambu Lab
spool or an old tag reused on other filament, and only the user can tell
which. With the setting off, the slot raises a prompt that asks.
"""

AUTO_ADD_UNKNOWN_RFID_DEFAULT = False


def auto_add_unknown_rfid_enabled(raw: str | None) -> bool:
    """The stored setting value as a bool; unset or blank means the default."""
    if raw is None or not raw.strip():
        return AUTO_ADD_UNKNOWN_RFID_DEFAULT
    return raw.strip().lower() == "true"
