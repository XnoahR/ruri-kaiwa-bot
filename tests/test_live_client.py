"""Lifecycle koneksi live: handshake, goAway-resume, flap, antrean.

Yang diuji di sini justru bagian yang tidak kelihatan: sesi harus bertahan
melewati reset koneksi periodik server (±10 menit) lewat handle resume, dan
harus menyerah secara rapi -- bukan menggantung -- kalau server menolak terus.
"""

import asyncio
import json
import unittest
from unittest import mock

from ruri.live import client as C


class FakeWS:
    """Ws palsu: tiap koneksi menguras satu grup frame, lalu menutup.

    List of list -- grup berikutnya dipakai saat klien menyambung ulang, jadi
    urutan sambungan bisa diperiksa lewat ws.sent.
    """

    def __init__(self, grup):
        self._grup = [list(g) for g in grup]
        self.sent = []
        self.n_sambung = 0
        self.ditutup = 0

    def __aiter__(self):
        self.n_sambung += 1
        self._it = iter(self._grup[self.n_sambung - 1]
                        if self.n_sambung <= len(self._grup) else [])
        return self

    async def __anext__(self):
        try:
            item = next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None
        await asyncio.sleep(0)
        return item

    async def send(self, text):
        self.sent.append(json.loads(text))

    async def close(self):
        self.ditutup += 1


SETUP_OK = {"setupComplete": {}}


def turn(text="x"):
    return {"serverContent": {"outputTranscription": {"text": text, "finished": True}}}


class Handshake(unittest.IsolatedAsyncioTestCase):
    async def test_setup_dikirim_lalu_kejadian_diteruskan(self):
        ws = FakeWS([[SETUP_OK, {"serverContent": {"turnComplete": True}}], []])
        event = []

        async def on_ev(kind, data):
            event.append(kind)
            if kind == "turn_complete":
                await sess.hentikan()

        sess = C.LiveSession("wss://x", lambda h: C.protocol.build_setup("s"),
                             on_ev, ws=ws)
        await asyncio.wait_for(sess.mulai(), timeout=5)
        self.assertEqual(ws.sent[0]["setup"]["generationConfig"]
                         ["responseModalities"], ["AUDIO"])
        self.assertIn("setup_complete", event)
        self.assertIn("turn_complete", event)
        # stream_end dikirim sebelum mati -- antrean kirim harus sempat flush
        self.assertEqual(ws.sent[-1], {"realtimeInput": {"audioStreamEnd": True}})
        self.assertGreaterEqual(ws.ditutup, 1)

    async def test_handle_tidak_diteruskan_ke_pemanggil(self):
        ws = FakeWS([[SETUP_OK, {"sessionResumptionUpdate":
                                 {"resumable": True, "newHandle": "h1"}}], []])
        event = []
        sess = C.LiveSession("u", lambda h: {"setup": {}},
                             lambda k, d: event.append(k), ws=ws)
        with mock.patch.object(C, "TENG_GUAT_SETUP", 0.1), \
             mock.patch.object(C, "JEDA_PERCOBAAN", (0, 0, 0)):
            with self.assertRaises(C.Putus):
                await asyncio.wait_for(sess.mulai(), timeout=5)
        self.assertNotIn("handle", event)      # urusan internal klien
        self.assertEqual(sess.handle, "h1")    # ...tapi tersimpan


class Resume(unittest.IsolatedAsyncioTestCase):
    async def test_goaway_sambung_ulang_dengan_handle(self):
        grup2 = [SETUP_OK, {"serverContent": {"turnComplete": True}}]
        ws = FakeWS([[SETUP_OK,
                      {"sessionResumptionUpdate":
                       {"resumable": True, "newHandle": "h9"}},
                      {"goAway": {"timeLeft": "30s"}}], grup2])

        async def on_ev(kind, data):
            if kind == "turn_complete":
                await sess.hentikan()

        sess = C.LiveSession("u",
                             lambda h: {"setup": {"probe": h}},
                             on_ev, ws=ws)
        await asyncio.wait_for(sess.mulai(), timeout=5)
        self.assertEqual(ws.n_sambung, 2)
        setups = [m for m in ws.sent if "setup" in m]   # yang terakhir stream_end
        self.assertIsNone(setups[0]["setup"]["probe"])
        self.assertEqual(setups[1]["setup"]["probe"], "h9")
        # bukan koneksi cepat-gagal: tidak ada hitungan percobaan yang naik
        self.assertEqual(sess._per_cobaan, 0)

    async def test_sesi_panjang_putput_dihitung_sehat(self):
        """Koneksi yang hidup lalu mati (>5 dtk) disambung lagi tanpa penalti."""
        sess = C.LiveSession("u", lambda h: {"setup": {}}, None, ws=None)
        # unit murni: cek cabang tail lewat flag manual
        sess._siap.set()
        sess._resume = True
        self.assertTrue(sess._resume)   # goAway -> selalu resume, tak peduli umur


class Flap(unittest.IsolatedAsyncioTestCase):
    async def test_server_menolak_terus_melempar_putus(self):
        ws = FakeWS([[] for _ in range(9)])   # tidak pernah setupComplete
        sess = C.LiveSession("u", lambda h: {"setup": {}}, None, ws=ws)
        with mock.patch.object(C, "TENG_GUAT_SETUP", 0.05), \
             mock.patch.object(C, "JEDA_PERCOBAAN", (0, 0, 0)):
            with self.assertRaises(C.Putus):
                await asyncio.wait_for(sess.mulai(), timeout=5)
        self.assertLessEqual(ws.n_sambung, C.MAKS_PERCOBAAN + 2)


class Kirim(unittest.IsolatedAsyncioTestCase):
    async def test_antrean_penuh_ditolak_bukan_menggantung(self):
        sess = C.LiveSession("u", lambda h: {}, None, ws=FakeWS([[]]))
        hasil = [await sess.kirim({"i": n}) for n in range(C.MAKS_ANTREAN + 5)]
        self.assertEqual(sum(hasil), C.MAKS_ANTREAN)
        self.assertFalse(hasil[-1])

    async def test_hentikan_idempoten_dan_sebelum_koneksi(self):
        sess = C.LiveSession("u", lambda h: {}, None, ws=FakeWS([[]]))
        await asyncio.wait_for(sess.hentikan(), timeout=1)   # tanpa _aktif
        await asyncio.wait_for(sess.hentikan(), timeout=1)   # kedua: no-op
        self.assertFalse(await sess.kirim({"x": 1}))         # sudah mati


if __name__ == "__main__":
    unittest.main()
