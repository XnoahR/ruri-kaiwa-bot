# Brief

Ringkasan teknis Ruri buat orang yang mau ikut ngoprek tanpa harus membaca
seluruh kode. Kalau kamu cuma mau memakainya, `README.md` sudah cukup.

## Masalahnya

Latihan kaiwa butuh lawan bicara yang sabar, tersedia kapan saja, dan mau
membetulkan kalimatmu tanpa memutus obrolan. Ruri menempati peran itu di dalam
Discord: kamu bicara di kanal suara, dia menjawab dengan suara.

## Pipa

```
suara masuk (Discord)   PCM 48k stereo, per pembicara
  -> dave.py            buka lapisan enkripsi ujung-ke-ujung
  -> bot.KaiwaSink      kumpulkan sampai orangnya berhenti bicara
  -> audio.to_upload    ffmpeg -> wav mono 16k
  -> stt.transcribe     API transkripsi
  -> llm.complete       model bahasa
  -> llm.split_reply    pisahkan obrolan dari koreksi
  -> tts.speak          Fish Audio
  -> bot._speak         ffmpeg -> kembali ke kanal
```

## Anggaran waktu, terukur

| bagian | waktu |
|---|---|
| transkripsi (Groq `whisper-large-v3`) | ~0,35 s |
| model bahasa (`gemini-2.5-flash`) | ~3,0 s |
| model bahasa (`oc/mimo-v2.5-free`) | ~5,5 s |
| suara (Fish Audio) | ~3 s |
| **satu giliran** | **7–9 s** |

Yang paling mahal berikutnya adalah menstream model bahasa ke TTS: kalimat
pertama bisa dibacakan sementara sisanya masih ditulis. Belum dikerjakan.

## Berkas

| berkas | isi | butuh aqt/discord? |
|---|---|---|
| `bot.py` | perintah, sink suara, deteksi giliran, penjaga sambungan | ya |
| `dave.py` | tambalan dekripsi E2EE | ya |
| `stt.py` | transkripsi: jalur OpenAI dan Gemini | tidak |
| `tts.py` | Fish Audio | tidak |
| `llm.py` | prompt, level JLPT, pemisah balasan/koreksi | tidak |
| `furigana.py` | MeCab/UniDic, tanpa model bahasa | tidak |
| `audio.py` | ukuran suara, konversi ffmpeg | tidak |
| `config.py` | penggabungan config bersarang | tidak |
| `providers.py` | streaming ke penyedia model | tidak |
| `session.py` | riwayat dan level per kanal | tidak |

Yang tidak butuh discord bisa dites tanpa jaringan; itu yang ada di `tests/`.

## Keadaan sekarang

Jalan: dekripsi DAVE (96–98% paket), transkripsi, model bahasa, suara, obrolan
teks, menu suara, nongkrong 24 jam, koreksi tata bahasa.

Batasnya:

- **Akurasi transkripsi turun drastis untuk ucapan pelan atau berbisik.**
  Whisper mengisi kekosongan dengan frasa umum dari data latihannya
  (`ご視聴ありがとうございました` adalah yang paling sering muncul).
- **Kata non-Jepang** ditranskripsikan menurut bahasa yang terdeteksi. Dengan
  Whisper dan `language=ja` hasilnya katakana; dengan Gemini bisa lompat ke
  aksara lain.
- **Kalimat sangat pendek** lebih rapuh daripada kalimat utuh.

## Kalau ada yang rusak

Log per giliran sudah menjawab sebagian besar pertanyaan:

```
giliran dari X: 1500 ms, rms 0.12 | dave ok=71 gagal=5 ...
  terdengar: おはよう
  balas: おはよう。よく眠れた？
```

- tidak ada baris `giliran dari` -> audio tidak sampai; periksa `dave gagal`
- `dilewati: terlalu pelan` -> ambang `stt.silence_ms`/rms, atau memang berbisik
- `tidak ada ucapan yang dikenali` -> transkripsi mengembalikan kosong
- `gagal` mendekati `ok` -> dekripsi DAVE bermasalah

Untuk mendengar apa yang benar-benar dikirim, nyalakan `stt.save_clips` dan
klipnya tersimpan di `/tmp`.
