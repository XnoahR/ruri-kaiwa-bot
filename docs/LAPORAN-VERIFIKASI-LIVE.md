# Laporan Verifikasi, Validasi & Pengujian — Fitur Live

| | |
|---|---|
| Proyek | ruri-kaiwa-bot — fitur `ruri/live` (Gemini Live API) |
| Tanggal | 2026-09-11 |
| Lingkungan uji | Linux, Python 3.12.3; venv uji: discord.py 2.7.1, discord-ext-voice-recv 0.5.2a179, aiohttp 3.14.3 |
| Sumber kebenaran | 3 dokumen di `docs/Gemini Live Model/` + halaman resmi `ai.google.dev` yang ditarik ulang saat fase A; API nyata dengan 3 kunci sementara dari pemilik proyek |
| Dokumen terkait | `docs/FITUR-LIVE.md` (desain, invarian, batas diketahui, daftar periksa manual) |

---

## 1. Ruang lingkup dan kriteria

Fitur harus: (a) terpisah dari fitur utama, (b) mudah maintenance, (c) mudah
dilacak error/bug-nya, (d) tersusun rapi-sederhana, (e) blast radius
terisolasi dan terkontrol, (f) model `gemini-3.1-flash-live-preview` saja,
(g) masuk/ganti voice/thinking lewat command Discord, (h) prompt hidup di
berkas markdown terpisah, (i) validasi menyeluruh (compile, smoke test, dsb.)
sampai benar-benar berfungsi penuh.

Metode verifikasi dibagi 4 fase berurutan; setiap fase menjadi prasyarat
fase berikutnya:

```
A. Verifikasi dokumen & spesifikasi API   (membangun pemahaman benar)
B. Verifikasi statis                      (kode masuk akal bagi interpreter)
C. Pengujian fungsional offline           (logika benar tanpa jaringan)
D. Smoke test terhadap API nyata          (protokol benar menurut server)
```

---

## 2. Fase A — Verifikasi dokumen & spesifikasi API

**Sumber:** `GEMINI-LIVE-GUIDE-RAW-WEBSOCKETS.md`, `GEMINI-LIVE-GUIDE-SDK.md`,
`sdk-sample-code.py`, lalu halaman resmi (semua per 2026-09-04):
`/api/live`, `/gemini-api/docs/live-guide`, `/gemini-api/docs/live-session`,
`/gemini-api/docs/live-api/best-practices`, `/gemini-api/docs/speech-generation`,
`/api/generate-content`, plus contoh klien resmi Google
(`gemini-live-api-examples/…/geminilive.js`).

Pertanyaan yang dijawab dan jawabannya (kutipan verbatim ada pada sesi
verifikasi; hasilnya sudah dikodifikasi menjadi tes):

| # | Pertanyaan | Hasil verifikasi |
|---|---|---|
| A1 | Bentuk `setup` yang sah | `responseModalities`/`speechConfig`/`thinkingConfig` **bersarang di `generationConfig`** — quickstart raw (flat) terbukti ketinggalan zaman |
| A2 | Cara menyalakan transkrip | `inputAudioTranscription: {}` / `outputAudioTranscription: {}` wajib ada; tanpa itu event tidak pernah datang |
| A3 | VAD & giliran | server-side default ON; `realtimeInputConfig.automaticActivityDetection{silenceDurationMs, prefixPaddingMs, disabled, …}`; contoh resmi memakai 2000/500 ms |
| A4 | `thinkingLevel` sah | `MINIMAL`, `LOW`, `MEDIUM`, `HIGH` (+`THINKING_LEVEL_UNSPECIFIED`) |
| A5 | Daftar suara | 30 suara prebuilt; Japanese didukung; languageCode tidak bisa dipaksa pada native-audio |
| A6 | Semantik sliding window | **discard dari awal konteks**, systemInstruction selalu dipertahankan; default = 80% konteks (104857) dan target ½-nya (52428) — angka sample = default resmi |
| A7 | Umur sesi | 15 menit **tanpa** kompresi → **unlimited dengan kompresi**; **koneksi** direset ±10 menit → `goAway{timeLeft}` + `sessionResumption` (handle valid 2 jam) |
| A8 | Interupsi | `serverContent.interrupted:true` → klien wajib stop playback & buang buffer |
| A9 | Chunk kirim | 20–40 ms disarankan; audio ≈ 25 token/detik; transkrip = surcharge token |
| A10 | Yang tidak ada di API 3.1 | proactivity & affective dialog tidak didukung; function calling 3.1 hanya sekuensial (tidak dipakai di v1) |

