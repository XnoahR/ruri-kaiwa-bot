"""Penggabungan config dan pemilihan provider."""

import json
import os
import tempfile
import unittest

from ruri import config


class Muat(unittest.TestCase):
    def muat(self, isi):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as fh:
            json.dump(isi, fh)
            nama = fh.name
        try:
            return config.load(nama)
        finally:
            os.unlink(nama)

    def test_kunci_yang_hilang_diisi_bawaan(self):
        cfg = self.muat({"prefix": "?"})
        self.assertEqual(cfg["prefix"], "?")
        self.assertIn("silence_ms", cfg["stt"])
        self.assertIn("persona", cfg["kaiwa"])

    def test_penggabungan_bersarang_tidak_menghapus_saudaranya(self):
        cfg = self.muat({"stt": {"silence_ms": 2000}})
        self.assertEqual(cfg["stt"]["silence_ms"], 2000)
        self.assertEqual(cfg["stt"]["min_speech_ms"],
                         config.DEFAULTS["stt"]["min_speech_ms"])

    def test_provider_selalu_punya_max_tokens(self):
        """providers.py membacanya tanpa penjaga; entry salinan sering tidak punya."""
        cfg = self.muat({"llm": {"providers": [
            {"name": "X", "base_url": "http://x/v1", "model": "m", "api_key": "k"}]}})
        self.assertEqual(cfg["llm"]["providers"][0]["max_tokens"], 0)
        self.assertEqual(cfg["llm"]["providers"][0]["kind"], "openai")


class PilihProvider(unittest.TestCase):
    def test_memilih_berdasarkan_nama(self):
        cfg = {"llm": {"active_provider": "B", "providers": [
            {"name": "A"}, {"name": "B"}]}}
        self.assertEqual(config.active_provider(cfg)["name"], "B")

    def test_nama_tidak_ketemu_jatuh_ke_yang_pertama(self):
        cfg = {"llm": {"active_provider": "hilang", "providers": [{"name": "A"}]}}
        self.assertEqual(config.active_provider(cfg)["name"], "A")

    def test_tanpa_provider_mengembalikan_none(self):
        self.assertIsNone(config.active_provider({"llm": {"providers": []}}))


if __name__ == "__main__":
    unittest.main()
