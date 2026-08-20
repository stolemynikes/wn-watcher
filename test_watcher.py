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
import time
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


class TellingARealChallengeApartFromChat(unittest.TestCase):
    """A live tab alerted as challenged on 2026-08-18 with no challenge on it.
    Chat is English prose, "just a moment" is ordinary English, and the wording
    was matched against the whole page. A real interstitial replaces the page:
    Cloudflare's own markup, and a couple of hundred characters at most."""

    STREAM = ("magictraders_nl is live\n" + "chat line\n" * 400 +
              "someone typed just a moment\n" + "chat line\n" * 400)

    def read(self, text, challenge_dom=False):
        return watch.read_live_tab(text, SELECTORS, "", "", challenge_dom)

    def test_the_wording_in_a_full_page_is_not_a_challenge(self):
        self.assertFalse(self.read(self.STREAM)["challenge"])

    def test_but_it_is_still_reported_as_wording_seen(self):
        self.assertTrue(self.read(self.STREAM)["challenge_phrase"])

    def test_cloudflare_markup_is_believed_however_long_the_page(self):
        self.assertTrue(self.read(self.STREAM, challenge_dom=True)["challenge"])

    def test_the_real_interstitial_is_still_caught(self):
        self.assertTrue(self.read(
            "Just a moment...\nChecking your browser before accessing "
            "whatnot.com\nEnable JavaScript and cookies to continue")["challenge"])

    def test_a_healthy_page_reports_no_wording_at_all(self):
        self.assertFalse(self.read("Giveaway with 12 entries")["challenge_phrase"])

    def test_a_challenged_tab_is_still_read_as_no_giveaway(self):
        self.assertFalse(self.read("Just a moment...")["present"])


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


class WhereTheSellerShipsFrom(unittest.TestCase):
    """Reading a seller's country out of one Apollo-cache snapshot.

    Measured live 2026-08-18: a foreign seller's listings say "Ships from
    United Kingdom" / "Ships from Germany", a seller in your own country says
    nothing at all, and the tariff line — when customs apply — names both
    countries as ISO codes.
    """

    def test_a_foreign_seller_is_read_from_the_shipping_line(self):
        origin = watch.origin_from_cache(
            {"ok": True, "listings": 30, "shipsFrom": "Ships from Germany",
             "buyerCountry": "NL", "pair": None})
        self.assertTrue(origin["foreign"])

    def test_the_euro_does_not_hide_a_german_seller(self):
        # The whole reason currency cannot be used: DE and NL share it.
        origin = watch.origin_from_cache(
            {"ok": True, "listings": 44, "shipsFrom": "Ships from Germany",
             "buyerCountry": "NL", "pair": None})
        self.assertTrue(origin["foreign"])

    def test_no_shipping_line_at_all_means_your_own_country(self):
        origin = watch.origin_from_cache(
            {"ok": True, "listings": 34, "shipsFrom": "", "buyerCountry": "NL",
             "pair": None})
        self.assertFalse(origin["foreign"])

    def test_an_empty_cache_stays_unknown(self):
        origin = watch.origin_from_cache(
            {"ok": True, "listings": 0, "shipsFrom": "", "buyerCountry": "NL",
             "pair": None})
        self.assertIsNone(origin["foreign"])

    def test_the_tariff_line_is_believed_over_country_names(self):
        origin = watch.origin_from_cache(
            {"ok": True, "listings": 4, "shipsFrom": "Ships from United Kingdom",
             "buyerCountry": "NL", "pair": {"seller": "GB", "buyer": "NL"}})
        self.assertTrue(origin["foreign"])

    def test_a_country_we_cannot_name_is_unknown_not_foreign(self):
        origin = watch.origin_from_cache(
            {"ok": True, "listings": 4, "shipsFrom": "Ships from Ruritania",
             "buyerCountry": "ZZ", "pair": None})
        self.assertIsNone(origin["foreign"])

    def test_your_own_country_named_outright_is_not_foreign(self):
        origin = watch.origin_from_cache(
            {"ok": True, "listings": 4, "shipsFrom": "Ships from Netherlands",
             "buyerCountry": "NL", "pair": None})
        self.assertFalse(origin["foreign"])

    def test_what_was_learned_earlier_is_not_lost(self):
        previous = {"ships_from": "Ships from United Kingdom",
                    "buyer_country": "NL", "listings": 4, "foreign": None}
        origin = watch.origin_from_cache(
            {"ok": True, "listings": 0, "shipsFrom": "", "buyerCountry": "",
             "pair": None}, previous)
        self.assertTrue(origin["foreign"])


