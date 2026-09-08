"""Suara lewat Fish Audio.

Bentuk permintaannya mengikuti PhiCorvi, yang sudah terbukti jalan dengan
tingkat gratisnya: model dikirim lewat header, bukan di dalam body.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class TTSError(Exception):
    pass


def _base(cfg: dict) -> str:
    return str(cfg["fish"].get("base_url") or "https://api.fish.audio").rstrip("/")


def _request(cfg: dict, path: str, data: bytes | None = None,
             extra: dict | None = None, timeout: int | None = None) -> bytes:
    fish = cfg["fish"]
    head = {"Authorization": "Bearer %s" % str(fish.get("api_key") or "").strip(),
            "User-Agent": "Ruri/0.1 (+kaiwa bot)"}
    if data is not None:
        head["Content-Type"] = "application/json"
    head.update(extra or {})
    req = urllib.request.Request(
        _base(cfg) + path, data=data,
        method="POST" if data is not None else "GET", headers=head)
    return urllib.request.urlopen(
        req, timeout=timeout or int(fish.get("timeout_seconds") or 120)).read()


def speak(cfg: dict, text: str) -> bytes:
    """Satu kalimat -> byte audio (mp3 secara bawaan).

    Sengaja mp3 dan bukan wav: keluaran wav Fish perlu perbaikan header sebelum
    bisa dibaca pemutar biasa, sedangkan mp3 langsung ditelan ffmpeg.
    """
    fish = cfg["fish"]
    key = str(fish.get("api_key") or "").strip()
    if not key:
        raise TTSError("Fish Audio API key belum diisi di config.json")
    text = (text or "").strip()
    if not text:
        raise TTSError("tidak ada teks untuk dibacakan")

    body: dict = {"text": text, "format": str(fish.get("format") or "mp3")}
    ref = str(fish.get("voice_id") or "").strip()
    if ref:
        body["reference_id"] = ref
    if body["format"] == "wav":
        body["sample_rate"] = 44100

    try:
        return _request(cfg, "/v1/tts", json.dumps(body).encode(),
                        {"model": str(fish.get("model") or "s2.1-pro-free").strip()})
    except urllib.error.HTTPError as exc:
        raise TTSError(_describe(exc)) from None
    except Exception as exc:
        raise TTSError(str(exc)) from None


def credit(cfg: dict) -> str | None:
    """Sisa kredit, atau None kalau tidak terbaca. Dipakai perintah !status."""
    try:
        raw = _request(cfg, "/wallet/self/api-credit", timeout=15)
        data = json.loads(raw)
    except Exception:
        return None
    for k in ("credit", "balance", "amount"):
        if k in data:
            return str(data[k])
    return json.dumps(data)[:120]


def voice_name(cfg: dict, ident: str) -> str | None:
    """Nama suara dari katalog. Katalognya publik, jadi ini jalan bahkan sebelum
    kunci ditempel -- penting supaya `!voice` bisa memberi tahu kamu salah id
    alih-alih diam saja."""
    ident = str(ident or "").strip()
    if not ident:
        return None
    try:
        data = json.loads(urllib.request.urlopen(
            "%s/model/%s" % (_base(cfg), ident), timeout=15).read())
    except Exception:
        return None
    if not data.get("_id"):
        return None
    return str(data.get("title") or ident).strip()


def _describe(exc: urllib.error.HTTPError) -> str:
    try:
        detail = exc.read().decode("utf-8", "replace")[:200]
    except Exception:
        detail = ""
    if exc.code == 401:
        return "Fish Audio menolak kuncinya (401). Cek fish.api_key."
    if exc.code == 402:
        return "Kredit Fish Audio habis (402)."
    if exc.code == 429:
        return "Kena rate limit Fish Audio (429). Coba lagi sebentar lagi."
    return "Fish Audio HTTP %d: %s" % (exc.code, detail or exc.reason)
