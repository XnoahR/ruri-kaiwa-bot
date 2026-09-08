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


# Hiragana, katakana, kanji.
JEPANG_RE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff]")
LATIN_RE = re.compile(r"[A-Za-z]")


def balasan_jepang(teks: str) -> bool:
    """Balasan yang lebih banyak huruf Latin daripada Jepang bukan balasan.

    Sebagian model menalar dengan prosa biasa, bukan di dalam tag. Kalau
    penalarannya belum sampai ke penanda <balas> waktu tokennya habis, yang
    tersisa cuma potongan isi kepalanya -- "* Wait, must be",
    "Ruri's reaction:". Dulu itu tetap dikirim, karena satu-satunya alternatif
    adalah diam sama sekali. Sekarang ada model berikutnya di rantai, dan model
    berikutnya selalu lebih baik daripada isi kepala yang bocor ke layar lalu
    dibacakan keras-keras oleh mesin suara.

    Yang dihitung porsinya, bukan ada-tidaknya: penalaran yang bocor sering
    berakhir dengan kalimat Jepang yang benar menempel di ujungnya, dan
    menuntut "ada satu huruf Jepang" saja meloloskan seluruh paragraf yang
    mendahuluinya. Tanda baca dan angka tidak dihitung di kedua sisi -- yang
    dibandingkan cuma huruf.
    """
    kata, _fix = split_reply(teks)
    if not kata:
        return False
    jepang = len(JEPANG_RE.findall(kata))
    latin = len(LATIN_RE.findall(kata))
    return jepang > 0 and jepang >= latin


class Melantur(Exception):
    """Menjawab, tapi yang keluar bukan balasan."""


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


_jeda: dict = {}


def jatah_habis(exc) -> bool:
    """Jatahnya benar-benar habis, bukan cuma sedang ramai.

    Dua hal berbeda sama-sama datang sebagai 429. Yang satu berarti jatah
    harianmu sudah dipakai sampai kering dan baru pulih besok; yang satu lagi
    cuma "sebentar, lagi antre" -- panggilan berikutnya sering langsung
    dilayani. Membedakannya menentukan berapa lama providernya dijeda.
    """
    teks = str(exc).lower()
    return ("freeusagelimit" in teks or "quota" in teks or "exceeded" in teks
            or "per day" in teks or "daily" in teks or "insufficient" in teks)


# Provider yang baru kena batas diingat sebentar. Tanpa ini, selama jatahnya
# belum pulih, setiap giliran membuang satu panggilan gagal dulu -- menambah
# satu detik penuh ke tiap kalimat, untuk jawaban yang sudah pasti tidak datang.
#
# Tapi jedanya tidak boleh seragam. Throttle sesaat dijeda pendek saja: provider
# yang melayani separuh permintaan tetap berharga -- separuh giliran yang dia
# ambil itu jatah yang tidak jadi dipotong dari lapis terakhir, dan ongkos
# gagalnya cuma setengah detik.
JEDA_HABIS = 600
JEDA_SESAAT = 15
JEDA_DETIK = JEDA_HABIS      # nama lama, masih dipakai di luar


def dijeda(nama: str) -> bool:
    return _jeda.get(nama, 0.0) > time.monotonic()


def jedakan(nama: str, detik: int = JEDA_HABIS) -> None:
    _jeda[nama] = time.monotonic() + detik


def lupakan_jeda() -> None:
    _jeda.clear()


def kunci(p: dict) -> str:
    """Jeda dicatat per model, bukan per provider.

    Satu provider bisa menawarkan banyak model dengan jatah yang terpisah;
    menjeda seluruh provider gara-gara satu modelnya habis membuang yang lain.
    """
    return "%s/%s" % (p.get("name", "?"), p.get("model", "?"))


def varian(provider: dict) -> list:
    """Provider dipecah jadi satu entri per model.

    Tanpa daftar `models`, entrinya dikembalikan apa adanya -- bukan salinan.
    Pemanggilnya membandingkan provider yang menjawab dengan yang di rantai,
    dan salinan memutus perbandingan itu tanpa mengubah apa pun yang terlihat.
    """
    daftar = [m for m in (provider.get("models") or []) if m]
    if not daftar:
        return [provider]
    return [dict(provider, model=m) for m in daftar]


# Model mana yang mendapat giliran duluan, per provider.
_putaran: dict = {}


def urutan_model(provider: dict) -> list:
    """Model-model satu provider, digilir.

    Jatah gratis Gemini dihitung per model per hari -- kuotanya sendiri
    bernama GenerateRequestsPerDayPerProjectPerModel-FreeTier -- jadi delapan
    model berarti delapan jatah. Tapi hanya kalau dipakai bergantian: dipakai
    berurutan, yang pertama habis lebih dulu setiap hari dan sisanya menunggu
    giliran yang tidak pernah datang.
    """
    daftar = varian(provider)
    if len(daftar) < 2:
        return daftar
    nama = provider.get("name", "?")
    n = _putaran.get(nama, 0) % len(daftar)
    _putaran[nama] = n + 1
    return daftar[n:] + daftar[:n]


