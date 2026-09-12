"""Bentuk pesan Gemini Live API lewat WebSocket mentah.

Sengaja murni: hanya bangun & baca dict, nol jaringan, nol Discord. Yang bisa
dites tanpa internet harus tinggal di sini -- aturan repo.

Bentuk `setup` yang sah itu `generationConfig` BERSARANG, bukan datar. Halaman
quickstart raw-websocket menunjukkan `"setup": {"responseModalities": [...]}`
apa adanya di root, tapi itu contoh yang sudah ketinggalan zaman; contoh resmi
Google (repo gemini-live-api-examples, frontend/geminilive.js) dan API
reference sama-sama menaruhnya di dalam `generationConfig`. Yang di sini ikut
yang benar.
"""

from __future__ import annotations

import base64
from urllib.parse import quote_plus

# Bawaan satu-satunya yang terverifikasi; live.model di config boleh menggantinya
# (per keputusan) -- tapi ID yang tidak mengandung "live" tidak mungkin benar.
MODEL = "gemini-3.1-flash-live-preview"

WS_BASE = ("wss://generativelanguage.googleapis.com/ws/"
           "google.ai.generativelanguage.v1beta.GenerativeService"
           ".BidiGenerateContent")

# Sama seperti modul HTTP lain di repo: jangan pernah terlihat sebagai
# pustaka umum. Google tidak memblokir seperti Cloudflare, tapi identitas
# yang jujur membuat insiden di sisi mereka bisa dilacak.
UA = "Ruri/0.1 (+kaiwa bot)"


def ws_url(api_key: str) -> str:
    """URL koneksi. Kunci lewat query parameter -- bentuk resmi untuk
    aplikasi server-to-server; ephemeral token hanya perlu kalau browser."""
    return WS_BASE + "?key=" + quote_plus(api_key)


# 30 suara prebuilt resmi (halaman speech-generation). Urutan sama seperti di
# tabel supaya `!live suara` menampilkan sesuatu yang bisa dicocokkan.
VOICES = (
    "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede",
    "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba",
    "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia", "Achernar",
    "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi",
    "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat",
)

# Nilai thinkingLevel resmi. "minimal" dipakai bawaan: pada model live,
# thinking = latensi sebelum kalimat pertama terdengar.
THINKING = ("minimal", "low", "medium", "high")

# PCM 16-bit little-endian mono. Audio Discord di-downmix ke mono 48k di
# live/pipe.py tanpa resampling: Live API resmi menerima rate berapa saja
# ("it will resample if needed"), jadi konversinya dikerjakan server dengan
# filter yang benar, bukan decimation 3:2 asal buang sampel di sisi kita.
MIME_AUDIO = "audio/pcm;rate=48000"

# Batas sliding window. Angka ini bukan tebakan: konteks sesi live = 128k
# token, default server sudah 80% (=104857) sebagai trigger dan setengahnya
# (52428) sebagai target. Ditaruh eksplisit supaya kalau default Google berubah,
# perilaku kita tidak ikut diam-diam.
TRIGGER_TOKENS = 104857
TARGET_TOKENS = 52428


def build_setup(system_text: str, voice: str = "Zephyr", thinking: str = "minimal",
                *, model: str = MODEL, transcripts: bool = True,
                silence_ms: int = 2000, prefix_ms: int = 500,
                resume_handle: str | None = None) -> dict:
    """Pesan pertama (dan satu-satunya) per koneksi.

    `system_text` sudah hasil render prompt; fungsi ini tidak mengenal level
    atau persona -- itu urusan live/prompt.py.
    """
    thinking = thinking if thinking in THINKING else "minimal"
    setup: dict = {
        "model": "models/" + model,
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}},
            },
            "thinkingConfig": {"thinkingLevel": thinking.upper()},
        },
        "systemInstruction": {"parts": [{"text": system_text}]},
        # VAD server yang memutuskan giliran bicara. silence_ms sengaja longgar
        # (default 2000, persis contoh resmi): orang belajar berhenti di tengah
        # kalimat, dan memotongnya di situ mengirim potongan tak utuh.
        "realtimeInputConfig": {
            "automaticActivityDetection": {
                "disabled": False,
                "silenceDurationMs": int(silence_ms),
                "prefixPaddingMs": int(prefix_ms),
            },
        },
        # Tanpa ini sesi audio-only dipaksa mati server di 15 menit. Dengan
        # sliding window, sesi bisa berlanjut tak terbatas (karena konteks lama
        # dibuang, bukan karena kuotanya besar).
        "contextWindowCompression": {
            "triggerTokens": TRIGGER_TOKENS,
            "slidingWindow": {"targetTokens": TARGET_TOKENS},
        },
    }
    if transcripts:
        # Dua kunci kosong ini yang MENYALAKAN event transkrip. Tanpa keduanya,
        # server tidak pernah mengirim inputTranscription/outputTranscription.
        setup["inputAudioTranscription"] = {}
        setup["outputAudioTranscription"] = {}
    # SELALU disertakan, bahkan koneksi pertama tanpa handle: keberadaannya
    # yang membuat server mengirim SessionResumptionUpdate berisi handle.
    # Tanpa itu, `goAway` menyambung ke sesi BARU dan konteks obrolan hilang.
    sr: dict = {}
    if resume_handle:
        sr["handle"] = resume_handle
    setup["sessionResumption"] = sr
    return {"setup": setup}


