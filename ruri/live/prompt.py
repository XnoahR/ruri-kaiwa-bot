"""Teks prompt mode live.

Sistem instruksi dan pesan pembuka dihidupkan dari berkas
(`live_prompts/system.md`, `live_prompts/opening.md`) supaya bisa diedit tanpa
menyentuh kode; kalau berkasnya tidak ada, string bawaan di bawah yang jalan.
File tidak ada != bot mati.

Yang diganti hanya `{{persona}}`, `{{level}}`, `{{level_hint}}` -- tanpa engine
template. Penanda lain dibiarkan utuh: kalau salah ketik, yang kelihatan di
prompt adalah `{{namanya}}`, bukan kekosongan yang misterius.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("ruri")

PLACEHOLDER = ("persona", "level", "level_hint")

# Catatan bentuk keluaran: di mode live yang keluar adalah SUARA, jadi tidak
# ada `<balas>`/`<koreksi>` -- penanda itu perlu stream teks untuk disaring.
# Aturannya tetap: pendek, bahasa Jepang, akhiri pertanyaan.
DEFAULT_SYSTEM = """{{persona}}

Kamu sedang latihan percakapan bahasa Jepang (kaiwa) lewat SUARA. Yang kamu
hasilkan hanyalah ucapan; tidak ada teks yang akan dibaca siapa pun.

Tahan diri di level JLPT {{level}}: {{level_hint}}

Aturan bicara:
- Balas selalu dalam bahasa Jepang. Jangan menyelipkan bahasa lain.
- Paling banyak 2 kalimat pendek. Ini percakapan lisan.
- Jangan ucapkan tanda baca, emoji, atau apa pun yang bukan kalimat.
- Tulis angka sebagai kata Jepang, bukan angka Arab.
- Akhiri dengan satu pertanyaan ringan supaya percakapan jalan terus.

Masukanmu adalah ucapan manusia yang mungkin berantakan -- orang yang sedang
belajar bicara pelan, salah ucap, dan salah pilih kata. Kalau sebuah kata
terdengar janggal, anggap itu bagian dari latihan: jangan dikoreksi, jangan
diulang, langsung menanggapi maksudnya. Jangan pernah mengoreksi nama,
termasuk namamu sendiri. Kalau seluruh kalimat tidak masuk akal, tanya ulang
dengan santai.

Bicaralah seperti mengobrol, bukan seperti mengajar. Kalau lawan bicara diam,
tunggu; jangan mengisi kesunyian."""

DEFAULT_OPENING = ("Mulailah percakapan: sapa yang ada di ruangan dengan bahasa "
                   "Jepang yang singkat dan natural, lalu ajukan satu pertanyaan "
                   "ringan untuk memantik obrolan.")


def render(text: str, nilai: dict) -> str:
    for k in PLACEHOLDER:
        if k in nilai:
            text = text.replace("{{%s}}" % k, str(nilai[k]))
    return text


def baca_file(path: str | None, fallback: str) -> str:
    """Isi berkas, atau fallback kalau tidak ada/kosong.

    Berkas yang gagal dibaca diberi tahu ke log -- bukan ke kanal; pengguna
    tidak bisa berbuat apa-apa soal path yang salah ketik di config.
    """
    if not path:
        return fallback
    if not os.path.isabs(path):
        from .. import config as conf
        path = os.path.join(conf.HERE, path)
    try:
        with open(path, encoding="utf-8") as fh:
            isi = fh.read().strip()
    except FileNotFoundError:
        return fallback
    except OSError as exc:
        log.warning("live: berkas prompt %s tidak terbaca (%s); pakai bawaan",
                    path, exc)
        return fallback
    return isi or fallback


def system_text(cfg: dict, level: str) -> str:
    from ..llm import LEVEL_HINT, LEVELS

    live = cfg["live"]
    level = level if level in LEVELS else "N4"
    dasar = baca_file(live.get("system_file"), DEFAULT_SYSTEM)
    return render(dasar, {
        "persona": str(cfg["kaiwa"].get("persona") or "").strip(),
        "level": level,
        "level_hint": LEVEL_HINT[level],
    })


def opening_text(cfg: dict) -> str:
    return baca_file(cfg["live"].get("opening_file"), DEFAULT_OPENING)