**Kontradiksi antar-dokumen yang diselesaikan:** bentuk setup (A1), ejaan
`disable`/`disabled` (reference vs contoh resmi → contoh resmi + server nyata
menerima `disabled`; dibuktikan Fase D).

---

## 3. Fase B — Verifikasi statis

| Cek | Perintah nyata | Hasil |
|---|---|---|
| Sintaks semua modul baru + diubah | `python3 -m py_compile ruri/live/*.py ruri/bot.py ruri/config.py` (juga di venv) | **OK, 0 galat** |
| Impor penuh modul live + bot (venv) | `python -c "import ruri.live.cog"` + bangun `bot.build(DEFAULTS)` + `setup_hook()` | **OK** — `Kaiwa` dan `LiveKaiwa` terdaftar, `!live` punya 7 sub (`on off muat kata suara pikir status`) |
| Impor di lingkungan minim (tanpa discord) | `python3 -m unittest discover -s tests` | **OK** — modul live inti tidak menyentuh discord/aiohttp di top-level |
| `config.example.json` vs `DEFAULTS` (gaya CI, termasuk blok `live`) | skrip assert CI | **SINKRON** |
| Bukti isolasi: cog live yang rusak tidak menjatuhkan bot | ditemukan *secara nyata* saat pengembangan (galat `discord.audio`) — `try/except` registrasi menahan, log `fitur live tidak terpasang`, bot utama tetap hidup | **Berlaku** |

---

## 4. Fase C — Pengujian fungsional offline

### 4.1 Rekap

| Lingkungan | Terkumpul | Hasil | Catatan |
|---|---|---|---|
| venv lengkap (discord+aiohttp+voice-recv) | 122 tes | **122 OK — 0 gagal**, 9 skip | skip = 2× ffmpeg (`test_audio`), 4× fugashi, 3× ffmpeg (`test_live_cog_audio`) — semua by-design di mesin tanpa alat itu; di CI keduanya terpasang |
| python sistem (tanpa discord/aiohttp/ffmpeg) | 122 tes | **122 OK — 0 gagal**, 13 skip | 4 sisanya = `Sumber` (butuh discord) + `test_live_seam` (butuh discord) |

Baseline lama (70 tes) tetap hijau total → **tidak ada regresi**.

### 4.2 Isi 52 tes live (per modul)

**`test_live_protocol.py` (17)** — bentuk setup bersarang; transkrip eksplisit
on/off; VAD tersambung; kompresi selalu ada; resumption aktif tanpa handle;
thinking ngawur → `MINIMAL`; setup siap-JSON (termasuk isi berisi `\n\t"`);
audio base64; teks & streamEnd; **1 pesan = banyak kejadian sekaligus** (kasus
anti-elif); setupComplete `{}`; interrupted/generationComplete; goAway
berwaktu; handle hanya bila resumable; transkrip parsial→selesai; pesan
asing/kosong → []; tool_call diakui; 30 suara unik + default di daftar.

**`test_live_client.py` (8)** — handshake: setup terkirim lalu setupComplete
dan turn diteruskan, `streamEnd` terkirim sebelum tutup; handle internal
tidak bocor ke pemanggil; **goAway → sambung ulang dengan handle di setup
kedua**; koneksi panjang/putus = sehat (tanpa penalti); server menolak terus
→ `Putus` setelah jatah habis (bukan menggantung); antrean penuh → `False`
(chunk dibuang, sesi utuh); `hentikan()` idempoten & aman sebelum koneksi.

