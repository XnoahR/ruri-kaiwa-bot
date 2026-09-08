"""Otak kaiwa: prompt, kunci level JLPT, dan pemisahan koreksi dari obrolan."""

from __future__ import annotations

import re
import time

from . import providers

LEVELS = ("N5", "N4", "N3", "N2", "N1")

LEVEL_HINT = {
    "N5": "kosakata dan pola paling dasar. Kalimat pendek, bentuk です/ます, "
          "kata kerja sehari-hari saja.",
    "N4": "kosakata sehari-hari, bentuk て, bentuk biasa, potensial. Hindari "
          "kanji dan idiom yang jarang.",
    "N3": "percakapan sehari-hari yang lancar, termasuk bentuk pasif, kausatif, "
          "dan penghubung yang umum.",
    "N2": "bahasa alami orang dewasa termasuk ungkapan idiomatik dan nuansa "
          "sopan-santun.",
    "N1": "bahasa penuh tanpa ditahan-tahan, termasuk ungkapan sastrawi.",
}

OPEN, CLOSE = "<koreksi>", "</koreksi>"
BLOCK_RE = re.compile(re.escape(OPEN) + r"(.*?)(?:" + re.escape(CLOSE) + r"|\Z)", re.S)

# Bagian yang benar-benar diucapkan dibungkus penanda sendiri. Sebagian model --
# DeepSeek V4 Flash salah satunya -- menalar dengan prosa biasa, bukan di dalam
# tag <think>, jadi penalarannya tidak bisa disaring dari luar. Dengan penanda
# ini, apa pun yang dia pikirkan jatuh di luar dan tidak pernah sampai ke layar
# maupun ke mesin suara.
SAY_RE = re.compile(r"<balas>(.*?)(?:</balas>|\Z)", re.S)

RULES = """Aturan bicara:
- Balas selalu dalam bahasa Jepang. Jangan menyelipkan bahasa lain di bagian obrolan.
- Tahan diri di level JLPT %(level)s: %(hint)s
- Paling banyak %(n)d kalimat. Ini percakapan lisan, bukan karangan.
- Jangan pakai emoji, markdown, tanda bintang, atau daftar bernomor. Semua yang
  kamu tulis akan dibacakan keras-keras oleh mesin suara.
- Tulis angka dengan huruf Jepang, bukan angka Arab, supaya terbaca benar.
- Akhiri dengan satu pertanyaan lanjutan supaya percakapannya jalan terus.

Bungkus kalimat yang kamu ucapkan di antara <balas> dan </balas>. Apa pun di
luar penanda itu tidak akan dilihat siapa-siapa, jadi kalau kamu perlu berpikir
dulu, berpikirlah di luar sana. Jangan menjelaskan atau membahas penandanya.

Contoh balasan lengkap:

<balas>
おはよう。よく眠れた？今日は何をする予定なの？
</balas>"""

DENGAR_RULE = """Yang sampai padamu bukan ketikan, melainkan hasil pengenalan
suara, dan mesin itu sering salah dengar -- terutama pada nama, kata serapan,
dan bunyi yang mirip.

Karena itu:
- Jangan pernah mengoreksi nama, termasuk namamu sendiri dan nama lawan
  bicaramu. Kalau namamu tertulis aneh (ルリー, るり, Ruri), itu mesinnya, bukan
  dia. Terima saja dan lanjutkan.
- Kalau sebuah kata terlihat seperti salah dengar dan bukan kesalahan tata
  bahasa, abaikan saja. Jangan menyinggungnya, jangan mengoreksinya.
- Kalau seluruh kalimatnya tidak masuk akal, jangan menebak-nebak artinya --
  tanya ulang dengan santai."""

CORRECTION_RULE = """Koreksi hanya untuk kesalahan tata bahasa yang jelas --
bentuk kata kerja, partikel, susunan kalimat. Bukan untuk pilihan kata, bukan
untuk nama, bukan untuk sesuatu yang mungkin cuma salah dengar.

Kalau memang ada yang perlu dikoreksi, tambahkan blok ini persis setelah
balasanmu:

<koreksi>
asli: kalimat dia apa adanya
benar: versi yang lebih alami
kenapa: satu kalimat penjelasan, dalam bahasa Indonesia
</koreksi>

Kalau kalimatnya sudah wajar, jangan tulis blok itu sama sekali. Blok ini tidak
ikut dibacakan dan tidak dilihat sebagai bagian obrolan, jadi jangan
menyinggungnya di dalam balasanmu."""


