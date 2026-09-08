"""Membuka lapisan E2EE (DAVE) pada suara yang masuk.

discord-ext-voice-recv mendekripsi lapisan transport lalu langsung menyerahkan
hasilnya ke decoder Opus. Kalau kanalnya memakai enkripsi ujung-ke-ujung, isi
itu masih terbungkus satu lapis lagi, dan Opus menolaknya: "corrupted stream".

Menolak DAVE bukan jalan keluar -- Discord menutup sambungannya dengan kode
4017. Tapi kepingannya sebenarnya sudah lengkap: discord.py menjalankan
handshake MLS dan menyimpan sesinya di `VoiceConnectionState.dave_session`, dan
`davey` menyediakan `decrypt(user_id, media_type, packet)`. Yang tidak ada cuma
sambungan di antara keduanya, karena voice_recv memang belum menangani DAVE.
Modul ini yang menyambungkannya.

Semua kegagalan diam-diam mengembalikan data apa adanya: hasilnya paling buruk
sama dengan keadaan sekarang, tidak pernah lebih parah.
"""

from __future__ import annotations

import logging

log = logging.getLogger("ruri")

_kabar = [False]

# Penghitung mentah, dibaca per giliran supaya kegagalan tidak diam-diam.
STATS = {"ok": 0, "gagal": 0, "passthrough": 0, "tanpa_uid": 0, "opus_gagal": 0}


def ambil_stats() -> dict:
    keluar = dict(STATS)
    for k in STATS:
        STATS[k] = 0
    return keluar


def install() -> bool:
    """-> True kalau tambalannya terpasang."""
    try:
        import davey  # noqa: F401
        from discord.ext.voice_recv import reader
    except Exception as exc:
        log.warning("DAVE tidak bisa ditambal (%s); suara masuk mungkin bisu", exc)
        return False

    if getattr(reader.AudioReader, "_ruri_dave", False):
        return True

    asli = reader.AudioReader.__init__

    def __init__(self, *args, **kwargs):
        asli(self, *args, **kwargs)
        try:
            _bungkus(self)
        except Exception as exc:
            log.warning("lapisan DAVE gagal dipasang di reader: %s", exc)

    reader.AudioReader.__init__ = __init__
    reader.AudioReader._ruri_dave = True
    _tahan_paket_rusak()
    log.info("tambalan DAVE terpasang")
    return True


def _tahan_paket_rusak() -> None:
    """Satu paket rusak tidak boleh mematikan pendengaran.

    PacketRouter.run() menangkap exception apa pun dari loop-nya, lalu di blok
    `finally` memanggil stop_listening(). Jadi satu OpusError -- misalnya dari
    paket yang tiba sebelum sesi DAVE siap -- bikin bot tetap duduk di kanal
    suara tapi budek sampai di-join ulang. Paket yang gagal dilewati saja;
    kehilangan dua puluh milidetik audio tidak ada artinya dibanding kehilangan
    seluruh sisa percakapan.
    """
    try:
        from discord.opus import OpusError
        from discord.ext.voice_recv import opus as vr_opus
    except Exception:
        return
    if getattr(vr_opus.PacketDecoder, "_ruri_tahan", False):
        return

    asli = vr_opus.PacketDecoder._decode_packet

    def _decode_packet(self, packet):
        try:
            return asli(self, packet)
        except OpusError as exc:
            STATS["opus_gagal"] += 1
            log.debug("paket ssrc=%s dilewati: %s", getattr(packet, "ssrc", "?"), exc)
            # PCM senyap sepanjang satu frame, bukan kosong -- lihat _diam().
            return packet, b"\x00" * 3840

    vr_opus.PacketDecoder._decode_packet = _decode_packet
    vr_opus.PacketDecoder._ruri_tahan = True


def _diam() -> bytes:
    """Paket Opus "senyap", bukan bytes kosong.

    Paket yang dibuang membuat rekamannya kehilangan dua puluh milidetik tanpa
    menyisakan jeda: potongan ucapan tersambung rapat, dan model transkripsi
    membaca sambungan itu sebagai kata yang tidak pernah diucapkan. Menggantinya
    dengan senyap menjaga garis waktunya tetap benar.
    """
    from discord.ext.voice_recv.rtp import OPUS_SILENCE

    return OPUS_SILENCE


def _sesi(rdr):
    state = getattr(rdr.voice_client, "_connection", None)
    sesi = getattr(state, "dave_session", None)
    if sesi is None or not getattr(sesi, "ready", False):
        return None
    return sesi


def _bungkus(rdr) -> None:
    import davey

    dalam = rdr.decryptor.decrypt_rtp
    audio = davey.MediaType.audio

    def decrypt_rtp(packet):
        data = dalam(packet)          # lapisan transport, seperti biasa
        sesi = _sesi(rdr)
        if sesi is None or not data:
            return data

        uid = rdr.voice_client._get_id_from_ssrc(packet.ssrc)
        if not uid:
            STATS["tanpa_uid"] += 1
            # SSRC-nya belum dipetakan ke siapa pun; tanpa itu tidak ada kunci
            # yang bisa dipakai. Paket ini memang hilang, tapi pemetaannya
            # datang tak lama setelah orangnya mulai bicara.
            return data

        try:
            if sesi.can_passthrough(uid):
                STATS["passthrough"] += 1
                return data
            keluar = sesi.decrypt(uid, audio, data)
        except Exception as exc:
            STATS["gagal"] += 1
            log.debug("DAVE gagal membuka paket dari %s: %s", uid, exc)
            return _diam()

        if not keluar:
            STATS["gagal"] += 1
            return _diam()
        STATS["ok"] += 1

        if not _kabar[0]:
            _kabar[0] = True
            log.info("DAVE terbuka; suara masuk bisa dibaca")
        return keluar

    rdr.decryptor.decrypt_rtp = decrypt_rtp
