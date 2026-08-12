#!/usr/bin/env python3
"""Tests. stdlib unittest, no dependency to install.

    python test_watcher.py

The state machine is pure on purpose, so the behaviour that matters — when a
giveaway counts as started, and when a second one is genuinely new — is tested
without needing a browser or a live stream.
"""

import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import watch

SELECTORS = json.loads(
    (pathlib.Path(__file__).with_name("selectors.json")).read_text(encoding="utf-8"))


class ReadingAPage(unittest.TestCase):
    """Text matching against the patterns in selectors.json."""

    def read(self, text):
        return watch.read_live_tab(text, SELECTORS)

    def test_detects_the_known_real_string(self):
        # This exact wording is what the old radar matched successfully, so it
        # is the one pattern here that is known real rather than guessed.
        r = self.read("Pokemon break\nGiveaway with 37 entries\nchat")
        self.assertTrue(r["present"])
        self.assertEqual(r["entries"], 37)

    def test_quiet_stream_is_not_a_giveaway(self):
        self.assertFalse(self.read("Pokemon break\n120 watching\nchat")["present"])

    def test_challenge_short_circuits(self):
        r = self.read("Just a moment...\nChecking your browser")
        self.assertTrue(r["challenge"])
        self.assertFalse(r["present"])

    def test_buyers_only_is_flagged(self):
        r = self.read("Giveaway with 5 entries\nBuyer appreciation giveaway")
        self.assertTrue(r["buyers_only"])

    def test_a_broken_pattern_does_not_crash(self):
        broken = {"live": {"giveaway_present": "Giveaway with \\d+ entr",
                           "entries": "([unclosed"}}
        r = watch.read_live_tab("Giveaway with 3 entries", broken)
        self.assertTrue(r["present"])
        self.assertIsNone(r["entries"])


class Signature(unittest.TestCase):
    """Entry count must not be part of identity, or every poll is a new
    giveaway and your phone never stops."""

    def test_entry_count_changing_is_the_same_giveaway(self):
        a = watch.signature("u", {"prize": "Booster box", "entries": 3})
        b = watch.signature("u", {"prize": "Booster box", "entries": 41})
        self.assertEqual(a, b)

    def test_a_different_prize_is_a_different_giveaway(self):
        self.assertNotEqual(
            watch.signature("u", {"prize": "Booster box"}),
            watch.signature("u", {"prize": "Sticker"}))

    def test_same_prize_on_two_streams_differs(self):
        self.assertNotEqual(watch.signature("a", {"prize": "Box"}),
                            watch.signature("b", {"prize": "Box"}))


class Tracker(unittest.TestCase):
    def feed(self, tracker, readings):
        """Returns the events produced, in order."""
        out = []
        for r in readings:
            out.append(tracker.update(r, watch.signature("u", r)))
        return out

    def test_needs_two_polls_before_announcing(self):
        t = watch.TabTracker(confirm=2)
        gw = {"present": True, "prize": "Box"}
        self.assertEqual(self.feed(t, [gw]), [None], "one frame is not enough")
        self.assertEqual(self.feed(t, [gw]), ["started"])

    def test_announces_once_not_every_poll(self):
        t = watch.TabTracker(confirm=2)
        gw = {"present": True, "prize": "Box"}
        events = self.feed(t, [gw] * 20)
        self.assertEqual(events.count("started"), 1)

    def test_a_flicker_never_announces(self):
        t = watch.TabTracker(confirm=2)
        gw, none = {"present": True, "prize": "Box"}, {"present": False}
        self.assertEqual(self.feed(t, [gw, none, gw, none]), [None] * 4)

    def test_second_giveaway_after_the_first_ends(self):
        t = watch.TabTracker(confirm=2)
        one = {"present": True, "prize": "Box"}
        two = {"present": True, "prize": "Sticker"}
        none = {"present": False}
        events = self.feed(t, [one, one, none, none, two, two])
        self.assertEqual(events, [None, "started", None, "ended", None, "started"])

    def test_chained_giveaway_without_a_gap_still_announces(self):
        # Sellers chain them; the old radar dropped the second one entirely.
        t = watch.TabTracker(confirm=2)
        one = {"present": True, "prize": "Box"}
        two = {"present": True, "prize": "Tin"}
        events = self.feed(t, [one, one, two, two])
        self.assertEqual(events.count("started"), 2)

    def test_entry_count_ticking_up_is_not_a_new_giveaway(self):
        t = watch.TabTracker(confirm=2)
        events = self.feed(t, [{"present": True, "prize": "Box", "entries": n}
                               for n in range(1, 30)])
        self.assertEqual(events.count("started"), 1)


class SelectorsFile(unittest.TestCase):
    """It is config, so it can be edited by hand and broken by hand."""

    def test_every_pattern_compiles(self):
        import re
        for section, entries in SELECTORS.items():
            if not isinstance(entries, dict):
                continue
            for name, value in entries.items():
                if name.startswith("_") or not isinstance(value, str):
                    continue
                if section == "purchases" and name in ("row_selector",):
                    continue
                with self.subTest(pattern=f"{section}.{name}"):
                    re.compile(value)

    def test_the_required_pattern_is_present(self):
        self.assertTrue(SELECTORS["live"].get("giveaway_present"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
