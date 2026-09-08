# Ruri 瑠璃

Partner kaiwa bahasa Jepang di Discord. Kamu ngomong di kanal suara, dia
dengerin, jawab dalam bahasa Jepang, dan ngomong balik — plus benerin kalimatmu
kalau janggal.

Namanya dari 瑠璃, lapis lazuli. 瑠璃色 adalah warna langit malam.

## Cara kerjanya

```
kamu ngomong di VC
   -> discord-ext-voice-recv   PCM 48k stereo, per orang
   -> lapisan DAVE             dekripsi ujung-ke-ujung (lihat ruri/dave.py)
   -> deteksi giliran          jeda antar-paket, bukan event speaking
   -> ffmpeg                   wav mono 16k
   -> API transkripsi          bentuk OpenAI, atau Gemini native
   -> model bahasa             provider apa pun: OpenAI-style / Anthropic
   -> pisah obrolan & koreksi
   -> Fish Audio TTS
   -> ffmpeg -> kanal suara
```

Transkrip apa yang kamu ucapkan ikut ditulis di kanal teks. Sering kali itu
sendiri yang paling banyak mengajari.

## Yang dibutuhkan

- **Python 3.10+** dan **ffmpeg**
- **Bot Discord** dengan *Message Content Intent* dinyalakan
  (*Server Members* tidak perlu)
- **API key Fish Audio** untuk suaranya
- **API key transkripsi** — Groq punya tier gratis dengan `whisper-large-v3`
- **Satu provider model bahasa** — apa pun yang OpenAI-compatible atau
  Anthropic. Router lokal juga bisa.

