"""Transkripsi lewat API berbentuk OpenAI `/audio/transcriptions`.

Whisper lokal sengaja tidak dipakai. Modelnya 460MB dan butuh sekitar satu
giga RAM waktu jalan, sementara targetnya VPS 1 vCPU/1GB -- dan transkripsi di
vCPU murah justru jadi bagian paling lambat dari satu giliran kaiwa. Dikerjakan
di sisi penyedia, hasilnya malah lebih cepat dan mesinnya tetap kosong.

Bentuk permintaannya standar OpenAI, jadi satu implementasi ini melayani Groq,
OpenAI, maupun server whisper sendiri yang meniru bentuk itu. Yang membedakan
cuma `base_url`, `model`, dan kuncinya di config.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
import uuid


class STTError(Exception):
    pass


# urllib mengirim "Python-urllib/3.x" sebagai User-Agent, dan Cloudflare --
# yang berdiri di depan Groq dan banyak penyedia lain -- memblokirnya dengan
# 403 error 1010. Menyebut nama sendiri sudah cukup untuk lolos.
UA = "Ruri/0.1 (+kaiwa bot)"


def _multipart(fields: dict, filename: str, blob: bytes,
               content_type: str) -> tuple:
    """Rakit badan multipart/form-data sendiri.

    Pakai urllib saja, bukan requests: satu permintaan sederhana tidak sepadan
    dengan menambah satu dependensi ke mesin yang RAM-nya cuma satu giga.
    """
    boundary = "ruri" + uuid.uuid4().hex
    sep = ("--" + boundary).encode()
    body = bytearray()
    for name, value in fields.items():
        if value in (None, ""):
            continue
        body += sep + b"\r\n"
        body += ('Content-Disposition: form-data; name="%s"\r\n\r\n' % name).encode()
        body += str(value).encode("utf-8") + b"\r\n"
    body += sep + b"\r\n"
    body += ('Content-Disposition: form-data; name="file"; filename="%s"\r\n'
             % filename).encode()
    body += ("Content-Type: %s\r\n\r\n" % content_type).encode()
    body += blob + b"\r\n"
    body += ("--%s--\r\n" % boundary).encode()
    return boundary, bytes(body)


def configured(cfg: dict) -> bool:
    return bool(str(cfg["stt"].get("api_key") or "").strip())


MIME = {"mp3": "audio/mpeg", "wav": "audio/wav",
        "ogg": "audio/ogg", "webm": "audio/webm"}

# Model transkripsi Gemini tetap model generatif: dia menerima perintah, bukan
# cuma berkas. Perintahnya sengaja galak soal "tulis ulang saja" -- tanpa itu
# dia bisa menjawab *tentang* audionya alih-alih menyalinnya.
GEMINI_PROMPT = (
    "The speaker is practising %(lang)s and is not a native speaker. "
    "Transcribe what they said.\n\n"
    "Always write the transcript in %(lang)s script. If they say a word from "
    "another language, transcribe it the way %(lang)s writes foreign words -- "
    "for Japanese that means katakana. Never switch the transcript to another "
    "language's script.\n\n"
    "Output only the transcript: no translation, no romanisation, no "
    "commentary, no quotation marks. If there is no speech, output nothing.")


def transcribe(cfg: dict, blob: bytes, fmt: str = "mp3") -> str:
    """Byte audio -> teks. String kosong berarti tidak ada ucapan."""
    if str(cfg["stt"].get("kind") or "openai") == "gemini":
        return _gemini(cfg, blob, fmt)
    return _openai(cfg, blob, fmt)


def _gemini(cfg: dict, blob: bytes, fmt: str) -> str:
    """Jalur native Google.

    Lapisan kompatibel-OpenAI milik Google tidak punya /audio/transcriptions
    (dia menjawab 404), jadi audionya dikirim sebagai inline_data ke
    generateContent. Klip satu giliran cuma puluhan kilobita, jauh di bawah
    batas permintaan inline.
    """
    s = cfg["stt"]
    key = str(s.get("api_key") or "").strip()
    if not key:
        raise STTError("stt.api_key belum diisi di config.json")
    if not blob:
        return ""
    model = str(s.get("model") or "gemini-3.5-transcribe").replace("models/", "")
    base = str(s.get("base_url") or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    bahasa = {"ja": "Japanese", "en": "English", "id": "Indonesian"}.get(
        str(s.get("language") or "ja"), str(s.get("language") or "Japanese"))

    body = {
        "contents": [{"parts": [
            {"text": GEMINI_PROMPT % {"lang": bahasa}},
            {"inline_data": {"mime_type": MIME.get(fmt, "audio/mpeg"),
                             "data": base64.b64encode(blob).decode()}},
        ]}],
        "generationConfig": {"temperature": 0},
    }
    req = urllib.request.Request(
        "%s/models/%s:generateContent" % (base, model),
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": key,
                 "User-Agent": UA},
    )
    try:
        raw = urllib.request.urlopen(
            req, timeout=int(s.get("timeout_seconds") or 60)).read()
    except urllib.error.HTTPError as exc:
        raise STTError(_describe(exc)) from None
    except Exception as exc:
        raise STTError(str(exc)) from None

    try:
        data = json.loads(raw)
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, ValueError):
        return ""
    # Model transkripsi khusus menaruh hasilnya di bagian `audioTranscription`,
    # bukan di `text` seperti model biasa. Keduanya diterima supaya config boleh
    # menunjuk gemini-3.5-transcribe maupun gemini-2.5-flash tanpa ganti kode.
    keluar = []
    for bagian in parts:
        if isinstance(bagian.get("text"), str):
            keluar.append(bagian["text"])
        trans = bagian.get("audioTranscription")
        if isinstance(trans, dict) and isinstance(trans.get("text"), str):
            keluar.append(trans["text"])
    return "".join(keluar).strip()


def _openai(cfg: dict, blob: bytes, fmt: str = "mp3") -> str:
    s = cfg["stt"]
    key = str(s.get("api_key") or "").strip()
    if not key:
        raise STTError("stt.api_key belum diisi di config.json")
    if not blob:
        return ""

    base = str(s.get("base_url") or "").rstrip("/")
    if not base:
        raise STTError("stt.base_url belum diisi")

    fields = {
        "model": str(s.get("model") or "whisper-large-v3-turbo"),
        "response_format": "json",
        # Bahasanya dikunci, tidak dibiarkan ditebak. Ucapan Jepang pendek dari
        # orang yang baru belajar gampang salah dikenali sebagai bahasa lain,
        # dan sekali salah tebak seluruh transkripsinya ikut ngaco.
        "language": str(s.get("language") or "ja"),
    }
    prompt = str(s.get("prompt") or "").strip()
    if prompt:
        fields["prompt"] = prompt

    ctype = MIME.get(fmt, "application/octet-stream")
    boundary, body = _multipart(fields, "ucapan." + fmt, blob, ctype)

    req = urllib.request.Request(
        base + "/audio/transcriptions",
        data=body,
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "multipart/form-data; boundary=" + boundary,
            "User-Agent": UA,
        },
    )
    try:
        raw = urllib.request.urlopen(
            req, timeout=int(s.get("timeout_seconds") or 60)).read()
    except urllib.error.HTTPError as exc:
        raise STTError(_describe(exc)) from None
    except Exception as exc:
        raise STTError(str(exc)) from None

    try:
        data = json.loads(raw)
    except ValueError:
        return raw.decode("utf-8", "replace").strip()
    return str(data.get("text") or "").strip()


def _describe(exc: urllib.error.HTTPError) -> str:
    try:
        detail = exc.read().decode("utf-8", "replace")[:200]
    except Exception:
        detail = ""
    if exc.code == 401:
        return "Kunci STT ditolak (401). Cek stt.api_key."
    if exc.code == 413:
        return "Klipnya kepanjangan buat penyedia ini (413). Turunkan stt.max_speech_ms."
    if exc.code == 429:
        return "Kena rate limit STT (429)."
    return "STT HTTP %d: %s" % (exc.code, detail or exc.reason)
