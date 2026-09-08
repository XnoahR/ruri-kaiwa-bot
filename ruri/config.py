"""Konfigurasi Ruri: satu berkas JSON di sebelah paketnya.

Kuncinya disimpan di config.json, bukan di kode, dan berkas itu masuk
.gitignore. config.example.json yang ikut ke repo.
"""

from __future__ import annotations

import copy
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(HERE, "config.json")

DEFAULTS: dict = {
    "discord_token": "",
    "prefix": "!",
    # Kosong berarti semua server boleh. Diisi berarti hanya id ini yang dilayani.
    "allowed_guilds": [],
    # Nama (atau id) kanal teks tempat dia boleh mengobrol. Transkrip suara juga
    # dikirim ke sini. Kosong = kanal mana pun.
    "chat_channel": "ruri-sinaga",
    # Nama (atau id) kanal suara yang dia tongkrongi terus. Kosong = tunggu !join.
    "voice_channel": "",
    # Ke mana transkrip giliran suara dikirim.
    #   "kanal" -- kartu gilirannya muncul di kanal teks, semua orang membacanya
    #   "dm"    -- cuma ke DM orang yang bicara
    #   "off"   -- tidak dicatat sama sekali; suaranya tetap jalan
    # Giliran yang datang dari ketikan selalu dijawab di kanal: kalimatnya sudah
    # terlihat di situ, dan jawaban yang pindah ke DM cuma terlihat seperti dia
    # tidak menjawab.
    "transcript_privacy": "kanal",
    # Masuk sendiri saat mulai, dan balik lagi kalau terputus.
    "auto_join": True,
    # Selang pemeriksaan sambungan suara, detik.
    "rejoin_seconds": 30,
    "llm": {
        "active_provider": "",
        # Bentuknya sama persis dengan providers di Amadeus Deck, jadi entry
        # yang sudah ada di sana bisa disalin apa adanya.
        "providers": [
            {
                "name": "Ollama",
                "kind": "openai",
                "base_url": "http://127.0.0.1:11434/v1",
                "model": "qwen2.5:14b",
                "api_key": "ollama",
                "context_window": 32768,
                "max_tokens": 0,
                # Isi "models" dengan beberapa nama model kalau providermu
                # menghitung jatah gratisnya per model -- Gemini begitu, kuotanya
                # bernama GenerateRequestsPerDayPerProjectPerModel-FreeTier.
                # Ruri menggilirnya tiap giliran, dan model yang jatahnya habis
                # dilewati tanpa menjatuhkan yang lain. Kosong = pakai "model".
                "models": [],
            }
        ],
        "max_tokens": 1200,
        "oneshot_max_tokens": 1500,
        "timeout_seconds": 90,
        "max_history_turns": 12,
    },
    "fish": {
        "base_url": "https://api.fish.audio",
        "api_key": "",
        # Model gratis Fish saat ini. Dikirim lewat header, bukan body.
        "model": "s2.1-pro-free",
        # reference_id suara pilihanmu; kosong = suara bawaan model.
        "voice_id": "",
        # Daftar suara buat perintah !voice. Diisi sendiri lewat
        # "!voice tambah <nama> <reference_id>", atau disalin dari PhiCorvi.
        "voices": [],
        # mp3 sengaja, bukan wav: keluaran wav Fish perlu perbaikan header
        # (PhiCorvi punya wav_repair untuk itu), sedangkan mp3 ditelan ffmpeg
        # apa adanya.
        "format": "mp3",
        "timeout_seconds": 120,
    },
    "stt": {
        # Bentuk OpenAI /audio/transcriptions. Groq punya tier gratis dan
        # whisper-large-v3-turbo; OpenAI atau server whisper sendiri juga cocok
        # -- yang berubah cuma tiga baris di bawah ini.
        # "openai" = multipart /audio/transcriptions (Groq, OpenAI, whisper
        # sendiri). "gemini" = generateContent native Google, karena lapisan
        # kompatibel-OpenAI mereka tidak punya rute transkripsi.
        "kind": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "api_key": "",
        "model": "whisper-large-v3",
        # wav: tanpa kompresi berkerugian. Lihat audio.to_upload().
        "upload_format": "wav",
        "language": "ja",
        # Whisper memakai ini untuk mencondongkan tebakannya. Diisi contoh
        # kalimat percakapan supaya dia mengharapkan obrolan sehari-hari,
        # bukan frasa layanan pelanggan seperti "お待たせしました".
        # Nama-nama yang sering salah dengar. Sebagian penyedia memakai ini
        # untuk mencondongkan tebakannya; yang mengabaikannya tidak dirugikan.
        "prompt": "ルリ、瑠璃、ルリちゃん、レイチ。日本語の会話練習です。",
        "timeout_seconds": 60,
        # Simpan klip yang dikirim ke /tmp, buat memastikan audionya benar.
        "save_clips": False,
        # Sunyi selama ini menandai giliranmu selesai. Sengaja panjang: orang
        # yang sedang belajar berhenti di tengah kalimat untuk menyusunnya, dan
        # memotongnya di situ mengirim potongan yang tidak utuh ke transkripsi
        # -- lalu Whisper menambalnya dengan frasa umum yang tidak pernah
        # diucapkan.
        "silence_ms": 1500,
        # Lebih pendek dari ini dianggap batuk, bukan kalimat.
        "min_speech_ms": 600,
        # Lebih panjang dari ini dipotong paksa, biar tidak menggantung.
        "max_speech_ms": 30000,
    },
    "kaiwa": {
        "level": "N4",
        "persona": (
            "Kamu Ruri -- Aoki Ruri, dari Ruri Dragon. Suatu pagi kamu bangun "
            "dengan tanduk naga di kepala, dan reaksimu waktu itu kira-kira "
            "\"oh, tanduk\". Begitulah caramu menghadapi hampir semua hal.\n\n"
            "Kamu bicara pendek dan datar. Tidak antusias, tidak memuji "
            "berlebihan, hampir tidak pernah pakai tanda seru. Kalau lawan "
            "bicaramu bilang sesuatu yang mengejutkan, kamu terima saja dulu.\n\n"
            "Tapi kamu tidak dingin. Kamu tetap bertanya balik karena memang "
            "ingin tahu, cuma nadanya santai. Kadang kamu ngantuk atau malas, "
            "dan kamu bilang apa adanya.\n\n"
            "Kamu bukan guru. Kalau bahasa Jepangnya salah, obrolan tetap "
            "jalan -- koreksinya punya tempatnya sendiri."
        ),
        "correction": True,
        "reply_max_sentences": 2,
        # Ingatan tiap orang dilupakan setelah sekian menit tidak dipakai. 0
        # berarti tidak pernah lupa sampai bot direstart -- dan kalimat pertama
        # seseorang besok pagi akan dijawab sebagai lanjutan obrolan tadi malam.
        "memory_idle_minutes": 30,
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path: str | None = None) -> dict:
    p = path or PATH
    raw: dict = {}
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as fh:
                raw = json.load(fh)
        except Exception as exc:
            raise SystemExit("config.json tidak terbaca: %s" % exc)
    cfg = _merge(DEFAULTS, raw)

    # stream_completion membaca provider["max_tokens"], dan entry yang disalin
    # dari Amadeus belum tentu punya kunci itu.
    for prov in cfg["llm"]["providers"]:
        prov.setdefault("max_tokens", 0)
        prov.setdefault("context_window", 0)
        prov.setdefault("kind", "openai")
    return cfg


def save(cfg: dict, path: str | None = None) -> None:
    p = path or PATH
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def provider_chain(cfg: dict) -> list:
    """Provider aktif dulu, sisanya jadi cadangan dengan urutan aslinya."""
    provs = cfg["llm"]["providers"]
    aktif = active_provider(cfg)
    if aktif is None:
        return []
    return [aktif] + [p for p in provs if p is not aktif]


def active_provider(cfg: dict) -> dict | None:
    provs = cfg["llm"]["providers"]
    if not provs:
        return None
    want = str(cfg["llm"].get("active_provider") or "").strip()
    for p in provs:
        if p.get("name") == want:
            return p
    return provs[0]