class DomesticOnlyFromAbroad(unittest.TestCase):
    """The one case that cannot be entered: domestic-only, seller abroad.

    Both halves must be known. Anything unknown announces — a spare buzz
    costs nothing, a missed giveaway costs the entry.
    """

    def reading(self, only_domestic, foreign):
        return {"present": True, "seller": "s", "prize": "Free box",
                "eligible": None,
                "ws_giveaway": {"onlyDomestic": only_domestic},
                "origin": {"ships_from": "Ships from United Kingdom",
                           "foreign": foreign}}

    def test_domestic_only_and_abroad_is_the_case(self):
        self.assertTrue(watch.foreign_domestic(self.reading(True, True)))

    def test_an_international_giveaway_from_abroad_is_fine(self):
        self.assertFalse(watch.foreign_domestic(self.reading(False, True)))

    def test_domestic_only_at_home_is_fine(self):
        self.assertFalse(watch.foreign_domestic(self.reading(True, False)))

    def test_an_unknown_country_is_not_the_case(self):
        self.assertFalse(watch.foreign_domestic(self.reading(True, None)))

    def test_no_socket_reading_yet_is_not_the_case(self):
        self.assertFalse(watch.foreign_domestic(
            {"present": True, "origin": {"foreign": True}}))


class ShadowingTheForeignDomesticFilter(unittest.TestCase):
    """Shadow mode must change nothing about what is sent — it only says what
    it would have silenced. Muting is a separate, opt-in setting."""

    class Spy:
        def __init__(self):
            self.sent = []

        def send(self, title, message, url, **kw):
            self.sent.append({"title": title, "message": message, "url": url})

    def announce(self, mode):
        reading = {"present": True, "seller": "pokeretreat",
                   "prize": "Free Triple Blister", "eligible": None,
                   "ws_giveaway": {"onlyDomestic": True, "onlyFollowers": True},
                   "origin": {"ships_from": "Ships from United Kingdom",
                              "foreign": True}}
        spy = self.Spy()
        watch.announce_giveaway(spy, "https://wn/live/x", reading, {}, mode)
        return spy.sent

    def test_shadow_still_sends(self):
        self.assertEqual(len(self.announce("shadow")), 1)

    def test_shadow_names_the_country_in_the_push(self):
        self.assertIn("United Kingdom", self.announce("shadow")[0]["message"])

    def test_mute_sends_nothing(self):
        self.assertEqual(self.announce("mute"), [])

    def test_off_sends_and_says_nothing_extra(self):
        sent = self.announce("off")
        self.assertEqual(len(sent), 1)
        self.assertNotIn("United Kingdom", sent[0]["message"])

    def test_an_enterable_giveaway_is_untouched_in_every_mode(self):
        for mode in ("shadow", "mute", "off"):
            spy = self.Spy()
            watch.announce_giveaway(
                spy, "https://wn/live/x",
                {"present": True, "seller": "magictraders_nl", "prize": "Box",
                 "eligible": None, "ws_giveaway": {"onlyDomestic": True},
                 "origin": {"ships_from": "", "foreign": False}}, {}, mode)
            with self.subTest(mode=mode):
                self.assertEqual(len(spy.sent), 1)


