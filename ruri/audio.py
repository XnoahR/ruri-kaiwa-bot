"""Jembatan format antara Discord dan penyedia transkripsi.

Discord memberi PCM 48kHz stereo 16-bit. Yang dikirim ke API cukup mp3 mono
16kHz: suara orang tidak butuh lebih, dan satu giliran lima detik jadi sekitar
20KB alih-alih satu megabyte. Di VPS yang bandwidth-nya dihitung, itu bukan
penghematan yang sepele.

Tidak ada numpy di sini. Sejak transkripsinya pindah ke sisi penyedia, satu-
satunya hitungan yang tersisa adalah kekerasan suara, dan itu tidak sepadan
dengan 32MB dependensi di mesin satu giga.
"""

from __future__ import annotations

import array
import shutil
import subprocess
import sys

IN_RATE = 48000
IN_CHANNELS = 2
OUT_RATE = 16000

SAMPLE_BYTES = 2
FRAME_BYTES = SAMPLE_BYTES * IN_CHANNELS

# Cukup untuk mengukur kekerasan; membaca semuanya tidak mengubah jawabannya.
RMS_SAMPLES = 4000


class AudioError(Exception):
    pass


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def duration_ms(raw: bytes) -> int:
    return int(len(raw) / FRAME_BYTES / IN_RATE * 1000)


def rms(raw: bytes) -> float:
    """Kekerasan rata-rata, 0..1. Dipakai membuang giliran yang isinya cuma
    napas atau derik keyboard sebelum sempat dikirim ke API."""
    if not raw:
        return 0.0
    usable = len(raw) - (len(raw) % SAMPLE_BYTES)
    if usable <= 0:
        return 0.0
    buf = array.array("h")
    buf.frombytes(raw[:usable])
    if sys.byteorder == "big":
        buf.byteswap()
    step = max(1, len(buf) // RMS_SAMPLES)
    total = 0
    count = 0
    for i in range(0, len(buf), step):
        v = buf[i]
        total += v * v
        count += 1
    if not count:
        return 0.0
    return (total / count) ** 0.5 / 32768.0


def to_upload(raw: bytes, fmt: str = "wav", bitrate: str = "64k") -> bytes:
    """PCM 48k stereo -> mono 16k, siap diunggah.

    Bawaannya wav, bukan mp3. Klip satu giliran cuma beberapa puluh kilobita
    entah bagaimanapun, dan kompresi berkerugian pada 32 kbps memakan justru
    detail yang membedakan bunyi-bunyi bahasa Jepang yang mirip. Hemat
    bandwidth-nya tidak sepadan dengan salah dengar.
    """
    if not raw:
        return b""
    if not have_ffmpeg():
        raise AudioError("ffmpeg tidak ditemukan di PATH")
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "s16le", "-ar", str(IN_RATE), "-ac", str(IN_CHANNELS), "-i", "pipe:0",
        "-ar", str(OUT_RATE), "-ac", "1",
    ]
    if fmt != "wav":
        cmd += ["-b:a", bitrate]
    cmd += ["-f", fmt, "pipe:1"]
    try:
        done = subprocess.run(cmd, input=raw, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=60)
    except subprocess.TimeoutExpired:
        raise AudioError("ffmpeg tidak selesai tepat waktu") from None
    if done.returncode != 0:
        raise AudioError(done.stderr.decode("utf-8", "replace")[:200])
    return done.stdout
