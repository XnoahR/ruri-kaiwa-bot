"""ganti_telinga: perpindahan sink yang bebas balapan reader voice_recv.

Murni asyncio (tanpa discord) jadi bisa dites di mesin mana pun. Yang diuji
di sini justru JALUR SIBUK: listener lama yang menolak ("Already receiving")
sedang menyelesaikan pembongkarannya -- di kode lama, tolakan pertama itu
langsung berarti kegagalan permanen.
"""

import unittest

from ruri.live.swap import ganti_telinga


class VC:
    def __init__(self, listening=False, tolak=0, galat_asli=None):
        self.listening = listening
        self.tolak = tolak                 # jumlah penolakan "Already receiving"
        self.galat_asli = galat_asli       # exception yang bukan balapan
        self.n_listen = 0
        self.n_stop = 0

    def is_listening(self):
        return self.listening

    def stop_listening(self):
        self.n_stop += 1
        self.listening = False

    def listen(self, sink):
        self.n_listen += 1
        if self.galat_asli:
            raise self.galat_asli
        if self.tolak:
            self.tolak -= 1
            raise RuntimeError("Already receiving audio.")
        self.listening = True


class Swap(unittest.IsolatedAsyncioTestCase):
    async def test_lepas_lalu_pasang(self):
        vc = VC(listening=True)
        self.assertTrue(await ganti_telinga(vc, object(), jeda=0.01))
        self.assertEqual(vc.n_stop, 1)
        self.assertEqual(vc.n_listen, 1)
        self.assertTrue(vc.listening)

    async def test_bertahan_saat_pembaca_lama_belum_rampung(self):
        """Skenyo bug produksi: stop sudah, tapi router lama menolak listen
        pertama. Harus dicoba ulang, bukan dianggap fatal."""
        vc = VC(listening=True, tolak=1)
        self.assertTrue(await ganti_telinga(vc, object(), jeda=0.01))
        self.assertEqual(vc.n_listen, 2)      # ditolak sekali, sukses sekali
        self.assertTrue(vc.listening)

    async def test_tanpa_pendengar_langsung_pasang(self):
        vc = VC()
        self.assertTrue(await ganti_telinga(vc, object(), jeda=0.01))
        self.assertEqual(vc.n_stop, 0)        # tidak ada yang perlu distop

    async def test_galat_asli_diserahkan_segera(self):
        """TypeError (sink salah) tidak boleh dicoba-coba 10x."""
        vc = VC(galat_asli=TypeError("sink must be an AudioSink"))
        self.assertFalse(await ganti_telinga(vc, object(), jeda=0.01))
        self.assertEqual(vc.n_listen, 1)

    async def test_sibuk_terus_menyerah_rapi(self):
        vc = VC(listening=False, tolak=99)
        self.assertFalse(await ganti_telinga(vc, object(), jeda=0.01, maks=4))
        self.assertEqual(vc.n_listen, 4)


if __name__ == "__main__":
    unittest.main()
