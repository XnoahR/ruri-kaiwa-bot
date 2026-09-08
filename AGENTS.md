# Untuk agen coding

Baca berkas ini dan `BRIEF.md` sebelum menyentuh kode. Keduanya sengaja ditulis
supaya kamu tidak perlu memindai seluruh repo untuk tahu apa yang boleh diubah.

## Perintah

```bash
python -m unittest discover -s tests   # tes; semuanya offline, tidak perlu kunci
./run.sh                               # jalankan botnya
./update.sh                            # di server: tarik lalu nyalakan ulang
```

Tes tidak menyentuh jaringan dan tidak butuh Discord. Kalau perubahanmu tidak
bisa dites tanpa jaringan, kemungkinan besar ia berada di berkas yang salah —
lihat tabel di `BRIEF.md` untuk memilih tempatnya.

## Yang menahan beban

Hal-hal berikut kelihatan seperti kerumitan yang bisa dirapikan. Bukan. Semuanya
lahir dari kegagalan nyata, dan menghapusnya akan mengulang kegagalan itu.

- **`dave.py` jangan dihapus atau "disederhanakan" jadi mematikan DAVE.**
  Mengumumkan versi DAVE 0 membuat Discord menutup sambungan dengan kode 4017,
  dan bot masuk-keluar kanal tanpa henti.
- **Paket yang gagal diganti senyap, bukan dibuang.** Membuangnya memampatkan
  garis waktu rekaman, dan transkripsinya membaca sambungan itu sebagai kata
  yang tidak pernah diucapkan.
- **`OpusError` ditangkap per paket.** `PacketRouter` memanggil
  `stop_listening()` di blok `finally`-nya; satu paket rusak yang lolos akan
  membuat bot duduk di kanal dalam keadaan tuli.
- **Penjaga suara memeriksa `is_listening()`, bukan cuma `is_connected()`.**
- **Balasan model dibungkus `<balas>`.** Sebagian model menalar dengan prosa
  biasa, bukan di dalam tag `<think>`; tanpa penanda, penalarannya sampai ke
  layar dan ikut dibacakan mesin suara.
- **Prompt memberi tahu model bahwa masukannya hasil pengenalan suara.** Tanpa
  itu ia mengoreksi salah dengar mesin seolah-olah itu kesalahan penggunanya.
- **`User-Agent` disebutkan di setiap permintaan HTTP.** urllib mengirim
  `Python-urllib/3.x`, dan Cloudflare memblokirnya dengan 403 error 1010.
- **wav, bukan mp3, untuk unggahan transkripsi.** Kompresi berkerugian memakan
  detail yang membedakan bunyi-bunyi bahasa Jepang yang mirip.

## Aturan

- **Jangan pernah meng-commit `config.json`.** Isinya kunci sungguhan. Yang
  masuk repo hanya `config.example.json`.
- **Kunci baru ditambahkan ke `config.DEFAULTS`, bukan dibaca langsung.**
  Config pengguna digabung di atas DEFAULTS, jadi kunci yang hilang tidak
  membuat pemasangan lama patah. CI memeriksa `config.example.json` tetap
  sinkron dengan DEFAULTS.
- **Kegagalan jaringan jangan sampai mematikan giliran.** Setiap pemanggil
  API membungkus kesalahannya jadi pesan yang bisa ditunjukkan ke pengguna.
- **Bahasa Indonesia untuk pesan pengguna dan komentar.** Prompt ke model juga
  Indonesia; yang berbahasa Jepang hanya isi percakapannya.
- **Komentar menjelaskan *kenapa*, bukan *apa*.** Kode sudah mengatakan apa.

## Menambah penyedia

- **Model bahasa**: tidak perlu kode. Tambahkan entri di `llm.providers` dengan
  `kind: "openai"` atau `"anthropic"`.
- **Transkripsi**: kalau bentuknya OpenAI `/audio/transcriptions`, cukup ubah
  `stt.base_url` dan `stt.model`. Bentuk lain butuh cabang baru di `stt.py`,
  seperti `_gemini()` yang sudah ada.
- **Suara**: `tts.py` khusus Fish Audio. Penyedia lain butuh berkas sendiri.

## Yang belum dikerjakan

- Streaming model bahasa ke TTS supaya kalimat pertama terdengar lebih cepat
- Slash command (sekarang masih prefix `!`)
- Sambungan ke deck Anki
