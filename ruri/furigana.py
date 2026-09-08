"""Furigana dan romaji tanpa model bahasa.

Ini tugas analisis morfologi, bukan tugas mengarang. Menyerahkannya ke model
berarti membayar beberapa detik dan beberapa ribu token untuk jawaban yang
sesekali salah -- dan model yang menalar dengan prosa biasa kadang kehabisan
token sebelum sempat menjawab sama sekali. MeCab menjawabnya dalam milidetik,
sama persis setiap kali.

Bentuk keluarannya `kanji[bacaan]`, konvensi yang sama dengan bidang furigana
di Anki, jadi hasilnya bisa langsung ditempel ke kartu.

Kalau fugashi tidak terpasang, `available()` mengembalikan False dan pemanggil
boleh jatuh balik ke model bahasa.
"""

from __future__ import annotations

import re

_tagger = None
_ready: bool | None = None

KANJI_RE = re.compile(r"[一-鿿々]")

# Youon dulu, karena dua aksara harus dicoba sebelum satu aksara.
YOUON = {
    "きゃ": "kya", "きゅ": "kyu", "きょ": "kyo",
    "しゃ": "sha", "しゅ": "shu", "しょ": "sho",
    "ちゃ": "cha", "ちゅ": "chu", "ちょ": "cho",
    "にゃ": "nya", "にゅ": "nyu", "にょ": "nyo",
    "ひゃ": "hya", "ひゅ": "hyu", "ひょ": "hyo",
    "みゃ": "mya", "みゅ": "myu", "みょ": "myo",
    "りゃ": "rya", "りゅ": "ryu", "りょ": "ryo",
    "ぎゃ": "gya", "ぎゅ": "gyu", "ぎょ": "gyo",
    "じゃ": "ja", "じゅ": "ju", "じょ": "jo",
    "ぢゃ": "ja", "ぢゅ": "ju", "ぢょ": "jo",
    "びゃ": "bya", "びゅ": "byu", "びょ": "byo",
    "ぴゃ": "pya", "ぴゅ": "pyu", "ぴょ": "pyo",
    "てぃ": "ti", "でぃ": "di", "とぅ": "tu", "どぅ": "du",
    "ふぁ": "fa", "ふぃ": "fi", "ふぇ": "fe", "ふぉ": "fo",
    "ゔぁ": "va", "ゔぃ": "vi", "ゔぇ": "ve", "ゔぉ": "vo",
    "うぃ": "wi", "うぇ": "we", "うぉ": "wo",
    "しぇ": "she", "ちぇ": "che", "じぇ": "je",
}

KANA = {
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o",
    "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko",
    "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so",
    "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to",
    "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no",
    "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho",
    "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo",
    "や": "ya", "ゆ": "yu", "よ": "yo",
    "ら": "ra", "り": "ri", "る": "ru", "れ": "re", "ろ": "ro",
    "わ": "wa", "を": "o", "ん": "n",
    "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go",
    "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
    "だ": "da", "ぢ": "ji", "づ": "zu", "で": "de", "ど": "do",
    "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
    "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po",
    "ゔ": "vu",
    "ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o",
    "ゃ": "ya", "ゅ": "yu", "ょ": "yo", "ゎ": "wa",
}

PUNCT = {"、": ",", "。": ".", "！": "!", "？": "?", "「": '"', "」": '"',
         "・": " ", "　": " ", "…": "...", "ー": ""}


def kata_to_hira(text: str) -> str:
    """Katakana -> hiragana. Yang bukan katakana dibiarkan."""
    out = []
    for ch in text or "":
        code = ord(ch)
        # ヴ dan ー tidak punya pasangan hiragana yang rapi; ditangani terpisah.
        if 0x30A1 <= code <= 0x30F6:
            out.append(chr(code - 0x60))
        else:
            out.append(ch)
    return "".join(out)


def has_kanji(text: str) -> bool:
    return bool(KANJI_RE.search(text or ""))


def is_kana(ch: str) -> bool:
    return "ぁ" <= ch <= "ゟ" or "ァ" <= ch <= "ヿ"


