"""Bot Discord Ruri: kaiwa dua arah lewat kanal suara."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import time

import discord
from discord.ext import commands, voice_recv

from . import audio, dave, furigana, llm, stt, tts
from .session import Session

log = logging.getLogger("ruri")



# Selang pemeriksaan giliran. Tidak perlu rapat: yang menentukan tetap
# silence_ms, ini cuma seberapa sering jam itu dilihat.
TICK = 0.2


class Turn:
    """Suara satu orang yang sedang menumpuk sampai dia berhenti bicara.

    Namanya ikut disimpan di sini, diambil dari objek yang datang bareng
    paketnya. Kalau tidak, namanya harus dicari lewat cache anggota server --
    dan cache itu butuh intent SERVER MEMBERS, satu izin istimewa yang tidak
    sepadan hanya demi sebuah label.
    """

    def __init__(self, name: str = "kamu") -> None:
        self.buf = bytearray()
        self.name = name or "kamu"
        self.last = time.monotonic()

    def add(self, pcm: bytes) -> None:
        self.buf += pcm
        self.last = time.monotonic()


class KaiwaSink(voice_recv.AudioSink):
    """Penampung suara masuk.

    Batas giliran ditentukan dari jeda antar-paket, bukan dari event "speaking"
    Discord: event itu bisa telat atau hilang, sedangkan jeda paket selalu ada.
    """

    def __init__(self, cog: "Kaiwa", guild_id: int) -> None:
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.turns: dict = {}

    def wants_opus(self) -> bool:
        return False

    def write(self, user, data) -> None:
        if user is None:
            return
        pcm = getattr(data, "pcm", None)
        if not pcm:
            return
        turn = self.turns.get(user.id)
        if turn is None:
            nama = getattr(user, "display_name", None) or getattr(user, "name", "") or "kamu"
            turn = self.turns[user.id] = Turn(nama)
        turn.add(pcm)

    def take_finished(self, silence_ms: int, max_bytes: int = 0) -> list:
        """Giliran yang sudah selesai, sekaligus dikeluarkan.

        Selain karena jeda, giliran juga dipotong kalau bufernya sudah
        kepanjangan: orang yang bicara tanpa jeda selama dua menit kalau tidak
        begini akan menahan dua menit audio di memori sebelum sempat diproses.
        """
        now = time.monotonic()
        done = []
        for uid, turn in list(self.turns.items()):
            diam = (now - turn.last) * 1000 >= silence_ms
            penuh = max_bytes and len(turn.buf) >= max_bytes
            if diam or penuh:
                done.append((uid, turn.name, bytes(turn.buf)))
                del self.turns[uid]
        return done

    def cleanup(self) -> None:
        self.turns.clear()


class Kaiwa(commands.Cog):
    def __init__(self, bot: commands.Bot, cfg: dict) -> None:
        self.bot = bot
        self.cfg = cfg
        self.sessions: dict = {}
        self.sinks: dict = {}
        self.text_channels: dict = {}
        self._watcher: asyncio.Task | None = None
        self._penjaga: asyncio.Task | None = None
        # Disetel True oleh !leave supaya penjaga tidak menariknya balik tiga
        # puluh detik kemudian. Dibersihkan lagi oleh !join.
        self._jangan_balik = False
        # Orang yang DM-nya ketutup, biar diberi tahu sekali saja.
        self._dm_gagal: set = set()

    # ------------------------------------------------------------ kanal
    def _cari_kanal(self, guild, tanda: str, suara: bool):
        """Cocokkan berdasarkan id kalau angka, kalau bukan berdasarkan nama."""
        tanda = str(tanda or "").strip().lstrip("#")
        if not tanda:
            return None
        daftar = guild.voice_channels if suara else guild.text_channels
        if tanda.isdigit():
            ident = int(tanda)
            return discord.utils.get(daftar, id=ident)
        return discord.utils.find(lambda ch: ch.name.lower() == tanda.lower(), daftar)

    def kanal_obrolan(self, guild):
        if guild is None:
            return None
        return self._cari_kanal(guild, self.cfg.get("chat_channel", ""), suara=False)

    def kanal_khusus(self, channel) -> bool:
        """Benar kalau kanal ini memang disediakan untuk dia.

        Di kanal seperti itu -- dan di DM -- menyebut namanya jadi mubazir:
        semua yang ditulis di sana memang ditujukan kepadanya.
        """
        if getattr(channel, "guild", None) is None:
            return True                      # DM
        tanda = str(self.cfg.get("chat_channel") or "").strip()
        if not tanda:
            return False                     # belum dikunci: tetap harus disebut
        if tanda.isdigit():
            return channel.id == int(tanda)
        return channel.name.lower() == tanda.lstrip("#").lower()

    def boleh_ngobrol(self, channel) -> bool:
        """Di kanal mana dia boleh menjawab sama sekali."""
        tanda = str(self.cfg.get("chat_channel") or "").strip()
        if not tanda or getattr(channel, "guild", None) is None:
            return True
        return self.kanal_khusus(channel)

    # Giliran yang lebih lama dari ini dianggap nyangkut, bukan sibuk. Dihitung
    # dari tenggat rantai model ditambah ruang untuk transkripsi dan suara.
    BATAS_NYANGKUT = 90

    def giliran_nyangkut(self, s: Session) -> bool:
        """Jangan biarkan satu giliran yang macet membuat dia tuli selamanya.

        Tenggat di `llm.complete_any` seharusnya sudah mencegahnya, tapi yang
        dijaga di sini kegagalan yang belum terpikirkan -- dan ongkos salah
        tebak cuma dua klip suara yang bertindihan sekali, jauh lebih murah
        daripada bot yang diam sampai ada yang sadar dan me-restart-nya.
        """
        if not s.busy:
            return False
        umur = time.monotonic() - (s.busy_sejak or 0.0)
        if umur < self.BATAS_NYANGKUT:
            return False
        log.warning("giliran sebelumnya nyangkut %.0f detik; dilanjut", umur)
        return True

    # ------------------------------------------------------------ bantuan
    def session(self, guild_id: int) -> Session:
        s = self.sessions.get(guild_id)
        if s is None:
            s = self.sessions[guild_id] = Session(self.cfg)
        return s

    def provider(self):
        from . import config as conf

        return conf.active_provider(self.cfg)

    def ringkas_rotasi(self) -> str:
        """Berapa model yang siap dipakai di tiap mata rantai."""
        bagian = []
        for prov in self.rantai_provider():
            varian = llm.varian(prov)
            siap = [v for v in varian if not llm.dijeda(llm.kunci(v))]
            bagian.append("%s %d/%d" % (prov.get("name", "?"),
                                        len(siap), len(varian)))
        return " - ".join(bagian) or "(kosong)"

    def rantai_provider(self) -> list:
        from . import config as conf

        return conf.provider_chain(self.cfg)

    # 瑠璃色, warna tradisional Jepang untuk lapis lazuli -- sesuai namanya.
    WARNA = 0x1E50A2

    # ------------------------------------------------------------ privasi
    MODE_LOG = ("kanal", "dm", "off")

    ALIAS_LOG = {"channel": "kanal", "pribadi": "dm", "private": "dm",
                 "mati": "off", "none": "off"}

    def mode_log(self) -> str:
        nilai = str(self.cfg.get("transcript_privacy") or "kanal").strip().lower()
        nilai = self.ALIAS_LOG.get(nilai, nilai)
        return nilai if nilai in self.MODE_LOG else "kanal"

    async def dm(self, uid: int | None):
        """Kanal pribadi orangnya, kalau bisa dibuka."""
        if not uid:
            return None
        try:
            orang = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
            return orang.dm_channel or await orang.create_dm()
        except Exception as exc:
            log.warning("nggak bisa buka DM ke %s -- %s", uid, exc)
            return None

    async def tujuan(self, key: int, uid: int | None, pribadi: bool):
        """Ke mana kartu satu giliran dikirim.

        Suara tidak pernah dipilih untuk diterbitkan. Kamu bicara di kanal
        suara, dan transkripnya -- termasuk kalimat yang salah beserta
        koreksinya -- muncul di kanal teks tempat semua orang membacanya. Buat
        sebagian orang itu sudah cukup untuk berhenti mencoba, dan orang yang
        berhenti mencoba tidak belajar apa-apa.

        Discord tidak punya pesan "cuma kamu yang bisa lihat" di luar balasan
        atas sebuah interaksi, dan suara bukan interaksi. Yang paling dekat
        adalah mengirimkannya ke DM orangnya sendiri.

        Giliran yang datang dari ketikan tetap di kanal apa pun modenya:
        kalimatnya sudah terlihat di situ, dan jawaban yang diam-diam pindah ke
        DM cuma terlihat seperti dia tidak menjawab.
        """
        if not pribadi:
            return self.text_channels.get(key)
        mode = self.mode_log()
        if mode == "off":
            return None
        if mode == "dm":
            ch = await self.dm(uid)
            if ch is None:
                await self._dm_tertutup(key, uid)
            return ch
        return self.text_channels.get(key)

    async def _dm_tertutup(self, key: int, uid: int | None,
                           exc: Exception | None = None) -> None:
        """Sekali saja per orang.

        Kartunya tidak terkirim, dan diam tanpa penjelasan membingungkan jauh
        lebih lama daripada satu baris pemberitahuan.
        """
        if not uid or uid in self._dm_gagal:
            return
        self._dm_gagal.add(uid)
        if exc is not None:
            log.warning("DM ke %s ditolak -- %s", uid, exc)
        ch = self.text_channels.get(key)
        if ch is None:
            return
        try:
            await ch.send(
                "<@%d> transkripmu mau kukirim lewat DM, tapi DM-mu ketutup. "
                "Nyalakan *Allow direct messages from server members* di "
                "setelan privasi server ini -- atau `%slog kanal` kalau nggak "
                "masalah kelihatan." % (uid, self.cfg["prefix"]))
        except Exception:
            pass

    async def kirim_giliran(self, key: int, siapa: str | None, ucapan: str | None,
                            kata: str, fix: dict | None, level: str,
                            uid: int | None = None,
                            pribadi: bool = False) -> None:
        """Satu giliran, satu kartu.

        Ucapan dan balasan dulu dikirim sebagai dua pesan terpisah, dan itu
        memecah satu giliran jadi dua benda yang harus disatukan lagi oleh mata.
        Sekarang keduanya satu kartu: ucapanmu jadi kutipan di atas, balasannya
        badan utama, koreksinya bidang tersendiri di bawah.
        """
        ch = await self.tujuan(key, uid, pribadi)
        if ch is None:
            return

        badan = ""
        if ucapan:
            badan += "> 🎙 **%s**\n> %s\n\n" % (siapa or "kamu", ucapan[:600])
        badan += kata[:2500]

        emb = discord.Embed(description=badan, colour=self.WARNA)
        me = self.bot.user
        try:
            emb.set_author(name=me.display_name if me else "Ruri",
                           icon_url=me.display_avatar.url if me else None)
        except Exception:
            emb.set_author(name="Ruri")
        if fix:
            baris = []
            if fix.get("asli"):
                baris.append("~~%s~~" % fix["asli"])
            if fix.get("benar"):
                baris.append("**%s**" % fix["benar"])
            if fix.get("kenapa"):
                baris.append("_%s_" % fix["kenapa"])
            emb.add_field(name="直し  ·  koreksi", value="\n".join(baris)[:1000],
                          inline=False)
        emb.set_footer(text="JLPT %s" % level)
        try:
            await ch.send(embed=emb)
            return
        except discord.Forbidden as exc:
            if pribadi and self.mode_log() == "dm":
                await self._dm_tertutup(key, uid, exc)
                return
        except Exception as exc:
            log.warning("embed ditolak -- %s", exc)
        # Kalau embed ditolak (izin kurang), lebih baik teks polos daripada
        # balasan yang hilang tanpa jejak -- tapi ke tujuan yang sama, bukan
        # balik ke kanal umum.
        try:
            await ch.send(kata[:1900])
        except Exception:
            pass

    async def _maaf(self, key: int, kalimat: str, catatan: str,
                    exc: Exception | None = None, uid: int | None = None,
                    pribadi: bool = False) -> None:
        """Beri tahu penggunanya tanpa memuntahkan isi perut.

        Pesan seperti "HTTP 429 from the API" di tengah percakapan bukan cuma
        jelek -- dia memutus keberadaannya sebagai lawan bicara, dan tidak bisa
        ditindaklanjuti siapa pun kecuali yang menulis kodenya. Detail
        teknisnya tetap dicatat, cuma tidak di depan mata.
        """
        if exc is not None:
            log.warning("%s -- %s", catatan, exc)
        # Kabar gagal bukan transkrip: mode "off" menyembunyikan isi obrolan,
        # bukan alasan kenapa dia diam.
        ch = (await self.dm(uid)) if (pribadi and self.mode_log() == "dm") \
            else self.text_channels.get(key)
        if ch is None:
            ch = self.text_channels.get(key)
        if ch is None:
            return
        try:
            await ch.send("%s\n-# %s" % (kalimat, catatan))
        except Exception:
            pass

    async def say(self, guild_id: int, text: str) -> None:
        ch = self.text_channels.get(guild_id)
        if ch is not None:
            try:
                await ch.send(text[:1900])
            except Exception:
                pass

    # ------------------------------------------------------------ perintah
    @commands.command(name="join")
    async def join(self, ctx: commands.Context) -> None:
        """Masuk ke kanal suara tempat kamu berada."""
        if ctx.author.voice is None or ctx.author.voice.channel is None:
            await ctx.send("Kamu belum ada di kanal suara mana pun.")
            return
        channel = ctx.author.voice.channel
        if ctx.voice_client is not None:
            await ctx.voice_client.move_to(channel)
        else:
            await channel.connect(cls=voice_recv.VoiceRecvClient)

        gid = ctx.guild.id
        self._jangan_balik = False
        self.text_channels[gid] = self.kanal_obrolan(ctx.guild) or ctx.channel
        sink = KaiwaSink(self, gid)
        self.sinks[gid] = sink
        ctx.voice_client.listen(sink)
        if self._watcher is None or self._watcher.done():
            self._watcher = asyncio.create_task(self._watch())

        s = self.session(gid)
        await ctx.send(
            "Masuk ke **%s**. Level **%s**. Ngomong aja, aku dengerin.\n"
            "`%sbantuan` buat daftar perintah."
            % (channel.name, s.level, self.cfg["prefix"])
        )

    @commands.command(name="leave", aliases=["disconnect", "dc", "keluar"])
    async def leave(self, ctx: commands.Context) -> None:
        """Keluar dari kanal suara."""
        if ctx.voice_client is None:
            await ctx.send("Aku lagi nggak di kanal suara.")
            return
        self._jangan_balik = True
        self.sinks.pop(ctx.guild.id, None)
        await ctx.voice_client.disconnect()
        await ctx.send("Sampai nanti. `%sjoin` kalau mau aku balik." % self.cfg["prefix"])

    @commands.command(name="level")
    async def level(self, ctx: commands.Context, value: str = "") -> None:
        """Kunci level JLPT, misal: !level n3"""
        s = self.session(ctx.guild.id)
        value = value.strip().upper()
        if not value:
            await ctx.send("Sekarang di level **%s**. Ganti: `%slevel n3`"
                           % (s.level, self.cfg["prefix"]))
            return
        if value not in llm.LEVELS:
            await ctx.send("Level yang ada: %s" % ", ".join(llm.LEVELS))
            return
        s.level = value
        await ctx.send("Oke, aku tahan di **%s**. %s" % (value, llm.LEVEL_HINT[value]))

    @commands.command(name="yomi")
    async def yomi(self, ctx: commands.Context) -> None:
        """Furigana + romaji buat kalimat terakhirku."""
        s = self.session(ctx.guild.id)
        kalimat = s.terakhir(ctx.author.id)
        if not kalimat:
            await ctx.send("Belum ada kalimat buat dibaca.")
            return
        if furigana.available():
            # Jalur cepat: analisis morfologi, milidetik, dan jawabannya sama
            # persis tiap kali. Modelnya cuma dipakai kalau kamusnya tidak ada.
            try:
                out = await asyncio.to_thread(furigana.both, kalimat)
            except Exception as exc:
                log.warning("furigana gagal -- %s", exc)
                await ctx.send("Lagi nggak bisa baca yang itu.")
                return
            await ctx.send("```\n%s\n```" % out[:1800])
            return
        await self._helper(ctx, llm.FURIGANA_SYS, "furigana")

    @commands.command(name="arti")
    async def arti(self, ctx: commands.Context) -> None:
        """Terjemahan kalimat terakhirku."""
        await self._helper(ctx, llm.TRANSLATE_SYS, "arti")

    async def _helper(self, ctx: commands.Context, system: str, label: str) -> None:
        s = self.session(ctx.guild.id)
        kalimat = s.terakhir(ctx.author.id)
        if not kalimat:
            await ctx.send("Belum ada kalimat buat di-%s." % label)
            return
        prov = self.provider()
        if prov is None:
            await ctx.send("Belum ada provider di config.")
            return
        async with ctx.typing():
            try:
                out = await asyncio.to_thread(
                    llm.one_shot, self.cfg, prov, system, kalimat)
            except Exception as exc:
                log.warning("perintah %s gagal -- %s", label, exc)
                await ctx.send("Lagi nggak bisa. Coba lagi sebentar.")
                return
        await ctx.send("**%s**\n%s" % (label, out[:1800]))

    @commands.command(name="ulang")
    async def ulang(self, ctx: commands.Context) -> None:
        """Bacakan lagi kalimat terakhirku."""
        s = self.session(ctx.guild.id)
        kalimat = s.terakhir(ctx.author.id)
        if not kalimat:
            await ctx.send("Belum ada yang bisa diulang.")
            return
        await self._speak(ctx.guild, kalimat)

    @commands.command(name="reset")
    async def reset(self, ctx: commands.Context, arg: str = "") -> None:
        """Lupakan percakapan sejauh ini. `!reset semua` buat semua orang."""
        s = self.session(ctx.guild.id)
        if arg.strip().lower() in ("semua", "all"):
            s.reset(semua=True)
            await ctx.send("Ingatanku buat semua orang di sini aku kosongkan.")
            return
        s.reset(ctx.author.id)
        await ctx.send("Obrolan kita aku lupakan. Mulai dari awal.")

    @commands.command(name="log", aliases=["privasi"])
    async def log_mode(self, ctx: commands.Context, mode: str = "") -> None:
        """Ke mana transkrip suara dikirim: kanal / dm / off."""
        from . import config as conf

        p = self.cfg["prefix"]
        mode = self.ALIAS_LOG.get(mode.strip().lower(), mode.strip().lower())
        if not mode:
            await ctx.send(
                "Transkrip suara sekarang: **%s**\n"
                "`%slog kanal` kelihatan semua orang - "
                "`%slog dm` cuma ke DM yang ngomong - "
                "`%slog off` nggak dicatat sama sekali"
                % (self.mode_log(), p, p, p))
            return
        if mode not in self.MODE_LOG:
            await ctx.send("Yang ada: %s" % ", ".join(self.MODE_LOG))
            return
        self.cfg["transcript_privacy"] = mode
        # Yang DM-nya dulu ketutup boleh dicoba lagi setelah setelannya diubah.
        self._dm_gagal.clear()
        conf.save(self.cfg)
        pesan = {
            "kanal": "Transkripnya balik kelihatan di kanal.",
            "dm": "Mulai sekarang transkrip suara cuma masuk ke DM yang ngomong. "
                  "Pastikan DM dari anggota server nggak kamu tutup.",
            "off": "Transkrip suara nggak aku catat lagi. Suaranya tetap jalan.",
        }
        await ctx.send(pesan[mode])

    @commands.command(name="voice", aliases=["suara"])
    async def voice(self, ctx: commands.Context, *, arg: str = "") -> None:
        """Lihat daftar suara, atau ganti: !voice 5 / !voice <reference_id>"""
        daftar = self.cfg["fish"].get("voices") or []
        arg = arg.strip()
        sekarang = self.cfg["fish"].get("voice_id") or ""

        # "!voice" saja sudah menampilkan daftarnya, tapi "!voice list" itu
        # tebakan pertama siapa pun -- dan tanpa ini dia jatuh ke jalur
        # reference_id lalu ditolak katalog dengan pesan yang membingungkan.
        if arg.lower() in ("list", "daftar", "ls", "?"):
            arg = ""

        if not arg:
            if not daftar:
                await ctx.send(
                    "Belum ada suara tersimpan.\n"
                    "Tambah: `%svoice tambah <nama> <reference_id>`\n"
                    "Atau pakai langsung: `%svoice <reference_id>`"
                    % (self.cfg["prefix"], self.cfg["prefix"]))
                return
            baris = []
            for i, v in enumerate(daftar, 1):
                tanda = " **<- sekarang**" if v.get("id") == sekarang else ""
                baris.append("`%2d.` %s%s" % (i, v.get("name", "?"), tanda))
            await ctx.send("**Suara yang tersedia**\n" + "\n".join(baris) +
                           "\n\nGanti: `%svoice <nomor>`" % self.cfg["prefix"])
            return

        if arg.lower().startswith("tambah "):
            sisa = arg[7:].strip().rsplit(" ", 1)
            if len(sisa) != 2:
                await ctx.send("Formatnya: `%svoice tambah <nama> <reference_id>`"
                               % self.cfg["prefix"])
                return
            nama, ident = sisa[0].strip(), sisa[1].strip()
            asli = await asyncio.to_thread(tts.voice_name, self.cfg, ident)
            if asli is None:
                await ctx.send("Id itu nggak ada di katalog Fish Audio.")
                return
            daftar.append({"name": nama or asli, "id": ident})
            self.cfg["fish"]["voices"] = daftar
            self._simpan()
            await ctx.send("Ditambahin: **%s** (nomor %d)" % (nama or asli, len(daftar)))
            return

        if arg.isdigit():
            n = int(arg)
            if not 1 <= n <= len(daftar):
                await ctx.send("Nomornya 1 sampai %d." % len(daftar))
                return
            pilih = daftar[n - 1]
            self.cfg["fish"]["voice_id"] = pilih["id"]
            self._simpan()
            await ctx.send("Suara diganti ke **%s**." % pilih.get("name", "?"))
            return

        # dianggap reference_id mentah -- dicek dulu ke katalog biar salah ketik
        # ketahuan sekarang, bukan waktu dia gagal bersuara di tengah obrolan
        nama = await asyncio.to_thread(tts.voice_name, self.cfg, arg)
        if nama is None:
            await ctx.send(
                "`%s` bukan reference_id yang dikenal katalog Fish Audio.\n"
                "`%svoice list` buat lihat pilihan, `%svoice 5` buat ganti."
                % (arg[:40], self.cfg["prefix"], self.cfg["prefix"]))
            return
        self.cfg["fish"]["voice_id"] = arg
        self._simpan()
        await ctx.send("Suara diganti ke **%s**." % nama)

    def _simpan(self) -> None:
        from . import config as conf

        conf.save(self.cfg)

    @commands.command(name="channel", aliases=["kanal"])
    async def channel(self, ctx: commands.Context, *, arg: str = "") -> None:
        """Atur kanal tempat dia boleh ngobrol. `!channel here` paling gampang."""
        arg = arg.strip()
        if not arg:
            tanda = self.cfg.get("chat_channel") or ""
            if not tanda:
                await ctx.send("Sekarang dia bales di **kanal mana pun**.\n"
                               "Kunci ke satu kanal: `%schannel here`" % self.cfg["prefix"])
                return
            ada = self.kanal_obrolan(ctx.guild)
            await ctx.send(
                "Kanal obrolan: `%s` %s\n`%schannel here` buat pindah ke sini, "
                "`%schannel off` buat bebas di mana saja."
                % (tanda, "(ketemu: %s)" % ada.mention if ada else "**-- kanalnya nggak ada**",
                   self.cfg["prefix"], self.cfg["prefix"]))
            return

        if arg.lower() in ("off", "mati", "semua", "any"):
            self.cfg["chat_channel"] = ""
            self._simpan()
            await ctx.send("Oke, aku bales di kanal mana pun sekarang.")
            return

        if arg.lower() in ("here", "sini", "ini"):
            target = ctx.channel
        else:
            target = self._cari_kanal(ctx.guild, arg, suara=False)
            if target is None:
                await ctx.send("Kanal `%s` nggak ketemu di server ini." % arg)
                return

        # Disimpan sebagai id, bukan nama: nama kanal bisa diganti kapan saja,
        # dan kalau itu terjadi bot-nya diam tanpa sebab yang kelihatan.
        self.cfg["chat_channel"] = str(target.id)
        self.text_channels[ctx.guild.id] = target
        self._simpan()
        await ctx.send("Mulai sekarang aku cuma ngobrol di %s." % target.mention)

    @commands.command(name="vc")
    async def vc(self, ctx: commands.Context, *, arg: str = "") -> None:
        """Atur kanal suara yang ditongkrongi 24 jam."""
        arg = arg.strip()
        if arg.lower() in ("off", "mati"):
            self.cfg["auto_join"] = False
            self._jangan_balik = True
            self._simpan()
            await ctx.send("Nongkrong 24 jam dimatiin. Pakai `%sjoin` kalau perlu."
                           % self.cfg["prefix"])
            return

        if not arg or arg.lower() in ("here", "sini", "ini"):
            if ctx.author.voice is None or ctx.author.voice.channel is None:
                sekarang = self.cfg.get("voice_channel") or "(belum diatur)"
                await ctx.send("Kanal suara sekarang: `%s`\nMasuk ke kanal suara "
                               "dulu, terus `%svc` buat mindahin ke situ."
                               % (sekarang, self.cfg["prefix"]))
                return
            target = ctx.author.voice.channel
        else:
            target = self._cari_kanal(ctx.guild, arg, suara=True)
            if target is None:
                await ctx.send("Kanal suara `%s` nggak ketemu." % arg)
                return

        self.cfg["voice_channel"] = str(target.id)
        self.cfg["auto_join"] = True
        self._jangan_balik = False
        self._simpan()
        await ctx.send("Aku bakal nongkrong di **%s** terus." % target.name)
        await self._pastikan_masuk()

    @commands.command(name="status")
    async def status(self, ctx: commands.Context) -> None:
        """Keadaan bot: provider, suara, level, kredit Fish."""
        s = self.session(ctx.guild.id)
        prov = self.provider()
        credit = await asyncio.to_thread(tts.credit, self.cfg)
        await ctx.send(
            "Level **%s** - provider **%s** - suara `%s`\n"
            "STT `%s` - kredit Fish: %s\n"
            "Rotasi model: %s\n"
            "Transkrip **%s** - ingatan per orang, lupa setelah %s menit nganggur"
            % (s.level,
               (prov or {}).get("name", "-"),
               self.cfg["fish"].get("voice_id") or "(bawaan)",
               self.cfg["stt"].get("model"),
               credit if credit is not None else "tidak terbaca",
               self.ringkas_rotasi(),
               self.mode_log(),
               self.cfg["kaiwa"].get("memory_idle_minutes"))
        )

    @commands.command(name="bantuan")
    async def bantuan(self, ctx: commands.Context) -> None:
        p = self.cfg["prefix"]
        await ctx.send(
            "\n".join([
                "`%sjoin` masuk ke kanal suaramu, `%sleave` / `%sdisconnect` keluar" % (p, p, p),
                "`%slevel n3` kunci level JLPT" % p,
                "`%syomi` furigana kalimat terakhirku" % p,
                "`%sarti` terjemahannya" % p,
                "`%sulang` bacakan lagi" % p,
                "`%svoice` daftar suara, `%svoice 5` ganti" % (p, p),
                "`%schannel here` kunci ke kanal ini (di situ nggak usah di-tag)" % p,
                "`%schannel off` bebas di mana saja (tapi harus di-tag)" % p,
                "`%svc` tongkrongi kanal suaramu 24 jam, `%svc off` matiin" % (p, p),
                "`%slog dm` transkrip suara cuma ke DM-mu, `%slog kanal` balikin" % (p, p),
                "`%sreset` lupakan obrolan kita, `%sreset semua` buat semua orang" % (p, p),
                "`%sstatus` keadaan bot" % p,
            ])
        )

    # ------------------------------------------------------------ jaga suara
    async def mulai_penjaga(self) -> None:
        if self._penjaga is None or self._penjaga.done():
            self._penjaga = asyncio.create_task(self._jaga_suara())

    async def _jaga_suara(self) -> None:
        """Masuk sendiri dan balik lagi kalau terputus.

        Discord memutus sambungan suara sendiri sesekali -- pindah wilayah,
        server suara direstart, jaringan tersendat. Tanpa ini, bot yang
        seharusnya siaga terus diam-diam jadi tidak ada sampai ada yang sadar.
        """
        await self.bot.wait_until_ready()
        selang = max(10, int(self.cfg.get("rejoin_seconds") or 30))
        try:
            while True:
                try:
                    if self.cfg.get("auto_join", True) and not self._jangan_balik:
                        await self._pastikan_masuk()
                except Exception as exc:
                    log.debug("penjaga suara: %s", exc)
                await asyncio.sleep(selang)
        except asyncio.CancelledError:
            pass

    @staticmethod
    def _masih_dengar(vc) -> bool:
        try:
            return bool(vc.is_listening())
        except Exception:
            return True          # nggak bisa dipastikan -- jangan pasang ulang terus

    async def _pastikan_masuk(self) -> None:
        for guild in self.bot.guilds:
            ids = self.cfg.get("allowed_guilds") or []
            if ids and guild.id not in ids:
                continue
            tujuan = self._cari_kanal(guild, self.cfg.get("voice_channel", ""), suara=True)
            if tujuan is None:
                continue
            vc = guild.voice_client
            sebab = ""
            if vc is None or not vc.is_connected():
                vc = await tujuan.connect(cls=voice_recv.VoiceRecvClient)
                sebab = "masuk"
            elif vc.channel.id != tujuan.id:
                await vc.move_to(tujuan)
                sebab = "pindah"
            elif not self._masih_dengar(vc):
                # Duduk di kanal bukan berarti mendengar. PacketRouter memanggil
                # stop_listening() di blok finally-nya kalau ada error yang
                # lolos, dan sesudah itu bot tetap kelihatan hadir sementara
                # tidak ada satu pun paket yang sampai. Ini yang menemukannya.
                sebab = "dipasang ulang (tadinya tuli)"
            else:
                continue

            sink = KaiwaSink(self, guild.id)
            self.sinks[guild.id] = sink
            guild.voice_client.listen(sink)
            self.text_channels[guild.id] = (
                self.kanal_obrolan(guild) or self.text_channels.get(guild.id))
            if self._watcher is None or self._watcher.done():
                self._watcher = asyncio.create_task(self._watch())
            log.info("kanal suara %s di %s: %s", tujuan.name, guild.name, sebab)

    # ------------------------------------------------------------ giliran
    async def _watch(self) -> None:
        """Satu jam untuk semua server: cek siapa yang sudah selesai bicara."""
        try:
            while True:
                await asyncio.sleep(TICK)
                max_ms = int(self.cfg["stt"].get("max_speech_ms") or 30000)
                max_bytes = int(max_ms / 1000 * audio.IN_RATE) * audio.FRAME_BYTES
                for gid, sink in list(self.sinks.items()):
                    for uid, who, raw in sink.take_finished(
                            int(self.cfg["stt"].get("silence_ms") or 900), max_bytes):
                        asyncio.create_task(self._handle(gid, uid, who, raw))
        except asyncio.CancelledError:
            pass

    async def _handle(self, guild_id: int, uid: int, who: str,
                      raw: bytes) -> None:
        cfg = self.cfg
        ms = audio.duration_ms(raw)
        if ms < int(cfg["stt"].get("min_speech_ms") or 400):
            return
        limit = int(cfg["stt"].get("max_speech_ms") or 30000)
        if ms > limit:
            raw = raw[: int(limit / 1000 * audio.IN_RATE) * audio.FRAME_BYTES]

        keras = audio.rms(raw)
        st = dave.ambil_stats()
        log.info("giliran dari %s: %d ms, rms %.4f | dave ok=%d gagal=%d "
                 "passthrough=%d tanpa_uid=%d opus_gagal=%d",
                 who, ms, keras, st["ok"], st["gagal"], st["passthrough"],
                 st["tanpa_uid"], st["opus_gagal"])
        if keras < 0.004:
            # Napas, klik mouse, kipas. Tidak usah dibawa ke penyedia.
            log.info("  dilewati: terlalu pelan")
            return

        s = self.session(guild_id)
        if s.busy and not self.giliran_nyangkut(s):
            # Satu giliran pada satu waktu: dua permintaan bersamaan berarti dua
            # klip suara yang saling tindih di kanal yang sama. Tapi ini dicatat
            # -- ucapan yang dibuang diam-diam tidak bisa dibedakan dari bot
            # yang rusak, oleh siapa pun, termasuk yang menulis kodenya.
            log.info("  dilewati: giliran sebelumnya belum selesai (%.0f detik)",
                     time.monotonic() - (s.busy_sejak or time.monotonic()))
            return
        s.busy = True
        s.busy_sejak = time.monotonic()
        try:
            try:
                fmt = str(cfg["stt"].get("upload_format") or "wav")
                clip = await asyncio.to_thread(audio.to_upload, raw, fmt)
                if cfg["stt"].get("save_clips"):
                    # Satu-satunya cara tahu apakah yang salah audionya atau
                    # transkripsinya: dengarkan sendiri apa yang dikirim.
                    try:
                        nama = "/tmp/ruri-klip-%s.%s" % (time.strftime("%H%M%S"), fmt)
                        with open(nama, "wb") as fh:
                            fh.write(clip)
                        log.info("  klip disimpan: %s (%d KB)", nama, len(clip) // 1024)
                    except Exception:
                        pass
                heard = await asyncio.to_thread(stt.transcribe, cfg, clip, fmt)
            except Exception as exc:
                await self._maaf(guild_id, "ん、聞こえなかった。",
                                 "suaranya nggak kebaca, coba lagi", exc,
                                 uid=uid, pribadi=True)
                return
            if not heard:
                log.info("  tidak ada ucapan yang dikenali")
                return
            log.info("  terdengar: %s", heard)

            await self._respond(guild_id, heard, speak=True, siapa=who, uid=uid)
        finally:
            s.busy = False

    async def _respond(self, key: int, said: str, speak: bool,
                       siapa: str | None = None,
                       uid: int | None = None) -> None:
        """Satu giliran percakapan, dari mana pun asalnya.

        Jalur suara dan jalur teks berbagi ini: yang membedakan cuma dari mana
        kalimatnya datang dan apakah jawabannya perlu dibacakan.
        """
        cfg = self.cfg
        s = self.session(key)
        # `siapa` cuma diisi jalur suara, dan justru giliran suara yang tidak
        # pernah dipilih untuk diterbitkan. Lihat tujuan().
        pribadi = siapa is not None
        rantai = self.rantai_provider()
        if not rantai:
            await self._maaf(key, "……",
                             "belum ada provider model bahasa di config",
                             uid=uid, pribadi=pribadi)
            return

        s.add_user(uid, said)
        try:
            reply, dipakai = await asyncio.to_thread(
                llm.complete_any, cfg, rantai, llm.system_prompt(cfg, s.level),
                s.history(cfg, uid), 0, llm.balasan_jepang,
                float(cfg["llm"].get("turn_deadline_seconds") or 0))
        except llm.SemuaGagal as exc:
            if exc.kena_batas:
                await self._maaf(key, "ちょっと待って。",
                                 "lagi kena batas pemakaian — coba lagi sebentar",
                                 exc, uid=uid, pribadi=pribadi)
            else:
                await self._maaf(key, "ごめん、今ちょっと無理みたい。",
                                 "model bahasanya lagi nggak bisa dihubungi", exc,
                                 uid=uid, pribadi=pribadi)
            s.buang_terakhir(uid)
            return
        if dipakai is not rantai[0]:
            # Dengan rotasi model, "provider pertama" bukan lagi yang seharusnya
            # menjawab -- yang berguna dicatat itu siapa yang benar-benar bicara.
            log.info("dijawab oleh %s", llm.kunci(dipakai))

        spoken, fix = llm.split_reply(reply)
        if not spoken:
            log.warning("  balasan kosong; mentah=%d karakter", len(reply))
            return
        log.info("  balas: %s", spoken)
        s.add_bot(uid, spoken)
        # Di obrolan teks, pesannya sudah kelihatan tepat di atas -- mengutipnya
        # lagi cuma jadi gema.
        await self.kirim_giliran(key, siapa, said if siapa else None,
                                 spoken, fix, s.level, uid=uid, pribadi=pribadi)

        guild = self.bot.get_guild(key)
        if speak and guild is not None:
            await self._speak(guild, spoken)

    # ------------------------------------------------------------ jalur teks
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Kaiwa lewat ketikan: sebut namanya, dia jawab.

        Berguna sendiri, bukan cuma cadangan kalau suaranya rewel -- kadang kamu
        memang lagi tidak bisa bersuara.
        """
        if message.author.bot or not message.content:
            return
        if message.content.startswith(self.cfg["prefix"]):
            return
        if not self.boleh_ngobrol(message.channel):
            return
        # Di kanal khususnya, sebutan tidak diperlukan.
        if not (self.kanal_khusus(message.channel)
                or self.bot.user in message.mentions):
            return

        teks = re.sub(r"<@!?%d>" % self.bot.user.id, "", message.content).strip()
        if not teks:
            if self.bot.user in message.mentions:
                await message.channel.send("Ada apa? Sapa aku pakai bahasa Jepang.")
            return

        key = message.guild.id if message.guild else message.channel.id
        self.text_channels[key] = message.channel
        s = self.session(key)
        if s.busy and not self.giliran_nyangkut(s):
            return
        s.busy = True
        s.busy_sejak = time.monotonic()
        try:
            async with message.channel.typing():
                # Dibacakan hanya kalau dia memang sedang di kanal suara bareng
                # kamu; kalau tidak, klip suaranya tidak akan terdengar siapa pun.
                di_vc = bool(message.guild and message.guild.voice_client
                             and message.guild.voice_client.is_connected())
                await self._respond(key, teks, speak=di_vc,
                                    uid=message.author.id)
        finally:
            s.busy = False

    # ------------------------------------------------------------ suara
    async def _speak(self, guild: discord.Guild, text: str) -> None:
        vc = guild.voice_client
        if vc is None or not vc.is_connected():
            return
        try:
            data = await asyncio.to_thread(tts.speak, self.cfg, text)
        except tts.TTSError as exc:
            # Teksnya sudah sampai; yang hilang cuma suaranya.
            await self._maaf(guild.id, "", "suaranya lagi ngadat, teksnya aja dulu",
                             exc)
            return

        suffix = "." + str(self.cfg["fish"].get("format") or "mp3")
        fd, path = tempfile.mkstemp(prefix="ruri-", suffix=suffix)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)

        # Klip sebelumnya dibiarkan selesai, tidak dipotong: memotongnya di
        # tengah kalimat terdengar seperti bot yang rusak.
        for _ in range(100):
            if not vc.is_playing():
                break
            await asyncio.sleep(0.1)

        def done(err) -> None:
            try:
                os.unlink(path)
            except OSError:
                pass
            if err:
                log.warning("pemutaran gagal: %s", err)

        try:
            vc.play(discord.FFmpegPCMAudio(path), after=done)
        except Exception as exc:
            done(None)
            await self._maaf(guild.id, "", "klipnya nggak bisa diputar", exc)


