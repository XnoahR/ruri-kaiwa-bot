"""Perpindahan 'telinga' pada satu VoiceRecvClient.

Satu-satunya cara aman memindah sink di koneksi suara yang sama, dipakai
kedua arah: !live on (ke LiveSink) dan !live off (kembali ke KaiwaSink).

Kenapa tidak stop_listening() -> listen() biasa: voice_recv 0.5.x melepas
socket listener secara sinkron, TETAPI PacketRouter-nya menyelesaikan
pembongkaran di thread sendiri dan `finally`-nya memanggil
voice_client.stop_listening() pada pembaca yang SEDANG terpasang saat itu.
Listen baru yang mendarat sebelum 'hantu' itu selesai akan dibunuh oleh
pembaca lamanya sendiri -- inilah balapan yang bikin mode non-live bisu
setelah !live off (laporan produksi 2026-09-11). Jeda sebelum pasang
memberi hantu itu kesempatan selesai selagi _reader masih MISSING (no-op),
dan retry menyerap sisa kasusnya ("Already receiving").
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("ruri")


async def ganti_telinga(vc, sink, *, label: str = "", jeda: float = 0.15,
                        maks: int = 10) -> bool:
    """Hentikan pembaca aktif, tunggu pembongkarannya, lalu pasang `sink`.

    True = terpasang. False = menyerah; pemanggil WAJIB memulihkan keadaan
    (mode tetap hidup tanpa telinga lebih baik daripada menggantung).
    """
    for percobaan in range(1, maks + 1):
        if vc.is_listening():
            try:
                vc.stop_listening()
            except Exception as exc:
                log.debug("swap[%s]: stop_listening -- %s", label, exc)
            await asyncio.sleep(jeda)
        try:
            vc.listen(sink)
            return True
        except Exception as exc:
            if "receiving" not in str(exc).lower():
                # TypeError sink salah jenis dsb. -- bukan balapan, jangan
                # dicoba 10 kali; yang bisa sembuh sendiri hanya "Already".
                log.warning("swap[%s]: pasang sink gagal -- %s", label, exc)
                return False
            log.info("swap[%s]: pembaca lama belum rampung (percobaan %d)",
                     label, percobaan)
            await asyncio.sleep(jeda)
    log.warning("swap[%s]: telinga tidak berhasil dipasang setelah %d "
                "percobaan", label, maks)
    return False
