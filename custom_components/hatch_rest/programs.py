"""On-device Hatch Rest program (schedule) models and encoding."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

DAY_BITS = {
    "sunday": 1,
    "monday": 2,
    "tuesday": 4,
    "wednesday": 8,
    "thursday": 16,
    "friday": 32,
    "saturday": 64,
}

ALL_DAYS = list(DAY_BITS.keys())

# Match Hatch factory defaults (Nap Time-ish warm orange).
DEFAULT_PROGRAM_COLOR = (235, 142, 72, 127)  # RGBA
DEFAULT_PROGRAM_TRACK = 3  # pink noise
DEFAULT_PROGRAM_VOLUME = 51
DEFAULT_PROGRAM_DURATION = 7230  # ~2h
DEFAULT_PROGRAM_TIME = time(13, 0, 0)


@dataclass
class HatchRestDays:
    """Weekday bitmask for a program (Su=1 … Sa=64)."""

    mask: int = 0

    @classmethod
    def from_hex(cls, value: str) -> HatchRestDays:
        """Parse hex bitmask from device."""
        return cls(int(value or "0", 16))

    @classmethod
    def from_names(cls, days: list[str] | None) -> HatchRestDays:
        """Build from weekday name list."""
        mask = 0
        for name in days or []:
            bit = DAY_BITS.get(name.lower())
            if bit:
                mask |= bit
        return cls(mask)

    @classmethod
    def every_day(cls) -> HatchRestDays:
        """All seven days."""
        return cls(127)

    def to_hex(self) -> str:
        """Encode as two-digit hex."""
        return f"{self.mask & 0xFF:02X}"

    def names(self) -> list[str]:
        """Return enabled weekday names."""
        return [name for name, bit in DAY_BITS.items() if self.mask & bit]

    def as_dict(self) -> dict[str, Any]:
        """Serialize for HA attributes."""
        return {"mask": self.mask, "days": self.names()}


@dataclass
class HatchRestFlags:
    """Program flags (exists bit required for a real slot)."""

    exists: bool = False
    enabled: bool = False
    sleep_timer: bool = False
    light_off: bool = False
    light_on: bool = False
    sound_off: bool = False
    sound_on: bool = False

    @classmethod
    def from_hex(cls, value: str) -> HatchRestFlags:
        """Parse flags hex from device (EGL/ESL)."""
        raw = int(value or "0", 16)
        return cls(
            exists=bool(raw & 128),
            enabled=bool(raw & 64),
            sleep_timer=bool(raw & 16),
            light_off=bool(raw & 8),
            light_on=bool(raw & 4),
            sound_off=bool(raw & 2),
            sound_on=bool(raw & 1),
        )

    def to_byte(self) -> int:
        """Encode flags to a single byte."""
        raw = 0
        if self.exists:
            raw |= 128
        if self.enabled:
            raw |= 64
        if self.sleep_timer:
            raw |= 16
        if self.light_off:
            raw |= 8
        if self.light_on:
            raw |= 4
        if self.sound_off:
            raw |= 2
        if self.sound_on:
            raw |= 1
        return raw

    def to_hex(self) -> str:
        """Encode as two-digit hex."""
        return f"{self.to_byte():02X}"

    def as_dict(self) -> dict[str, Any]:
        """Serialize for HA attributes."""
        return {
            "exists": self.exists,
            "enabled": self.enabled,
            "sleep_timer": self.sleep_timer,
            "light_on": self.light_on,
            "light_off": self.light_off,
            "sound_on": self.sound_on,
            "sound_off": self.sound_off,
        }


@dataclass
class HatchRestProgram:
    """One on-device program slot (1–10)."""

    index: int
    name: str = ""
    exists: bool = False
    enabled: bool = False
    power: int = 255
    volume: int = 0
    track: int = 0
    duration_seconds: int = 0
    presets: int = 0
    color: tuple[int, int, int, int] = (0, 0, 0, 255)  # RGBA
    time_of_day: time | None = None
    days: HatchRestDays = field(default_factory=HatchRestDays)
    flags: HatchRestFlags = field(default_factory=HatchRestFlags)
    buttons: int = 0

    @property
    def active(self) -> bool:
        """Whether this slot is a real, enabled schedule."""
        return self.exists and self.enabled

    def color_hex(self) -> str:
        """RGBA as device hex."""
        r, g, b, a = self.color
        return f"{r:02X}{g:02X}{b:02X}{a:02X}"

    def time_command(self) -> str:
        """EST payload using today's local date + program clock time."""
        now = datetime.now()
        tod = self.time_of_day or time(0, 0, 0)
        stamp = datetime(
            now.year, now.month, now.day, tod.hour, tod.minute, tod.second
        )
        return stamp.strftime("%Y%m%d%H%M%S")

    def as_dict(self) -> dict[str, Any]:
        """Serialize for HA attributes / service responses."""
        return {
            "index": self.index,
            "name": self.name,
            "exists": self.exists,
            "enabled": self.enabled,
            "active": self.active,
            "power": self.power,
            "volume": self.volume,
            "track": self.track,
            "duration_seconds": self.duration_seconds,
            "presets": self.presets,
            "color": {
                "r": self.color[0],
                "g": self.color[1],
                "b": self.color[2],
                "a": self.color[3],
            },
            "time": self.time_of_day.isoformat(timespec="seconds")
            if self.time_of_day
            else None,
            "days": self.days.as_dict(),
            "flags": self.flags.as_dict(),
            "buttons": self.buttons,
        }

    @classmethod
    def default_slot(cls, index: int, *, enabled: bool = True) -> HatchRestProgram:
        """Factory defaults for creating a new slot without the Hatch app."""
        flags = HatchRestFlags(
            exists=True,
            enabled=enabled,
            light_on=True,
            sound_on=True,
        )
        return cls(
            index=index,
            name=f"Program {index}",
            exists=True,
            enabled=enabled,
            power=255,
            volume=DEFAULT_PROGRAM_VOLUME,
            track=DEFAULT_PROGRAM_TRACK,
            duration_seconds=DEFAULT_PROGRAM_DURATION,
            color=DEFAULT_PROGRAM_COLOR,
            time_of_day=DEFAULT_PROGRAM_TIME,
            days=HatchRestDays.every_day(),
            flags=flags,
        )

    def merge_service_data(self, data: dict[str, Any]) -> HatchRestProgram:
        """Overlay service fields onto this program (partial update)."""
        updated = deepcopy(self)
        updated.exists = True

        if "name" in data and data["name"] is not None:
            updated.name = str(data["name"])
        if "enabled" in data and data["enabled"] is not None:
            updated.enabled = bool(data["enabled"])
        if "power" in data and data["power"] is not None:
            updated.power = int(data["power"])
        if "volume" in data and data["volume"] is not None:
            updated.volume = int(data["volume"])
        if "track" in data and data["track"] is not None:
            updated.track = int(data["track"])
        if "duration_seconds" in data and data["duration_seconds"] is not None:
            updated.duration_seconds = int(data["duration_seconds"])
        if "buttons" in data and data["buttons"] is not None:
            updated.buttons = int(data["buttons"])
        if "days" in data and data["days"] is not None:
            updated.days = HatchRestDays.from_names(data["days"])

        raw_time = data.get("time")
        if isinstance(raw_time, str) and raw_time:
            updated.time_of_day = _parse_hhmmss(raw_time)

        color = data.get("color")
        if isinstance(color, dict):
            r, g, b, a = updated.color
            updated.color = (
                int(color.get("r", r)),
                int(color.get("g", g)),
                int(color.get("b", b)),
                int(color.get("a", a)),
            )

        flags = replace(updated.flags)
        flags.exists = True
        flags.enabled = updated.enabled
        if "sleep_timer" in data and data["sleep_timer"] is not None:
            flags.sleep_timer = bool(data["sleep_timer"])
        if "light_on" in data and data["light_on"] is not None:
            flags.light_on = bool(data["light_on"])
            if flags.light_on:
                flags.light_off = False
        if "sound_on" in data and data["sound_on"] is not None:
            flags.sound_on = bool(data["sound_on"])
            if flags.sound_on:
                flags.sound_off = False
        updated.flags = flags
        return updated

    @classmethod
    def from_service_data(cls, data: dict[str, Any]) -> HatchRestProgram:
        """Build a full program from HA service call fields (new / replace)."""
        base = cls.default_slot(int(data["index"]), enabled=bool(data.get("enabled", True)))
        return base.merge_service_data(data)