def build(cfg: dict) -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.voice_states = True
    intents.guilds = True
    # intents.members sengaja tidak diminta: satu-satunya keperluannya cuma
    # nama penutur, dan itu sudah ikut di objek yang datang bareng paket suara.

    bot = commands.Bot(command_prefix=cfg["prefix"], intents=intents,
                       help_command=None)

    @bot.event
    async def on_ready() -> None:
        log.info("masuk sebagai %s", bot.user)
        cog = bot.get_cog("Kaiwa")
        if cog is None:
            return
        # Kanal yang salah ketik atau belum dibuat bikin bot diam tanpa sebab
        # yang kelihatan. Lebih baik ketahuan sekarang, di baris pertama log.
        for guild in bot.guilds:
            if cfg.get("chat_channel") and cog.kanal_obrolan(guild) is None:
                log.warning("kanal teks %r nggak ada di %s -- dia nggak akan "
                            "bales sapaan sampai kanalnya dibikin",
                            cfg["chat_channel"], guild.name)
            if cfg.get("voice_channel") and cog._cari_kanal(
                    guild, cfg["voice_channel"], suara=True) is None:
                log.warning("kanal suara %r nggak ada di %s",
                            cfg["voice_channel"], guild.name)
        await cog.mulai_penjaga()

    @bot.check
    async def allowed(ctx: commands.Context) -> bool:
        ids = cfg.get("allowed_guilds") or []
        return not ids or (ctx.guild is not None and ctx.guild.id in ids)

    async def setup() -> None:
        await bot.add_cog(Kaiwa(bot, cfg))

    bot.setup_hook = setup
    return bot
