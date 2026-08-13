#!/usr/bin/env python3
"""Tests. stdlib unittest, no dependency to install.

    python test_watcher.py

The state machine is pure on purpose, so the behaviour that matters — when a
giveaway counts as started, and when a second one is genuinely new — is tested
without needing a browser or a live stream.
"""

import json
import os
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

    def test_big_giveaways_are_still_detected(self):
        # \\d+ alone failed outright on a grouped number — so a giveaway with
        # 1,234 entries was invisible, and those are the big ones.
        for text, expected in [("Giveaway with 1 entry", 1),
                               ("Giveaway with 402 entries", 402),
                               ("Giveaway with 1,234 entries", 1234),
                               ("Giveaway with 1.234 entries", 1234),
                               ("Giveaway with 1 234 entries", 1234),
                               ("Giveaway with 12k entries", 12000),
                               ("Giveaway with 1.5k entries", 1500)]:
            with self.subTest(text=text):
                r = self.read(text)
                self.assertTrue(r["present"], "presence matters more than the count")
                self.assertEqual(r["entries"], expected)

    def test_a_number_elsewhere_is_not_a_giveaway(self):
        self.assertFalse(self.read("140 watching · 12k followers")["present"])

    def test_prize_does_not_capture_the_entry_wording(self):
        # An early guess matched "Giveaway with 37 entries" and called the
        # prize "with 37 entries". A wrong prize is worse than none.
        self.assertEqual(self.read("Giveaway with 37 entries")["prize"], "")

    def test_prize_is_the_line_after_the_count(self):
        # Confirmed against the live site: the banner has no "Prize:" label,
        # the title is simply the next line down.
        r = self.read("Giveaway with 5 entries\nCharizard ETB")
        self.assertEqual(r["prize"], "Charizard ETB")

    def test_a_broken_pattern_does_not_crash(self):
        broken = {"live": {"giveaway_present": "Giveaway with \\d+ entr",
                           "entries": "([unclosed"}}
        r = watch.read_live_tab("Giveaway with 3 entries", broken)
        self.assertTrue(r["present"])
        self.assertIsNone(r["entries"])


class TheRealBanner(unittest.TestCase):
    """Transcribed from screenshots of the live site, 2026-08-12. These are the
    only fixtures here that are known real rather than invented."""

    COLLAPSED = "Giveaway with 6 entries\nChat  Watching\nOhhhhhhh"
    NOT_ELIGIBLE = ("Giveaway with 11 entries\ntb 3\nNot eligible\n"
                    "Terms & Conditions\nChat  Watching")
    ENTERABLE = ("Giveaway with 78 entries\n"
                 "🎁 FREE BOOSTER PACK + FREE SHIPPING 📦 #16\n"
                 "Open Mobile App To Enter\nTerms & Conditions\nChat")
    NO_GIVEAWAY = "tcgcardsheaven 5.0\n241\nChat  Watching\nGood luck"

    def read(self, text, dom=""):
        return watch.read_live_tab(text, SELECTORS, dom)

    def test_collapsed_banner_still_detects_the_giveaway(self):
        r = self.read(self.COLLAPSED)
        self.assertTrue(r["present"])
        self.assertEqual(r["entries"], 6)

    def test_collapsed_means_eligibility_unknown_not_ineligible(self):
        # Conflating these would silence real giveaways.
        self.assertIsNone(self.read(self.COLLAPSED)["eligible"])

    def test_not_eligible_is_read_from_the_page(self):
        r = self.read(self.NOT_ELIGIBLE)
        self.assertIs(r["eligible"], False)
        self.assertEqual(r["prize"], "tb 3")

    def test_enterable_is_read_from_the_page(self):
        r = self.read(self.ENTERABLE)
        self.assertIs(r["eligible"], True)
        self.assertIn("FREE BOOSTER PACK", r["prize"])
        self.assertEqual(r["entries"], 78)

    def test_prize_never_captures_the_eligibility_line(self):
        for text in (self.NOT_ELIGIBLE, self.ENTERABLE, self.COLLAPSED):
            with self.subTest(text=text[:30]):
                self.assertNotIn("eligible", self.read(text)["prize"].lower())
                self.assertNotIn("Open Mobile App", self.read(text)["prize"])

    def test_collapsed_prize_comes_from_the_dom_when_it_is_there(self):
        # The title is what you actually want in the push, and collapsed it is
        # not on screen — but the page has often already built the node.
        r = self.read(self.COLLAPSED, dom="Giveaway with 6 entries\ntb 3\nNot eligible")
        self.assertEqual(r["prize"], "tb 3")

    def test_a_stream_with_no_giveaway_stays_quiet(self):
        r = self.read(self.NO_GIVEAWAY)
        self.assertFalse(r["present"])