class TheShadowbanTally(unittest.TestCase):
    """Counts only draws that are actually evidence: you were eligible, you
    could enter, and the pool was small enough that the entrant list is
    complete. Foreign domestic-only giveaways — the ones you are barred from
    — must never count as a miss."""

    def fresh(self):
        return {"shadowban_stats": {"eligible_draws": 0, "listed": 0,
                                    "missing": 0}}

    def reading(self, only_domestic=False, foreign=False):
        return {"ws_giveaway": {"onlyDomestic": only_domestic},
                "origin": {"ships_from": "Ships from United Kingdom",
                           "foreign": foreign}}

    def winner(self, entrants, eligible=True, count=20):
        return {"username": "someoneelse", "prize": "Box",
                "entrants": entrants, "was_eligible": eligible,
                "entry_count": count}

    def test_present_in_a_small_pool_is_a_listed_draw(self):
        st = self.fresh()
        watch.record_draw_for_shadowban(st, self.reading(),
                                        self.winner({"me", "bob"}), "me")
        self.assertEqual(st["shadowban_stats"],
                         {"eligible_draws": 1, "listed": 1, "missing": 0})

    def test_absent_from_a_small_pool_is_a_miss(self):
        st = self.fresh()
        watch.record_draw_for_shadowban(st, self.reading(),
                                        self.winner({"bob", "ann"}), "me")
        self.assertEqual(st["shadowban_stats"],
                         {"eligible_draws": 1, "listed": 0, "missing": 1})

    def test_a_foreign_domestic_giveaway_is_never_counted(self):
        st = self.fresh()
        reason = watch.record_draw_for_shadowban(
            st, self.reading(only_domestic=True, foreign=True),
            self.winner({"bob"}), "me")
        self.assertIn("not counted", reason)
        self.assertEqual(st["shadowban_stats"]["eligible_draws"], 0)

    def test_a_pool_over_fifty_is_not_trusted(self):
        st = self.fresh()
        watch.record_draw_for_shadowban(
            st, self.reading(), self.winner({"bob"}, count=105), "me")
        self.assertEqual(st["shadowban_stats"]["eligible_draws"], 0)

    def test_an_ineligible_draw_is_not_counted(self):
        st = self.fresh()
        watch.record_draw_for_shadowban(
            st, self.reading(), self.winner({"bob"}, eligible=False), "me")
        self.assertEqual(st["shadowban_stats"]["eligible_draws"], 0)

    def test_a_missing_entrant_list_is_not_counted(self):
        st = self.fresh()
        watch.record_draw_for_shadowban(
            st, self.reading(), self.winner(None), "me")
        self.assertEqual(st["shadowban_stats"]["eligible_draws"], 0)

    def test_an_unknown_pool_size_is_not_counted(self):
        st = self.fresh()
        watch.record_draw_for_shadowban(
            st, self.reading(), self.winner({"bob"}, count=None), "me")
        self.assertEqual(st["shadowban_stats"]["eligible_draws"], 0)

    def test_a_domestic_giveaway_from_a_home_seller_still_counts(self):
        st = self.fresh()
        watch.record_draw_for_shadowban(
            st, self.reading(only_domestic=True, foreign=False),
            self.winner({"me"}), "me")
        self.assertEqual(st["shadowban_stats"]["listed"], 1)


class PresenceDropAlert(unittest.TestCase):
    """A tab whose socket has gone silent means you have fallen out of the
    stream. Warn once, clear when it recovers, never touch the tab."""

    class Spy:
        def __init__(self): self.sent=[]
        def send(self, title, message, url, **kw):
            self.sent.append({"title": title, "message": message})

    def state(self, seconds_ago):
        return {"u": {"last_frame": time.time() - seconds_ago, "cdp": None}}

    def test_a_silent_tab_warns(self):
        spy=self.Spy(); lost=set()
        watch.check_presence(spy, "u", self.state(120), lost, 90)
        self.assertEqual(len(spy.sent), 1)
        self.assertIn("u", lost)

    def test_a_lively_tab_does_not_warn(self):
        spy=self.Spy(); lost=set()
        watch.check_presence(spy, "u", self.state(5), lost, 90)
        self.assertEqual(spy.sent, [])

    def test_it_warns_only_once_while_still_silent(self):
        spy=self.Spy(); lost=set()
        st=self.state(200)
        watch.check_presence(spy, "u", st, lost, 90)
        watch.check_presence(spy, "u", st, lost, 90)
        self.assertEqual(len(spy.sent), 1)

    def test_recovery_re_arms_the_warning(self):
        spy=self.Spy(); lost=set()
        watch.check_presence(spy, "u", self.state(200), lost, 90)  # warns
        watch.check_presence(spy, "u", self.state(2), lost, 90)    # recovers
        self.assertNotIn("u", lost)
        watch.check_presence(spy, "u", self.state(200), lost, 90)  # warns again
        self.assertEqual(len(spy.sent), 2)

    def test_zero_threshold_disables_it(self):
        spy=self.Spy(); lost=set()
        watch.check_presence(spy, "u", self.state(9999), lost, 0)
        self.assertEqual(spy.sent, [])

    def test_a_freshly_attached_tab_is_not_judged_silent(self):
        spy=self.Spy(); lost=set()
        watch.check_presence(spy, "u", self.state(0), lost, 90)
        self.assertEqual(spy.sent, [])

    def test_an_unknown_tab_is_a_no_op(self):
        spy=self.Spy()
        watch.check_presence(spy, "gone", {}, set(), 90)
        self.assertEqual(spy.sent, [])


