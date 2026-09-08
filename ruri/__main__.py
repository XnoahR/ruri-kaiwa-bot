"""Titik masuk: `python -m ruri` (atau ./run.sh)."""

from __future__ import annotations

import logging

from . import config, dave, stt, tts
from .bot import build

log = logging.getLogger("ruri")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    # Dipasang setelah logging siap supaya hasilnya kelihatan di log,
    # dan tetap jauh sebelum sambungan suara pertama dibuat.
    dave.install()

    # voice_recv mencatat tiap SenderReport RTCP di level INFO -- satu baris
    # per detik, yang menenggelamkan semua yang benar-benar perlu dilihat.
    logging.getLogger("discord.ext.voice_recv").setLevel(logging.WARNING)

    cfg = config.load()
    token = str(cfg.get("discord_token") or "").strip()
    if not token:
        raise SystemExit(
            "discord_token masih kosong.\n"
            "Salin config.example.json jadi config.json, lalu isi tokennya.")

    if not stt.configured(cfg):
        log.warning("stt.api_key kosong -- dia bakal dengerin tapi nggak paham "
                    "apa pun sampai kuncinya diisi")
    if not str(cfg["fish"].get("api_key") or "").strip():
        log.warning("fish.api_key kosong -- dia bakal ngetik tapi nggak bersuara")
    build(cfg).run(token, log_handler=None)


if __name__ == "__main__":
    main()
