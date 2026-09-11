"""Jembatan audio cog live: Sumber (24k mono -> 48k stereo).

Butuh discord (impor modul) dan ffmpeg (konversi) -- di mesin tanpa keduanya
dilewati, persis pola test_audio yang skipUnless ffmpeg.
"""

import math
import shutil
import struct
import unittest

try:
    import discord  # noqa: F401
    from ruri.live import cog as C
except ImportError:
    C = None

@unittest.skipIf(C is None, "discord/voice-recv tidak terpasang")
@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg tidak ada")
class Sumber(unittest.TestCase):
    def test_24k_mono_jadi_48k_stereo(self):
        src = C.Sumber()
        n = 24000                                  # 1 detik
        nada = struct.pack("<%dh" % n, *(int(9000 * math.sin(i * 0.25))
                                         for i in range(n)))
        src.push(nada)
        src.tutup_input()
        out = b""
        while True:
            frame = src.read()          # bytes mentah -- kontrak discord 2.x
            if not frame:
                break
            out += frame
            if len(out) > 10 * 48000 * 4:
                self.fail("tidak habis-habis")
        src.cleanup()
        # 1 detik @48k stereo 16-bit = 192000 B; toleransi ekor/buffer.
        self.assertGreater(len(out), 150_000)
        self.assertEqual(len(out) % C.FRAME_BYTES, 0)

    def test_push_setelah_tutup_diam_diam_saja(self):
        src = C.Sumber()
        src.tutup_input()
        src.push(b"\x00\x10" * 100)                # tidak boleh meledak
        src.cleanup()

    def test_eof_bisa_dibaca_sampai_habis(self):
        src = C.Sumber()
        src.cleanup()                              # mati sebelum lahir
        self.assertIsNone(src.read())


if __name__ == "__main__":
    unittest.main()
