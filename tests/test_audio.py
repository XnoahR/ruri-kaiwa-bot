"""Ukuran suara dan konversi format."""

import math
import struct
import unittest

from ruri import audio


def nada(detik=1.0, amplitudo=9000, hz=440):
    n = int(audio.IN_RATE * detik)
    keluar = bytearray()
    for i in range(n):
        v = int(amplitudo * math.sin(2 * math.pi * hz * i / audio.IN_RATE))
        keluar += struct.pack("<hh", v, v)
    return bytes(keluar)


class Ukuran(unittest.TestCase):
    def test_durasi(self):
        self.assertEqual(audio.duration_ms(nada(1.0)), 1000)
        self.assertEqual(audio.duration_ms(b""), 0)

    def test_rms_membedakan_bicara_dari_senyap(self):
        self.assertGreater(audio.rms(nada(0.5)), 0.1)
        self.assertEqual(audio.rms(b"\x00" * 4000), 0.0)
        self.assertEqual(audio.rms(b""), 0.0)

    def test_rms_tahan_potongan_ganjil(self):
        """Buffer bisa berakhir di tengah cuplikan; itu tidak boleh melempar."""
        self.assertGreaterEqual(audio.rms(nada(0.1) + b"\x01"), 0.0)


@unittest.skipUnless(audio.have_ffmpeg(), "ffmpeg tidak ada")
class Konversi(unittest.TestCase):
    def test_wav_mono_16k(self):
        keluar = audio.to_upload(nada(1.0), "wav")
        self.assertEqual(keluar[:4], b"RIFF")
        # 1 detik mono 16-bit pada 16kHz ~= 32000 bita, plus header
        self.assertAlmostEqual(len(keluar), 32000, delta=2000)

    def test_kosong_tetap_kosong(self):
        self.assertEqual(audio.to_upload(b"", "wav"), b"")


if __name__ == "__main__":
    unittest.main()
