"""Pemisahan balasan dari penalaran dan koreksi.

Ini bagian yang paling sering rusak diam-diam: kalau penandanya meleset,
penalaran model bocor ke layar dan ikut dibacakan mesin suara.
"""

import time
import unittest

from ruri import llm


def K(nama, model=None):
    """Kunci jeda: jeda dicatat per model, bukan per provider."""
    return llm.kunci({"name": nama} if model is None
                     else {"name": nama, "model": model})


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

    def test_memisahkan_jatah_habis_dari_antrean_sesaat(self):
        """Dua-duanya 429, tapi yang satu pulih besok dan yang satu pulih
        detik berikutnya."""
        for teks in ('{"type":"FreeUsageLimitError"}',
                     "You exceeded your current quota",
                     "requests per day limit reached"):
            with self.subTest(habis=teks):
                self.assertTrue(llm.jatah_habis(teks))
        for teks in ("Rate limited. Wait a moment and try again.",
                     "HTTP 429 from the API",
                     "too many requests"):
            with self.subTest(sesaat=teks):
                self.assertFalse(llm.jatah_habis(teks))


class RantaiProvider(unittest.TestCase):
    def setUp(self):
        self.asli = llm.complete
        # Jeda itu keadaan tingkat modul: tanpa dibersihkan, jeda dari satu tes
        # bocor ke tes berikutnya dan bikin kegagalan yang membingungkan.
        llm.lupakan_jeda()
        llm.lupakan_putaran()
        self.jeda_ulang = llm.JEDA_ULANG
        llm.JEDA_ULANG = 0
        self.dipanggil = []

    def tearDown(self):
        llm.complete = self.asli
        llm.lupakan_jeda()
        llm.lupakan_putaran()
        llm.JEDA_ULANG = self.jeda_ulang

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
        # A cuma antre sesaat, jadi dia dicoba sekali lagi; B jatahnya habis
        # dan tidak diulang.
        self.assertEqual(len(ctx.exception.kegagalan), 3)
        self.assertEqual([k for k, _e in ctx.exception.kegagalan],
                         [K("A"), K("B"), K("A")])

    def test_kegagalan_campuran_bukan_soal_batas(self):
        """Kalau satu putus koneksi, pesannya jangan bilang 'kena batas'."""
        self.pasang({"A": RuntimeError("HTTP 429"),
                     "B": RuntimeError("connection refused")})
        with self.assertRaises(llm.SemuaGagal) as ctx:
            llm.complete_any({}, self.RANTAI, "sys", [])
        self.assertFalse(ctx.exception.kena_batas)


class SaringBocoran(unittest.TestCase):
    """Penalaran yang bocor lebih buruk daripada satu model dilewati."""

    def test_menolak_penalaran_yang_diekori_kalimat_jepang(self):
        """Bocoran sering berakhir dengan kalimat yang benar menempel di
        ujungnya; menuntut 'ada satu huruf Jepang' meloloskan paragrafnya."""
        for teks in ("-> Wait, needs to be casual and short. おはよう。",
                     "Japanese only? Yes. Let me answer: おはよう",
                     "Okay so the user greeted me in Japanese, I should reply "
                     "in kind with something short. おはよう"):
            with self.subTest(teks=teks[:30]):
                self.assertFalse(llm.balasan_jepang(teks))

    def test_romaji_sedikit_tetap_lolos(self):
        """Balasan Jepang yang menyelipkan satu kata Latin masih balasan."""
        self.assertTrue(llm.balasan_jepang("<balas>コーヒー飲む？OK？</balas>"))

    def test_menolak_potongan_penalaran(self):
        for teks in ("*   Wait, must be", 'Ruri\'s reaction: "', "<",
                     "Okay, the user said good morning, so I should", ""):
            with self.subTest(teks=teks):
                self.assertFalse(llm.balasan_jepang(teks))

    def test_menerima_balasan_jepang(self):
        for teks in ("<balas>おはよう。よく眠れた？</balas>",
                     "おはよう。",   # tanpa penanda pun sah kalau isinya Jepang
                     "<balas>ラーメン食べたい</balas>"):
            with self.subTest(teks=teks):
                self.assertTrue(llm.balasan_jepang(teks))

    def test_koreksi_saja_bukan_balasan(self):
        """Kalau yang keluar cuma blok koreksi, tidak ada yang bisa diucapkan."""
        self.assertFalse(llm.balasan_jepang(
            "<koreksi>\nasli: それ\nbenar: これ\n</koreksi>"))

    def test_yang_melantur_dilewati_ke_model_berikutnya(self):
        asli = llm.complete
        llm.lupakan_jeda(); llm.lupakan_putaran()
        dipakai = []

        def palsu(cfg, provider, system, messages, max_tokens=0):
            dipakai.append(provider["model"])
            if provider["model"] == "m1":
                return "*   Wait, must be"
            return "<balas>おはよう</balas>"
        llm.complete = palsu
        try:
            teks, p = llm.complete_any(
                {}, [{"name": "G", "models": ["m1", "m2"]}], "s", [],
                saring=llm.balasan_jepang)
        finally:
            llm.complete = asli
            llm.lupakan_jeda(); llm.lupakan_putaran()
        self.assertEqual(dipakai, ["m1", "m2"])
        self.assertEqual(p["model"], "m2")
        self.assertIn("おはよう", teks)


