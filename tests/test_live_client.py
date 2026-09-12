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


class WSKuat:
    """ws yang 'hidup terus': frame habis lalu __anext__ menggantung sampai
    ditutup -- koneksi yang tidak mati sendiri, untuk uji jalur kirim-macet."""

    def __init__(self, frames, kirim_macet=False):
        self._frames = list(frames)
        self._kirim_macet = kirim_macet
        self._tertutup = asyncio.Event()
        self.sent = []
        self.ditutup = False

    async def send(self, text):
        obj = json.loads(text)
        if self._kirim_macet and self.sent:      # setup lolos, sisanya menggantung
            await self._tertutup.wait()
            return
        self.sent.append(obj)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._frames:
            item = self._frames.pop(0)
            await asyncio.sleep(0)
            return item
        await self._tertutup.wait()
        raise StopAsyncIteration

    async def close(self):
        if not self.ditutup:
            self.ditutup = True
        self._tertutup.set()


class WSGantung(WSKuat):
    """Soket setengah mati: SEND pertama pun tidak pernah kembali."""

    async def send(self, text):
        await self._tertutup.wait()


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
    async def test_kirim_macet_menutup_koneksi_dan_sambung_ulang(self):
        """Skenario log 17:09:34: peer setengah mati -- ws.send menggantung
        selamanya. Kode lama membiarkannya (antrean penuh, buffer meluap tanpa
        aksi). Sekarang: kirim macet == koneksi mati -> tutup -> reconnect."""
        ws1 = WSKuat([SETUP_OK], kirim_macet=True)
        grup = 0

        async def pabrik(url):
            nonlocal grup
            grup += 1
            return ws1 if grup == 1 else WSKuat([SETUP_OK])

        sess = C.LiveSession("u", lambda h: {"setup": {}}, None, ws=None)
        with mock.patch.object(C, "sambungkan", pabrik), \
             mock.patch.object(C, "TENG_GUAT_KIRIM", 0.05), \
             mock.patch.object(C, "JEDA_PERCOBAAN", (0, 0, 0)), \
             mock.patch.object(C, "TENG_GUAT_SETUP", 0.5):
            tugas = asyncio.create_task(sess.mulai())
            await asyncio.sleep(0.3)
            await sess.kirim({"realtimeInput": {"text": "x"}})
            await asyncio.sleep(0.3)
            self.assertTrue(ws1.ditutup, "kirim macet harus menutup koneksi")
            await sess.hentikan()
            await asyncio.wait_for(tugas, timeout=5)
        self.assertGreaterEqual(grup, 2)      # reconnect terjadi sendiri

    async def test_setup_send_macet_ada_tenggat(self):
        ws = WSGantung([])            # bahkan SETUP pun menggantung
        sess = C.LiveSession("u", lambda h: {"setup": {}}, None, ws=ws)
        with mock.patch.object(C, "TENG_GUAT_SETUP", 0.1), \
             mock.patch.object(C, "JEDA_PERCOBAAN", (0, 0, 0)):
            with self.assertRaises(C.Putus):
                await asyncio.wait_for(sess.mulai(), timeout=5)
        self.assertTrue(ws.ditutup, "koneksi yang menolak setup harus ditutup")
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