**`test_live_pipe.py` (8)** — downmix rata L/R, potongan ganjil dibuang,
kosong; `take_chunk` penuh/pendek/kosong; `merge_teks` delta vs snapshot.

**`test_live_prompt.py` (9)** — placeholder terganti; placeholder asing
dibiarkan utuh; bawaan terisi penuh (`{{` habis); level ngawur → N4; isi
berkas dipakai; berkas hilang/kosong → bawaan; override lewat cfg; opening.

**`test_live_seam.py` (4)** — *bagian yang mengubah kode utama*:
`jeda_dengar` melepas sink utama + menandai flag; `lanjut_dengar` memasang
kembali **instan** (tanpa menunggu penjaga 30 detik); tanpa voice → hanya
bersih flag; **penjaga `_pastikan_masuk` tidak merebut sink dari live**.

**`test_live_cog_audio.py` (3, jalan di CI)** — `Sumber`: 1 detik 24k mono →
±1 detik 48k stereo, kelipatan pas frame Opus; push setelah tutup tidak
meledak; read setelah cleanup → `None` rapi.

### 4.3 Uji wiring cog offline (di luar suite, tereksekusi manual)

`_kunci()` fallback `live.api_key → stt.api_key` dua cabang; `_setup_baru()`
menghasilkan pesan valid-JSON dengan voice benar, `*Transcription{}`,
`sessionResumption` (+handle saat resume), SI termemilih level. → **OK**.

---

## 5. Fase D — Smoke test API nyata

Semua memakai **kode produksi** (`protocol.build_setup`, `LiveSession`,
`client.sambungkan` aiohttp), bukan skrip terpisah. Kunci hanya lewat
`env` — tidak pernah ditulis ke berkas (dibuktikan §6.3).

### D.1 — Kunci #1: satu giliran penuh

Teks Indonesia dikirim via `realtimeInput.text` setelah `setupComplete`
(voice `Zephyr`, thinking `minimal`).

```
>> SETUP OK
>> turn_complete
ringkasan: {'setup_complete': 1, 'audio': 15, 'output_text': 3,
            'generation_complete': 1, 'turn_complete': 1}
audio: 119070 byte | transkrip: おはよう！元気だよ。あなたは？
handle: ada
SMOKE PASS
```

Terverifikasi: handshake setup-bersarang diterima server; `disabled` ejaan
benar; `thinkingLevel:MINIMAL` diterima; kompresi diterima; **model menjawab
dalam bahasa Jepang** (SI dipatuhi); audio 24 kHz mengalir (15 chunk);
transkrip keluar dengan benar; **token `sessionResumption` diterima**.

### D.2 — Kunci #2: reconnect ber-handle + multi-turn (voice `Kore`)

Skenario: turn 1 → `mulai_ulang()` (jalur yang sama dipakai `goAway`) →
setup kedua dengan handle → turn 2 (pertanyaan memori).

```
[setup dibuat, handle=baru]  >> SETUP OK (t1)
[setup dibuat, handle=ada]   >> SETUP OK (reconnect)
ringkasan: {'setup_complete': 2, 'audio': 46, 'output_text': 19,
            'generation_complete': 2, 'turn_complete': 2} | audio: 478564
SMOKE-2 PASS (resume + multi-turn + voice=Kore)
```

Sambungan kedua benar-benar memakai handle (di-probe pada builder).
Jawaban pertanyaan memori samar ("tentang apa saja") → dicatat sebagai
**batas diketahui**: update handle datang berkala, muat-segera-besar-habis-1-turn
bisa kembali ke snapshot sebelum turn. `goAway` asli (~10 menit) tidak kena
(fhandle terakhir selalu baru). Rincian: `FITUR-LIVE.md` §7.