def system_prompt(cfg: dict, level: str) -> str:
    kaiwa = cfg["kaiwa"]
    level = level if level in LEVELS else "N4"
    parts = [str(kaiwa.get("persona") or "").strip()]
    parts.append(RULES % {
        "level": level,
        "hint": LEVEL_HINT[level],
        "n": max(1, int(kaiwa.get("reply_max_sentences") or 3)),
    })
    parts.append(DENGAR_RULE)
    if kaiwa.get("correction", True):
        parts.append(CORRECTION_RULE)
    return "\n\n".join(p for p in parts if p)


def split_reply(text: str) -> tuple:
    """-> (yang diucapkan, koreksi dict atau None)

    Koreksinya dikeluarkan dari teks yang dibacakan: kalau ikut terbaca, mesin
    suara akan mengeja tag XML-nya keras-keras.
    """
    m = BLOCK_RE.search(text or "")
    kata = SAY_RE.search(text or "")
    if kata:
        spoken = kata.group(1).strip()
    else:
        # Tanpa penanda, kembali ke perilaku lama: seluruh teks dikurangi blok
        # koreksi. Lebih baik sesekali kebobolan daripada diam sama sekali.
        spoken = BLOCK_RE.sub("", text or "").strip()
    if not m:
        return spoken, None
    fix: dict = {}
    for line in m.group(1).strip().splitlines():
        k, _, v = line.partition(":")
        k = k.strip().lower()
        if k in ("asli", "benar", "kenapa") and v.strip():
            fix[k] = v.strip()
    return spoken, (fix or None)


class SemuaGagal(Exception):
    """Semua provider sudah dicoba dan tidak ada yang menjawab."""

    def __init__(self, kegagalan: list) -> None:
        self.kegagalan = kegagalan
        super().__init__("; ".join("%s: %s" % (n, e) for n, e in kegagalan))

    @property
    def kena_batas(self) -> bool:
        return all(is_rate_limited(e) for _n, e in self.kegagalan) if self.kegagalan else False


def is_rate_limited(exc) -> bool:
    """Batas pemakaian, bukan kerusakan.

    Penyedia menyebutnya dengan macam-macam nama -- 429, "rate limit",
    "FreeUsageLimitError", "quota" -- jadi yang dicocokkan bentuk pesannya,
    bukan tipe pengecualiannya.
    """
    teks = str(exc).lower()
    return ("429" in teks or "rate limit" in teks or "ratelimit" in teks
            or "freeusagelimit" in teks or "quota" in teks or "exceeded" in teks)


# Provider yang baru kena batas diingat sebentar. Tanpa ini, selama jatahnya
# belum pulih, setiap giliran membuang satu panggilan gagal dulu -- menambah
# satu detik penuh ke tiap kalimat, untuk jawaban yang sudah pasti tidak datang.
JEDA_DETIK = 600
_jeda: dict = {}


def dijeda(nama: str) -> bool:
    return _jeda.get(nama, 0.0) > time.monotonic()


def jedakan(nama: str, detik: int = JEDA_DETIK) -> None:
    _jeda[nama] = time.monotonic() + detik


def lupakan_jeda() -> None:
    _jeda.clear()


def complete_any(cfg: dict, rantai: list, system: str, messages: list,
                 max_tokens: int = 0) -> tuple:
    """Coba provider satu per satu sampai ada yang menjawab.

    -> (teks, provider yang dipakai). Melempar SemuaGagal kalau habis semua.

    Provider gratis kena batas pemakaian pada jam-jam sibuk, dan satu giliran
    yang hilang gara-gara itu terasa seperti bot yang rusak. Provider kedua
    biasanya punya kuota yang sama sekali terpisah.
    """
    siap = [p for p in rantai if not dijeda(p.get("name", "?"))]
    # Kalau semuanya sedang dijeda, coba juga -- jeda itu tebakan, dan tebakan
    # tidak boleh jadi alasan untuk tidak menjawab sama sekali.
    urutan = siap or rantai

    kegagalan: list = []
    for provider in urutan:
        nama = provider.get("name", "?")
        try:
            teks = complete(cfg, provider, system, messages, max_tokens)
        except Exception as exc:
            kegagalan.append((nama, exc))
            if is_rate_limited(exc):
                jedakan(nama)
            continue
        _jeda.pop(nama, None)
        return teks, provider
    raise SemuaGagal(kegagalan)