def lupakan_putaran() -> None:
    _putaran.clear()


# Jeda sebelum mencoba ulang yang cuma kena antrean sesaat.
JEDA_ULANG = 1.2

# Satu giliran kaiwa punya umur guna. Jawaban yang datang setelah setengah
# menit tidak menjawab apa-apa lagi: orangnya sudah bicara lagi, atau sudah
# menyerah. Ini bukan soal kesabaran, tapi soal apa yang menahan giliran
# berikutnya -- selama satu giliran masih jalan, yang lain dibuang.
#
# Tenggatnya menjadi wajib begitu rantainya panjang. Dua puluh delapan model
# yang masing-masing boleh menggantung sembilan puluh detik berarti satu
# kalimat bisa menyandera bot selama empat puluh dua menit, dan dari luar itu
# terlihat persis seperti bot yang tuli.
TENGGAT_DETIK = 30


def _coba(cfg: dict, kandidat: list, system: str, messages: list,
          max_tokens: int, kegagalan: list, saring=None, batas: float = 0.0):
    for p in kandidat:
        sisa = (batas - time.monotonic()) if batas else 0.0
        if batas and sisa <= 1.0:
            # Sisa waktunya tidak cukup untuk jawaban yang berguna; berhenti
            # di sini lebih baik daripada membuka satu sambungan lagi yang
            # tetap harus ditinggalkan.
            break
        try:
            teks = complete(cfg, p, system, messages, max_tokens,
                            timeout=int(sisa) if batas else 0)
            if saring is not None and not saring(teks):
                raise Melantur(repr((teks or "")[:80]))
        except Exception as exc:
            kegagalan.append((kunci(p), exc))
            if is_rate_limited(exc):
                jedakan(kunci(p), JEDA_HABIS if jatah_habis(exc) else JEDA_SESAAT)
            continue
        _jeda.pop(kunci(p), None)
        return teks, p
    return None


def complete_any(cfg: dict, rantai: list, system: str, messages: list,
                 max_tokens: int = 0, saring=None, tenggat: float = 0.0) -> tuple:
    """Coba provider satu per satu sampai ada yang menjawab.

    -> (teks, provider yang dipakai). Melempar SemuaGagal kalau habis semua.

    Provider gratis kena batas pemakaian pada jam-jam sibuk, dan satu giliran
    yang hilang gara-gara itu terasa seperti bot yang rusak. Provider kedua
    biasanya punya kuota yang sama sekali terpisah -- begitu juga model kedua
    di provider yang sama.
    """
    batas = time.monotonic() + (tenggat or TENGGAT_DETIK)
    semua = [p for prov in rantai for p in urutan_model(prov)]
    siap = [p for p in semua if not dijeda(kunci(p))]
    # Kalau semuanya sedang dijeda, coba juga -- jeda itu tebakan, dan tebakan
    # tidak boleh jadi alasan untuk tidak menjawab sama sekali.
    urutan = siap or semua

    kegagalan: list = []
    hasil = _coba(cfg, urutan, system, messages, max_tokens, kegagalan, saring,
                  batas)
    if hasil is not None:
        return hasil

    # Semuanya gagal. Yang cuma kena antrean sesaat biasanya sudah dilayani
    # sedetik kemudian; satu percobaan ulang jauh lebih murah daripada giliran
    # yang hilang, dan ongkosnya cuma dibayar pada giliran yang memang sudah
    # gagal.
    sesaat = [p for p in urutan
              if any(k == kunci(p) and is_rate_limited(e) and not jatah_habis(e)
                     for k, e in kegagalan)]
    if sesaat and (batas - time.monotonic()) > JEDA_ULANG + 1.0:
        time.sleep(JEDA_ULANG)
        hasil = _coba(cfg, sesaat, system, messages, max_tokens, kegagalan, saring,
                      batas)
        if hasil is not None:
            return hasil
    raise SemuaGagal(kegagalan)


def complete(cfg: dict, provider: dict, system: str, messages: list,
             max_tokens: int = 0, timeout: int = 0) -> str:
    """Kumpulkan seluruh balasan. Streaming tidak berguna di sini -- kalimatnya
    baru bisa dibacakan setelah utuh."""
    out: list = []
    providers.stream_completion(
        provider,
        str(provider.get("api_key") or "").strip(),
        system,
        messages,
        max_tokens=max_tokens or int(cfg["llm"].get("max_tokens") or 600),
        timeout=timeout or int(cfg["llm"].get("timeout_seconds") or 90),
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