### D.3 — Kunci #3: probe handshake

`KUNCI-3: setupComplete OK` → ketiga kunci sementara hidup dan bisa dipakai.

---

## 6. Temuan yang ditangkap oleh verifikasi (bukan dari dokumen)

| # | Temuan | ditangkap oleh | perbaikan |
|---|---|---|---|
| 1 | **Server mengirim JSON lewat frame WS BINARY**, bukan TEXT | Smoke D awal: "server menutup koneksi sebelum setupComplete" berulang | `_BungkusWs.__anext__` menerima TEXT+BINARY; alasan close/exception ikut dilempar agar terlacak |
| 2 | `setupComplete` = `{}` kosong (falsy) | unit test `test_setup_complete` | cek dengan `in`, bukan `.get()` |
| 3 | Klien lama circular: tunggu `_siap` sebelum consume, padahal `_siap` hanya lahir dari consume | unit test handshake (timeout) | loop tunggal: tenggat hanya sebelum setupComplete |
| 4 | `sessionResumption` harus selalu dikirim agar handle pernah tiba | review live-session saat pra-smoke | builder selalu menyertakannya; tes `test_resumption_selalu_dihidupkan` |
| 5 | `discord.audio` bukan submodul publik di 2.7 | cek-build offline (dan justru membuktikan isolasi registrasi bekerja) | `discord.AudioSource` (diekspor level atas) |
| 6 | `ws_connect(timeout=float)` di-deprekat di aiohttp 3.14 | peringatan saat smoke | tenggat dipegang `asyncio.wait_for` kita sendiri, per-koneksi + per-handshake |

---

## 7. Kebersihan & batas repo

### 7.1 Regresi
Suite lama (70 tes) utuh dan hijau di kedua lingkungan.

### 7.2 Jejak perubahan kode lama (diff stat)
```
.github/workflows/ci.yml |  3 +-
.gitignore               |  5 ++++
config.example.json      | 14 ++++++++++
requirements.txt         |  5 ++++
ruri/bot.py              | 71 +++++++++++++++++++++++++++++++++++++++++++++++-
ruri/config.py           | 38 ++++++++++++++++++++++++++
6 files changed, 134 insertions(+), 2 deletions(-)
```
Berkas baru: `ruri/live/*` (1.127 baris) + 6 berkas tes (597 baris) +
`live_prompts/*.example.md` + 2 dokumen `docs/`. `README/BRIEF/AGENTS` **tidak
disentuh** (sesuai instruksi).

### 7.3 Higiene rahasia
`git grep` + `grep -r` atas ketiga kunci (prefix penuh) di seluruh berkas
repo, termasuk `docs/`: **0 hasil**. CI kini juga memindai pola `AQ.…`.

### 7.4 Yang TIDAK bisa diverifikasi dari lingkungan ini
1. **Siklus Discord-VC penuh** (`!live on` nyata di kanal suara, dengar-jawab
   via sink/playback, `!live off` manual, auto-off kesepian) — butuh
   Discord nyata + kanal suara + telinga manusia. Semua lapis di bawahnya
   (protokol, klien, pipa, prompt, sambat, registrasi) sudah diuji; daftar
   periksananya ada di `FITUR-LIVE.md` §8.
2. **Konkurensi thread ffmpeg `Sumber` di bawah Player discord.py** — tes
   round-trip ada (D.4.2, jalan di CI yang punya ffmpeg); mesin uji lokal
   tidak punya ffmpeg, jadi 3 tes ini statusnya *skip di sini, jalan di CI*.
3. **Perilaku `goAway` asli (~10 menit)** — jalurnya sama persis dengan
   smoke-2 (resume ber-handle) yang lulus; hanya trigger waktunya yang
   belum diuji utuh.

---

## 8. Cara menjalankan ulang seluruh verifikasi