def audio_message(pcm: bytes) -> dict:
    """Satu chunk audio masuk. Base64 di sini, bukan di pemanggil, supaya yang
    dikembalikan selalu JSON-ready dan mudah dibandingkan di tes."""
    return {"realtimeInput": {"audio": {
        "mimeType": MIME_AUDIO,
        "data": base64.b64encode(pcm).decode("ascii"),
    }}}


def text_message(text: str) -> dict:
    """Teks sebagai 'ucapan' user -- jalur smoke-test tanpa mikrofon."""
    return {"realtimeInput": {"text": text}}


def stream_end_message() -> dict:
    """Bilang ke server bahwa stream audio berhenti, supaya dia flush sisa
    buffer-nya alih-alih menunggu hening penuh silenceDurationMs."""
    return {"realtimeInput": {"audioStreamEnd": True}}


def parse_server(obj: dict) -> list:
    """Satu pesan server -> daftar kejadian, BOLEH lebih dari satu.

    Satu pesan bisa membawa audio sekaligus transkrip sekaligus akhir turn.
    Karena itu tiap cabang di bawah dicek terpisah, bukan `elif`: rantai elif
    akan memakan salah satunya, dan gejalanya aneh -- suara keluar tapi
    transkripnya hilang, atau turnComplete tidak pernah terbit.
    """
    if not obj:
        return []
    kejadian: list = []

    if "setupComplete" in obj:
        # `in`, bukan .get(): setupComplete datang sebagai dict KOSONG, dan {}
        # itu falsy -- dengan .get() handshake tidak akan pernah selesai.
        kejadian.append(("setup_complete", None))

    sc = obj.get("serverContent")
    if sc:
        turn = sc.get("modelTurn")
        if turn:
            for part in turn.get("parts") or []:
                inline = part.get("inlineData")
                if inline and inline.get("data"):
                    kejadian.append(("audio", base64.b64decode(inline["data"])))
                elif part.get("text"):
                    kejadian.append(("model_text", part["text"]))
        if sc.get("interrupted"):
            kejadian.append(("interrupted", None))
        in_tr = sc.get("inputTranscription")
        if in_tr:
            kejadian.append(("input_text", in_tr.get("text") or ""))
            if in_tr.get("finished"):
                kejadian.append(("input_done", None))
        out_tr = sc.get("outputTranscription")
        if out_tr:
            kejadian.append(("output_text", out_tr.get("text") or ""))
            if out_tr.get("finished"):
                kejadian.append(("output_done", None))
        if sc.get("generationComplete"):
            kejadian.append(("generation_complete", None))
        if sc.get("turnComplete"):
            kejadian.append(("turn_complete", None))

    if obj.get("goAway"):
        kejadian.append(("go_away", obj["goAway"].get("timeLeft")))

    sru = obj.get("sessionResumptionUpdate")
    if sru and sru.get("resumable") and sru.get("newHandle"):
        kejadian.append(("handle", sru["newHandle"]))

    tc = obj.get("toolCall")
    if tc:
        # Tool tidak diaktifkan di fitur ini; tetap diakui supaya tidak ada
        # pesan server yang hilang tanpa jejak kalau konfigurasi berubah nanti.
        kejadian.append(("tool_call", tc))

    return kejadian