## Pasang

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.json config.json     # lalu isi kuncinya
./run.sh
```

Undang botnya dengan scope `bot` dan izin: *View Channels*, *Send Messages*,
*Read Message History*, *Connect*, *Speak*.

## Perintah

| perintah | apa yang terjadi |
|---|---|
| `!join` / `!leave` (`!disconnect`, `!dc`) | masuk / keluar kanal suara |
| `!vc` | tongkrongi kanal suaramu 24 jam, `!vc off` matikan |
| `!channel here` | kunci obrolan ke kanal ini — di situ tidak perlu di-tag |
| `!channel off` | bebas di kanal mana pun, tapi harus di-tag |
| `!level n3` | kunci level JLPT (N5–N1) |
| `!voice` / `!voice list` | daftar suara, `!voice 5` untuk ganti |
| `!voice tambah <nama> <id>` | simpan suara Fish baru |
| `!yomi` / `!arti` | furigana+romaji / terjemahan kalimat terakhirnya |
| `!ulang` | bacakan lagi |
| `!log dm` | transkrip suara cuma ke DM yang bicara; `!log kanal` / `!log off` |
| `!reset` | lupakan obrolanmu; `!reset semua` untuk semua orang |
| `!status` | provider, suara, level, kredit Fish |

## Beberapa keputusan, dan alasannya

**DAVE dibuka sendiri.** Kanal suara Discord memakai enkripsi ujung-ke-ujung,
dan `discord-ext-voice-recv` mendekripsi lapisan transport lalu langsung
menyerahkan hasilnya ke Opus — yang menolaknya sebagai *corrupted stream*.
Menolak DAVE bukan jalan keluar: Discord menutup sambungan dengan kode 4017.
Kepingannya sebenarnya lengkap — discord.py menjalankan handshake MLS dan
menyimpan sesinya, `davey` menyediakan `decrypt()` — cuma tidak pernah
disambungkan. `ruri/dave.py` yang menyambungkannya.

**Paket yang gagal diganti senyap, bukan dibuang.** Paket yang hilang begitu
saja membuat rekamannya kehilangan dua puluh milidetik tanpa menyisakan jeda:
potongan ucapan tersambung rapat, dan transkripsinya membaca sambungan itu
sebagai kata yang tidak pernah diucapkan.

**Satu paket rusak tidak boleh mematikan pendengaran.** `PacketRouter` memanggil
`stop_listening()` di blok `finally`-nya, jadi satu `OpusError` bikin bot tetap
duduk di kanal tapi budek. Paket yang gagal dilewati saja.

**Penjaga suara memeriksa `is_listening()`, bukan cuma sambungannya.** Hadir di
kanal bukan berarti mendengar.

**Satu giliran punya tenggat menyeluruh, bukan cuma timeout per permintaan.**
Rantai dua puluh delapan model yang masing-masing boleh menggantung sembilan
puluh detik berarti satu kalimat bisa menyandera bot selama empat puluh dua
menit — dan selama giliran itu berjalan, semua ucapan berikutnya dibuang. Dari
luar tidak ada bedanya dengan bot yang tuli, karena yang dibuang tidak
meninggalkan jejak apa pun di log. Sekarang tenggatnya 30 detik
(`llm.turn_deadline_seconds`), sisa waktunya dioper sebagai timeout permintaan
berikutnya, ucapan yang dilewati dicatat, dan giliran yang tetap nyangkut lebih
dari sembilan puluh detik dianggap mati lalu dilangkahi.

**Balasan yang lebih banyak huruf Latin daripada Jepang dibuang, bukan
dikirim.** Sebagian model
menalar dengan prosa biasa; kalau penalarannya belum sampai ke penanda `<balas>`
waktu tokennya habis, yang tersisa cuma potongan isi kepalanya. Dulu itu tetap
dikirim karena satu-satunya alternatif adalah diam. Sekarang ada model
berikutnya di rantai, dan model berikutnya selalu lebih baik daripada isi kepala
yang bocor ke layar lalu dibacakan keras-keras oleh mesin suara. Yang dihitung
porsinya, bukan ada-tidaknya: penalaran yang bocor sering berakhir dengan
kalimat Jepang yang benar menempel di ujungnya.

**Model digilir, bukan diurutkan.** Jatah gratis Gemini dihitung per model per
hari -- kuotanya sendiri bernama
`GenerateRequestsPerDayPerProjectPerModel-FreeTier` -- jadi delapan model
berarti delapan jatah. Tapi hanya kalau dipakai bergantian: dipakai berurutan,
yang pertama habis lebih dulu setiap hari dan sisanya menunggu giliran yang
tidak pernah datang. Isi `models` di satu provider, dan tiap giliran memakai
model berikutnya; yang jatahnya habis dilewati tanpa menjatuhkan sisanya.

**Batas pemakaian ada dua macam, dan bedanya jauh.** `FreeUsageLimitError` atau
`quota exceeded` berarti kering sampai besok; *"Rate limited, wait a moment"*
berarti sedetik lagi juga dilayani. Yang pertama dijeda sepuluh menit, yang
kedua lima belas detik -- dan kalau semua pilihan habis sedangkan sebagiannya
cuma antre, satu percobaan ulang setelah 1,2 detik masih jauh lebih murah
daripada giliran yang hilang. Jangan percaya `retryDelay` dari Google: dia
menyebut 29 detik untuk kuota yang sebenarnya harian.

**Ingatannya per orang, bukan per server.** Satu ruangan berbagi level JLPT
dan pengeras suara, tapi tidak isi percakapan. Kalau tidak, orang yang baru
menyapa disambut lanjutan obrolan orang sebelumnya -- dan dari sisi dia itu
tidak terlihat seperti salah ingat, melainkan seperti mengarang. Ingatannya
juga punya umur (`kaiwa.memory_idle_minutes`, 30 menit): obrolan yang ditinggal
setengah jam bukan lagi obrolan yang sama meski orangnya sama.

**Transkrip suara bisa dikirim ke DM saja.** Kalimat yang kamu ketik memang kamu
pilih untuk diterbitkan; kalimat yang kamu ucapkan tidak. Melihat percobaan
sendiri beserta koreksinya terpampang di kanal yang dibaca semua orang cukup
untuk membuat sebagian orang berhenti mencoba. Discord tidak punya pesan "cuma
kamu yang bisa lihat" di luar balasan atas interaksi, dan suara bukan interaksi
-- jadi yang terdekat adalah DM (`transcript_privacy`). Giliran dari ketikan
tetap dijawab di kanal: kalimatnya sudah terlihat di situ, dan jawaban yang
diam-diam pindah ke DM cuma terlihat seperti dia tidak menjawab.

**Deteksi giliran dari jeda antar-paket.** Event "speaking" Discord bisa telat
atau hilang; jeda paket selalu ada. Ambangnya (`stt.silence_ms`) sengaja longgar
— orang yang sedang belajar berhenti di tengah kalimat, dan memotongnya di situ
mengirim potongan tidak utuh ke transkripsi.

**Modelnya diberi tahu bahwa masukannya hasil pengenalan suara.** Tanpa itu, dia
mengoreksi salah dengar mesin seolah-olah itu kesalahanmu — termasuk mengeyel
soal ejaan namanya sendiri.

**Balasan dibungkus penanda `<balas>`.** Sebagian model menalar dengan prosa
biasa, bukan di dalam tag `<think>`, jadi penalarannya tidak bisa disaring dari
luar. Dengan penanda, apa pun yang dia pikirkan jatuh di luar.

**Furigana tidak lewat model bahasa.** `ruri/furigana.py` memakai MeCab/UniDic:
milidetik, dan jawabannya sama persis tiap kali. Kamusnya 249MB dan ikut termuat
ke memori, jadi di mesin kecil paket itu tidak dipasang dan `!yomi` jatuh balik
ke model bahasa.

**wav, bukan mp3.** Klip satu giliran cuma puluhan kilobita entah bagaimanapun,
dan kompresi berkerugian memakan justru detail yang membedakan bunyi-bunyi
bahasa Jepang yang mirip.

**User-Agent disebutkan.** urllib mengirim `Python-urllib/3.x`, dan Cloudflare —
yang berdiri di depan Groq dan banyak penyedia lain — memblokirnya dengan 403.

**`ruri/providers.py` disalin dari add-on Amadeus Deck** tanpa perubahan logika;
berkas itu memang sudah nol impor `aqt`.

## Belum ada

- Sambungan ke deck Anki (kosakata yang sedang dipelajari masuk ke obrolan)
- Slash command; sekarang masih prefix `!`
- Streaming LLM ke TTS supaya kalimat pertama terdengar lebih cepat