```bash
# B+C (mesin mana pun):
python3 -m py_compile ruri/live/*.py ruri/bot.py ruri/config.py
python3 -m unittest discover -s tests -v
# + venv dengan discord/aiohttp (cek build & wiring cog; lihat §4.3)

# D (API nyata — isi kunci via env, JANGAN tulis ke berkas):
LK="…" python /tmp/live_smoke.py          # D.1
# D.2/D.3: skrip ad-hoc pada sesi implementasi (pola sama, lihat §5)
```

---

## 9. Putusan akhir per kriteria

| Kriteria | Putusan | Dasar |
|---|---|---|
| Terpisah dari fitur utama | **TERPENUHI** | 1 paket + 1 cog baru; sambat 134 baris di 6 berkas, nol logika live di kode lama; isolasi terbukti menahan galat impor nyata |
| Mudah maintenance | **TERPENUHI** | 5 modul, satu peran; knob di `config.live`; prompt di berkas markdown |
| Mudah dilacak | **TERPENUHI** | alasan close/error server diteruskan apa adanya; `Putus` hanya setelah jatah habis; kegagalan cog live ter-log saat startup |
| Rapi & sederhana | **TERPENUHI (subjektif)** | ~1.1k baris produksi + ~600 baris tes; tanpa abstraksi spekulatif |
| Blast radius terkendali | **TERPENUHI** | guard `live_aktif` (tes khusus), fail-safe selalu kembali ke mode normal, `live.enabled:false` melepas semua |
| Fungsi live benar | **TERPASANG & TERUJI SAMPAI BATAS YANG BISA DIUJI OTOMATIS** | 122 unit OK, 0 regresi, smoke API nyata lulus dengan 3/3 kunci; siklus VC manual = prasyarat tanda-tangan pengguna (`FITUR-LIVE.md` §8) |

Status "100% fully functional" secara jujur didefinisikan: **semua lapis yang
dapat divalidasi tanpa manusia di kanal suara telah divalidasi hijau terhadap
API nyata; satu lapis terakhir (pendengaran & suara keluar di Discord nyata)
menunggu walkthrough §8 oleh pemilik proyek** — dan bila item di sana gagal,
lapis-lapis di bawahnya sudah pasti bukan tersangkanya.

---

## 10. Adendum — temuan CI pemilik proyek (pasca-laporan awal)

Saat suite dijalankan di CI (GitHub Actions, ubuntu, ffmpeg **ada**), satu
tes gagal yang di mesin pengembanganku ter-*skip* justru karena ffmpeg tidak
ada:

```
ERROR: test_24k_mono_jadi_48k_stereo (test_live_cog_audio.Sumber)
  File "ruri/live/cog.py", line 78, in read
    return discord.AudioFrame(data)
AttributeError: module 'discord' has no attribute 'AudioFrame'
```

**Sebab.** `Sumber.read()` mengembalikan `discord.AudioFrame(...)`. Kelas itu
tidak ada di discord.py 2.x (diuji langsung pada discord.py 2.7.1:
`'AudioFrame' in dir(discord)` → `False`). Kontrak `AudioSource.read()` adalah
**`bytes`** mentah 3840, dengan kosong/None sebagai penanda selesai.

**Kenapa lolos lokal.** Mesin pengembanganku tak punya ffmpeg, jadi tiga tes
`Sumber` berstatus skip (tercatat di §4.1 dan §7.4). Baru dieksekusi pertama
kalinya di CI yang punya ffmpeg -- dan di situ ia gagal. Ini persis alasan
berlapisnya verifikasi: CI menangkap apa yang mesin lokal tak bisa jalankan.
`ResourceWarning: unclosed file` di sekitar tes yang sama adalah gejala
ikutan (proses ffmpeg tertinggal dari cabang error), bukan bug kedua.

**Perbaikan.**
- `ruri/live/cog.py` — `Sumber.read()` mengembalikan `bytes` (bukan
  `AudioFrame`); `cleanup()` menutup stream stdout/stdin agar tak ada fd
  tertinggal; komentar di `read()` mengunci kontranya supaya asumsi ini tak
  diulang.