class RotasiModel(unittest.TestCase):
    """Jatah gratis Gemini dihitung per model per hari, jadi delapan model
    berarti delapan jatah -- tapi hanya kalau dipakai bergantian."""

    def setUp(self):
        self.asli = llm.complete
        llm.lupakan_jeda(); llm.lupakan_putaran()
        self.jeda_ulang = llm.JEDA_ULANG
        llm.JEDA_ULANG = 0
        self.dipakai = []

        def palsu(cfg, provider, system, messages, max_tokens=0):
            self.dipakai.append(provider["model"])
            return "ok"
        llm.complete = palsu

    def tearDown(self):
        llm.complete = self.asli
        llm.lupakan_jeda(); llm.lupakan_putaran()
        llm.JEDA_ULANG = self.jeda_ulang

    PROV = {"name": "Gemini", "models": ["m1", "m2", "m3"]}

    def test_giliran_berputar_tiap_panggilan(self):
        for _ in range(6):
            llm.complete_any({}, [self.PROV], "s", [])
        self.assertEqual(self.dipakai, ["m1", "m2", "m3", "m1", "m2", "m3"])

    def test_model_yang_habis_dilewati_tanpa_menjatuhkan_yang_lain(self):
        def palsu(cfg, provider, system, messages, max_tokens=0):
            self.dipakai.append(provider["model"])
            if provider["model"] == "m1":
                raise RuntimeError("You exceeded your current quota")
            return "ok"
        llm.complete = palsu
        llm.complete_any({}, [self.PROV], "s", [])       # m1 habis -> m2
        self.dipakai.clear()
        for _ in range(3):
            llm.complete_any({}, [self.PROV], "s", [])
        self.assertNotIn("m1", self.dipakai, "yang habis harusnya dijeda")
        self.assertEqual(set(self.dipakai), {"m2", "m3"})

    def test_tanpa_daftar_models_entrinya_utuh(self):
        """Provider satu model dikembalikan apa adanya, bukan salinan --
        pemanggilnya membandingkan identitas."""
        prov = {"name": "A", "model": "x"}
        self.assertIs(llm.varian(prov)[0], prov)

    def test_kunci_jeda_memisahkan_model(self):
        a = {"name": "G", "model": "m1"}
        b = {"name": "G", "model": "m2"}
        self.assertNotEqual(llm.kunci(a), llm.kunci(b))


class JedaProvider(unittest.TestCase):
    """Provider yang baru kena batas dilewati sebentar, supaya tiap giliran
    tidak membuang satu panggilan gagal dulu."""

    def setUp(self):
        self.asli = llm.complete
        llm.lupakan_jeda()
        llm.lupakan_putaran()
        self.jeda_ulang = llm.JEDA_ULANG
        llm.JEDA_ULANG = 0
        self.dipanggil = []

    def tearDown(self):
        llm.complete = self.asli
        llm.lupakan_jeda()
        llm.lupakan_putaran()
        llm.JEDA_ULANG = self.jeda_ulang

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

    def test_antrean_sesaat_dicoba_ulang_sekali(self):
        """Antrean sesaat biasanya sudah lewat sedetik kemudian, dan giliran
        yang hilang lebih mahal daripada satu percobaan lagi."""
        habis = [RuntimeError("Rate limited. Wait a moment and try again.")]

        def kadang(cfg, provider, system, messages, max_tokens=0):
            self.dipanggil.append(provider["name"])
            if habis:
                raise habis.pop()
            return "ok"
        llm.complete = kadang
        teks, dipakai = llm.complete_any({}, [{"name": "A"}], "s", [])
        self.assertEqual(teks, "ok")
        self.assertEqual(self.dipanggil, ["A", "A"])

    def test_jatah_habis_tidak_dicoba_ulang(self):
        """Kalau jatahnya memang kering, mencoba lagi cuma menambah jeda."""
        self.pasang({"A": RuntimeError("You exceeded your current quota")})
        with self.assertRaises(llm.SemuaGagal):
            llm.complete_any({}, [{"name": "A"}], "s", [])
        self.assertEqual(self.dipanggil, ["A"])

    def test_antrean_sesaat_dijeda_sebentar_saja(self):
        """Provider yang melayani separuh permintaan masih menghemat jatah
        lapis terakhir; jangan dibuang sepuluh menit karena satu kali antre."""
        self.pasang({"A": RuntimeError("Rate limited. Wait a moment and try again."),
                     "B": "ok"})
        llm.complete_any({}, self.RANTAI, "s", [])
        sisa = llm._jeda[K("A")] - time.monotonic()
        self.assertLessEqual(sisa, llm.JEDA_SESAAT)
        self.assertGreater(sisa, 0)

    def test_jatah_habis_dijeda_lama(self):
        self.pasang({"A": RuntimeError('{"type":"FreeUsageLimitError"}'), "B": "ok"})
        llm.complete_any({}, self.RANTAI, "s", [])
        self.assertGreater(llm._jeda[K("A")] - time.monotonic(), llm.JEDA_SESAAT)

    def test_kegagalan_biasa_tidak_menjedakan(self):
        """Putus koneksi itu sesaat; jangan dihukum sepuluh menit."""
        self.pasang({"A": RuntimeError("connection refused"), "B": "ok"})
        llm.complete_any({}, self.RANTAI, "s", [])
        self.assertFalse(llm.dijeda(K("A")))

    def test_berhasil_membatalkan_jedanya(self):
        llm.jedakan(K("A"))
        self.pasang({"A": "ok", "B": "ok"})
        llm.complete_any({}, [{"name": "A"}], "s", [])
        self.assertFalse(llm.dijeda(K("A")))

    def test_semua_dijeda_tetap_dicoba(self):
        """Jeda itu tebakan; jangan sampai bikin dia diam total."""
        llm.jedakan(K("A")); llm.jedakan(K("B"))
        self.pasang({"A": RuntimeError("HTTP 429"), "B": "ok"})
        teks, dipakai = llm.complete_any({}, self.RANTAI, "s", [])
        self.assertEqual(dipakai["name"], "B")
