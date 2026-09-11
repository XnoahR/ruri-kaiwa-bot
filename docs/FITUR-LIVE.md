# Fitur Live — catatan implementasi & perubahan

Dokumen ini berdiri sendiri. `README.md`, `BRIEF.md`, dan `AGENTS.md` sengaja
**tidak** disentuh atas permintaan; semua yang baru dan semua yang berubah ada
di sini. (Kalau nanti mau diserap ke tiga dokumen itu, jadikan §7 bahan
mentahnya.)

## 1. Apa itu

Mode **live**: ngobrol suara langsung dengan `gemini-3.1-flash-live-preview`
di kanal suara Discord. Audio user dialirkan terus-menerus ke Gemini lewat
WebSocket; suara jawaban model diputar kembali. Rantai `ffmpeg -> stt -> llm
-> tts` milik mode utama dilewati seluruhnya — giliran bicara diputuskan VAD
di sisi Google, dan model bisa diinterupsi secara alami (barge-in).

Masuk/keluar lewat perintah; pipa utama tidak tahu-menahu soal ini sampai
perintah `!live on` dipanggil.

```
!live on | off | muat | status
!live suara [<nama>]        (30 suara prebuilt Gemini; default Zephyr)
!live pikir [minimal|low|medium|high]
!live kata <teks>           (teks sebagai ucapan; uji-coba tanpa mikrofon)
```

## 2. Berkas baru

| berkas | isi | butuh discord? | dites offline? |
|---|---|---|---|
| `ruri/live/__init__.py` | penanda paket | tidak | - |
| `ruri/live/protocol.py` | bangun/parsing pesan JSON, konstanta (model, 30 suara, thinking, angka kompresi) | tidak | ya |
| `ruri/live/client.py`   | `LiveSession`: handshake, pump, kirim-antre, `goAway`/`sessionResumption`, reconnect berjenjang, `hentikan`, `mulai_ulang` | tidak (aiohttp diimpor tertunda) | ya (ws palsu) |
| `ruri/live/pipe.py`     | `downmix` stereo->mono, `take_chunk`, `merge_teks` | tidak | ya |
| `ruri/live/prompt.py`   | muat prompt dari berkas + placeholder `{{persona}} {{level}} {{level_hint}}` + bawaan kode | tidak | ya |
| `ruri/live/cog.py`      | `LiveKaiwa` (perintah), `LiveSink`, `Sumber` (ffmpeg 24k->48k), kartu transkrip, penjaga sepi | **ya** | hanya `Sumber` (skipUnless ffmpeg) |
| `live_prompts/system.example.md` / `opening.example.md` | template prompt ber-tag XML; salin jadi `system.md` / `opening.md` untuk dipakai | - | - |
| `tests/test_live_protocol.py`, `test_live_client.py`, `test_live_pipe.py`, `test_live_prompt.py`, `test_live_cog_audio.py`, `test_live_seam.py` | 34 tes baru | - | ya |

## 3. Yang BERUBAH pada berkas lama

Semuanya sambat resmi dan pasif selama fitur tidak dipakai:

| berkas | perubahan |
|---|---|
| `ruri/bot.py` | `Kaiwa.live_aktif: set` + metode `jeda_dengar()/lanjut_dengar()`; guard `live_aktif` di `_pastikan_masuk`, `on_message`, `join`, `leave`; `build().setup()` mendaftarkan `LiveKaiwa` dalam `try/except` (live rusak = bot utama tetap hidup) |
| `ruri/config.py` | blok `DEFAULTS["live"]` (enabled, api_key, model, voice, thinking, transcripts, chunk_ms, silence_duration_ms, prefix_padding_ms, auto_off_minutes, system_file, opening_file) |
| `config.example.json` | salinan `live` (CI menuntut sinkron) |
| `requirements.txt` | `aiohttp` dinyatakan eksplisit |
| `.gitignore` | `live_prompts/*.md` dikecualikan, `*.example.md` tetap ikut repo |
| `.github/workflows/ci.yml` | cek-impor mencakup `ruri/live/*`; pemindai kunci mengenal format `AQ.` |

