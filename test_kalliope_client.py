"""Test senza rete: python -m unittest test_kalliope_client"""
import unittest

import kalliope_client as kc


class Auth(unittest.TestCase):
    # Vettore di prova del manuale ufficiale «KalliopePBX V4 - Documentazione API per CDR», par. 1.3
    def test_vettore_del_manuale(self):
        dp = kc.digest_password("admin", "b5a8fdcf2f8d5acdad33c4a072a97d7a")
        self.assertEqual(dp, "dd7b0be7fa37d6cbaf0b842bf7532f229cb79ab8d54d509c2aa7eea27a53cd5e")
        h = kc.build_auth_header("admin", "default", dp,
                                 nonce="bfb79078ff44c35714af28b7412a702b", created="2016-04-29T15:48:26Z")
        self.assertIn('Digest="+PJg7Tb3v98XnL6iJVv+v5hwhYjdzQ2tIWxvJB2cE40="', h)
        self.assertTrue(h.startswith('RestApiUsernameToken Username="admin", Domain="default"'))

    def test_header_sempre_diverso(self):
        self.assertNotEqual(kc.build_auth_header("u", "d", "x"), kc.build_auth_header("u", "d", "x"))


class Esito(unittest.TestCase):
    def test_risposta_operatore(self):
        c = {"status": "OK", "detail": [{"dest_type": "queue", "userEvents": [{"userExten": "201", "answerTime": "x"}]}]}
        self.assertEqual(kc.classify_call(c), ("answered", None))

    def test_cellulare_inoltrato(self):
        c = {"status": "OK", "detail": [{"dest_type": "callg", "userEvents": []}, {"dest_type": "obl", "answer_time": "x"}]}
        self.assertEqual(kc.classify_call(c), ("answered", "obl"))

    def test_segreteria(self):
        c = {"status": "OK", "detail": [{"dest_type": "ivr"}, {"dest_type": "voicemail"}]}
        self.assertEqual(kc.classify_call(c)[0], "voicemail")

    def test_solo_messaggio(self):
        c = {"status": "OK", "detail": [{"dest_type": "checktime"}]}
        self.assertEqual(kc.classify_call(c)[0], "message")

    def test_persa_in_coda(self):
        c = {"status": "CANCELED", "detail": [{"dest_type": "ivr"}, {"dest_type": "queue", "userEvents": [{"userExten": "201"}]}]}
        self.assertEqual(kc.classify_call(c), ("missed", "queue"))


class Scrittura(unittest.TestCase):
    def test_periodo(self):
        letto = {"id": 9, "priority": 0, "enabled": True, "hourFrom": "9", "minuteFrom": "0", "hourTo": "18", "minuteTo": "30",
                 "weekDayFrom": "mon", "weekDayTo": "fri", "monthDayFrom": "*", "monthDayTo": "*", "month": "*",
                 "overrideFailoverEnabled": True, "failover": {"id": 4, "destinationType": 5, "callgroupId": 10}}
        w = kc.writable_period(letto)
        self.assertEqual((w["timeFrom"], w["timeTo"]), ("09:00", "18:30"))
        self.assertNotIn("id", w)
        self.assertEqual(w["failover"], {"destinationType": 5, "callgroupId": 10})

    def test_tutto_il_giorno(self):
        letto = {"priority": 1, "hourFrom": "*", "minuteFrom": "*", "hourTo": "*", "minuteTo": "*", "weekDayFrom": "*",
                 "weekDayTo": "*", "monthDayFrom": "25", "monthDayTo": "26", "month": "dec"}
        w = kc.writable_period(letto)
        self.assertEqual((w["timeFrom"], w["timeTo"]), ("00:00", "23:59"))


class Riservatezza(unittest.TestCase):
    def test_campi_riservati(self):
        r = kc._redact({"extension": "201", "servicesPin": "1234", "accounts": [{"username": "a", "password": "p"}]})
        self.assertEqual(r["servicesPin"], "***")
        self.assertEqual(r["accounts"][0]["password"], "***")
        self.assertEqual(r["extension"], "201")

    def test_maschera(self):
        self.assertEqual(kc.mask_number("0331635123"), "0331635xxx")
        self.assertEqual(kc.mask_number("201"), "201")


if __name__ == "__main__":
    unittest.main()
