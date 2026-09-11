"""Loader prompt live: placeholder, fallback berkas, bawaan."""

import os
import tempfile
import unittest

from ruri import config
from ruri.live import prompt as PM


class Render(unittest.TestCase):
    def test_placeholder_terganti(self):
        out = PM.render("Halo {{persona}} level {{level}}",
                        {"persona": "Ruri", "level": "N3"})
        self.assertEqual(out, "Halo Ruri level N3")

    def test_placeholder_asing_dibiarkan_utuh(self):
        """Salah ketik harus terlihat, bukan berubah jadi kekosongan."""
        self.assertEqual(PM.render("{{ngawur}}", {"level": "N4"}), "{{ngawur}}")

    def test_bawaan_lengkap_terisi(self):
        cfg = config.DEFAULTS
        teks = PM.system_text(cfg, "N5")
        self.assertIn("N5", teks)
        self.assertNotIn("{{", teks)
        self.assertIn(cfg["kaiwa"]["persona"][:20], teks)

    def test_level_ngawur_jatuh_ke_n4(self):
        self.assertIn("N4", PM.system_text(config.DEFAULTS, "N9"))


class Berkas(unittest.TestCase):
    def tulis(self, isi):
        fd, path = tempfile.mkstemp(suffix=".md")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(isi)
        self.addCleanup(os.unlink, path)
        return path

    def test_isi_berkas_dipakai(self):
        p = self.tulis("PROMPT SAYA {{level}}")
        self.assertEqual(PM.baca_file(p, "FALLBACK"), "PROMPT SAYA {{level}}")

    def test_tidak_ada_jatuh_ke_bawaan(self):
        self.assertEqual(PM.baca_file("/tmp/tidak-ada-sekali-x.md", "B"), "B")
        self.assertEqual(PM.baca_file(None, "B"), "B")

    def test_berkas_kosong_jatuh_ke_bawaan(self):
        p = self.tulis("   \n  ")
        self.assertEqual(PM.baca_file(p, "B"), "B")

    def test_system_file_dari_cfg(self):
        p = self.tulis("Kamu {{persona}}; jangan pakai markdown.")
        cfg = {"live": {"system_file": p}, "kaiwa": {"persona": "Ruri"}}
        self.assertEqual(PM.system_text(cfg, "N4"),
                         "Kamu Ruri; jangan pakai markdown.")

    def test_opening_override(self):
        p = self.tulis("Mulai.")
        cfg = {"live": {"opening_file": p}}
        self.assertEqual(PM.opening_text(cfg), "Mulai.")


if __name__ == "__main__":
    unittest.main()