# ---------------------------------------------------------------- penganalisis
def available() -> bool:
    global _ready
    if _ready is None:
        try:
            import fugashi  # noqa: F401
            import unidic_lite  # noqa: F401

            _ready = True
        except Exception:
            _ready = False
    return bool(_ready)


def tagger():
    global _tagger
    if _tagger is None:
        from fugashi import Tagger

        _tagger = Tagger()
    return _tagger


# UniDic memisahkan dua hal yang memang beda: `kana` itu bacaan ejaannya, `pron`
# itu bunyi sebenarnya. Partikel は tercatat ハ di `kana` tapi ワ di `pron`, dan
# こんにちは tercatat コンニチハ tapi diucapkan コンニチワ. Furigana harus ikut
# ejaan; romaji harus ikut bunyi.
def _reading(word, spoken: bool = False) -> str:
    feat = getattr(word, "feature", None)
    keys = ("pron", "kana", "pronBase", "kanaBase") if spoken else (
        "kana", "pron", "kanaBase", "pronBase")
    for key in keys:
        val = getattr(feat, key, None) if feat is not None else None
        if val and val != "*":
            return kata_to_hira(val)
    return ""


def _pos3(word) -> str:
    feat = getattr(word, "feature", None)
    return (getattr(feat, "pos3", "") or "") if feat is not None else ""


def _pos(word) -> tuple:
    feat = getattr(word, "feature", None)
    return (getattr(feat, "pos1", "") or "", getattr(feat, "pos2", "") or "")


def _romaji_kana(word) -> str:
    """Bacaan buat romaji.

    Ejaannya yang dipakai, bukan bunyinya: `pron` menulis vokal panjang dengan
    ー, jadi 映画 jadi "eega" dan 先生 jadi "sensee". Kecuali untuk partikel dan
    kata seruan, karena di situ justru ejaannya yang menyesatkan -- partikel は
    dieja ハ tapi diucapkan ワ.
    """
    pos1, _pos2 = _pos(word)
    spoken = pos1 in ("助詞", "感動詞")
    return _reading(word, spoken=spoken)


def _attaches(word, prev) -> bool:
    """Benar kalau kata ini nempel ke kata sebelumnya waktu diromanisasi.

    Hepburn menulis kata kerja beserta bantuannya sebagai satu kata --
    `ikimashita`, bukan `iki mashi ta`.

    Yang perlu hati-hati: UniDic menandai 見, 行き, dan 続け sama-sama
    `動詞 非自立可能`, padahal itu cuma berarti kata kerja itu *bisa* jadi
    bantuan, bukan bahwa di sini dia sedang jadi bantuan. Menempelkan semuanya
    bikin `を見た` jadi "omita". Jadi kata kerja hanya menempel kalau memang
    didahului 接続助詞 -- yaitu て/で pada ~ている, ~てみる, ~ていく.
    """
    pos1, pos2 = _pos(word)
    prev1, prev2 = _pos(prev) if prev is not None else ("", "")

    if pos1 in ("助動詞", "接尾辞", "補助記号"):
        return True
    if pos1 == "助詞" and pos2 == "接続助詞":
        return True
    if pos1 == "動詞" and prev1 == "助詞" and prev2 == "接続助詞":
        return True
    # Nomina サ変 + する ditulis satu kata: "benkyoushimasu", bukan
    # "benkyou shimasu".
    if pos1 == "動詞" and prev1 == "名詞" and "サ変" in _pos3(prev):
        return True
    # Nomina berderet tanpa partikel di antaranya membentuk kata majemuk:
    # 日本 + 語 ditulis "nihongo", bukan "nihon go".
    if pos1 == "名詞" and prev1 in ("名詞", "接頭辞"):
        return True
    return False


# Bacaan yang pilihan UniDic-nya bakal menyesatkan orang yang lagi belajar.
# Sengaja cuma diisi kasus yang benar-benar sering muncul, bukan tempat
# penampungan tambalan.
OVERRIDE = {"日本": "にほん"}


