"""Ingatan per orang, dan umurnya.

Kalau bagian ini rusak, rusaknya tidak kelihatan sebagai galat: dia kelihatan
sebagai bot yang menjawab orang asing dengan lanjutan obrolan orang lain.
"""

import time
import unittest

from ruri import session
from ruri.session import Session

CFG = {
    "kaiwa": {"level": "N4", "memory_idle_minutes": 30},
    "llm": {"max_history_turns": 12},
}


def cfg(**kaiwa):
    isi = dict(CFG["kaiwa"]); isi.update(kaiwa)
    return {"kaiwa": isi, "llm": dict(CFG["llm"])}


class IngatanPerOrang(unittest.TestCase):
    def test_obrolan_satu_orang_tidak_bocor_ke_orang_lain(self):
        s = Session(cfg())
        s.add_user(111, "ラーメン食べたい")
        s.add_bot(111, "いいね")
        self.assertEqual(s.history(s.cfg, 222), [],
                         "orang kedua harusnya mulai dari kosong")
        self.assertEqual(len(s.history(s.cfg, 111)), 2)

    def test_tiap_orang_menyimpan_gilirannya_sendiri(self):
        s = Session(cfg())
        s.add_user(111, "A"); s.add_bot(111, "a")
        s.add_user(222, "B"); s.add_bot(222, "b")
        self.assertEqual([t["content"] for t in s.history(s.cfg, 111)], ["A", "a"])
        self.assertEqual([t["content"] for t in s.history(s.cfg, 222)], ["B", "b"])

    def test_tanpa_id_jatuh_ke_ingatan_bersama(self):
        """Jalur yang tidak tahu siapa yang bicara tetap harus jalan."""
        s = Session(cfg())
        s.add_user(None, "だれ？")
        self.assertEqual(len(s.history(s.cfg, None)), 1)
        self.assertEqual(s.history(s.cfg, 111), [])

    def test_reset_cuma_menghapus_miliknya(self):
        s = Session(cfg())
        s.add_user(111, "A"); s.add_user(222, "B")
        s.reset(111)
        self.assertEqual(s.history(s.cfg, 111), [])
        self.assertEqual(len(s.history(s.cfg, 222)), 1)

    def test_reset_semua_menghapus_semuanya(self):
        s = Session(cfg())
        s.add_user(111, "A"); s.add_user(222, "B")
        s.reset(semua=True)
        self.assertEqual(s.history(s.cfg, 111), [])
        self.assertEqual(s.history(s.cfg, 222), [])

    def test_batas_riwayat_dihitung_per_orang(self):
        s = Session(cfg())
        c = {"kaiwa": CFG["kaiwa"], "llm": {"max_history_turns": 2}}
        for i in range(10):
            s.add_user(111, str(i))
        self.assertEqual(len(s.history(c, 111)), 4)


class UmurIngatan(unittest.TestCase):
    def test_lupa_setelah_lama_nganggur(self):
        s = Session(cfg(memory_idle_minutes=30))
        s.add_user(111, "ラーメン食べたい")
        s.orang(111).sentuh = time.monotonic() - 31 * 60
        self.assertEqual(s.history(s.cfg, 111), [],
                         "obrolan yang ditinggal setengah jam bukan obrolan yang sama")

    def test_masih_ingat_kalau_belum_lewat(self):
        s = Session(cfg(memory_idle_minutes=30))
        s.add_user(111, "ラーメン食べたい")
        s.orang(111).sentuh = time.monotonic() - 29 * 60
        self.assertEqual(len(s.history(s.cfg, 111)), 1)

    def test_nol_berarti_tidak_pernah_lupa(self):
        s = Session(cfg(memory_idle_minutes=0))
        s.add_user(111, "A")
        s.orang(111).sentuh = time.monotonic() - 86400
        self.assertEqual(len(s.history(s.cfg, 111)), 1)

    def test_nilai_ngawur_jatuh_ke_bawaan(self):
        for nilai in ("banyak", None, {}):
            with self.subTest(nilai=nilai):
                s = Session(cfg(memory_idle_minutes=nilai))
                self.assertEqual(s._kadaluarsa(),
                                 session.KADALUARSA_MENIT * 60)


class KalimatTerakhir(unittest.TestCase):
    def test_milikmu_lebih_dulu(self):
        s = Session(cfg())
        s.add_bot(111, "こんにちは")
        s.add_bot(222, "おはよう")
        self.assertEqual(s.terakhir(111), "こんにちは")

    def test_jatuh_ke_ruangan_kalau_kamu_belum_bicara(self):
        """Kamu baru masuk dan mau tahu bacaan kalimat yang barusan terdengar."""
        s = Session(cfg())
        s.add_bot(111, "おはよう")
        self.assertEqual(s.terakhir(999), "おはよう")

    def test_buang_terakhir_tidak_meledak_saat_kosong(self):
        s = Session(cfg())
        s.buang_terakhir(111)
        self.assertEqual(s.history(s.cfg, 111), [])


if __name__ == "__main__":
    unittest.main()
