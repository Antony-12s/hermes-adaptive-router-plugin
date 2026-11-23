import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from dashboard import ladder, router, router_engine
from training import seed, train


class FollowupReplyTests(unittest.TestCase):
    def test_bare_choices_and_go_aheads_are_followups(self):
        for text in ("1", "3", "C\n1", "ข้อ 2", "เอาข้อ 2", "1, 3", "1 และ 3", "ok", "ต่อเลย", "ทำหมดเลย เคมะ",
                     "yes do it", "เเก้เลย", "สาดไปครับสมาชิก", "ทั้งสองอย่าง", "go ahead", "the second one",
                     "pick option 3", "take the first one"):
            self.assertTrue(router.is_followup(text), text)

    def test_status_questions_thanks_and_real_requests_are_not(self):
        for text in ("เสร็จยัง", "ได้ยินไหม", "ขอบคุณ", "thanks", "ถึงไหนแล้ว", "สลับที่กัน", "ack", "555", "4444",
                     "ok ลบไฟล์ build ทิ้งด้วย", "1 เเค่ต้นเเบบช่ายมะไปลงเครื่องอื่นไม่ต้องทำช่ายมะ", ""):
            self.assertFalse(router.is_followup(text), text)


class PrototypeTests(unittest.TestCase):
    def test_a_short_command_finds_its_own_group_inside_a_mixed_tier(self):
        learner = router.Learner()
        learner.TEMPERATURE = ladder.LEARNER_TEMPERATURE
        for text in ("ขอบคุณ", "ว่าไง", "สวัสดี", "ฝันดีนะ", "เหนื่อยจัง", "ได้ยินไหม", "ดีจัง", "เยี่ยม"):
            learner.learn(text, "0", group="chitchat")
        for text in ("สลับที่กัน", "ย้ายปุ่มไปขวา", "ลบปุ่มนี้ออก", "สลับสองการ์ดนี้"):
            learner.learn(text, "2", group="followup_edit")
        for text in ("อธิบายความต่างระหว่าง TCP กับ UDP ว่าใช้ตอนไหนพร้อมตัวอย่างที่ใช้จริงในระบบเครือข่าย",
                     "เขียนอีเมลถึงลูกค้าแจ้งว่าการส่งของจะล่าช้าสองวันพร้อมขอโทษและเสนอส่วนลด",
                     "เขียน SQL หายอดขายรวมรายเดือนของปีที่แล้วแยกตามภูมิภาค"):
            learner.learn(text, "2", group="explain_concept")
        probs, _ = learner.predict("สลับปุ่มที่กัน", list(ladder.LABELS))
        self.assertEqual(max(probs, key=probs.get), "2")
        self.assertEqual(set(learner.label_scores("x", ["0", "2", "5"])), {"0", "2", "5"})


class FollowupEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = router_engine.Engine(Path(self.tmp.name), lambda: [], lambda key, base_url: {"ok": False},
                                           lambda *a: ({}, 0))

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_choice_reply_keeps_the_previous_tier_of_its_own_session(self):
        first = self.engine.decide("แก้บั๊กปุ่มบันทึกส่งคำขอสองครั้ง แล้วเขียนเทสต์", session="s1", use_jev=False)
        reply = self.engine.decide("1", session="s1", use_jev=False)
        self.assertEqual(reply["by"], "followup")
        self.assertEqual(reply["tier"], first["tier"])
        self.assertNotEqual(self.engine.decide("1", session="s2", use_jev=False)["by"], "followup")

    def test_an_old_turn_is_not_followed(self):
        self.engine.decide("แก้บั๊กปุ่มบันทึกส่งคำขอสองครั้ง แล้วเขียนเทสต์", session="s1", use_jev=False)
        with closing(sqlite3.connect(Path(self.tmp.name) / "harp-router.sqlite3")) as db, db:   # age the first turn
            db.execute("UPDATE decisions SET ts = ts - ?", (router_engine.FOLLOWUP_WINDOW + 3600,))
        self.assertNotEqual(self.engine.decide("1", session="s1", use_jev=False)["by"], "followup")


class ExtraDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = train.load_extra()
        cls.base = {r["prompt"].casefold() for r in train.load_rows()}

    def test_size_ids_and_prompts_are_unique_and_new(self):
        self.assertGreaterEqual(len(self.rows), 1400)
        self.assertEqual(len({r["id"] for r in self.rows}), len(self.rows))
        prompts = [r["prompt"].casefold() for r in self.rows]
        self.assertEqual(len(set(prompts)), len(prompts))
        self.assertFalse(set(prompts) & self.base)

    def test_rows_have_tiers_groups_and_both_languages(self):
        self.assertEqual({r["tier"] for r in self.rows}, {0, 1, 2, 3, 4})
        self.assertTrue(all(r["task_group"] and r["template"] for r in self.rows))
        self.assertTrue({"th", "en"} <= {r["language"] for r in self.rows})

    def test_held_out_rows_never_share_a_template_with_training(self):
        held = {r["template"] for r in self.rows if r["split"] == "evaluation"}
        trained = {r["template"] for r in self.rows if r["split"] == "train"}
        self.assertTrue(held)
        self.assertFalse(held & trained)
        self.assertEqual({r["tier"] for r in self.rows if r["split"] == "evaluation"}, {0, 1, 2, 3, 4})

    def test_followup_replies_are_left_to_the_session_rule(self):
        self.assertFalse([r["prompt"] for r in self.rows if router.is_followup(r["prompt"])])

    def test_seeding_extra_rows_keeps_their_group(self):
        with tempfile.TemporaryDirectory() as home:
            rows = [r for r in self.rows if r["split"] == "train"][:3]
            self.assertEqual(seed.seed(home, rows), 3)
            with closing(sqlite3.connect(Path(home) / "harp-router.sqlite3")) as db:
                stored = db.execute("SELECT grp FROM samples WHERE source='seed' ORDER BY id").fetchall()
            self.assertEqual([g for (g,) in stored], [r["task_group"] for r in rows])

    def test_trained_learner_sends_a_status_ping_to_the_cheapest_tier(self):
        learner = train.train_learner([r for r in train.load_rows() + self.rows if r["split"] == "train"])
        learner.TEMPERATURE = ladder.LEARNER_TEMPERATURE
        self.assertEqual(ladder.predict_tier(learner, "เสร็จยัง")[0], 0)
        self.assertGreaterEqual(ladder.predict_tier(learner, "แก้บั๊กปุ่มบันทึกส่งคำขอสองครั้ง แล้วเขียนเทสต์")[0], 3)


if __name__ == "__main__":
    unittest.main()
