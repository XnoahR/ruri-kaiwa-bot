"""Sambat resmi di bot.py: jeda_dengar/lanjut_dengar + guard live_aktif.

Perubahan pada kode utama cuma ini, jadi ini pula yang paling berhak dites.
Butuh discord untuk mengimpor bot.py; tanpa itu dilewati (sama seperti
berkas tes lain yang menyentuh modul Discord).
"""

import asyncio
import unittest

try:
    from ruri import bot as B
    from ruri import config
except ImportError:
    B = None


class VC:
    def __init__(self):
        self.listening = False
        self.connected = True
        self.sink = None
        self.n_listen = 0
        self.n_stop = 0

    def is_connected(self):
        return self.connected

    def is_playing(self):
        return False

    def is_listening(self):
        return self.listening

    def listen(self, sink):
        self.n_listen += 1
        self.sink = sink
        self.listening = True

    def stop_listening(self):
        self.n_stop += 1
        self.listening = False


class Guild:
    def __init__(self):
        self.id = 7
        self.voice_client = VC()
        self.name = "g"


class Bot:
    def __init__(self):
        self.guild = Guild()
        self.guilds = [self.guild]

    def get_guild(self, gid):
        return self.guild if gid == self.guild.id else None

    def get_user(self, uid):
        return None


@unittest.skipIf(B is None, "discord tidak terpasang")
class Sambat(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = Bot()
        self.kaiwa = B.Kaiwa(self.bot, dict(config.DEFAULTS))

    async def test_jeda_dengar_melepas_sink_utama(self):
        g = self.bot.guild
        self.kaiwa.sinks[g.id] = B.KaiwaSink(self.kaiwa, g.id)
        g.voice_client.listen(self.kaiwa.sinks[g.id])
        self.kaiwa.jeda_dengar(g.id)
        self.assertIn(g.id, self.kaiwa.live_aktif)
        self.assertNotIn(g.id, self.kaiwa.sinks)

    async def test_lanjut_dengar_memasang_kembali(self):
        g = self.bot.guild
        vc = g.voice_client
        vc.listen(object())                      # sink "live"
        self.kaiwa.jeda_dengar(g.id)
        await self.kaiwa.lanjut_dengar(g.id)
        self.assertNotIn(g.id, self.kaiwa.live_aktif)
        self.assertIsInstance(self.kaiwa.sinks.get(g.id), B.KaiwaSink)
        self.assertTrue(vc.listening)
        self.assertEqual(vc.n_listen, 2)         # pasang ulang tanpa menunggu 30 dtk
        # watchers berjalan: matikan rapi
        if self.kaiwa._watcher and not self.kaiwa._watcher.done():
            self.kaiwa._watcher.cancel()
            try:
                await self.kaiwa._watcher
            except asyncio.CancelledError:
                pass

    async def test_lanjut_dengar_tanpa_voice_hanya_bersihkan_flag(self):
        self.kaiwa.jeda_dengar(999)
        await self.kaiwa.lanjut_dengar(999)
        self.assertNotIn(999, self.kaiwa.live_aktif)
        self.assertNotIn(999, self.kaiwa.sinks)

    async def test_penjaga_tidak_merebut_sink_dari_live(self):
        """Inti isolasi: selama live_aktif, _pastikan_masuk lewat."""
        g = self.bot.guild
        g.voice_client.listening = True          # "telinga live"
        self.kaiwa.live_aktif.add(g.id)
        self.kaiwa.cfg["auto_join"] = True
        self.kaiwa.cfg["voice_channel"] = ""     # tak ada tujuan -> tak ada kerja
        await self.kaiwa._pastikan_masuk()       # harus no-op untuk gid live
        self.assertEqual(g.voice_client.n_listen, 0)
        self.assertNotIn(g.id, self.kaiwa.sinks)


if __name__ == "__main__":
    unittest.main()
