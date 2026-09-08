"""Pemisahan balasan dari penalaran dan koreksi.

Ini bagian yang paling sering rusak diam-diam: kalau penandanya meleset,
penalaran model bocor ke layar dan ikut dibacakan mesin suara.
"""

import unittest

from ruri import llm


class PisahBalasan(unittest.TestCase):
    def test_penalaran_di_luar_penanda_dibuang(self):
        mentah = (
            'Kita perlu merespons dalam bahasa Jepang. User bilang "ruri sinaga".\n\n'
            "<balas>\nおはよう。よく眠れた？\n</balas>"
        )
        kata, fix = llm.split_reply(mentah)
        self.assertEqual(kata, "おはよう。よく眠れた？")
        self.assertNotIn("Kita perlu", kata)
        self.assertIsNone(fix)

    def test_koreksi_terbaca_dan_tidak_ikut_diucapkan(self):
        mentah = (
            "<balas>\nそう。どんな映画だった？\n</balas>\n"
            "<koreksi>\nasli: 昨日映画を見たです\nbenar: 昨日映画を見ました\n"
            "kenapa: bentuk lampau tidak digabung dengan です\n</koreksi>"
        )
        kata, fix = llm.split_reply(mentah)
        self.assertEqual(kata, "そう。どんな映画だった？")
        self.assertNotIn("<", kata)
        self.assertEqual(fix["benar"], "昨日映画を見ました")
        self.assertIn("です", fix["kenapa"])

    def test_tanpa_penanda_tetap_menjawab(self):
        """Model yang lupa menulis penanda tidak boleh bikin dia bisu."""
        kata, fix = llm.split_reply("おはよう。")
        self.assertEqual(kata, "おはよう。")
        self.assertIsNone(fix)

    def test_blok_kepotong_di_tengah_stream(self):
        """max_tokens habis di tengah blok -- yang sempat ditulis tetap dipakai."""
        kata, fix = llm.split_reply("<balas>\nはい。\n</balas>\n<koreksi>\nbenar: これです")
        self.assertEqual(kata, "はい。")
        self.assertEqual(fix["benar"], "これです")

    def test_penanda_hasil_satu_tembak(self):
        self.assertEqual(llm.extract_result("ngoceh\n<hasil>\nisi\n</hasil>"), "isi")
        self.assertEqual(llm.extract_result("kepotong\n<hasil>\nsisa"), "sisa")
        self.assertEqual(llm.extract_result("tanpa penanda"), "tanpa penanda")


class Prompt(unittest.TestCase):
    def setUp(self):
        from ruri import config

        self.cfg = config.DEFAULTS

    def test_level_terkunci_di_prompt(self):
        for level in llm.LEVELS:
            self.assertIn(level, llm.system_prompt(self.cfg, level))

    def test_level_ngawur_jatuh_ke_n4(self):
        self.assertIn("N4", llm.system_prompt(self.cfg, "N9"))

    def test_aturan_salah_dengar_selalu_ada(self):
        """Tanpa ini dia mengoreksi salah dengar mesin seolah kesalahan pengguna."""
        sp = llm.system_prompt(self.cfg, "N4")
        self.assertIn("pengenalan\nsuara", sp.replace("\r", ""))
        self.assertIn("Jangan pernah mengoreksi nama", sp)

    def test_penanda_balas_diajarkan(self):
        self.assertIn("<balas>", llm.system_prompt(self.cfg, "N4"))


if __name__ == "__main__":
    unittest.main()
