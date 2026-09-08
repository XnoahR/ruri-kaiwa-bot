"""Keadaan satu sesi kaiwa.

Satu sesi per kanal suara. Level dan riwayat tidak dibuat global karena dua
kanal bisa jalan bersamaan dengan level berbeda.
"""

from __future__ import annotations

import threading


class Session:
    def __init__(self, cfg: dict) -> None:
        self.level = str(cfg["kaiwa"].get("level") or "N4").upper()
        self.turns: list = []
        self.last_bot = ""
        self.last_user = ""
        # Satu giliran diproses pada satu waktu. Tanpa ini, dua orang yang
        # bicara bersamaan akan menumpuk dua permintaan model dan dua klip
        # suara yang saling tindih.
        self.lock = threading.Lock()
        self.busy = False

    def add_user(self, text: str) -> None:
        self.last_user = text
        self.turns.append({"role": "user", "content": text})

    def add_bot(self, text: str) -> None:
        self.last_bot = text
        self.turns.append({"role": "assistant", "content": text})

    def history(self, cfg: dict) -> list:
        keep = max(0, int(cfg["llm"].get("max_history_turns") or 12)) * 2
        return list(self.turns[-keep:]) if keep else []

    def reset(self) -> None:
        self.turns.clear()
        self.last_bot = ""
        self.last_user = ""