class OwnPresenceInThePresenceFeed(unittest.TestCase):
    """Pulling your own account out of a presence_diff, and reporting a drop
    that lands during a live giveaway."""

    def diff(self, leaves=(), joins=()):
        mk=lambda names:{f"k{i}":{"metas":[{"user":{"username":n}}]}
                         for i,n in enumerate(names)}
        return {"leaves": mk(leaves), "joins": mk(joins)}

    def test_reads_a_username_out_of_leaves(self):
        got=watch._presence_usernames(self.diff(leaves=["Alice","BOB"])["leaves"])
        self.assertEqual(got, {"alice", "bob"})

    def test_a_missing_side_is_empty_not_an_error(self):
        self.assertEqual(watch._presence_usernames(None), set())

    def test_a_malformed_meta_is_skipped(self):
        block={"k":{"metas":[{"nouser":1},{"user":{"noname":1}},
                             {"user":{"username":"ok"}}]}}
        self.assertEqual(watch._presence_usernames(block), {"ok"})

    class Spy:
        def __init__(self): self.sent=[]
        def send(self,*a,**k): self.sent.append(a)

    def test_a_drop_during_a_giveaway_pushes(self):
        spy=self.Spy()
        ws={"u":{"attached_at":0.0}}
        watch.report_presence_events(spy,"u",[("left",1000.0)],ws,True)
        self.assertEqual(len(spy.sent),1)

    def test_a_drop_with_no_giveaway_only_logs(self):
        spy=self.Spy()
        ws={"u":{"attached_at":0.0}}
        watch.report_presence_events(spy,"u",[("left",1000.0)],ws,False)
        self.assertEqual(spy.sent,[])

    def test_a_stale_active_id_no_longer_forces_a_push(self):
        # active_id is set but no giveaway is on screen now -> no push
        spy=self.Spy()
        ws={"u":{"attached_at":0.0,"active_id":"p1"}}
        watch.report_presence_events(spy,"u",[("left",1000.0)],ws,False)
        self.assertEqual(spy.sent,[])

    def test_a_rejoin_never_pushes(self):
        spy=self.Spy()
        ws={"u":{"attached_at":0.0}}
        watch.report_presence_events(spy,"u",[("joined",1000.0)],ws,True)
        self.assertEqual(spy.sent,[])

    def test_events_are_consumed_once(self):
        ws={"u":{"presence_events":[("left",1.0)]}}
        self.assertEqual(watch.pop_presence_events(ws,"u"),[("left",1.0)])
        self.assertEqual(watch.pop_presence_events(ws,"u"),[])


class PresenceGraceAfterAttach(unittest.TestCase):
    """A leave inside the attach grace window is connection churn, not a real
    drop, so it must be adopted silently — no event, no push."""

    def feed(self, entry, leaves=(), joins=()):
        # mimic the on_frame presence_diff branch against a built entry
        import time as _t
        left = "me" in set(n.lower() for n in leaves)
        joined = "me" in set(n.lower() for n in joins)
        if _t.time() - entry["attached_at"] < watch.PRESENCE_ATTACH_GRACE:
            if left and not joined: entry["present"]=False
            elif joined and not left: entry["present"]=True
        elif left and not joined and entry["present"] is not False:
            entry["present"]=False; entry["presence_events"].append(("left", _t.time()))
        elif joined and not left and entry["present"] is not True:
            entry["present"]=True; entry["presence_events"].append(("joined", _t.time()))

    def fresh(self, attached_ago):
        return {"present": None, "presence_events": [],
                "attached_at": time.time() - attached_ago}

    def test_a_leave_just_after_attach_is_silent(self):
        e=self.fresh(2)   # 2s after attach, inside grace
        self.feed(e, leaves=["me"])
        self.assertEqual(e["presence_events"], [])
        self.assertIs(e["present"], False)   # state still adopted

    def test_a_leave_well_after_attach_is_reported(self):
        e=self.fresh(120)  # long after attach
        e["present"]=True
        self.feed(e, leaves=["me"])
        self.assertEqual(len(e["presence_events"]), 1)

    def test_a_real_drop_then_rejoin_after_grace_logs_both(self):
        e=self.fresh(120); e["present"]=True
        self.feed(e, leaves=["me"])
        self.feed(e, joins=["me"])
        kinds=[k for k,_ in e["presence_events"]]
        self.assertEqual(kinds, ["left","joined"])