## 4. Alur

```
!live on ──► Kaiwa.jeda_dengar(gid)          (sink utama dilepas; penjaga lewat)
         ──► vc.stop_listening() ─► vc.listen(LiveSink)
         ──► LiveSession.mulai():
               ws_connect ─► setup{generationConfig{AUDIO, voice, thinking},
                                   systemInstruction, AAD, kompresi,
                                   sessionResumption, *Transcription{}}
               ◄─ setupComplete ─► kirim teks opening.md
               stream:  sink.buf ─downmix─► chunk 40 ms ─► realtimeInput.audio
               balas:   inlineData(24k) ─► Sumber(ffmpeg) ─► vc.play
               kartu:   input/outputTranscription ─► embed ke kanal
               goAway   ─► close ─► sambung ulang dengan handle (konteks utuh)
!live off/_sepi/Putus ──► streamEnd ─► ws tutup ─► vc.stop_listening
                      ──► Kaiwa.lanjut_dengar(gid)  (sink utama terpasang lagi)
```

## 5. Invarian — jangan "dirapikan" (padanan §"Yang menahan beban")

- **`responseModalities` BERSARANG di `generationConfig`.** Quickstart
  raw-websocket Google menunjukkannya datar di root `setup`; contoh itu
  ketinggalan zaman. Contoh resmi `geminilive.js` + API reference: bersarang.
- **Frame server adalah BINARY, bukan TEXT**, walau isinya JSON. Wrapper yang
  hanya menerima TEXT menganggap setiap balasan sebagai koneksi putus (bug
  nyata yang ditemukan smoke test pertama).
- **Satu pesan server bisa membawa audio + transkrip + turnComplete
  sekaligus** — parser `parse_server` mengecek tiap cabang, bukan `elif`.
- **`setupComplete` datang sebagai `{}` kosong** — falsy; harus dicek dengan
  `in`, bukan `.get()`.
- **`inputAudioTranscription: {}` / `outputAudioTranscription: {}` wajib
  ada** untuk menerima transkrip; tanpa keduanya event-nya tidak pernah datang.
- **`sessionResumption` selalu ikut dikirim** (bahkan tanpa handle) supaya
  server mengirim update token; tanpa token, `goAway` = sesi baru = konteks
  hilang.
- **`contextWindowCompression` wajib tetap ada**: tanpanya sesi audio-only
  dibunuh server di menit ke-15. Angka 104857/52428 = default resmi
  (80% dari 128k dan setengahnya) yang ditulis eksplisit supaya perubahan
  default Google tidak mengubah perilaku kita diam-diam.
- **`Sumber.read()` mengembalikan BYTES mentah 3840**, bukan objek frame —
  `discord.AudioFrame` tidak ada di discord.py 2.x (kontrak `AudioSource.read
  -> bytes`, `b""`/None = selesai). Kesalahan ini lolos dari mesin tanpa
  ffmpeg (tesnya skip) dan baru ditangkap CI yang punya ffmpeg — lihat
  `LAPORAN-VERIFIKASI-LIVE.md` §10.
- **`interrupted` harus langsung menghentikan `vc.play` dan membuang buffer**
  — kalau tidak, model "terus bicara menimpa" user.
- **`live_aktif` adalah satu-satunya penghubung** kedua fitur; jangan pernah
  memindahkan logika live ke `bot.py` atau sebaliknya.
- **Kegagalan total mode live selalu kembali ke mode normal**
  (`_jaga -> Putus -> lanjut_dengar`), tidak pernah duduk tuli — sama
  filosofinya dengan penjaga suara utama.
