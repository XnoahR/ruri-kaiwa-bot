"""Helper audio/teks mode live."""

import struct
import unittest

from ruri.live import pipe


class Downmix(unittest.TestCase):
    def stereo(self, *pairs):
        return b"".join(struct.pack("<hh", *p) for p in pairs)

    def test_rata_rata_kiri_kanan(self):
        out = pipe.downmix(self.stereo((0, 2), (4, 6)))
        self.assertEqual(struct.unpack("<hh", out), (1, 5))

    def test_potongan_ganjil_dibuang(self):
        """Buffer bisa berakhir di tengah frame stereo; byte setengah sampel
        tidak boleh diteruskan -- merusak fase seluruh stream sesudahnya."""
        penuh = self.stereo((10, 20))
        out = pipe.downmix(penuh + b"\x01\x02\x03")
        self.assertEqual(out, struct.pack("<h", 15))

    def test_kosong(self):
        self.assertEqual(pipe.downmix(b""), b"")
        self.assertEqual(pipe.downmix(b"\x00\x01\x02"), b"")


class Chunk(unittest.TestCase):
    def test_ambil_penuh_dan_sisa_tinggal(self):
        buf = bytearray(b"abcdefghij")
        self.assertEqual(pipe.take_chunk(buf, 4), b"abcd")
        self.assertEqual(bytes(buf), b"efghij")

    def test_akhir_boleh_pendek(self):
        buf = bytearray(b"abc")
        self.assertEqual(pipe.take_chunk(buf, 4), b"abc")
        self.assertEqual(pipe.take_chunk(buf, 4), b"")

    def test_kosong(self):
        self.assertEqual(pipe.take_chunk(bytearray(), 8), b"")


class MergeTeks(unittest.TestCase):
    def test_delta_ditempel(self):
        self.assertEqual(pipe.merge_teks("こん", "にちは"), "こんにちは")

    def test_snapshot_penuh_mengganti(self):
        lama = "今日はいい天気"
        snapshot = "今日はいい天気ですね"
        self.assertEqual(pipe.merge_teks(lama, snapshot), snapshot)

    def test_kosong_sisi_sisi(self):
        self.assertEqual(pipe.merge_teks("", "halo"), "halo")
        self.assertEqual(pipe.merge_teks("halo", ""), "halo")
        self.assertEqual(pipe.merge_teks("", ""), "")


if __name__ == "__main__":
    unittest.main()
