"""Modbus RTU over MQTT (transparante DR164-gateway): FC03 lezen en FC06 schrijven.

Schrijven gebeurt alleen via writes.WriteGuard (allowlist + budget); deze module
controleert zelf ook de allowlist, zodat er geen omweg bestaat.
"""
from __future__ import annotations

import asyncio
import logging
import struct
from collections.abc import Awaitable, Callable

_LOGGER = logging.getLogger(__name__)


class ModbusError(Exception):
    """Algemene Modbus-fout (CRC, verkeerd antwoord)."""


class ModbusTimeout(ModbusError):
    """Geen (volledig) antwoord binnen de time-out."""


class ModbusException(ModbusError):
    """Slave antwoordde met een exception-frame (bijv. 2 = ongeldig adres)."""

    def __init__(self, code: int) -> None:
        super().__init__(f"Modbus-exception {code}")
        self.code = code


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def add_crc(frame: bytes) -> bytes:
    return frame + struct.pack("<H", crc16(frame))


def valid_crc(frame: bytes) -> bool:
    return len(frame) >= 4 and struct.unpack("<H", frame[-2:])[0] == crc16(frame[:-2])


def build_read_request(slave: int, start: int, count: int) -> bytes:
    if not 1 <= count <= 125:
        raise ValueError("count moet 1..125 zijn")
    return add_crc(struct.pack(">BBHH", slave, 3, start, count))


def build_write_request(slave: int, addr: int, value: int) -> bytes:
    if not 0 <= value <= 0xFFFF:
        raise ValueError("waarde buiten 16-bit bereik")
    return add_crc(struct.pack(">BBHH", slave, 6, addr, value))


def parse_write_response(frame: bytes, request: bytes) -> None:
    if not valid_crc(frame):
        raise ModbusError("CRC-fout")
    if frame[1] == 0x86:
        raise ModbusException(frame[2])
    if frame != request:
        raise ModbusError("echo komt niet overeen met schrijfverzoek")


def parse_read_response(frame: bytes, slave: int, count: int) -> list[int]:
    if not valid_crc(frame):
        raise ModbusError("CRC-fout")
    if frame[0] != slave:
        raise ModbusError("antwoord van verkeerde slave")
    if frame[1] == 0x83:
        raise ModbusException(frame[2])
    if frame[1] != 3 or len(frame) != 5 + 2 * count or frame[2] != 2 * count:
        raise ModbusError("ongeldig antwoord")
    return list(struct.unpack(f">{count}H", frame[3 : 3 + 2 * count]))


class ModbusBus:
    """Eén verzoek tegelijk; antwoorden worden strikt aan dat verzoek gekoppeld."""

    def __init__(
        self,
        send: Callable[[bytes], Awaitable[None]],
        timeout: float = 3.0,
        quiet: float = 0.3,
        gap: float = 0.03,
    ) -> None:
        self._send = send
        self._timeout = timeout
        self._quiet = quiet
        self._gap = gap
        self._lock = asyncio.Lock()
        self._buf = bytearray()
        self._expect: tuple[int, int, int] | None = None   # slave, functie, lengte bij succes
        self._fut: asyncio.Future[bytes] | None = None

    def feed(self, data: bytes) -> None:
        """Aanroepen met elk ontvangen MQTT-bericht van de DR164 (/up)."""
        if self._expect is None or self._fut is None or self._fut.done():
            return  # onverwacht of laat frame: negeren
        self._buf.extend(data)
        self._try_complete()

    def _try_complete(self) -> None:
        assert self._expect is not None
        slave, fc, length = self._expect
        buf = self._buf
        while buf and buf[0] != slave:
            del buf[0]
        if len(buf) < 5:
            return
        if buf[1] == fc | 0x80:
            need = 5
        elif buf[1] == fc:
            need = length
        else:
            del buf[0]
            self._try_complete()
            return
        if len(buf) < need:
            return
        frame = bytes(buf[:need])
        del buf[:need]
        if self._fut is not None and not self._fut.done():
            self._fut.set_result(frame)

    async def read_holding(self, slave: int, start: int, count: int) -> list[int]:
        frame = await self._transact(build_read_request(slave, start, count), (slave, 3, 5 + 2 * count))
        return parse_read_response(frame, slave, count)

    async def write_single(self, slave: int, addr: int, value: int, service: bool = False) -> None:
        """FC06. Regeling: alleen writes.ALLOWED. Servicemodus: alleen writes.SERVICE (tabelgrenzen).
        Negatieve waarden worden als 16-bit two's complement verstuurd."""
        from .writes import ALLOWED, SERVICE  # noqa: PLC0415 - dubbele controle op de allowlist
        lim = (SERVICE if service else ALLOWED).get((slave, addr))
        if lim is None or not lim[0] <= value <= lim[1]:
            raise ModbusError(f"schrijven geweigerd: slave {slave} reg {addr} = {value} niet toegestaan")
        request = build_write_request(slave, addr, value & 0xFFFF)
        frame = await self._transact(request, (slave, 6, 8))
        parse_write_response(frame, request)

    async def write_bits(self, slave: int, addr: int, mask: int, on: bool) -> tuple[int, int]:
        """Lezen-aanpassen-schrijven van toegestane commando-bits (writes.BIT_COMMANDS).
        Leest het register vers uit, verandert alleen `mask` en schrijft terug. Geeft (oud, nieuw)."""
        from .writes import bit_write_value  # noqa: PLC0415
        current = (await self.read_holding(slave, addr, 1))[0]
        try:
            new = bit_write_value(slave, addr, current, mask, on)
        except ValueError as err:
            raise ModbusError(f"schrijven geweigerd: {err}") from err
        if new == current:
            return current, new
        request = build_write_request(slave, addr, new)
        frame = await self._transact(request, (slave, 6, 8))
        parse_write_response(frame, request)
        return current, new

    async def _transact(self, request: bytes, expect: tuple[int, int, int]) -> bytes:
        slave = expect[0]
        async with self._lock:
            loop = asyncio.get_running_loop()
            self._buf.clear()
            self._fut = loop.create_future()
            self._expect = expect
            timed_out = False
            try:
                await self._send(request)
                frame = await asyncio.wait_for(self._fut, self._timeout)
            except asyncio.TimeoutError as exc:
                timed_out = True
                raise ModbusTimeout(f"time-out slave {slave} ({request[:6].hex(' ')})") from exc
            finally:
                self._expect = None
                self._fut = None
                self._buf.clear()
                # Na een time-out even stil blijven: een late respons wordt dan genegeerd
                # en kan niet aan het volgende verzoek worden gekoppeld.
                await asyncio.sleep(self._quiet if timed_out else self._gap)
            return frame


async def read_registers(
    bus: ModbusBus, slave: int, start: int, count: int, max_failures: int = 4
) -> tuple[dict[int, int], list[int]]:
    """Lees een blok. Bij een exception (ongeldig adres) wordt gehalveerd om de
    leesbare registers te redden. Bij een time-out wordt NIET gesplitst."""
    out: dict[int, int] = {}
    failed: list[int] = []

    async def _read(s: int, c: int) -> None:
        try:
            vals = await bus.read_holding(slave, s, c)
        except ModbusException:
            if c == 1:
                failed.append(s)
                return
            if len(failed) >= max_failures:
                failed.extend(range(s, s + c))
                return
            half = c // 2
            await _read(s, half)
            await _read(s + half, c - half)
            return
        out.update({s + i: v for i, v in enumerate(vals)})

    await _read(start, count)
    return out, failed