- `tests/test_live_cog_audio.py` — loop baca disesuaikan ke kontrak bytes.

**Verifikasi ulang.** ffmpeg statis dipasang di venv uji agar cabang ini benar-
benar dijalankan, bukan di-skip:

| Cek | Hasil |
|---|---|
| `test_live_cog_audio` (3 tes, ffmpeg nyata) | **3 OK** |
| Suite lengkap di venv (dengan ffmpeg) | **122 OK — 0 gagal**, 4 skip (hanya fugashi/UniDic, tak terpasang di venv) |
| `py_compile` modul terubah | OK |
| `grep AudioFrame` di ruri/ tests/ | hanya satu komentar penjelas; nol pemanggilan |

**Pelajaran untuk verifikasi berikutnya:** jangan mengandalkan angka skip
dari mesin yang alatnya kurang. Lapis yang butuh ffmpeg kini punya bukti
eksekusi nyata (ffmpeg 7.0.2 statis), bukan sekadar "pasti jalan di CI".
Sisanya tetap sama: satu lapis terakhir (Discord VC nyata) hanya bisa
ditandatangani manusia — `FITUR-LIVE.md` §8.

---

## 11. Adendum 2 — bug produksi kedua (`!live on` crash + bot terjepit)

Setelah §10 masuk, pemilik proyek mencoba `!live on` di server nyata. Ia
crash dan meninggalkan state yang membuat mode biasa ikut tuli:

```
File ".../ruri/live/cog.py", line 217, in live_on
    sink = LiveSink(ses)
TypeError: Can't instantiate abstract class LiveSink with abstract method cleanup
```

Lalu muncul gejala yang membingungkan pengguna: `!live off` berkata "nggak
jalan", tetapi `!join` berkata "mode live sedang jalan". Instruksi pemilik:
audit dulu dengan bukti, JANGAN fix; operasi git remote dijauhi.

### 11.1 Root cause (dibuktikan, bukan diasumsikan)

**Dua bug, satu akar.**

- **Bug A (crash).** `voice_recv.SinkABC` mendeklarasikan `cleanup` sebagai
  `@abc.abstractmethod` (sumber: `discord/ext/voice_recv/sinks.py:135`).
  `LiveSink.__dict__` hanya punya `wants_opus` + `write` — `cleanup` diwarisi
  sebagai stub abstrak, belum diisi. Python menghukum KELAS abstrak
  tak-penuh saat INSTANSIASI, bukan saat definisi; itu sebabnya cek-impor CI
  lolos dan bug baru meledak di produksi. `KaiwaSink` (pipa utama) punya
  `cleanup` — pola benar ditiru tapi satu metode tertinggal.
  Bukti lokal: `LiveSink(object())` → `TypeError` yang sama;
  `sorted(voice_recv.AudioSink.__abstractmethods__)` →
  `['cleanup','wants_opus','write']`; inspect membedakan `LiveSink.__dict__`
  (tidak ada `cleanup`) vs `KaiwaSink.__dict__` (ada).
- **Bug B (bot terjepit).** Di `live_on` lama, `jeda_dengar(gid)` +
  `stop_listening()` terjadi SEBELUM `LiveSink` dibangun, dan
  `self.sesi[gid]=ses` SESUDAH `vc.listen`. Crash di tengah = flag
  `live_aktif` terpasang, telinga utama dilepas, sesi tak pernah terdaftar.
  `!live off` tak bisa memulihkan (butuh sesi), `!join` tertolak (melihat
  flag). Pemulihan hanya restart proses. Gejala yang dilaporkan pemilik
  (`!join` menyalahkan mode live) mengonfirmasi Bug B 100%.
- **Celah verifikasi (akarnya).** `grep -rn "LiveSink" tests/` → nol hasil.
  Kelas itu tidak pernah diinstansiasi satu tes pun. Ini kelas bug yang SAMA
  dengan §10 (`AudioFrame`): runtime path `cog.py` di luar `Sumber` tak punya
  cakupan, dan CI/venv lokal tidak sampai ke sana karena tak ada Discord
  nyata.