def _parse_hhmmss(value: str) -> time:
    parts = [int(p) for p in value.split(":")]
    return time(
        parts[0],
        parts[1] if len(parts) > 1 else 0,
        parts[2] if len(parts) > 2 else 0,
    )


def parse_program_time(value: str) -> time | None:
    """Parse EGT payload `yyyyMMddHHmmss` into a time-of-day."""
    if not value:
        return None
    # Strip optional TZ letter (U/E/N) if present.
    raw = value.strip()
    if len(raw) >= 15 and raw[14].isalpha():
        raw = raw[:14]
    if len(raw) < 14:
        # Sometimes device returns HHMMSS-only or bare hex — try HH:MM:SS-ish
        if ":" in raw:
            try:
                return _parse_hhmmss(raw)
            except ValueError:
                return None
        return None
    try:
        stamp = datetime.strptime(raw[:14], "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return stamp.time()


def format_device_clock(now: datetime | None = None, tz_name: str = "America/New_York") -> str:
    """Build ST payload: yyyyMMddHHmmss + Hatch TZ letter."""
    tz = ZoneInfo(tz_name)
    stamp = (now or datetime.now(tz=timezone.utc)).astimezone(tz)
    body = stamp.strftime("%Y%m%d%H%M%S")
    # Hatch DateUtils.d: America/* with DST rules → U, Europe → E, else N
    if tz_name.startswith("America/"):
        return body + "U"
    if tz_name.startswith("Europe/"):
        return body + "E"
    return body + "N"
