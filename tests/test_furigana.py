"""Furigana dan romaji. Dilewati kalau kamus MeCab tidak terpasang."""

import unittest

from ruri import furigana as F

pakai = unittest.skipUnless(F.available(), "fugashi/unidic-lite tidak terpasang")


class Kana(unittest.TestCase):
    def test_katakana_ke_hiragana(self):
        self.assertEqual(F.kata_to_hira("ミマシタ"), "みました")
        self.assertEqual(F.kata_to_hira("あいう"), "あいう")

    def test_okurigana_ditinggal_di_luar_kurung(self):
        self.assertEqual(F._annotate_word("見ました", "みました"), "見[み]ました")
        self.assertEqual(F._annotate_word("映画", "えいが"), "映画[えいが]")
        self.assertEqual(F._annotate_word("お茶", "おちゃ"), "お茶[ちゃ]")

    def test_tanpa_kanji_dibiarkan(self):
        self.assertEqual(F._annotate_word("ラーメン", "らーめん"), "ラーメン")

    def test_romaji_dasar(self):
        self.assertEqual(F.kana_to_romaji("がっこう"), "gakkou")   # sokuon
        self.assertEqual(F.kana_to_romaji("しゅうまつ"), "shuumatsu")  # youon
        self.assertEqual(F.kana_to_romaji("しんぶん"), "shinbun")
        self.assertEqual(F.kana_to_romaji("きんようび"), "kin'youbi")  # n sebelum vokal


@pakai
class Kalimat(unittest.TestCase):
    KASUS = [
        ("こんにちは。", "Konnichiwa."),
        ("映画を見た。", "Eiga o mita."),
        ("週末は家で休みます。", "Shuumatsu wa ie de yasumimasu."),
        ("昨日行きました。", "Kinou ikimashita."),
        ("日本語を勉強します。", "Nihongo o benkyoushimasu."),
        ("学校へ行って、先生に会いました。", "Gakkou e itte, sensei ni aimashita."),
        ("続けています。", "Tsuzuketeimasu."),
    ]

    def test_romaji(self):
        for jepang, harap in self.KASUS:
            with self.subTest(jepang):
                self.assertEqual(F.romaji(jepang), harap)

    def test_partikel_wa_bukan_ha(self):
        self.assertIn(" wa ", F.romaji("週末は家で休みます。"))

    def test_furigana(self):
        self.assertEqual(F.annotate("日本語"), "日本[にほん]語[ご]")
        self.assertEqual(F.annotate("お茶を飲む"), "お茶[ちゃ]を飲[の]む")

    def test_dua_baris(self):
        self.assertEqual(len(F.both("こんにちは。").splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
