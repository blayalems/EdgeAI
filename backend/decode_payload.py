"""Python port of decoder/ttn_payload_decoder.js — same 9-byte v1 contract
(firmware/main/lora_telemetry.h). Used when a webhook delivers only the
raw frm_payload (e.g. no formatter installed in the TTN console).
"""
from __future__ import annotations

ACTIONS = ("LOG", "SPRAY", "LOCKOUT", "FAULT")


class PayloadError(ValueError):
    pass


def decode_uplink(b: bytes) -> dict:
    if len(b) != 9:
        raise PayloadError(f"expected 9 bytes, got {len(b)}")
    if b[0] != 0x01:
        raise PayloadError(f"unknown payload version 0x{b[0]:02x}")
    flags = b[3]
    if b[7] >= len(ACTIONS):
        raise PayloadError(f"unknown action code 0x{b[7]:02x}")
    return {
        "version": b[0],
        "n_pest": (b[1] << 8) | b[2],
        "soil_safe": bool(flags & 0x01),
        "soil_fault": bool(flags & 0x02),
        "camera_fault": bool(flags & 0x04),
        "infer_ready": bool(flags & 0x08),
        "lockout_active": bool(flags & 0x10),
        "soil_vwc_pct": None if b[4] == 0xFF else b[4],
        "batt_mv": (b[5] << 8) | b[6],
        "action": ACTIONS[b[7]],
        "sprays_today": b[8],
    }


def encode_uplink(d: dict) -> bytes:
    """Inverse of decode_uplink — used by the simulator and tests."""
    action = d.get("action", "LOG")
    if action not in ACTIONS:
        raise PayloadError(f"unknown action {action!r}")
    for key, maximum, optional in (("n_pest", 0xFFFF, False),
                                   ("batt_mv", 0xFFFF, False),
                                   ("sprays_today", 0xFF, True)):
        if key not in d and not optional:
            raise PayloadError(f"missing {key}")
        value = d.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, int):
            raise PayloadError(f"{key} must be an integer")
        if not 0 <= value <= maximum:
            raise PayloadError(f"{key} outside 0..{maximum}")
    vwc = d.get("soil_vwc_pct")
    if vwc is not None and (isinstance(vwc, bool) or
                            not isinstance(vwc, int) or not 0 <= vwc <= 100):
        raise PayloadError("soil_vwc_pct must be null or an integer 0..100")
    for key in ("soil_safe", "soil_fault", "camera_fault", "infer_ready",
                "lockout_active"):
        if key in d and not isinstance(d[key], bool):
            raise PayloadError(f"{key} must be boolean")
    flags = (
        (0x01 if d.get("soil_safe") else 0)
        | (0x02 if d.get("soil_fault") else 0)
        | (0x04 if d.get("camera_fault") else 0)
        | (0x08 if d.get("infer_ready") else 0)
        | (0x10 if d.get("lockout_active") else 0)
    )
    return bytes([
        0x01,
        (d["n_pest"] >> 8) & 0xFF, d["n_pest"] & 0xFF,
        flags,
        0xFF if vwc is None else int(vwc) & 0xFF,
        (d["batt_mv"] >> 8) & 0xFF, d["batt_mv"] & 0xFF,
        ACTIONS.index(action),
        d.get("sprays_today", 0) & 0xFF,
    ])
