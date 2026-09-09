"""pyhatchbabyrestasync.

Derived from kjoconnor's pyhatchbabyrest repo.
All rights reserved.
https://github.com/kjoconnor/pyhatchbabyrest/blob/master/LICENSE

Extended with on-device Program (schedule) EG*/ES* support from Hatch Sleep APK.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
from time import monotonic

from bleak.backends.device import BLEDevice
from bleak_retry_connector import (
    BleakAbortedError,
    BleakClientWithServiceCache,
    BleakConnectionError,
    BleakNotFoundError,
    BleakOutOfConnectionSlotsError,
    establish_connection,
)

from .const import (
    CHAR_FEEDBACK,
    CHAR_RX,
    CHAR_TX,
    PROGRAM_COMMAND_TIMEOUT,
    PROGRAM_SLOT_COUNT,
    PyHatchBabyRestSound,
)
from .programs import (
    HatchRestDays,
    HatchRestFlags,
    HatchRestProgram,
    format_device_clock,
    parse_program_time,
)

_LOGGER = logging.getLogger(__name__)

_STATUS_CODES = frozenset(
    {"OK", "E01", "E02", "E03", "E04", "E05", "E06", "Unknown"}
)


def _assert_value(check_val: list[str], index: int, assert_val: str):
    if check_val[index] != assert_val:
        raise ValueError(f'response[{index}] "{check_val[index]}" != "{assert_val}"')


def _parse_hex_int(value: str | None, default: int = 0) -> int:
    if not value:
        return default
    try:
        return int(value.strip(), 16)
    except ValueError:
        return default


def _parse_color_hex(value: str | None) -> tuple[int, int, int, int]:
    """Parse device color payload RRGGBBAA into RGBA."""
    if not value or len(value) < 8:
        return (0, 0, 0, 255)
    raw = value[:8]
    return (
        int(raw[0:2], 16),
        int(raw[2:4], 16),
        int(raw[4:6], 16),
        int(raw[6:8], 16),
    )


class PyHatchBabyRestAsync:
    """An asynchronous interface to a Hatch Rest device using bleak."""

    def __init__(self, ble_device: BLEDevice) -> None:
        """Init PyHatchBabyRestAsync."""
        self.device = ble_device
        self.address = ble_device.address

        self._client: BleakClientWithServiceCache | None = None
        self._active_operations: int = 0

        # connection synchronization primitizes / state
        self._connection_cv = asyncio.Condition()
        self._connecting: bool = False
        self._command_lock = asyncio.Lock()
        self._rx_notify_started = False
        self._pending_status: asyncio.Future[str] | None = None
        self._pending_payload: str | None = None
        self._pending_binary = bytearray()

        # cached device state
        self.color: tuple[int, int, int] | None = None
        self.brightness: int | None = None
        self.sound: PyHatchBabyRestSound | None = None
        self.volume: int | None = None
        self.power: bool | None = None
        self.programs: dict[int, HatchRestProgram] = {}

    def _set_active_operations(self, amount: int):
        """Change the number of running tasks."""
        if amount > 0:
            _LOGGER.debug("Incrementing self._active_operations by %d", amount)
            self._active_operations += 1
        if amount < 0:
            _LOGGER.debug("Decrementing self._active_operations by %d", abs(amount))
            self._active_operations -= 1
        _LOGGER.debug("self._active_operations = %d", self._active_operations)

    def _client_disconnected(self, client: BleakClientWithServiceCache) -> None:
        """Callback for when the client disconnects."""
        _LOGGER.debug("API client has successfully disconnected")
        self._client = None
        self._rx_notify_started = False
        if self._pending_status and not self._pending_status.done():
            self._pending_status.set_exception(
                BleakConnectionError("Hatch Rest disconnected during command")
            )
        self._pending_status = None

    async def _client_connect(self) -> None:
        """Connect to the device."""
        async with self._connection_cv:
            if self._client and self._client.is_connected:
                _LOGGER.debug(
                    "self._client = %s and and self._client.is_connected = %s -- using existing connection",
                    self._client,
                    self._client.is_connected,
                )
                return

            if self._connecting:
                _LOGGER.debug(
                    "self._connecting = %s -- wait for connection to establish",
                    self._connecting,
                )
                await self._connection_cv.wait()
                return

            _LOGGER.debug("No existing connection -- setting self._connecting = True")
            self._connecting = True

        try:
            client = await establish_connection(
                BleakClientWithServiceCache,
                self.device,
                self.device.address,
                disconnected_callback=self._client_disconnected,
            )
            _LOGGER.debug("Client connected: %s", client.is_connected)

        except (
            BleakNotFoundError,
            BleakOutOfConnectionSlotsError,
            BleakAbortedError,
            BleakConnectionError,
            Exception,  # noqa: BLE001
        ) as e:
            _LOGGER.warning("Exception during _client_connect -- %r", e)
            client = None

        async with self._connection_cv:
            self._connecting = False
            self._client = client
            self._connection_cv.notify_all()

    async def _client_disconnect(self) -> None:
        """Disconnect from the device."""
        if self._client and self._active_operations == 0:
            _LOGGER.debug(
                "self._client = %s and self._running_tasks = %d, attempting to disconnect",
                self._client,
                self._active_operations,
            )
            try:
                if self._rx_notify_started:
                    try:
                        await self._client.stop_notify(CHAR_RX)
                    except Exception as e:  # noqa: BLE001
                        _LOGGER.debug("stop_notify CHAR_RX failed: %r", e)
                    self._rx_notify_started = False
                await self._client.disconnect()

            except (
                BleakNotFoundError,
                BleakOutOfConnectionSlotsError,
                BleakAbortedError,
                BleakConnectionError,
                Exception,  # noqa: BLE001
            ) as e:
                _LOGGER.warning("Exception during _client_disconnect -- %r", e)
        else:
            _LOGGER.debug(
                "self._client = %s and self._running_tasks = %d, cannot currently disconnect",
                self._client,
                self._active_operations,
            )

    def _on_rx_notify(self, _handle: int, data: bytearray) -> None:
        """Handle CHAR_RX notifications (command responses)."""
        try:
            text = bytes(data).decode("utf-8")
        except UnicodeDecodeError:
            text = ""

        future = self._pending_status
        if future is None or future.done():
            _LOGGER.debug("Ignoring unexpected RX: %r / %s", text, data.hex())
            return

        if text in _STATUS_CODES:
            future.set_result(text)
            return

        # Intermediate payload packet(s) before OK/E0x.
        self._pending_payload = text
        self._pending_binary.extend(data)
        _LOGGER.debug("RX payload <- %r (%s)", text, data.hex())

    async def _ensure_rx_notify(self) -> None:
        """Subscribe to RX notifications once per connection."""
        if not self._client or not self._client.is_connected:
            raise BleakConnectionError("Not connected")
        if self._rx_notify_started:
            return
        await self._client.start_notify(CHAR_RX, self._on_rx_notify)
        self._rx_notify_started = True

    async def _transact(
        self, command: str, timeout: float = PROGRAM_COMMAND_TIMEOUT
    ) -> tuple[str, str, bytes]:
        """Write a TX command and wait for RX status (+ optional payload).

        Returns (status, payload_text, payload_bytes).
        """
        if not self._client or not self._client.is_connected:
            raise BleakConnectionError("Not connected")

        await self._ensure_rx_notify()
        loop = asyncio.get_running_loop()
        self._pending_payload = None
        self._pending_binary = bytearray()
        self._pending_status = loop.create_future()

        _LOGGER.debug("TX -> %s", command)
        await self._client.write_gatt_char(
            char_specifier=CHAR_TX,
            data=bytearray(command, "utf-8"),
            response=True,
        )

        try:
            status = await asyncio.wait_for(self._pending_status, timeout=timeout)
        finally:
            self._pending_status = None

        payload = self._pending_payload or ""
        binary = bytes(self._pending_binary)
        _LOGGER.debug("RX status %s payload=%r", status, payload)
        return status, payload, binary

    async def _send_command(self, command: str):
        """Send a command do the device.

        :param command: The command to send.
        """
        if log_timing := _LOGGER.isEnabledFor(logging.DEBUG):
            start = monotonic()
            _LOGGER.debug("Started _send_command at %s", datetime.now().isoformat())

        async with self._command_lock:
            self._set_active_operations(1)
            await self._client_connect()

            try:
                await self._client.write_gatt_char(  # pyright: ignore[reportOptionalMemberAccess]
                    char_specifier=CHAR_TX,
                    data=bytearray(command, "utf-8"),
                    response=True,
                )

            except (
                BleakNotFoundError,
                BleakOutOfConnectionSlotsError,
                BleakAbortedError,
                BleakConnectionError,
                Exception,  # noqa: BLE001
            ) as e:
                _LOGGER.warning("Exception during _send_command -- %r", e)

            self._set_active_operations(-1)
        # seemingly need some time for Hatch Rest to "catch up"
        await asyncio.sleep(1)
        await self.refresh_data()

        if log_timing:
            _LOGGER.debug(
                "Finished _send_command at %s (total of %.3f seconds)",
                datetime.now().isoformat(),
                monotonic() - start,  # pyright: ignore[reportPossiblyUnboundVariable]
            )

    async def refresh_data(self):
        """Refresh data from Hatch Rest device."""
        if log_timing := _LOGGER.isEnabledFor(logging.DEBUG):
            start = monotonic()
            _LOGGER.debug("Started refresh_data at %s", datetime.now().isoformat())

        self._set_active_operations(1)
        await self._client_connect()

        try:
            raw_char_read = await self._client.read_gatt_char(CHAR_FEEDBACK)  # pyright: ignore[reportOptionalMemberAccess]
            _LOGGER.debug("Raw char read from refresh_data: %s", raw_char_read)

            response = [hex(x) for x in raw_char_read]

            # Make sure the data is where we think it is
            _assert_value(response, 5, "0x43")  # color
            _assert_value(response, 10, "0x53")  # audio
            _assert_value(response, 13, "0x50")  # power

            red, green, blue, brightness = [int(x, 16) for x in response[6:10]]

            sound = PyHatchBabyRestSound(int(response[11], 16))
            volume = int(response[12], 16)

            power = not bool(int("11000000", 2) & int(response[14], 16))

            self.color = (red, green, blue)
            _LOGGER.debug("refresh_data color: %s", self.color)
            self.brightness = brightness
            _LOGGER.debug("refresh_data brightness: %s", self.brightness)
            self.sound = sound
            _LOGGER.debug("refresh_data sound: %s", self.sound)
            self.volume = volume
            _LOGGER.debug("refresh_data volume: %s", self.volume)
            self.power = power
            _LOGGER.debug("refresh_data power: %s", self.power)

        except (
            BleakNotFoundError,
            BleakOutOfConnectionSlotsError,
            BleakAbortedError,
            BleakConnectionError,
            Exception,  # noqa: BLE001
        ) as e:
            _LOGGER.warning("Exception during refresh_data -- %r", e)

        self._set_active_operations(-1)
        await self._client_disconnect()

        if log_timing:
            _LOGGER.debug(
                "Finished refresh_data at %s (total of %.3f seconds)",
                datetime.now().isoformat(),
                monotonic() - start,  # pyright: ignore[reportPossiblyUnboundVariable]
            )

    async def _get_program_fields(self, index: int) -> HatchRestProgram:
        """Fallback field-by-field program load (EGL/EG*)."""
        status, flags_hex, _ = await self._transact(f"EGL{index:02X}")
        if status != "OK":
            raise RuntimeError(f"EGL{index:02X} failed: {status}")

        flags = HatchRestFlags.from_hex(flags_hex)
        if not flags.exists:
            return HatchRestProgram(
                index=index,
                exists=False,
                enabled=False,
                flags=flags,
            )

        async def field(cmd: str) -> str:
            st, payload, _ = await self._transact(f"{cmd}{index:02X}")
            if st != "OK":
                raise RuntimeError(f"{cmd}{index:02X} failed: {st}")
            return payload

        color = _parse_color_hex(await field("EGC"))
        duration = _parse_hex_int(await field("EGD"))
        power = _parse_hex_int(await field("EGI"), 255)
        buttons = _parse_hex_int(await field("EGM"))
        track = _parse_hex_int(await field("EGN"))
        presets = _parse_hex_int(await field("EGP"))
        tod = parse_program_time(await field("EGT"))
        volume = _parse_hex_int(await field("EGV"))
        name = await field("EGX")
        days = HatchRestDays.from_hex(await field("EGW"))

        return HatchRestProgram(
            index=index,
            name=name or f"Program {index}",
            exists=True,
            enabled=flags.enabled,
            power=power,
            volume=volume,
            track=track,
            duration_seconds=duration,
            presets=presets,
            color=color,
            time_of_day=tod,
            days=days,
            flags=flags,
            buttons=buttons,
        )

    async def _require(self, cmd: str) -> None:
        status, _, _ = await self._transact(cmd)
        if status != "OK":
            raise RuntimeError(f"{cmd} failed: {status}")

    async def _write_full_program(self, program: HatchRestProgram) -> HatchRestProgram:
        """Write all program fields (caller holds lock + connection)."""
        flags = program.flags
        flags.exists = True
        flags.enabled = program.enabled
        # Schedules should turn light/sound on when they fire unless explicitly set.
        if not flags.light_on and not flags.light_off:
            flags.light_on = True
        if not flags.sound_on and not flags.sound_off:
            flags.sound_on = True
        program.exists = True
        program.flags = flags

        idx = program.index
        await self._require(f"ESB{idx:02X}")
        await self._require(f"EST{program.time_command()}")
        await self._require(f"ESD{program.duration_seconds & 0xFFFF:04X}")
        await self._require(f"ESW{program.days.to_hex()}")
        await self._require(f"ESI{program.power & 0xFF:02X}")
        await self._require(f"ESC{program.color_hex()}")
        await self._require(f"ESN{program.track & 0xFF:02X}")
        await self._require(f"ESV{program.volume & 0xFF:02X}")
        await self._require(f"ESX{program.name or f'Program {idx}'}")
        await self._require(f"ESM{(program.buttons & 0xFFFF):04X}0000")
        await self._require(f"ESL{flags.to_hex()}")
        await self._require("ESF")

        written = await self._get_program_fields(idx)
        self.programs[idx] = written
        return written

    async def get_program(self, index: int) -> HatchRestProgram:
        """Load one on-device program slot (1–10) via EGL field path."""
        if index < 1 or index > PROGRAM_SLOT_COUNT:
            raise ValueError(f"Program index must be 1–{PROGRAM_SLOT_COUNT}")

        async with self._command_lock:
            self._set_active_operations(1)
            await self._client_connect()
            try:
                if not self._client or not self._client.is_connected:
                    raise BleakConnectionError("Could not connect to Hatch Rest")
                program = await self._get_program_fields(index)
                self.programs[index] = program
                return program
            finally:
                self._set_active_operations(-1)
                await self._client_disconnect()

    async def get_programs(self) -> dict[int, HatchRestProgram]:
        """Load all program slots 1–10 via EGL field path."""
        async with self._command_lock:
            self._set_active_operations(1)
            await self._client_connect()
            try:
                if not self._client or not self._client.is_connected:
                    raise BleakConnectionError("Could not connect to Hatch Rest")

                results: dict[int, HatchRestProgram] = {}
                for index in range(1, PROGRAM_SLOT_COUNT + 1):
                    results[index] = await self._get_program_fields(index)

                self.programs = results
                return results
            finally:
                self._set_active_operations(-1)
                await self._client_disconnect()

    async def sync_clock(self, tz_name: str = "America/New_York") -> str:
        """Set device clock (ST) so schedules fire at the right local time."""
        payload = format_device_clock(tz_name=tz_name)
        async with self._command_lock:
            self._set_active_operations(1)
            await self._client_connect()
            try:
                if not self._client or not self._client.is_connected:
                    raise BleakConnectionError("Could not connect to Hatch Rest")
                await self._require(f"ST{payload}")
                # Read back GT for confirmation.
                status, gt, _ = await self._transact("GT")
                _LOGGER.info(
                    "Synced Hatch clock ST%s (GT status=%s payload=%r)",
                    payload,
                    status,
                    gt,
                )
                return gt or payload
            finally:
                self._set_active_operations(-1)
                await self._client_disconnect()

    async def set_program(self, program: HatchRestProgram) -> HatchRestProgram:
        """Write one program slot, then re-read it."""
        if program.index < 1 or program.index > PROGRAM_SLOT_COUNT:
            raise ValueError(f"Program index must be 1–{PROGRAM_SLOT_COUNT}")

        async with self._command_lock:
            self._set_active_operations(1)
            await self._client_connect()
            try:
                if not self._client or not self._client.is_connected:
                    raise BleakConnectionError("Could not connect to Hatch Rest")
                return await self._write_full_program(program)
            finally:
                self._set_active_operations(-1)
                await self._client_disconnect()

    async def set_program_from_service(self, data: dict) -> HatchRestProgram:
        """Create or patch a program from service fields (merge by default)."""
        index = int(data["index"])
        if index < 1 or index > PROGRAM_SLOT_COUNT:
            raise ValueError(f"Program index must be 1–{PROGRAM_SLOT_COUNT}")
        replace = bool(data.get("replace", False))

        async with self._command_lock:
            self._set_active_operations(1)
            await self._client_connect()
            try:
                if not self._client or not self._client.is_connected:
                    raise BleakConnectionError("Could not connect to Hatch Rest")

                existing = await self._get_program_fields(index)
                if replace or not existing.exists:
                    program = HatchRestProgram.from_service_data(data)
                else:
                    program = existing.merge_service_data(data)
                return await self._write_full_program(program)
            finally:
                self._set_active_operations(-1)
                await self._client_disconnect()

    async def set_program_enabled(self, index: int, enabled: bool) -> HatchRestProgram:
        """Enable/disable a slot; creates a default program if the slot is empty."""
        if index < 1 or index > PROGRAM_SLOT_COUNT:
            raise ValueError(f"Program index must be 1–{PROGRAM_SLOT_COUNT}")

        async with self._command_lock:
            self._set_active_operations(1)
            await self._client_connect()
            try:
                if not self._client or not self._client.is_connected:
                    raise BleakConnectionError("Could not connect to Hatch Rest")

                existing = await self._get_program_fields(index)
                if not existing.exists:
                    return await self._write_full_program(
                        HatchRestProgram.default_slot(index, enabled=enabled)
                    )

                existing.enabled = enabled
                existing.flags.enabled = enabled
                existing.flags.exists = True
                await self._require(f"ESB{index:02X}")
                await self._require(f"ESL{existing.flags.to_hex()}")
                await self._require("ESF")
                written = await self._get_program_fields(index)
                self.programs[index] = written
                return written
            finally:
                self._set_active_operations(-1)
                await self._client_disconnect()

    async def clear_program(self, index: int) -> None:
        """Clear (delete) one program slot."""
        if index < 1 or index > PROGRAM_SLOT_COUNT:
            raise ValueError(f"Program index must be 1–{PROGRAM_SLOT_COUNT}")

        empty_flags = HatchRestFlags(exists=False, enabled=False)
        async with self._command_lock:
            self._set_active_operations(1)
            await self._client_connect()
            try:
                if not self._client or not self._client.is_connected:
                    raise BleakConnectionError("Could not connect to Hatch Rest")

                await self._require(f"ESB{index:02X}")
                await self._require(f"ESL{empty_flags.to_hex()}")
                await self._require("ESF")
                self.programs[index] = HatchRestProgram(
                    index=index, exists=False, enabled=False, flags=empty_flags
                )
            finally:
                self._set_active_operations(-1)
                await self._client_disconnect()

    async def turn_power_on(self):
        """Power on the Hatch Rest device."""
        command = f"SI{1:02x}"
        _LOGGER.debug("API command: turn_power_on")
        await self._send_command(command)

    async def turn_power_off(self):
        """Power off the Hatch Rest device."""
        command = f"SI{0:02x}"
        _LOGGER.debug("API command: turn_power_off")
        await self._send_command(command)

    async def set_sound(self, sound: int):
        """Set the sound of the Hatch Rest device."""
        command = f"SN{sound:02x}"
        _LOGGER.debug("API command: set_sound to %s", command)
        return await self._send_command(command)

    async def set_volume(self, volume: int):
        """Set the volume of the Hatch Rest device."""
        command = f"SV{volume:02x}"
        _LOGGER.debug("API command: set_volume to %s", command)
        return await self._send_command(command)

    async def set_color(self, red: int, green: int, blue: int):
        """Set the color of the Hatch Rest device."""
        command = f"SC{red:02x}{green:02x}{blue:02x}{self.brightness:02x}"
        _LOGGER.debug("API command: set_color to %s", command)
        return await self._send_command(command)

    async def set_brightness(self, brightness: int):
        """Set the brightness of the Hatch Rest device."""
        if self.color:
            command = f"SC{self.color[0]:02x}{self.color[1]:02x}{self.color[2]:02x}{brightness:02x}"
        _LOGGER.debug("API command: set_brightness to %s", command)
        return await self._send_command(command)

    @property
    def name(self):
        """Return the name of the Hatch Rest device."""
        return self.device.name
