"""Bentuk pesan protokol live.

Bagian yang paling mungkin salah diam-diam: setup yang meleset strukturnya
ditolak server tanpa pesan berguna, dan parser yang memakai elif kehilangan
salah satu field ketika satu pesan membawa beberapa sekaligus.
"""

import base64
import unittest

from ruri.live import protocol as P


class Setup(unittest.TestCase):
    def setUp(self):
        self.s = P.build_setup("SYSTEM", voice="Kore", thinking="low")["setup"]

    def test_response_modalitas_bersarang_di_generation_config(self):
        """Bukan di root setup: quickstart yang flat sudah ketinggalan zaman."""
        gc = self.s["generationConfig"]
        self.assertEqual(gc["responseModalities"], ["AUDIO"])
        self.assertEqual(
            gc["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"],
            "Kore")
        self.assertEqual(gc["thinkingConfig"]["thinkingLevel"], "LOW")

    def test_model_format_resource(self):
        self.assertEqual(self.s["model"], "models/" + P.MODEL)

    def test_transkrip_harus_diminta_eksplisit(self):
        with_on = P.build_setup("x")["setup"]
        self.assertIn("inputAudioTranscription", with_on)
        self.assertIn("outputAudioTranscription", with_on)
        off = P.build_setup("x", transcripts=False)["setup"]
        self.assertNotIn("inputAudioTranscription", off)

    def test_vad_tersambung(self):
        aad = self.s["realtimeInputConfig"]["automaticActivityDetection"]
        self.assertFalse(aad["disabled"])
        self.assertEqual(aad["silenceDurationMs"], 2000)
        self.assertEqual(aad["prefixPaddingMs"], 500)

    def test_kompresi_konteks_selalu_ada(self):
        """Tanpa ini sesi mati paksa di 15 menit."""
        cw = self.s["contextWindowCompression"]
        self.assertEqual(cw["triggerTokens"], P.TRIGGER_TOKENS)
        self.assertEqual(cw["slidingWindow"]["targetTokens"], P.TARGET_TOKENS)

    def test_resumption_selalu_dihidupkan(self):
        """Tanpa field ini server tak pernah mengirim handle, dan goAway akan
        memulai sesi BARU -- konteks hilang."""
        baru = P.build_setup("x")["setup"]
        self.assertIn("sessionResumption", baru)
        self.assertNotIn("handle", baru["sessionResumption"])
        resume = P.build_setup("x", resume_handle="h42")["setup"]
        self.assertEqual(resume["sessionResumption"], {"handle": "h42"})

    def test_thinking_ngawur_jatuh_ke_minimal(self):
        s = P.build_setup("x", thinking="sekuat-tenaga")
        self.assertEqual(s["setup"]["generationConfig"]
                         ["thinkingConfig"]["thinkingLevel"], "MINIMAL")

    def test_siap_di_json(self):
        import json
        json.dumps(P.build_setup("halo\n\t\"kutip\""))


class PesanKeluar(unittest.TestCase):
    def test_audio_base64_siap_kirim(self):
        msg = P.audio_message(b"\x01\x02\x03")
        ri = msg["realtimeInput"]
        self.assertEqual(ri["audio"]["mimeType"], P.MIME_AUDIO)
        self.assertEqual(base64.b64decode(ri["audio"]["data"]), b"\x01\x02\x03")

    def test_teks_dan_stream_end(self):
        self.assertEqual(P.text_message("hai"),
                         {"realtimeInput": {"text": "hai"}})
        self.assertEqual(P.stream_end_message(),
                         {"realtimeInput": {"audioStreamEnd": True}})


class PesanMasuk(unittest.TestCase):
    def test_satu_pesan_beberapa_field_sekaligus(self):
        """Ini alasan utama file tes ini ada. Chaining elif akan membuat salah
        satu dari ketiganya hilang tanpa suara."""
        audio_b64 = base64.b64encode(b"PCM").decode()
        ev = P.parse_server({
            "serverContent": {
                "modelTurn": {"parts": [{"inlineData": {"data": audio_b64}}]},
                "outputTranscription": {"text": "こんにちは", "finished": True},
                "turnComplete": True,
            },
            "sessionResumptionUpdate": {"resumable": True, "newHandle": "abc"},
        })
        self.assertIn(("audio", b"PCM"), ev)
        self.assertIn(("output_text", "こんにちは"), ev)
        self.assertIn(("output_done", None), ev)  # selesai, bukan parsial
        self.assertIn(("turn_complete", None), ev)
        self.assertIn(("handle", "abc"), ev)

    def test_setup_complete(self):
        self.assertEqual(P.parse_server({"setupComplete": {}}),
                         [("setup_complete", None)])

    def test_interrupt_dan_generation_complete(self):
        ev = P.parse_server({"serverContent": {
            "interrupted": True, "generationComplete": True}})
        self.assertIn(("interrupted", None), ev)
        self.assertIn(("generation_complete", None), ev)

    def test_go_away_dengan_sisa_waktu(self):
        self.assertEqual(P.parse_server({"goAway": {"timeLeft": "30s"}}),
                         [("go_away", "30s")])

    def test_handle_hanya_kalau_resumable(self):
        ev = P.parse_server({"sessionResumptionUpdate":
                             {"resumable": False, "newHandle": "x"}})
        self.assertEqual(ev, [])

    def test_transkrip_input_parsial_lalu_selesai(self):
        ev = P.parse_server({"serverContent": {
            "inputTranscription": {"text": "やっ"}}})
        self.assertEqual(ev, [("input_text", "やっ")])
        ev2 = P.parse_server({"serverContent": {
            "inputTranscription": {"text": "やあ", "finished": True}}})
        self.assertIn(("input_text", "やあ"), ev2)
        self.assertIn(("input_done", None), ev2)

    def test_pesan_kosong_dan_asing(self):
        self.assertEqual(P.parse_server({}), [])
        self.assertEqual(P.parse_server(None), [])
        self.assertEqual(P.parse_server({"usageMetadata": {}}), [])

    def test_tool_call_diakui_meski_tidak_dipakai(self):
        ev = P.parse_server({"toolCall": {"functionCalls": []}})
        self.assertEqual([e[0] for e in ev], ["tool_call"])


class Konstanta(unittest.TestCase):
    def test_suara_resmi_30_dan_unik(self):
        self.assertEqual(len(P.VOICES), 30)
        self.assertEqual(len(set(P.VOICES)), 30)
        for nama in ("Zephyr", "Kore", "Iapetus", "Sulafat"):
            self.assertIn(nama, P.VOICES)

    def test_default_bawaan_ada_di_daftar(self):
        self.assertIn(P.build_setup("x")["setup"]["generationConfig"]
                      ["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]
                      ["voiceName"], P.VOICES)


if __name__ == "__main__":
    unittest.main()
