"""Lapisan Discord untuk mode live: perintah, sink, playback, kartu transkrip.

Satu-satunya modul di ruri/live yang butuh discord; itu sebabnya tidak ada
unit test di sini -- status yang sama dengan bot.py. Logikanya sengaja tipis:
semua yang bisa dipindah ke protocol/client/prompt sudah pindah.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import threading
import time

import discord
from discord.ext import commands, voice_recv

from . import client, pipe, prompt, protocol, swap

# Catatan: AudioSource diambil lewat discord.AudioSource, bukan discord.audio.*
# -- tidak ada submodul discord.audio di 2.7; yang ada discord.player.
log = logging.getLogger("ruri")

WARNA = 0x1E50A2           # 瑠璃色, sama dengan kartu mode utama
FRAME_BYTES = 3840         # 20 ms stereo 48k 16-bit -- satu frame Opus

# Batas bufer LiveSink, byte mono 48k (~96 B/ms => ~10 detik).
MAKS_BUFER = 960_000
MELUAP_JEDA = 30.0         # detik antar-laporan luapan -- jeritan 100 baris
                           # per detik justru MENYIMPAN penyebabnya
MACET_PULIH = 5.0          # luapan selama ini = link macet -> mulai_ulang()


class Sumber(discord.AudioSource):
    """PCM 24 kHz mono dari model -> stream 48 kHz stereo untuk Discord.

    Satu proses ffmpeg per "mulut" (segmen antara dua interupsi): push()
    mengisi stdin, read() stdout dipanggil thread audio discord.py. Menutup
    input berarti EOF -> ffmpeg flush -> read() None -> pemutaran selesai
    sendiri, tanpa memotong kalimat di tengah seperti yang terjadi kalau
    kita stop paksa tiap turn.
    """

    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error",
             "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", "pipe:0",
             "-f", "s16le", "-ar", "48000", "-ac", "2", "pipe:1"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL)
        self.stream = self.proc.stdout
        self._lock = threading.Lock()
        self._tutup = False

    def push(self, pcm: bytes) -> None:
        with self._lock:
            if self._tutup:
                return
            try:
                self.proc.stdin.write(pcm)
            except (BrokenPipeError, ValueError):
                self._tutup = True

    def tutup_input(self) -> None:
        with self._lock:
            if self._tutup:
                return
            self._tutup = True
        try:
            self.proc.stdin.close()
        except Exception:
            pass

    def read(self):
        """Kontrak discord.py 2.x: bytes mentah 3840 (48k stereo s16le) per
        panggilan; kosong/None menghentikan pemutaran. `discord.AudioFrame`
        TIDAK ADA di 2.x -- versi lama/perpustakaan lain; membaca asumsi itu
        dari luar tidak akan pernah lolos tes yang benar-benar berjalan.
        """
        try:
            data = self.stream.read(FRAME_BYTES)
        except Exception:
            return None
        if not data:
            return None
        if len(data) < FRAME_BYTES:
            data = data + b"\x00" * (FRAME_BYTES - len(data))
        return data

    def cleanup(self) -> None:
        self.tutup_input()
        try:
            self.proc.terminate()
            self.proc.wait(timeout=1)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        for fh in (self.stream, self.proc.stdout, self.proc.stdin):
            try:
                if fh is not None:
                    fh.close()
            except Exception:
                pass


class LiveSink(voice_recv.AudioSink):
    """Kumpulkan PCM semua pembicara jadi satu stream mono 48k.

    Sengaja tidak ada pemisahan per orang maupun deteksi giliran: di mode
    live yang memisahkan giliran adalah VAD server. Dua orang ngomong
    bersamaan akan terdengar beruntun di stream -- model tetap bisa ikut
    dua-duanya, dan itu perilaku yang bisa dimaafkan untuk v1.
    """

    def __init__(self, ses: "Sesi") -> None:
        super().__init__()
        self.ses = ses

    def wants_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        # Bukan hiasan: voice_recv menjadikannya @abstractmethod, dan Python
        # menghukum KELAS yang belum lengkap baru saat INSTANSIASI -- kelas yang
        # belum penuh tetap legal didefinisikan, jadi tes yang tidak pernah
        # membuat LiveSink tidak akan pernah menangkapnya. (Bug produksi
        # 2026-09-11; pengamannya ada di tests/test_live_cog_flow.)
        # Isi yang jujur: buang PCM yang tersisa supaya audio basi tidak
        # menetes ke sesi berikutnya.
        self.ses.buf.clear()

    def write(self, user, data) -> None:
        pcm = getattr(data, "pcm", None)
        if not pcm:
            return
        ses = self.ses
        ses.buf += pipe.downmix(bytes(pcm))
        ses.last_dengar = time.monotonic()
        if len(ses.buf) <= MAKS_BUFER:
            return
        # Lebih dari ~10 detik tanpa dikosongkan pompa = link ke model tidak
        #.consume. Buang yang lama, simpan yang paling baru: topik ruangan
        # sudah lewat jauh.
        del ses.buf[: len(ses.buf) - MAKS_BUFER]
        sekarang = time.monotonic()
        if ses.macet_sejak is None:
            ses.macet_sejak = sekarang
            ses.macet_log = sekarang
            ses.macet_count = 0
            log.warning("live g%s: buffer audio meluap, sisa lama dibuang -- "
                        "dugaan link ke model macet", ses.gid)
        else:
            ses.macet_count += 1
            if sekarang - ses.macet_log >= MELUAP_JEDA:
                log.warning("live g%s: masih meluap %.0f detik (+%d buangan)",
                            ses.gid, sekarang - ses.macet_sejak, ses.macet_count)
                ses.macet_log = sekarang
                ses.macet_count = 0
        if (sekarang - ses.macet_sejak >= MACET_PULIH
                and not ses.pulih_dipanggil and not ses.dibersihkan):
            # Macet >5 detik = link mati; menunggu tidak menolong. Putus-sambung
            # dengan handle (konteks sesi utuh). _pompa mereset episode ini
            # setelah kirim pertama sukses; kalau gagal terus, _jaga yang
            # menyerah dan mengembalikan mode normal.
            ses.pulih_dipanggil = True
            log.warning("live g%s: macet %.0f detik -- menyambung ulang WS",
                        ses.gid, sekarang - ses.macet_sejak)
            try:
                asyncio.get_running_loop().create_task(ses.client.mulai_ulang())
            except RuntimeError:          # tak ada loop (hanya bisa di tes aneh)
                pass


class Sesi:
    def __init__(self, gid: int, vc, channel, cli: client.LiveSession) -> None:
        self.gid = gid
        self.vc = vc
        self.channel = channel
        self.client = cli
        self.buf = bytearray()
        self.src: Sumber | None = None
        self.in_teks = ""
        self.out_teks = ""
        self.lahir = time.monotonic()
        self.last_dengar = time.monotonic()
        self.tugas: asyncio.Task | None = None
        self.pompa: asyncio.Task | None = None
        self.dibersihkan = False
        # Episode 'kirim macet' (lihat MAKS_BUFER/MACET_PULIH): waktu mulai,
        # waktu log terakhir, penghitung di antaranya, dan flag pemulihan.
        self.macet_sejak: float | None = None
        self.macet_log = 0.0
        self.macet_count = 0
        self.pulih_dipanggil = False


class LiveKaiwa(commands.Cog):
    def __init__(self, bot: commands.Bot, cfg: dict, kaiwa: commands.Cog) -> None:
        self.bot = bot
        self.cfg = cfg
        self.kaiwa = kaiwa           # sambat resmi: jeda_dengar/lanjut_dengar
        self.sesi: dict = {}         # gid -> Sesi
        self._penjaga: asyncio.Task | None = None

    # ------------------------------------------------------------ perintah
    @commands.group(name="live", invoke_without_command=True)
    async def live(self, ctx: commands.Context) -> None:
        """Bicara langsung dengan model suara. `!live` untuk bantuan."""
        p = self.cfg["prefix"]
        await ctx.send(
            "Mode live: ngomong langsung sama Gemini, dia jawab pakai suara.\n"
            "`%slive on` masuk (harus sudah di kanal suara) - `%slive off` keluar\n"
            "`%slive suara` daftar suara, `%slive suara Leda` ganti\n"
            "`%slive pikir [minimal|low|medium|high]` - `%slive muat` pakai ulang "
            "prompt/suara/thinking yang baru\n"
            "`%slive kata <teks>` kirim teks sebagai ucapan - `%slive status`"
            % (p, p, p, p, p, p, p, p))

    @live.command(name="on")
    async def live_on(self, ctx: commands.Context) -> None:
        gid = ctx.guild.id
        if gid in self.sesi:
            await ctx.send("Mode live sudah jalan di sini. `%slive off` dulu."
                           % self.cfg["prefix"])
            return
        if ctx.author.voice is None or ctx.author.voice.channel is None:
            await ctx.send("Kamu belum ada di kanal suara mana pun.")
            return
        key = self._kunci()
        if not key:
            await ctx.send("Belum ada API key Gemini. Isi `live.api_key` "
                           "(atau `stt.api_key`) di config.")
            return
        if str(self.cfg["live"].get("model") or "").lower().find("live") < 0:
            log.warning("live: model %r bukan model live -- koneksi hampir "
                        "pasti ditolak", self.cfg["live"].get("model"))

        target = ctx.author.voice.channel
        vc = ctx.guild.voice_client
        if vc is None:
            vc = await target.connect(cls=voice_recv.VoiceRecvClient)
        elif vc.channel.id != target.id:
            await vc.move_to(target)

        # Urutan penting: SEMUA komponen dibangun dulu (murni, tanpa efek
        # samping), baru pendengaran diserahkan. Bug 2026-09-11: LiveSink
        # dibangun setelah jeda_dengar dan gagal di instansiasi -- flag
        # tinggal terpasang, telinga utama sudah dilepas, dan sesi tidak pernah
        # terdaftar: !live off tidak bisa memulihkan, !join tertolak. Gagal
        # sebelum penyerahan = tidak ada yang perlu di-rollback.
        ses = Sesi(gid, vc, None, None)
        ses.channel = (self.kaiwa.kanal_obrolan(ctx.guild) or ctx.channel)
        url = protocol.ws_url(key)
        cli = client.LiveSession(
            url,
            lambda h: self._setup_baru(gid, h),
            lambda k, d: self._event(ses, k, d),
        )
        ses.client = cli
        sink = LiveSink(ses)

        # Serah terima pendengaran -- lewat swap.ganti_telinga, BUKAN
        # stop_listening()+listen() langsung: itulah balapan produksi
        # (pembongkaran reader lama membunuh reader baru / dua reader hidup
        # bersamaan -> DAVE gagal ~50%). Gagal total: lanjut_dengar() sudah
        # memulihkan sendiri, dan sesi live tidak pernah terdaftar.
        self.kaiwa.jeda_dengar(gid)
        if not await swap.ganti_telinga(vc, sink, label="live g%s" % gid):
            await self.kaiwa.lanjut_dengar(gid)
            await ctx.send("Aku nggak bisa mulai mendengar di sini. "
                           "-# Detailnya di log.")
            return
        self.sesi[gid] = ses
        ses.tugas = self.bot.loop.create_task(self._jaga(ses))
        ses.pompa = self.bot.loop.create_task(self._pompa(ses))
        self._mulai_penjaga()
        await ctx.send("Mode **live** nyala di **%s**. Ngomong aja -- sekarang "
                       "dia yang dengerin langsung, bukan aku." % target.name)

    @live.command(name="off")
    async def live_off(self, ctx: commands.Context) -> None:
        gid = ctx.guild.id
        ses = self.sesi.get(gid)
        if ses is None:
            # Sisa kegagalan lama: flag terpasang tanpa sesi (dulu crash
            # meninggalkan ini, dan !live off tidak bisa menyentuhnya).
            # Jangan biarkan pengguna terjepit di antara dua pesan yang
            # saling bertentangan -- bersihkan dan kembalikan telinga.
            if gid in self.kaiwa.live_aktif:
                await self.kaiwa.lanjut_dengar(gid)
                await ctx.send("Flag live sisa crash lama kubersihkan; "
                               "pendengaran mode biasa aktif lagi.")
            else:
                await ctx.send("Mode live lagi nggak jalan di sini.")
            return
        await self._bersih(ses, pasang_ulang=True)
        await ctx.send("Beres, pendengaran kembali ke mode biasa.")

    @live.command(name="muat")
    async def live_muat(self, ctx: commands.Context) -> None:
        """Sambung ulang dengan prompt/suara/thinking terbaru, konteks utuh."""
        ses = self.sesi.get(ctx.guild.id)
        if ses is None:
            await ctx.send("Mode live lagi nggak jalan di sini.")
            return
        await ses.client.mulai_ulang()
        await ctx.send("Koneksi diulang dengan setelan terbaru -- sesi dan "
                       "ingatan model tetap lanjut.")

    @live.command(name="kata")
    async def live_kata(self, ctx: commands.Context, *, teks: str = "") -> None:
        """Kirim teks sebagai ucapan (ujicoba tanpa mikrofon juga)."""
        ses = self.sesi.get(ctx.guild.id)
        if ses is None:
            await ctx.send("Nggak ada sesi live di sini. `%slive on` dulu."
                           % self.cfg["prefix"])
            return
        teks = teks.strip()
        if not teks:
            await ctx.send("Formatnya: `%slive kata <teks>`" % self.cfg["prefix"])
            return
        ok = await ses.client.kirim(protocol.text_message(teks))
        await ctx.send("Terkirim." if ok else "Koneksi sedang macet; teksnya dibuang.")

    @live.command(name="suara")
    async def live_suara(self, ctx: commands.Context, *, nama: str = "") -> None:
        l = self.cfg["live"]
        nama = nama.strip()
        if not nama or nama.lower() in ("daftar", "list", "ls", "?"):
            baris = ["`%s`" % v + ("  <- sekarang" if v == l.get("voice") else "")
                     for v in protocol.VOICES]
            await ctx.send("**Suara live (Gemini prebuilt)**\n" + "  ".join(baris)
                           + "\n\nGanti: `%slive suara <nama>`" % self.cfg["prefix"])
            return
        padan = next((v for v in protocol.VOICES if v.lower() == nama.lower()), None)
        if padan is None:
            await ctx.send("`%s` bukan suara prebuilt Gemini. `%slive suara` "
                           "buat daftar." % (nama[:40], self.cfg["prefix"]))
            return
        l["voice"] = padan
        self._simpan()
        pesan = "Suara live: **%s**." % padan
        if ctx.guild.id in self.sesi:
            pesan += " Pakai `%slive muat` biar langsung kedengeran." % self.cfg["prefix"]
        await ctx.send(pesan)

    @live.command(name="pikir")
    async def live_pikir(self, ctx: commands.Context, level: str = "") -> None:
        l = self.cfg["live"]
        level = level.strip().lower()
        if not level:
            await ctx.send("Thinking sekarang: **%s**. Pilihan: %s. "
                           "Makin tinggi makin pintar, makin lambat jawaban "
                           "pertamanya." % (l.get("thinking"),
                                            ", ".join(protocol.THINKING)))
            return
        if level not in protocol.THINKING:
            await ctx.send("Yang ada: %s" % ", ".join(protocol.THINKING))
            return
        l["thinking"] = level
        self._simpan()
        pesan = "Thinking live: **%s**." % level
        if ctx.guild.id in self.sesi:
            pesan += " Pakai `%slive muat` biar langsung berlaku." % self.cfg["prefix"]
        await ctx.send(pesan)

    @live.command(name="status")
    async def live_status(self, ctx: commands.Context) -> None:
        l = self.cfg["live"]
        ses = self.sesi.get(ctx.guild.id)
        kunci = self._kunci()
        baris = [
            "model **%s** - suara **%s** - thinking **%s** - transkrip **%s**"
            % (l.get("model"), l.get("voice"), l.get("thinking"),
               "nyala" if l.get("transcripts", True) else "mati"),
            "api key: %s" % ("ada" if kunci else "**kosong**"),
            "prompt: %s" % ("berkas" if l.get("system_file") else "bawaan"),
        ]
        if ses is None:
            baris.append("sesi: tidak jalan")
        else:
            baris.append("sesi: **hidup** %.0f detik - resume: %s - buffer: %d KB"
                         % (time.monotonic() - ses.lahir,
                            "ada" if ses.client.handle else "belum",
                            len(ses.buf) // 1024))
        await ctx.send("\n".join(baris))

    # ------------------------------------------------------------ batre
    def _kunci(self) -> str:
        l = self.cfg["live"]
        return str(l.get("api_key") or "").strip() or \
            str(self.cfg["stt"].get("api_key") or "").strip()

    def _simpan(self) -> None:
        from .. import config as conf
        conf.save(self.cfg)

    def _setup_baru(self, gid: int, handle: str | None) -> dict:
        l = self.cfg["live"]
        level = self.kaiwa.session(gid).level if hasattr(self.kaiwa, "session") else "N4"
        return protocol.build_setup(
            prompt.system_text(self.cfg, level),
            voice=str(l.get("voice") or "Zephyr"),
            thinking=str(l.get("thinking") or "minimal"),
            model=str(l.get("model") or protocol.MODEL),
            transcripts=bool(l.get("transcripts", True)),
            silence_ms=int(l.get("silence_duration_ms") or 2000),
            prefix_ms=int(l.get("prefix_padding_ms") or 500),
            resume_handle=handle,
        )

    async def _pompa(self, ses: Sesi) -> None:
        """Bufor mono 48k -> potongan chunk_ms -> kirim.

        Chunk hanya dilepas dari bufer SETELAH kirim sukses -- antrean penuh
        tidak boleh lagi memakan audio (yang lama: take_chunk lalu dibuang).
        """
        n = max(2, int(self.cfg["live"].get("chunk_ms") or 40)) * 96  # 96 B/ms
        try:
            while True:
                await asyncio.sleep(0.02)
                while len(ses.buf) >= n:
                    chunk = bytes(ses.buf[:n])
                    if not await ses.client.kirim(protocol.audio_message(chunk)):
                        break
                    del ses.buf[:n]
                    if ses.macet_sejak is not None:
                        # Lalulintas pulih -- tutup episode macet.
                        ses.macet_sejak = None
                        ses.macet_count = 0
                        ses.pulih_dipanggil = False
        except asyncio.CancelledError:
            pass

    async def _jaga(self, ses: Sesi) -> None:
        try:
            await ses.client.mulai()
        except asyncio.CancelledError:
            raise
        except client.Putus as exc:
            log.warning("live g%s: mati -- %s", ses.gid, exc)
            await self._kartu(ses, "ん… mode live terputus terus, kita balik ke "
                                   "mode biasa dulu.", "live: %s" % exc)
            await self._bersih(ses, pasang_ulang=True)
            return
        await self._bersih(ses, pasang_ulang=True)   # berhenti normal

    # ------------------------------------------------------------ kejadian
    async def _event(self, ses: Sesi, kind: str, data) -> None:
        if kind == "audio":
            self._suara(ses, data)
        elif kind == "setup_complete":
            await self._buka(ses)
        elif kind == "input_text":
            ses.in_teks = pipe.merge_teks(ses.in_teks, data)
        elif kind == "input_done":
            await self._kartu(ses, "> 🎙 %s" % (ses.in_teks or ""))
            ses.in_teks = ""
        elif kind == "output_text":
            ses.out_teks = pipe.merge_teks(ses.out_teks, data)
        elif kind == "output_done":
            await self._kartu(ses, "%s" % (ses.out_teks or ""), sebagai_bot=True)
            ses.out_teks = ""
        elif kind == "interrupted":
            self._potong(ses)
            ses.out_teks = ""
        elif kind == "turn_complete":
            if ses.out_teks:
                await self._kartu(ses, ses.out_teks, sebagai_bot=True)
                ses.out_teks = ""
        ses.last_dengar = time.monotonic()

    async def _buka(self, ses: Sesi) -> None:
        teks = prompt.opening_text(self.cfg).strip()
        if teks:
            await ses.client.kirim(protocol.text_message(teks))

    def _suara(self, ses: Sesi, pcm: bytes) -> None:
        vc = ses.vc
        if ses.src is not None:
            ses.src.push(pcm)
            return
        src = Sumber()
        src.push(pcm)
        ses.src = src
        self.bot.loop.create_task(self._putar(ses, src, vc))

    async def _putar(self, ses: Sesi, src: Sumber, vc) -> None:
        # Klip yang masih tersisa dibiarkan selesai: memotongnya di tengah
        # kalimat terdengar seperti bot rusak. 30 x 20ms = cukup untuk ekor.
        for _ in range(30):
            if not vc.is_playing():
                break
            await asyncio.sleep(0.02)
        def done(err) -> None:
            if err:
                log.warning("live g%s: pemutaran: %s", ses.gid, err)
            self.bot.loop.call_soon_threadsafe(self._src_selesai, ses, src)
        try:
            vc.play(src, after=done)
        except Exception as exc:
            log.warning("live g%s: vc.play gagal -- %s", ses.gid, exc)
            self._src_selesai(ses, src)

    def _src_selesai(self, ses: Sesi, src: Sumber) -> None:
        if ses.src is src:
            ses.src = None
        src.cleanup()

    def _potong(self, ses: Sesi) -> None:
        src, ses.src = ses.src, None
        try:
            if ses.vc.is_playing():
                ses.vc.stop()
        except Exception:
            pass
        if src is not None:
            src.cleanup()

    # ------------------------------------------------------------ kartu
    async def _kartu(self, ses: Sesi, badan: str, sebagai_bot: bool = False) -> None:
        if not badan or not ses.channel:
            return
        if not self.cfg["live"].get("transcripts", True):
            return
        try:
            emb = discord.Embed(description=badan[:2500], colour=WARNA)
            me = self.bot.user
            if sebagai_bot and me is not None:
                emb.set_author(name=me.display_name,
                               icon_url=me.display_avatar.url)
            emb.set_footer(text="LIVE")
            await ses.channel.send(embed=emb)
        except Exception as exc:
            log.debug("live: kartu gagal -- %s", exc)

    # ------------------------------------------------------------ bersih
    async def _bersih(self, ses: Sesi, pasang_ulang: bool) -> None:
        if ses.dibersihkan:
            return
        ses.dibersihkan = True
        self.sesi.pop(ses.gid, None)
        if ses.pompa:
            ses.pompa.cancel()
        try:
            await asyncio.wait_for(ses.client.hentikan(), timeout=5)
        except Exception:
            pass
        if ses.tugas and ses.tugas is not asyncio.current_task():
            ses.tugas.cancel()
        try:
            if ses.vc.is_connected():
                if ses.vc.is_playing():
                    ses.vc.stop()
                if ses.vc.is_listening():
                    ses.vc.stop_listening()
        except Exception:
            pass
        if ses.src is not None:
            ses.src.cleanup()
            ses.src = None
        if pasang_ulang:
            await self.kaiwa.lanjut_dengar(ses.gid)

    # ------------------------------------------------------------ penjaga
    def _mulai_penjaga(self) -> None:
        if self._penjaga is None or self._penjaga.done():
            self._penjaga = self.bot.loop.create_task(self._sepi())

    async def _sepi(self) -> None:
        """Keluar sendiri dari sesi yang ruangannya sudah lama kosong."""
        try:
            while self.sesi:
                await asyncio.sleep(15)
                menit = int(self.cfg["live"].get("auto_off_minutes") or 0)
                if menit <= 0:
                    continue
                batas = menit * 60
                for ses in list(self.sesi.values()):
                    if time.monotonic() - ses.last_dengar > batas:
                        log.info("live g%s: sepi %d menit, keluar sendiri",
                                 ses.gid, menit)
                        await self._kartu(ses, "Sepi ya… aku keluar dari mode "
                                              "live. `%slive on` lagi kapan pun."
                                          % self.cfg["prefix"])
                        await self._bersih(ses, pasang_ulang=True)
        except asyncio.CancelledError:
            pass

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member,
                                    before: discord.VoiceState,
                                    after: discord.VoiceState) -> None:
        """Bot terlempar dari kanal (kick, VC penuh, delete) -> matikan live
        dengan rapi; kalau tidak, sesi menggantung tanpa telinga."""
        if member != self.bot.user or member.guild is None:
            return
        ses = self.sesi.get(member.guild.id)
        if ses is None:
            return
        if after.channel is None or after.channel.id != ses.vc.channel.id:
            log.info("live: kanal suara hilang, sesi ditutup")
            await self._bersih(ses, pasang_ulang=False)