class PresenceStateResync(unittest.TestCase):
    """The full viewer list on (re)join. Seeing yourself in a fresh state
    after a drop is the proof it was a rotation, not a real drop."""

    def block(self, *names):
        return {f"k{i}":{"metas":[{"user":{"username":n}}]} for i,n in enumerate(names)}

    def test_reads_the_full_list(self):
        got=watch._presence_usernames(self.block("me","bob","ann"))
        self.assertEqual(got, {"me","bob","ann"})

    def test_rotation_rejoin_is_seen_via_state(self):
        # simulate: present True -> a "left" made it False -> presence_state
        # with you in it -> a rejoined event
        entry={"present":False,"presence_events":[]}
        data=self.block("me","bob")
        here="me" in watch._presence_usernames(data)
        if here and entry["present"] is not True:
            if entry["present"] is False:
                entry["presence_events"].append(("rejoined",1.0))
            entry["present"]=True
        self.assertEqual([k for k,_ in entry["presence_events"]], ["rejoined"])
        self.assertIs(entry["present"], True)

    def test_a_state_without_you_confirms_a_drop(self):
        entry={"present":True,"presence_events":[]}
        data=self.block("bob","ann")
        here="me" in watch._presence_usernames(data)
        if not here and entry["present"] is True:
            entry["present"]=False
            entry["presence_events"].append(("left",1.0))
        self.assertEqual([k for k,_ in entry["presence_events"]], ["left"])


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


class GiveawayTypeFromTheSocket(unittest.TestCase):
    """The entry-rule flags come from a socket frame, not the DOM — every
    product carries this sub-object, mostly false, so the label must stay
    empty unless something is actually set."""

    def test_open_giveaway_has_no_label(self):
        self.assertEqual(watch.describe_giveaway_type({
            "buyerAppreciation": False, "onlyFollowers": False,
            "onlyDomestic": False, "onlyTriviaWinners": False,
            "partyPurchase": False}), "")

    def test_none_has_no_label(self):
        self.assertEqual(watch.describe_giveaway_type(None), "")

    def test_followers_only_is_labelled(self):
        self.assertEqual(watch.describe_giveaway_type({
            "onlyFollowers": True}), "Followers only")

    def test_buyers_only_is_labelled(self):
        self.assertEqual(watch.describe_giveaway_type({
            "buyerAppreciation": True}), "Buyers only")

    def test_multiple_rules_are_joined(self):
        label = watch.describe_giveaway_type({
            "onlyFollowers": True, "onlyDomestic": True})
        self.assertIn("Followers only", label)
        self.assertIn("Domestic only", label)


class CurrentGiveawayFromTheSocket(unittest.TestCase):
    """Which learned product is 'the' giveaway right now is whatever the
    most recent entry-count frame named — not just whichever product we
    happen to have full details for."""

    def test_nothing_learned_yet_is_none(self):
        self.assertIsNone(watch.current_giveaway_info({}, "https://wn/live/x"))

    def test_no_active_id_yet_is_none(self):
        ws_state = {"https://wn/live/x": {"products": {"p1": {"name": "Box"}},
                                          "active_id": None}}
        self.assertIsNone(watch.current_giveaway_info(ws_state, "https://wn/live/x"))

    def test_active_id_without_a_product_record_is_none(self):
        ws_state = {"https://wn/live/x": {"products": {}, "active_id": "p1"}}
        self.assertIsNone(watch.current_giveaway_info(ws_state, "https://wn/live/x"))

    def test_returns_the_active_products_record(self):
        ws_state = {"https://wn/live/x": {
            "products": {"p1": {"name": "Sticker pack"},
                         "p2": {"name": "Booster box"}},
            "active_id": "p2"}}
        info = watch.current_giveaway_info(ws_state, "https://wn/live/x")
        self.assertEqual(info["name"], "Booster box")


class WinnerFromTheSocket(unittest.TestCase):
    """giveaway_won is a one-shot event; the winner must be handed to the
    caller exactly once, not re-delivered every poll like ws_giveaway is."""

    def test_no_winner_yet_is_none(self):
        self.assertIsNone(watch.pop_ws_winner({}, "https://wn/live/x"))
        ws_state = {"https://wn/live/x": {"winner": None}}
        self.assertIsNone(watch.pop_ws_winner(ws_state, "https://wn/live/x"))

    def test_a_winner_is_returned_once_then_gone(self):
        ws_state = {"https://wn/live/x": {
            "winner": {"pid": "p1", "username": "schpepijn", "prize": "Box"}}}
        first = watch.pop_ws_winner(ws_state, "https://wn/live/x")
        self.assertEqual(first["username"], "schpepijn")
        second = watch.pop_ws_winner(ws_state, "https://wn/live/x")
        self.assertIsNone(second)


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
