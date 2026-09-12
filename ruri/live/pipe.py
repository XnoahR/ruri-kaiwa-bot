"""Helper murni untuk alir audio/teks mode live.

Downmix saja, tanpa ffmpeg dan tanpa resampling: Live API menerima rate berapa
pun dan me-resample sendiri dengan filter yang benar. Decimation 48k->16k di
sini akan membuang detail konsonan Jepang yang persis ingin didengar model.
"""

from __future__ import annotations

import array

# Discord mengirim 48 kHz stereo 16-bit. Satu frame 20 ms = 960 stereo.
RATE = 48000
BPS_MONO = RATE * 2          # byte/detik mono 16-bit


def downmix(raw: bytes) -> bytes:
    """PCM stereo 16-bit -> mono. Rata-rata L+R.

    Potongan ganjil (buffer terpotong di tengah frame) dibuang -- satu frame
    20 ms tidak akan mengubah satu kata pun, dan mengirim byte setengah
    sampel justru merusak fase seluruh stream sesudahnya.
    """
    usable = len(raw) - (len(raw) % 4)
    if usable <= 0:
        return b""
    a = array.array("h")
    a.frombytes(raw[:usable])
    out = array.array("h", ((a[i] + a[i + 1]) // 2 for i in range(0, len(a), 2)))
    return out.tobytes()


def take_chunk(buf: bytearray, n: int) -> bytes:
    """Ambil n byte terdepan (boleh lebih pendek kalau sisa tidak penuh).

    Mengembalikan b"" kalau kosong. Byte sisanya tetap di buf -- tidak ada
    yang hilang di tengah kalimat karena pemotongan chunk.
    """
    if not buf:
        return b""
    ambil = bytes(buf[:n])
    del buf[:n]
    return ambil


def merge_teks(lama: str, baru: str) -> str:
    """Gabungkan potongan transkrip streaming.

    Server tidak menjamin apakah tiap pesan membawa delta atau snapshot penuh.
    Deteksi satu arah yang aman: kalau teks baru mengawali dengan akumulasi
    lama, itu snapshot -> ganti; selain itu, delta -> tempel. Tanpa ini
    transkrip kartu bisa terduplikasi atau terpotong tergantung modelnya.
    """
    if not baru:
        return lama
    if not lama:
        return baru
    if baru.startswith(lama):
        return baru
    return lama + baru