- **Kunci tidak pernah masuk repo**: `live.api_key` hanya di `config.json`
  (gitignored); CI memindai format `AIza…`, `gsk_…`, `sk-fish-…`, `AQ.…`.
- **`!yomi/!arti/!ulang/!reset` dan kartu `<koreksi>` tidak berlaku di mode
  live**: mediumnya suara, keluarannya suara — tidak ada teks untuk disaring.

## 6. Verifikasi yang sudah dijalankan (2026-09-11)

| lapis | hasil |
|---|---|
| `python -m unittest discover -s tests` — python sistem (tanpa discord) | **118 OK**, 9 skip (ffmpeg/fugashi/discord tidak ada — by design) |
| sama di venv discord+aiohttp+voice-recv 0.5.2a179, discord.py 2.7.1 | **118 OK**, 9 skip |
| bangun bot offline dengan `DEFAULTS` + registrasi kedua cog | OK; `!live` punya 7 sub |
| wiring cog: fallback `live.api_key -> stt.api_key`, render setup + handle | OK |
| **Smoke nyata** (kode produksi, kunci #1): handshake, `text_message` → audio 119.070 B, transkrip `おはよう！元気だよ。あなたは？`, `turnComplete`, handle resume tiba | **PASS** |
| **Smoke-2** (kunci #2): 2 sambungan, `mulai_ulang` dengan handle, multi-turn, voice `Kore` → 478.564 B audio, 2× turnComplete | **PASS** |
| **Probe kunci #3**: setupComplete diterima | **PASS** |
| `config.example.json` vs `DEFAULTS` (cek gaya CI, termasuk `live`) | sinkron |
| `git grep` ketiga kunci di repo | nol |

## 7. Batas yang diketahui

- **Konteks pasca-`!live muat` bisa tertinggal satu snapshot.** Update handle
  datang berkala; muat segera setelah satu turn bisa menyambung ke snapshot
  sebelum turn itu (terlihat di smoke-2). Untuk `goAway` (~10 menit) tidak
  masalah: handle terakhir selalu baru. Kerja aman: `!live off` + `!live on`
  memang sesi baru.
- **Dua orang bicara bersamaan** terdengar beruntun (satu stream gabungan) —
  atribusi per penutur tidak ada di v1; transkrip "🎙" tanpa nama.
- **Transkrip live selalu ke kanal teks**, mengabaikan `!log dm` (jalur WS
  tidak tahu siapa penuturnya). `live.transcripts: false` untuk mematikannya.
- **Uji end-to-end dengan Discord + kanal suara sungguhan belum dilakukan** —
  tidak bisa dari mesin ini; lihat daftar kustom di bawah. Semua lapis di
  bawahnya sudah diuji terhadap API nyata.

## 8. Daftar periksa saat dicoba di server

1. `cp live_prompts/system.example.md live_prompts/system.md` (opsional; tanpa
   ini pakai bawaan kode), edit sesukanya.
2. Pastikan `stt.api_key` (atau `live.api_key`) berisi kunci Gemini dengan
   akses model live — ketiga kunci sementara ini sudah terbukti bisa.
3. `./update.sh` (setelah perubahan di-commit) — cek log: `fitur live
   terpasang` tidak muncul sebagai galat; daftar perintah memuat `live`.
4. Masuk VC → `!live on` → model menyapa duluan (berkas opening) → ngomong;
   potong bicaranya di tengah → dia harus berhenti <0,5 dtk.
5. `!live suara Leda` → `!live muat`; `!live pikir high` → `!live muat`.
6. `!live kata halo` (tanpa mic) → tetap menjawab bersuara.
7. `!live off` → mode biasa langsung mendengar lagi (ujicoba: `!join` tidak
   perlu; bicara normal harus langsung dijawab).
8. Diamkan ruangan ±30 menit → keluar sendiri ("Sepi ya…").
9. Log untuk menelusuri: `live: goAway`, `live g<id>:`, `fitur live tidak
   terpasang`.