def _annotate_word(surface: str, reading: str) -> str:
    """`見ました` + `みました` -> `見[み]ました`.

    Okurigana ditinggal di luar kurung, sama seperti bidang furigana Anki:
    yang perlu dibaca itu kanjinya, bukan kana yang sudah kelihatan.
    """
    if not surface or not reading or not has_kanji(surface):
        return surface
    if surface == reading:
        return surface

    head = 0
    while (head < len(surface) and head < len(reading)
           and surface[head] == reading[head] and is_kana(surface[head])):
        head += 1

    tail = 0
    while (tail < len(surface) - head and tail < len(reading) - head
           and surface[-1 - tail] == reading[-1 - tail]
           and is_kana(surface[-1 - tail])):
        tail += 1

    core = surface[head:len(surface) - tail] if tail else surface[head:]
    core_read = reading[head:len(reading) - tail] if tail else reading[head:]
    if not core or not core_read or not has_kanji(core):
        return surface
    return "%s%s[%s]%s" % (surface[:head], core, core_read,
                           surface[len(surface) - tail:] if tail else "")


def annotate(text: str) -> str:
    """Kalimat -> kalimat dengan furigana dalam kurung siku."""
    if not available():
        raise RuntimeError("fugashi/unidic-lite belum terpasang")
    out = []
    for word in tagger()(text or ""):
        baca = OVERRIDE.get(word.surface) or _reading(word)
        out.append(_annotate_word(word.surface, baca))
    return "".join(out)


# ---------------------------------------------------------------- romaji
def kana_to_romaji(kana: str) -> str:
    out: list = []
    i = 0
    kana = kata_to_hira(kana or "")
    while i < len(kana):
        two = kana[i:i + 2]
        if two in YOUON:
            out.append(YOUON[two])
            i += 2
            continue
        ch = kana[i]
        if ch == "っ":
            # Konsonan rangkap: っと -> tto. Di ujung kata tidak ada yang bisa
            # digandakan, jadi dilewat saja.
            nxt = kana[i + 1:i + 3]
            son = YOUON.get(nxt) or KANA.get(kana[i + 1:i + 2], "")
            if son and son[0].isalpha():
                out.append(son[0])
            i += 1
            continue
        if ch == "ー":
            if out and out[-1] and out[-1][-1] in "aiueo":
                out.append(out[-1][-1])
            i += 1
            continue
        if ch == "ん":
            nxt = kana[i + 1:i + 2]
            son = KANA.get(nxt, "")
            # n' sebelum vokal atau y, supaya じんいん tidak terbaca jini-in.
            out.append("n'" if son[:1] in ("a", "i", "u", "e", "o", "y") else "n")
            i += 1
            continue
        if ch in KANA:
            out.append(KANA[ch])
            i += 1
            continue
        if ch in PUNCT:
            out.append(PUNCT[ch])
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def romaji(text: str) -> str:
    """Kalimat Jepang -> romaji Hepburn, dipisah per kata."""
    if not available():
        raise RuntimeError("fugashi/unidic-lite belum terpasang")
    # Kana dikumpulkan dulu per kata, baru diromanisasi sekali di akhir.
    # Kalau tiap token diromanisasi sendiri-sendiri, っ di ujung token tidak
    # punya konsonan berikutnya untuk digandakan: 行っ + て jadi "ite", bukan
    # "itte".
    kata: list = []
    prev = None
    for word in tagger()(text or ""):
        baca = OVERRIDE.get(word.surface) or _romaji_kana(word) or word.surface
        if not baca.strip():
            prev = word
            continue
        if kata and _attaches(word, prev):
            kata[-1] += baca
        else:
            kata.append(baca)
        prev = word
    line = " ".join(kana_to_romaji(k) for k in kata).strip()
    return line[:1].upper() + line[1:] if line else ""


def both(text: str) -> str:
    """Dua baris, bentuk yang sama dengan keluaran !yomi lewat model."""
    return "%s\n%s" % (annotate(text), romaji(text))