class TheWinOverlay(unittest.TestCase):
    """When a giveaway is drawn the stream shows "<name> won the giveaway!"
    over the video. Confirmed from a screenshot, 2026-08-13."""

    def winner(self, text):
        return watch.read_live_tab(text, SELECTORS)["winner"]

    def test_reads_the_winner(self):
        self.assertEqual(self.winner("junasxd won the giveaway!"), "junasxd")

    def test_chat_asking_who_won_is_not_a_winner(self):
        # Without requiring the exclamation mark and the line start, this
        # matched and reported the word "who" as the winner.
        self.assertEqual(self.winner("deezyripz\nwho won the giveaway?"), "")

    def test_chat_mentioning_a_name_is_not_a_winner(self):
        self.assertEqual(
            self.winner("justintjee\nyo did schpepijn won the giveaway"), "")

    def test_a_running_giveaway_has_no_winner_yet(self):
        self.assertEqual(self.winner("Giveaway with 67 entries"), "")

    def test_an_at_prefix_is_tolerated(self):
        self.assertEqual(self.winner("@junasxd won the giveaway!"), "junasxd")

    def test_it_names_whoever_won_not_only_you(self):
        # The comparison against my_username is what makes it yours; the
        # pattern itself must stay neutral.
        self.assertEqual(self.winner("someone_else won the giveaway!"),
                         "someone_else")


class ThePurchasesPanel(unittest.TestCase):
    """Transcribed from a real screenshot, 2026-08-12."""

    TEXT = """Activity
Purchases Offers Saved
Orders Community Boost
In Transit
PACKS / SINGLES IN SCHERM VANAF (ALLES GAAT OPE...
Purchased: €4.00
Date: 8/10/2026
In Transit
GEM VOL 5 BOOSTERBOX (18 PACKS)
Purchased: €12.49
Date: 8/10/2026
Completed
GIVVY 8 (pls bookmark our shows)
Purchased: €0.00
Date: 8/7/2026
Delivery date: 8/11/2026
Pending Review
`FREE PACKS & SHIPPING TWV €4,06 #16
Purchased: €0.00
Date: 8/6/2026
Delivery date: 8/11/2026
Download Purchase History"""

    def rows(self):
        return watch.parse_purchases(self.TEXT, SELECTORS)

    def test_finds_every_row(self):
        self.assertEqual(len(self.rows()), 4)

    def test_free_items_are_wins_and_paid_ones_are_not(self):
        won = [r["title"] for r in self.rows() if r["won"]]
        self.assertIn("GIVVY 8 (pls bookmark our shows)", won)
        self.assertNotIn("GEM VOL 5 BOOSTERBOX (18 PACKS)", won)

    def test_a_price_inside_a_title_is_not_the_row_price(self):
        # "FREE PACKS & SHIPPING TWV €4,06 #16" cost nothing despite the €4,06
        # in its name. Anchoring on the status badge is what prevents this.
        row = next(r for r in self.rows() if "TWV" in r["title"])
        self.assertEqual(row["price"], 0.0)
        self.assertTrue(row["won"])

    def test_status_is_captured(self):
        self.assertEqual({r["status"] for r in self.rows()},
                         {"In Transit", "Completed", "Pending Review"})

    def test_page_furniture_is_not_a_row(self):
        titles = [r["title"] for r in self.rows()]
        for junk in ("Activity", "Orders Community Boost",
                     "Download Purchase History"):
            self.assertNotIn(junk, titles)

    def test_rows_are_identified_beyond_their_title(self):
        # Titles repeat across shows ("FREE PACK/CARD + FREE SHIPPING #12"),
        # so the key includes date and price.
        keys = {f"{r['title']}|{r['date']}|{r['price']}" for r in self.rows()}
        self.assertEqual(len(keys), 4)

    def test_a_broken_row_pattern_yields_nothing_rather_than_raising(self):
        self.assertEqual(watch.parse_purchases(self.TEXT,
                                               {"purchases": {"row": "([bad"}}), [])