def complete(cfg: dict, provider: dict, system: str, messages: list,
             max_tokens: int = 0) -> str:
    """Kumpulkan seluruh balasan. Streaming tidak berguna di sini -- kalimatnya
    baru bisa dibacakan setelah utuh."""
    out: list = []
    providers.stream_completion(
        provider,
        str(provider.get("api_key") or "").strip(),
        system,
        messages,
        max_tokens=max_tokens or int(cfg["llm"].get("max_tokens") or 600),
        timeout=int(cfg["llm"].get("timeout_seconds") or 90),
        on_text=out.append,
        on_status=lambda _code: None,
        should_stop=lambda: False,
    )
    return "".join(out).strip()


RESULT_RE = re.compile(r"<hasil>(.*?)(?:</hasil>|\Z)", re.S)


def extract_result(text: str) -> str:
    """Ambil isi di antara <hasil>...</hasil>.

    Sebagian model -- DeepSeek V4 Flash salah satunya -- menalar dengan prosa
    biasa, bukan di dalam tag <think>, jadi penalarannya tidak bisa disaring
    dari luar. Penanda ini memindahkan masalahnya: apa pun yang dia pikirkan
    jatuh di luar penanda, dan yang di dalam tinggal diambil. Kalau penandanya
    tidak ada sama sekali, teks utuhnya dikembalikan daripada hilang.
    """
    m = RESULT_RE.search(text or "")
    return (m.group(1) if m else (text or "")).strip()


def one_shot(cfg: dict, provider: dict, system: str, user: str) -> str:
    """Anggaran tokennya lebih longgar daripada giliran obrolan.

    Model yang menalar dengan prosa biasa butuh ruang untuk selesai berpikir
    sebelum sampai ke <hasil>. Dengan anggaran obrolan yang ketat, dia terpotong
    di tengah penalaran dan tidak pernah sampai ke jawabannya.
    """
    return extract_result(complete(
        cfg, provider, system, [{"role": "user", "content": user}],
        max_tokens=int(cfg["llm"].get("oneshot_max_tokens") or 1500)))


# Prompt satu-tembak ini sengaja bertele-tele soal bentuk keluarannya, lengkap
# dengan contoh. Versi ringkasnya bikin sebagian model -- DeepSeek V4 Flash
# salah satunya -- menalar keras-keras di dalam jawabannya sampai kehabisan
# token sebelum sempat menjawab.

FURIGANA_SYS = """Kamu alat bantu baca, bukan asisten. Kamu menerima satu kalimat
Jepang dan mengeluarkan tepat dua baris, tanpa apa pun selain itu.

Baris 1: kalimat itu ditulis ulang. Setiap kanji atau gugus kanji diikuti
bacaannya di dalam kurung siku, persis setelahnya. Kana dibiarkan apa adanya.
Baris 2: seluruh kalimat dalam romaji.

Contoh masukan:
昨日、友達と映画を見ました。

Contoh keluaran:
昨日[きのう]、友達[ともだち]と映画[えいが]を見[み]ました。
Kinou, tomodachi to eiga o mimashita.

Kalau sebuah kanji punya lebih dari satu bacaan yang mungkin, pilih yang
paling umum dan lanjutkan. Jangan membahas pilihannya.

Mulai keluaranmu dengan <hasil> di baris pertama, lalu dua baris itu, lalu
</hasil>. Jangan menulis apa pun sebelum <hasil>."""

TRANSLATE_SYS = """Kamu penerjemah, bukan asisten. Kamu menerima satu kalimat
Jepang dan mengeluarkan tepat satu baris: terjemahannya dalam bahasa Indonesia
yang wajar.

Mulai keluaranmu dengan <hasil> di baris pertama, lalu terjemahannya, lalu
</hasil>. Jangan menulis apa pun sebelum <hasil>."""
