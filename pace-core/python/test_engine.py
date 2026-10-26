import unittest
from datetime import datetime, timedelta

import calendar_sync
import engine
import intent


def at(hour, minute=0):
    return datetime(2026, 10, 23, hour, minute, tzinfo=datetime.now().astimezone().tzinfo)


class EngineTest(unittest.TestCase):
    def test_overrun_moves_only_the_flexible_review(self):
        now = at(14, 10)
        project = engine.Event("p", "Project work", at(12, 0), at(14, 0), "flexible", True, "Code")
        meeting = engine.Event("m", "Design review", at(14, 30), at(15, 15), "fixed", False, "")
        review = engine.Event("r", "Optional review", at(16, 0), at(16, 45), "flexible", True, "")
        activity = engine.Activity("Code.exe", 4, False, now)
        found = engine.consider(now, [project, meeting, review], activity, set())
        self.assertIsNotNone(found)
        self.assertEqual(found["move"]["id"], "r")
        self.assertNotIn("m", found["move"]["id"])
        self.assertEqual(found["next_fixed_id"], "m")

    def test_kept_issue_stays_quiet(self):
        now = at(14, 10)
        project = engine.Event("p", "Project work", at(12, 0), at(14, 0), "flexible", True, "Code")
        meeting = engine.Event("m", "Design review", at(14, 30), at(15, 15), "fixed", False, "")
        activity = engine.Activity("Code", 1, False, now)
        key = engine.consider(now, [project, meeting], activity, set())["issue_key"]
        self.assertIsNone(engine.consider(now, [project, meeting], activity, {key}))

    def test_idle_or_paused_does_not_warn(self):
        now = at(14, 10)
        project = engine.Event("p", "Project work", at(12, 0), at(14, 0), "flexible", True, "Code")
        meeting = engine.Event("m", "Design review", at(14, 30), at(15, 15), "fixed", False, "")
        self.assertIsNone(engine.consider(now, [project, meeting], engine.Activity("Code", 1, True, now), set()))
        self.assertIsNone(engine.consider(now, [project, meeting], engine.Activity("Code", 400, False, now), set()))

    def test_fixed_move_is_refused(self):
        meeting = engine.Event("m", "Design review", at(14, 30), at(15, 15), "fixed", False, "")
        decision = engine.plan_intent(
            {"type": "move", "target": "design review", "when": "tomorrow"},
            [meeting],
            "18:00",
            at(13, 0),
        )
        self.assertNotIn("move", decision)
        self.assertIn("fixed", decision["message"])

    def test_mcp_content_block_is_not_an_event(self):
        events = [
            {"id": "a", "title": "Project work", "start": "2026-10-25T00:00:00+00:00", "end": "2026-10-25T01:00:00+00:00"},
            {"id": "b", "title": "Design review", "start": "2026-10-25T01:20:00+00:00", "end": "2026-10-25T02:00:00+00:00"},
        ]
        wrapped = [{"type": "text", "text": __import__("json").dumps(events), "id": "lc_75e5dabf-5519-4f1c-a8e9-a2ac6f7bc111"}]
        found = calendar_sync._as_list(wrapped)
        self.assertEqual([item["title"] for item in found], ["Project work", "Design review"])
        self.assertEqual(calendar_sync._normalize(found[0])["title"], "Project work")

    def test_work_and_reminder_requests(self):
        self.assertEqual(intent.work_kind("Prep me for the next meeting"), "prep")
        self.assertEqual(intent.work_kind("Remind me to leave in 20 minutes"), "remind")
        request = intent.reminder_request("Remind me to leave in 20 minutes")
        self.assertEqual(request["in_minutes"], 20)
        self.assertIn("leave", request["note"])
        self.assertIsNone(intent.work_kind("Move the review to tomorrow"))

    def test_parser_and_schema(self):
        parsed = intent.parse_rules("Move the optional review to tomorrow")
        self.assertEqual(parsed["type"], "move")
        self.assertEqual(parsed["when"], "tomorrow")
        self.assertIsNone(intent.validate({"type": "delete", "target": "x"}))
        self.assertIsNone(intent.validate({"type": "move", "target": "review", "extra": 1}))
        self.assertEqual(
            intent.validate({"type": "move", "target": "review", "when": "tomorrow"})["target"],
            "review",
        )


if __name__ == "__main__":
    unittest.main()