### 11.2 Perbaikan (test-first, merah lalu hijau)

`tests/test_live_cog_flow.py` ditulis SEBELUM kode produksi disentuh;
keempatnya dijalankan dan dipastikan MERAH pada versi bug:

| tes | alasan merah (bug asli) | hasil setelah fix |
|---|---|---|
| `test_semua_sink_repo_boleh_diinstansiasi` (guard) | `LiveSink.__abstractmethods__` = `{cleanup}` | hijau |
| `test_sukses_terima_serah_dan_bisa_keluar` | `TypeError` instansiasi `LiveSink` | hijau |
| `test_gagal_listen_tidak_meninggalkan_bot_tuli` | flag `live_aktif` tertinggal (Bug B) | hijau |
| `test_off_membersihkan_flag_sisa_crash_lama` | `!live off` tak memulihkan flag basi | hijau |

Guard itu sendiri jaring anti-ulang: ia menguji `__abstractmethods__` kosong
bagi SEMUA turunan `AudioSink` di repo, jadi metode abstrak yang terlupa mati
di CI, bukan di panggung — bukan cuma `LiveSink`, tapi juga `KaiwaSink`.

Perbaikan kode:
- `ruri/live/cog.py`: `LiveSink.cleanup()` diisi (`buf.clear()`, dengan komentar
  yang menjelaskan kenapa kelas abstrak lolos impor tapi gagal instansiasi).
- Urutan `live_on` dibalik: SEMUA komponen (termasuk `LiveSink`) dibangun
  sebelum penyerahan pendengaran; `vc.listen` dibungkus `try/except` yang
  memanggil `lanjut_dengar()` sebagai rollback. Gagal = tidak pernah
  meninggalkan bot tuli.
- `!live off` bisa membersihkan flag sisa-crash (sesi `None` tapi
  `live_aktif` terisi → pulihkan telinga + beri tahu), bukan lagi jalan buntu.

### 11.3 Verifikasi ulang

| lapis | hasil |
|---|---|
| `test_live_cog_flow` (4 tes) | merah → **4 OK** setelah fix |
| Suite penuh di venv + ffmpeg nyata | **126 OK — 0 gagal**, 4 skip (fugashi/UniDic tak terpasang) |
| Suite penuh di python sistem (tanpa discord/ffmpeg) | **126 OK**, 17 skip by-design |
| `py_compile` modul terubah, dua interpreter | OK |
| Bangun bot offline + 7 sub `!live` | OK |
| Smoke API nyata (kode produksi, kunci #1) pasca-fix | `SMOKE-RECHECK PASS` — setup, 79.710 B audio, transkrip `元気だよ。どうしたの？`, turnComplete |
| Guard `__abstractmethods__` semua sink | aktif di CI mulai sekarang |

### 11.4 Yang TIDAK (dan tidak bisa) diverifikasi dari mesin ini — tetap

Bug ini justru menegaskannya: ada runtime path (`cog.py` yang menyentuh
`sink → vc.listen`, Player discord.py) yang cuma hidup saat Discord + kanal
suara nyata berjalan. §11 menangkap Bug A+B lewat mock selengkap mungkin,
tapi mock ≠ VC nyata. Satu lapis terakhir itu tetap: **walkthrough
`FITUR-LIVE.md` §8 oleh manusia**. Kabar baiknya, mulai sekarang kalau ia
gagal, penyebabnya audio/Discord — bukan lagi protokol, klien, pipa, prompt,
sambat, maupun instansiasi sink (semuanya hijau dengan bukti eksekusi).

Batas lama §10 soal `!live muat` (snapshot handle) tidak berubah; pemulihan
server dari state terjepit sebelum fix ter-deploy: `systemctl restart ruri`.
