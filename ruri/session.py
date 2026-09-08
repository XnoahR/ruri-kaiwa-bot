"""Keadaan satu sesi kaiwa.

Satu sesi per server, tapi ingatannya per orang.

Yang dipakai bersama itu ruangannya: level JLPT, giliran bicara, pengeras
suaranya. Yang tidak boleh dipakai bersama itu isi percakapannya. Kalau satu
orang selesai membahas ramen lalu pergi, orang berikutnya yang menyapa akan
disambut lanjutan obrolan yang bukan miliknya -- dan bagi dia itu terlihat
seperti bot yang mengarang, bukan bot yang salah ingat.

Ingatan juga punya umur. Percakapan yang ditinggal setengah jam bukan lagi
percakapan yang sama meski orangnya sama; menyambungnya kembali membuat kalimat
pertama seseorang di hari itu dijawab seolah-olah dia belum berhenti bicara.
"""

from __future__ import annotations

import threading
import time

# Ingatan yang dibiarkan menganggur selama ini dianggap sudah selesai.
KADALUARSA_MENIT = 30


class Percakapan:
    """Ingatan satu orang di satu server."""

    def __init__(self) -> None:
        self.turns: list = []
        self.last_bot = ""
        self.last_user = ""
        self.sentuh = time.monotonic()

    def reset(self) -> None:
        self.turns.clear()
        self.last_bot = ""
        self.last_user = ""
        self.sentuh = time.monotonic()


class Session:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.level = str(cfg["kaiwa"].get("level") or "N4").upper()
        # Kalimat terakhirnya di ruangan ini, siapa pun lawan bicaranya. Dipakai
        # `!ulang` dari orang yang belum pernah bicara sendiri.
        self.last_bot = ""
        self._orang: dict = {}
        # Satu giliran diproses pada satu waktu. Tanpa ini, dua orang yang
        # bicara bersamaan akan menumpuk dua permintaan model dan dua klip
        # suara yang saling tindih. Ini memang milik ruangannya, bukan orangnya:
        # pengeras suaranya cuma satu.
        self.lock = threading.Lock()
        self.busy = False

    # ---------------------------------------------------------------- orang
    def _kadaluarsa(self) -> int:
        nilai = self.cfg.get("kaiwa", {}).get("memory_idle_minutes")
        try:
            menit = KADALUARSA_MENIT if nilai is None else int(nilai)
        except (TypeError, ValueError):
            menit = KADALUARSA_MENIT
        return max(0, menit) * 60

    def orang(self, uid: int | None) -> Percakapan:
        """Ingatan milik satu orang.

        `None` jatuh ke satu ingatan bersama: jalur yang tidak tahu siapa yang
        bicara tetap harus bisa jalan.
        """
        kunci = int(uid) if uid else 0
        p = self._orang.get(kunci)
        if p is None:
            p = self._orang[kunci] = Percakapan()
            return p
        batas = self._kadaluarsa()
        if batas and (time.monotonic() - p.sentuh) > batas:
            p.reset()
        return p

    def add_user(self, uid: int | None, text: str) -> None:
        p = self.orang(uid)
        p.last_user = text
        p.turns.append({"role": "user", "content": text})
        p.sentuh = time.monotonic()

    def add_bot(self, uid: int | None, text: str) -> None:
        p = self.orang(uid)
        p.last_bot = text
        p.turns.append({"role": "assistant", "content": text})
        p.sentuh = time.monotonic()
        self.last_bot = text

    def history(self, cfg: dict, uid: int | None) -> list:
        keep = max(0, int(cfg["llm"].get("max_history_turns") or 12)) * 2
        turns = self.orang(uid).turns
        return list(turns[-keep:]) if keep else []

    def terakhir(self, uid: int | None) -> str:
        """Kalimat terakhirnya untuk `!yomi` / `!arti` / `!ulang`.

        Yang dia katakan padamu lebih dulu; kalau kamu belum pernah bicara,
        apa pun yang terakhir terdengar di ruangan ini.
        """
        return self.orang(uid).last_bot or self.last_bot

    def buang_terakhir(self, uid: int | None) -> None:
        """Tarik kembali giliran yang gagal dijawab, biar tidak menumpuk."""
        turns = self.orang(uid).turns
        if turns:
            turns.pop()

    def reset(self, uid: int | None = None, semua: bool = False) -> None:
        if semua:
            self._orang.clear()
            self.last_bot = ""
            return
        self.orang(uid).reset()
