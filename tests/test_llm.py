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


class BatasPemakaian(unittest.TestCase):
    """Provider gratis kena batas pada jam sibuk. Satu giliran yang hilang
    gara-gara itu terasa seperti bot yang rusak."""

    def test_mengenali_berbagai_bentuk_pesannya(self):
        for teks in ("HTTP 429 from the API",
                     '{"type":"FreeUsageLimitError"}',
                     "Rate limit exceeded. Please try again later.",
                     "You exceeded your current quota"):
            with self.subTest(teks):
                self.assertTrue(llm.is_rate_limited(teks))

    def test_tidak_salah_menuduh_kegagalan_lain(self):
        for teks in ("connection refused", "invalid api key", "500 server error"):
            with self.subTest(teks):
                self.assertFalse(llm.is_rate_limited(teks))


class RantaiProvider(unittest.TestCase):
    def setUp(self):
        self.asli = llm.complete
        # Jeda itu keadaan tingkat modul: tanpa dibersihkan, jeda dari satu tes
        # bocor ke tes berikutnya dan bikin kegagalan yang membingungkan.
        llm.lupakan_jeda()
        self.dipanggil = []

    def tearDown(self):
        llm.complete = self.asli
        llm.lupakan_jeda()

    def pasang(self, hasil):
        """hasil: dict nama -> teks jawaban, atau Exception untuk gagal."""
        def palsu(cfg, provider, system, messages, max_tokens=0):
            nama = provider["name"]
            self.dipanggil.append(nama)
            keluar = hasil[nama]
            if isinstance(keluar, Exception):
                raise keluar
            return keluar
        llm.complete = palsu

    RANTAI = [{"name": "A"}, {"name": "B"}]

    def test_provider_kedua_menambal_yang_pertama(self):
        self.pasang({"A": RuntimeError("HTTP 429"), "B": "<balas>はい</balas>"})
        teks, dipakai = llm.complete_any({}, self.RANTAI, "sys", [])
        self.assertEqual(dipakai["name"], "B")
        self.assertEqual(self.dipanggil, ["A", "B"])
        self.assertIn("はい", teks)

    def test_yang_pertama_berhasil_tidak_menyentuh_cadangan(self):
        self.pasang({"A": "ok", "B": RuntimeError("jangan dipanggil")})
        _teks, dipakai = llm.complete_any({}, self.RANTAI, "sys", [])
        self.assertEqual(dipakai["name"], "A")
        self.assertEqual(self.dipanggil, ["A"])

    def test_semua_gagal_melapor_sebabnya(self):
        self.pasang({"A": RuntimeError("HTTP 429"),
                     "B": RuntimeError("FreeUsageLimitError")})
        with self.assertRaises(llm.SemuaGagal) as ctx:
            llm.complete_any({}, self.RANTAI, "sys", [])
        self.assertTrue(ctx.exception.kena_batas)
        self.assertEqual(len(ctx.exception.kegagalan), 2)

    def test_kegagalan_campuran_bukan_soal_batas(self):
        """Kalau satu putus koneksi, pesannya jangan bilang 'kena batas'."""
        self.pasang({"A": RuntimeError("HTTP 429"),
                     "B": RuntimeError("connection refused")})
        with self.assertRaises(llm.SemuaGagal) as ctx:
            llm.complete_any({}, self.RANTAI, "sys", [])
        self.assertFalse(ctx.exception.kena_batas)


class JedaProvider(unittest.TestCase):
    """Provider yang baru kena batas dilewati sebentar, supaya tiap giliran
    tidak membuang satu panggilan gagal dulu."""

    def setUp(self):
        self.asli = llm.complete
        llm.lupakan_jeda()
        self.dipanggil = []

    def tearDown(self):
        llm.complete = self.asli
        llm.lupakan_jeda()

    def pasang(self, hasil):
        def palsu(cfg, provider, system, messages, max_tokens=0):
            self.dipanggil.append(provider["name"])
            keluar = hasil[provider["name"]]
            if isinstance(keluar, Exception):
                raise keluar
            return keluar
        llm.complete = palsu

    RANTAI = [{"name": "A"}, {"name": "B"}]

    def test_yang_kena_batas_dilewati_di_giliran_berikutnya(self):
        self.pasang({"A": RuntimeError("HTTP 429"), "B": "ok"})
        llm.complete_any({}, self.RANTAI, "s", [])
        self.assertEqual(self.dipanggil, ["A", "B"])
        self.dipanggil.clear()
        llm.complete_any({}, self.RANTAI, "s", [])
        self.assertEqual(self.dipanggil, ["B"], "A harusnya dilewati")

    def test_kegagalan_biasa_tidak_menjedakan(self):
        """Putus koneksi itu sesaat; jangan dihukum sepuluh menit."""
        self.pasang({"A": RuntimeError("connection refused"), "B": "ok"})
        llm.complete_any({}, self.RANTAI, "s", [])
        self.assertFalse(llm.dijeda("A"))

    def test_berhasil_membatalkan_jedanya(self):
        llm.jedakan("A")
        self.pasang({"A": "ok", "B": "ok"})
        llm.complete_any({}, [{"name": "A"}], "s", [])
        self.assertFalse(llm.dijeda("A"))

    def test_semua_dijeda_tetap_dicoba(self):
        """Jeda itu tebakan; jangan sampai bikin dia diam total."""
        llm.jedakan("A"); llm.jedakan("B")
        self.pasang({"A": RuntimeError("HTTP 429"), "B": "ok"})
        teks, dipakai = llm.complete_any({}, self.RANTAI, "s", [])
        self.assertEqual(dipakai["name"], "B")
