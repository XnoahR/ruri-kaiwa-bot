"""Alur live_on/!live off -- ditulis SEBELUM perbaikan bug 'LiveSink cleanup'.

Empat tes di file ini harus MERAH pada kode versi server (commit yang gagal
di CI dan membuat bot terjepit tuli), dan hijau setelah perbaikan. Kalau salah
satunya diam-diam kembali merah, berarti asersi abstrak/rollback ada yang
dilanggar lagi.
"""

import asyncio
import copy
import types
import unittest

try:
    from ruri import bot as B
    from ruri import config
    from ruri.live import client as CL
    from ruri.live import cog as CG
except ImportError:
    B = CL = CG = None


class WSKuat:
    """ws sekali-pakai: setupComplete, lalu menggantung sampai ditutup.

    Meniru koneksi hidup; hentikan() melepas wait-nya seperti close sungguhan.
    """

    def __init__(self, frames):
        self._frames = list(frames)
        self._tertutup = asyncio.Event()
        self.sent = []

    async def send(self, text):
        self.sent.append(text)

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
        self._tertutup.set()


class VC:
    def __init__(self, cid=11, listen_gagal=False):
        self.channel = types.SimpleNamespace(id=cid, name="vc")
        self.listening = False
        self.n_gagal = 1 if listen_gagal else 0
        self.n_listen = 0
        self.n_stop = 0

    def is_connected(self):
        return True

    def is_playing(self):
        return False

    def is_listening(self):
        return self.listening

    def listen(self, sink):
        self.n_listen += 1
        if self.n_gagal:                      # hanya percobaan pertama yang gagal
            self.n_gagal -= 1
            raise RuntimeError("sink lain sedang terpasang")
        self.listening = True

    def stop_listening(self):
        self.n_stop += 1
        self.listening = False

    def stop(self):
        pass


class Guild:
    def __init__(self, vc):
        self.id = 7
        self.name = "g"
        self.voice_client = vc
        self.text_channels = []
        self.voice_channels = [vc.channel]

    def get_channel(self, cid):
        return None


class BotStub:
    def __init__(self, guild):
        self.guild = guild
        self.guilds = [guild]
        self.user = None

    def get_guild(self, gid):
        return self.guild if gid == self.guild.id else None


class Chan:
    def __init__(self):
        self.kirin = []

    async def send(self, *a, **k):
        self.kirin.append(str(a[0] if a else k))


def ctx_for(guild, out_ch):
    chan = guild.voice_client.channel
    author = types.SimpleNamespace(
        id=2, voice=types.SimpleNamespace(channel=chan), mention="")
    async def send(*a, **k):
        out_ch.kirin.append(str(a[0] if a else k))
    return types.SimpleNamespace(author=author, guild=guild,
                                 channel=out_ch, send=send)


def panggil(cog, nama, ctx):
    """Atribut perintah di dalam Cog bisa berupa function terikat ATAU objek
    Command yang .callback-nya UNBOUND (per 2.7). Semua bentuk dipanggil di
    sini supaya tes tidak pecah saat mekanisme discord.py bergeser."""
    import inspect
    f = getattr(cog, nama)
    cb = f if inspect.isroutine(f) else f.callback
    params = list(inspect.signature(cb).parameters)
    if params and params[0] == "self":
        return cb(cog, ctx)
    return cb(ctx)


@unittest.skipIf(CG is None, "discord tidak terpasang")
class Guard(unittest.TestCase):
    def test_semua_sink_repo_boleh_diinstansiasi(self):
        """Jaring anti-'AudioFrame kedua': kelas abstrak tak-penuh itu LEGAL
        saat definisi dan baru meledak saat instansiasi -- di produksi. Jadi
        yang diuji adalah kelengkapan __abstractmethods__ tiap AudioSink."""
        for cls in (B.KaiwaSink, CG.LiveSink):
            self.assertEqual(set(cls.__abstractmethods__), set(),
                             "%s belum mengimplementasikan %s"
                             % (cls.__name__, cls.__abstractmethods__))


@unittest.skipIf(CG is None, "discord tidak terpasang")
class Alur(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cfg = copy.deepcopy(config.DEFAULTS)
        self.cfg["stt"]["api_key"] = "KUNCI-UJI"
        self.ws_yg_dibuat = []

        async def sambungkan_palsu(url):
            ws = WSKuat([{"setupComplete": {}}])
            self.ws_yg_dibuat.append(ws)
            return ws
        self._sambungkan_asli = CL.sambungkan
        CL.sambungkan = sambungkan_palsu

    async def asyncTearDown(self):
        CL.sambungkan = self._sambungkan_asli

    def _pasang(self, vc):
        self.guild = Guild(vc)
        self.out = Chan()
        self.botstub = BotStub(self.guild)
        self.botstub.loop = asyncio.get_event_loop()   # live_on membuat task
        self.kaiwa = B.Kaiwa(self.botstub, self.cfg)
        self.live = CG.LiveKaiwa(self.botstub, self.cfg, self.kaiwa)
        return ctx_for(self.guild, self.out)

    async def test_sukses_terima_serah_dan_bisa_keluar(self):
        ctx = self._pasang(VC())
        await panggil(self.live, "live_on", ctx)
        self.assertIn(7, self.live.sesi)
        self.assertIn(7, self.kaiwa.live_aktif)
        self.assertNotIn(7, self.kaiwa.sinks)             # sink utama dilepas
        self.assertTrue(self.guild.voice_client.listening)  # sink live terpasang
        # setup sungguhan terkirim, dan opening terkirim setelah setupComplete
        await asyncio.sleep(0.05)
        self.assertTrue(self.ws_yg_dibuat[0].sent[0].startswith('{"setup"'))

        await panggil(self.live, "live_off", ctx)
        self.assertEqual(self.live.sesi, {})
        self.assertNotIn(7, self.kaiwa.live_aktif)
        self.assertIsInstance(self.kaiwa.sinks.get(7), B.KaiwaSink)  # telinga balik

    async def test_gagal_listen_tidak_meninggalkan_bot_tuli(self):
        """Inti Bug B: dulu flag terpasang SEBELUM pasang sink -> crash = tuli
        permanen. Sekarang penyerahan harus berhasil dulu atau di-rollback."""
        ctx = self._pasang(VC(listen_gagal=True))
        await panggil(self.live, "live_on", ctx)            # TIDAK boleh melempar
        self.assertNotIn(7, self.live.sesi)
        self.assertNotIn(7, self.kaiwa.live_aktif)       # flag di-rollback
        self.assertIsInstance(self.kaiwa.sinks.get(7), B.KaiwaSink)
        self.assertTrue(self.guild.voice_client.listening)  # telinga terpasang
        self.assertTrue(any("nggak bisa mulai mendengar" in m for m in
                            self.out.kirin), self.out.kirin)

    async def test_off_membersihkan_flag_sisa_crash_lama(self):
        """Jalur pemulihan mandiri: state terjepit (flag tanpa sesi) seperti
        yang ditinggalkan bug server -- !live off harus bisa memperbaikinya."""
        ctx = self._pasang(VC())
        self.kaiwa.jeda_dengar(7)                        # simulasi sisa crash
        self.assertIn(7, self.kaiwa.live_aktif)
        self.assertEqual(self.live.sesi, {})
        await panggil(self.live, "live_off", ctx)
        self.assertNotIn(7, self.kaiwa.live_aktif)
        self.assertIsInstance(self.kaiwa.sinks.get(7), B.KaiwaSink)
        self.assertTrue(self.guild.voice_client.listening)


if __name__ == "__main__":
    unittest.main()
