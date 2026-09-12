"""Koneksi WebSocket ke Gemini Live API.

Satu `LiveSession` = satu sesi percakapan, yang bisa berlangsung melewati
beberapa koneksi fisik: server me-reset koneksi tiap ±10 menit, dan sesi
disambung ulang lewat `sessionResumption.handle` tanpa kehilangan konteks.
`go_away` adalah kode resmi untuk itu -- bukan kegagalan.

aiohttp hanya disentuh di `sambungkan()`/`_BungkusWs`; seluruh logika lifecycle
bisa dites dengan ws palsu. Polanya sama dengan alasan modul lain di repo
ini: yang butuh jaringan dipisahkan dari yang hanya berpikir.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Awaitable, Callable

from . import protocol

log = logging.getLogger("ruri")

# Antrean kirim tidak boleh tumbuh tanpa batas: kalau koneksi macet sementara,
# audio lama lebih baik dibuang daripada memori habis atau percakapan tertunda
# semenit -- orang di ruangan sudah ganti topik.
MAKS_ANTREAN = 150

# Percobaan ulang sebelum menyerah. Jedanya naik: 0, 1, 3 detik.
MAKS_PERCOBAAN = 3
JEDA_PERCOBAAN = (0.0, 1.0, 3.0)

# Batas menunggu setupComplete. Konfigurasi yang ditolak server tidak pernah
# dibalas -- tanpa tenggat ini, sesi diam-diam menggantung selamanya.
TENG_GUAT_SETUP = 15


class Putus(Exception):
    """Sesi tidak bisa (atau tidak boleh) dilanjutkan."""


class LiveSession:
    def __init__(self, url: str, setup_builder: Callable[[str | None], dict],
                 on_event: Callable[[str, object], Awaitable[None] | None] | None,
                 ws=None) -> None:
        self.url = url
        self.setup_builder = setup_builder
        self.on_event = on_event
        self.handle: str | None = None      # token resume, bertahan antar-koneksi
        self.ws = ws                        # injeksi untuk tes; None -> aiohttp
        self._kirim: asyncio.Queue = asyncio.Queue(maxsize=MAKS_ANTREAN)
        self._siap: asyncio.Event = asyncio.Event()
        self._berhenti = False
        self._resume = False
        self._per_cobaan = 0
        self._aktif = None                  # ws koneksi berjalan (untuk hentikan)

    # ------------------------------------------------------------ hidup
    async def mulai(self) -> None:
        """Jaga koneksi sampai `hentikan()` dipanggil. Melempar Putus kalau
        percobaan ulang habis -- pemanggilnya yang memutuskan fallback."""
        while not self._berhenti:
            try:
                await self._sekali()
                return                          # berhenti normal, atau resume
            except (Putus, asyncio.CancelledError):
                raise
            except Exception as exc:
                if self._berhenti:
                    return
                self._per_cobaan += 1
                if self._per_cobaan > MAKS_PERCOBAAN:
                    raise Putus("gagal sambung berulang: %s" % exc) from exc
                jeda = JEDA_PERCOBAAN[min(self._per_cobaan - 1,
                                          len(JEDA_PERCOBAAN) - 1)]
                log.warning("live: koneksi putus (%s); percobaan %d/%d dalam %.0fs",
                            exc, self._per_cobaan, MAKS_PERCOBAAN, jeda)
                await asyncio.sleep(jeda)

    async def _sekali(self) -> None:
        if self.ws is not None:
            ws = self.ws
        else:
            # Tenggat sendiri untuk handshake TCP+WS: tanpa ini koneksi yang
            # menggantung diam-diam menahan `!live on` bermenit-menit.
            ws = await asyncio.wait_for(sambungkan(self.url),
                                        timeout=TENG_GUAT_SETUP + 10)
        self._aktif = ws
        self._siap.clear()
        t0 = time.monotonic()
        await ws.send(json.dumps(self.setup_builder(self.handle)))
        tugas_kirim = asyncio.create_task(self._pengirim(ws))
        siap = False
        try:
            it = ws.__aiter__()
            while not self._berhenti:
                try:
                    # Tenggat hanya sampai setupComplete. Setelah itu sesi
                    # boleh sunyi selama-lamanya (ruangan diam = bukan macet).
                    obj = await asyncio.wait_for(
                        it.__anext__(),
                        timeout=None if siap else TENG_GUAT_SETUP)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    raise ConnectionResetError(
                        "setupComplete tidak datang dalam %ds "
                        "(konfigurasi ditolak?)" % TENG_GUAT_SETUP)
                if obj is not None:
                    await self._uraikan(obj if isinstance(obj, dict)
                                       else json.loads(obj))
                if self._siap.is_set():
                    siap = True
        finally:
            tugas_kirim.cancel()
            try:
                await ws.close()
            except Exception:
                pass
            self._aktif = None
        # Sesi yang baru lahir dan langsung mati (<5 detik) dihitung sebagai
        # kegagalan; yang putus di tengah obrolan bukan salah kita dan bukan
        # salah percobaan -- handle-nya masih hidup, tinggal sambung lagi.
        if self._resume or (siap and (time.monotonic() - t0) >= 5):
            self._resume = False
            self._per_cobaan = 0
            raise ConnectionResetError("sambungkan ulang (goAway/sesi lama)")
        if not self._berhenti:
            # Koneksi mati tanpa disuruh: sebelum handshake = konfigurasi
            # ditolak/mati; sesudahnya tapi <5 detik = server menolak kita.
            # Keduanya hitungan ulang, bukan sesi yang berhenti normal.
            raise ConnectionResetError(
                "server menutup koneksi %s" %
                ("sebelum setupComplete" if not siap else "dalam <5 detik"))

    async def _uraikan(self, obj: dict) -> None:
        for kind, data in protocol.parse_server(obj):
            if kind == "setup_complete":
                self._siap.set()
                self._per_cobaan = 0
            elif kind == "handle":
                self.handle = data           # milik koneksi, bukan pemanggil
                continue
            elif kind == "go_away":
                log.info("live: goAway (sisa %s) -- resume ke koneksi baru", data)
                self._resume = True
            await self._teruskan(kind, data)

    async def _teruskan(self, kind: str, data) -> None:
        if self.on_event is None:
            return
        try:
            hasil = self.on_event(kind, data)
            if asyncio.iscoroutine(hasil):
                await hasil
        except Exception:
            log.exception("live: penanganan kejadian %s gagal", kind)

    async def _pengirim(self, ws) -> None:
        while True:
            obj = await self._kirim.get()
            try:
                await ws.send(json.dumps(obj))
            except Exception as exc:
                log.warning("live: kirim gagal (%s); antrean dibuang", exc)
                self._kosongkan()
                return
            finally:
                self._kirim.task_done()

    # ------------------------------------------------------------ kirim
    async def kirim(self, obj: dict) -> bool:
        """False kalau berhenti atau antrean penuh (chunk dibuang, sesi tidak
        dipecah dua oleh kemacetan)."""
        if self._berhenti:
            return False
        try:
            self._kirim.put_nowait(obj)
            return True
        except asyncio.QueueFull:
            return False

    def _kosongkan(self) -> None:
        while True:
            try:
                self._kirim.get_nowait()
                self._kirim.task_done()
            except asyncio.QueueEmpty:
                return

    # ------------------------------------------------------------ mati
    async def mulai_ulang(self) -> None:
        """Sambung ulang SEKETIKA dengan setup hasil build ulang, handle lama
        tetap dipakai -- jalur untuk `!live muat` (prompt/voice/thinking baru)
        tanpa mengorbankan konteks sesi."""
        self._resume = True
        ws = self._aktif
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    async def hentikan(self) -> None:
        """Bilang selamat tinggal (flush server), lalu tutup. Aman dipanggil
        dua kali, dan aman dipanggil sebelum ada koneksi."""
        if self._berhenti:
            return
        if self._aktif is not None:
            try:
                self._kirim.put_nowait(protocol.stream_end_message())
                await asyncio.wait_for(self._kirim.join(), timeout=2)
            except Exception:
                pass
        self._berhenti = True
        ws = self._aktif
        if ws is not None:
            try:
                await ws.close()          # melepas async for
            except Exception:
                pass


async def sambungkan(url: str):
    """Koneksi aiohttp asli. aiohttp diimpor di dalam fungsi supaya tes dan
    import modul ini di mesin tanpa aiohttp tidak langsung gagal."""
    import aiohttp

    sess = aiohttp.ClientSession()
    try:
        # Tanpa ws timeout: aiohttp baru (3.14) sudah men-deprekatangka float
        # di sini, dan tenggat handshake/per-pesan sudah kita pegang sendiri
        # lewat asyncio.wait_for di _sekali.
        ws = await sess.ws_connect(url)
    except Exception:
        await sess.close()
        raise
    return _BungkusWs(ws, sess)


class _BungkusWs:
    """aiohttp mengirim objek pesan mentah; interface kita maunya string JSON.
    CLOSE/CLOSING/ERROR/CLOSE_RECEIVED menghentikan iterasi seperti EOF."""

    def __init__(self, ws, sess) -> None:
        self._ws = ws
        self._sess = sess

    async def send(self, text: str) -> None:
        await self._ws.send_str(text)

    def __aiter__(self):
        return self

    async def __anext__(self):
        import aiohttp
        msg = await self._ws.receive()
        if msg.type == aiohttp.WSMsgType.TEXT:
            return msg.data
        if msg.type == aiohttp.WSMsgType.BINARY:
            # Terkonfirmasi di api.google.dev maupun server nyata: Google
            # mengirim pesan JSON-nya lewat frame BINARY, bukan TEXT. Wrapper
            # yang hanya menerima TEXT akan menganggap setiap balasan sebagai
            # koneksi tertutup -- persis bug yang ditemukan smoke test.
            data = msg.data
            return data.decode("utf-8", "replace") if isinstance(data, bytes) \
                else data
        if msg.type == aiohttp.WSMsgType.ERROR:
            # exception objek (TimeoutError, ClientConnectionError, ...) --
            # alasan sebenarnya; jangan sampai tertutup pesan generik.
            exc = getattr(msg, "data", None)
            raise ConnectionResetError("ws error: %r" % (exc,))
        # CLOSED / CLOSING / CLOSE_RECEIVED: msg.data = kode close,
        # msg.extra = alasan dari server. Keduanya yang membuat bug bisa
        # dilacak -- dibuang diam-diam sama dengan menutup mata.
        raise ConnectionResetError("ws ditutup (%s %s)"
                                   % (getattr(msg, "data", "?"),
                                      getattr(msg, "extra", "") or ""))

    async def close(self) -> None:
        try:
            await self._ws.close()
        finally:
            await self._sess.close()