class LinkingAWinToItsStream(unittest.TestCase):
    """The purchases row has no link, so a win is matched back to the giveaway
    we announced. Sending you to the wrong seller is worse than sending you to
    your purchases page, so a weak match must not be used."""

    def setUp(self):
        import time
        self.now = time.time()
        self.mem = {
            "https://wn/live/aaa": {
                "prize": "🎁 FREE BOOSTER PACK + FREE SHIPPING 📦 #16",
                "at": self.now},
            "https://wn/live/bbb": {
                "prize": "GIVVY 8 (pls bookmark our shows)", "at": self.now},
        }

    def match(self, title):
        return watch.match_stream(title, self.mem, self.now)

    def test_exact_title_finds_the_stream(self):
        self.assertEqual(self.match("GIVVY 8 (pls bookmark our shows)"),
                         "https://wn/live/bbb")

    def test_emoji_and_punctuation_do_not_break_it(self):
        self.assertEqual(self.match("FREE BOOSTER PACK + FREE SHIPPING #16"),
                         "https://wn/live/aaa")

    def test_a_different_sellers_similar_title_is_refused(self):
        # Real pair: this is a DIFFERENT seller's giveaway that shares the
        # words free, shipping and #16. Guessing here would send you to the
        # wrong stream.
        self.assertIsNone(self.match("`FREE PACKS & SHIPPING TWV €4,06 #16"))

    def test_an_unrelated_purchase_matches_nothing(self):
        self.assertIsNone(self.match("GEM VOL 5 BOOSTERBOX (18 PACKS)"))

    def test_stale_giveaways_are_not_matched(self):
        old = {"https://wn/live/ccc": {"prize": "GIVVY 8 (pls bookmark our shows)",
                                       "at": self.now - watch.WIN_MEMORY_SECONDS - 1}}
        self.assertIsNone(
            watch.match_stream("GIVVY 8 (pls bookmark our shows)", old, self.now))

    def test_memory_is_pruned(self):
        state = {"recent_giveaways": dict(self.mem)}
        state["recent_giveaways"]["https://wn/live/old"] = {
            "prize": "x", "at": self.now - watch.WIN_MEMORY_SECONDS - 1}
        watch.prune_state(state)
        self.assertNotIn("https://wn/live/old", state["recent_giveaways"])
        self.assertEqual(len(state["recent_giveaways"]), 2)


class NotificationShape(unittest.TestCase):
    """Matches the format the old radar used, which reads well on a lock
    screen: who on the title line, what underneath, then what to do."""

    class Spy:
        def __init__(self):
            self.sent = []

        def send(self, title, message, url, **kw):
            self.sent.append({"title": title, "message": message, "url": url})

    def announce(self, reading, url="https://wn/live/x"):
        spy = self.Spy()
        watch.announce_giveaway(spy, url, reading, {})
        return spy.sent

    def test_seller_is_on_the_title_line(self):
        sent = self.announce({"present": True, "seller": "danihagebeuktcg",
                              "prize": "FREE BOOSTER PACK #29", "eligible": True})
        self.assertEqual(sent[0]["title"], "🎁 Giveaway — danihagebeuktcg 🎁")

    def test_prize_leads_the_body(self):
        sent = self.announce({"present": True, "seller": "s",
                              "prize": "FREE BOOSTER PACK #29", "eligible": True})
        self.assertTrue(sent[0]["message"].startswith("FREE BOOSTER PACK #29"))

    def test_no_entry_count_anywhere(self):
        sent = self.announce({"present": True, "seller": "s", "prize": "Box",
                              "entries": 402, "eligible": True})
        blob = sent[0]["title"] + sent[0]["message"]
        self.assertNotIn("402", blob)
        self.assertNotIn("entries", blob.lower())

    def test_it_links_to_the_stream(self):
        sent = self.announce({"present": True, "seller": "s", "prize": "Box",
                              "eligible": True}, url="https://wn/live/abc")
        self.assertEqual(sent[0]["url"], "https://wn/live/abc")

    def test_unknown_seller_still_sends(self):
        sent = self.announce({"present": True, "seller": "", "prize": "Box",
                              "eligible": None})
        self.assertEqual(sent[0]["title"], "🎁 Giveaway 🎁")

    def test_collapsed_banner_says_something_useful(self):
        sent = self.announce({"present": True, "seller": "tcg_nl", "prize": "",
                              "eligible": None})
        self.assertIn("giveaway just started", sent[0]["message"].lower())

    def test_ineligible_sends_nothing(self):
        self.assertEqual(
            self.announce({"present": True, "seller": "s", "prize": "tb 3",
                           "eligible": False}), [])


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


class FindingChrome(unittest.TestCase):
    """Installing Chrome without admin rights on Windows puts it under
    LOCALAPPDATA, nowhere near Program Files — the same trap that made the
    radar think Tailscale wasn't installed."""

    def setUp(self):
        import chrome
        self.chrome = chrome
        self.env = dict(os.environ)
        self.real_is_file = pathlib.Path.is_file
        self.addCleanup(setattr, pathlib.Path, "is_file", self.real_is_file)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self.env)))

    def only(self, existing):
        pathlib.Path.is_file = lambda self: str(self) == existing

    def test_finds_a_per_user_windows_install(self):
        os.environ["LOCALAPPDATA"] = r"C:\Users\x\AppData\Local"
        target = next(c for c in self.chrome.chrome_candidates() if "AppData" in c)
        self.only(target)
        self.assertEqual(self.chrome.find_chrome(), target)

    def test_program_files_is_read_from_the_environment(self):
        # Not always on C:.
        os.environ["ProgramFiles"] = r"D:\Programs"
        self.assertTrue(any(c.startswith(r"D:\Programs")
                            for c in self.chrome.chrome_candidates()))

    def test_missing_chrome_returns_none_rather_than_raising(self):
        self.only("/definitely/not/here")
        import shutil as _sh
        real_which = _sh.which
        _sh.which = lambda n: None
        try:
            self.assertIsNone(self.chrome.find_chrome())
        finally:
            _sh.which = real_which


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
