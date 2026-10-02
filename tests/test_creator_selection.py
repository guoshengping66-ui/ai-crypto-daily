import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from trendradar.ai.analyzer import AIAnalysisResult, AIAnalyzer
from trendradar.core.analyzer import count_rss_frequency
from trendradar.core.loader import load_config
from trendradar.crawler.rss.parser import RSSParser
from trendradar.creator import CreatorSelector, canonical_url, fetch_hn_signals, same_event
from trendradar.notification.senders import _parse_creator_topic_cards, _send_creator_topics_to_bark


class CreatorSelectionTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 3, 1, tzinfo=timezone.utc)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = {"hn_enabled": False, "min_score": 24, "history_path": str(Path(self.temp.name) / "history.json"), "audit_path": str(Path(self.temp.name) / "run.json")}
        self.feeds = [{"id": "ai", "category": "ai", "publisher": "ai-news"}, {"id": "crypto", "category": "crypto", "publisher": "crypto-news"}]

    def item(self, title, slug, category="ai", age=1, **extra):
        return {"title": title, "url": f"https://example.com/{slug}", "summary": "A newly published source summary.", "feed_id": category, "category": category, "published_at": (self.now - timedelta(hours=age)).isoformat(), **extra}

    def selector(self):
        return CreatorSelector(self.config, self.feeds, self.now)

    def test_rss_group_retains_original_timestamp_for_strict_gate(self):
        item = self.item("OpenAI releases a new coding model", "release")
        stats, _ = count_rss_frequency([item], [], [], quiet=True)
        self.assertEqual(stats[0]["titles"][0]["published_at"], item["published_at"])
        self.assertEqual(stats[0]["titles"][0]["feed_id"], "ai")
        self.assertTrue(AIAnalyzer._creator_timestamp_is_within_24h(stats[0]["titles"][0]["published_at"], self.now))

    def test_rss_utc_date_survives_timezone_conversion(self):
        content = '<rss version="2.0"><channel><title>News</title><item><title>Launch</title><link>https://example.com/a</link><pubDate>Fri, 02 Oct 2026 19:00:00 -0400</pubDate></item></channel></rss>'
        item = RSSParser().parse(content)[0]
        self.assertEqual(item.published_at, "2026-10-02T23:00:00+00:00")

    def test_modified_time_alone_does_not_establish_a_fresh_article(self):
        feed = '<feed xmlns="http://www.w3.org/2005/Atom"><title>News</title><entry><title>Old article updated</title><link href="https://example.com/a"/><updated>2026-10-03T00:00:00Z</updated></entry></feed>'
        self.assertIsNone(RSSParser().parse(feed)[0].published_at)

    def test_specialist_source_scope_and_mixed_source_do_not_use_generic_crypto_keywords(self):
        selector = self.selector()
        specialist = self.item("Orion now remembers your entire project", "memory")
        mixed = self.item("OpenAI publishes a security protocol", "protocol", feed_id="general", category="")
        groups = selector.group_raw([specialist, mixed])
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["titles"]), 2)
        self.assertEqual(groups[0]["word"], "AI热点")

    def test_time_advertising_and_ordinary_price_noise_do_not_fill_slots(self):
        items = [self.item("OpenAI releases a coding model", "new"), self.item("OpenAI releases old model", "old", age=24.001), self.item("OpenAI releases future model", "future", age=-1), self.item("OpenAI releases an undated model", "undated", published_at=""), self.item("Sponsored crypto presale launch", "ad", "crypto"), self.item("Bitcoin price rises today", "price", "crypto")]
        selector = self.selector()
        selector.prepare(selector.group_raw(items), community_signals=[])
        self.assertEqual([x["url"] for x in selector.candidates], [items[0]["url"]])
        self.assertEqual(selector.diagnostics["counts"]["older_than_24h"], 1)

    def test_tracking_urls_cross_source_and_bilingual_versions_merge_but_new_events_survive(self):
        first = self.item("OpenAI releases GPT-6", "gpt6")
        chinese = self.item("OpenAI发布GPT-6", "gpt6-cn")
        self.assertTrue(same_event(first, chinese))
        self.assertTrue(same_event(first, {**first, "url": first["url"] + "?utm_source=rss"}))
        self.assertFalse(same_event(first, self.item("OpenAI releases GPT-7", "gpt7")))
        self.assertFalse(same_event(first, self.item("OpenAI GPT-6 outage", "outage")))
        self.assertFalse(same_event(self.item("OpenAI faces copyright lawsuit", "openai"), self.item("Google faces copyright lawsuit", "google")))
        self.assertEqual(canonical_url("https://example.com/a/amp?utm_source=x&id=2#top"), "https://example.com/a?id=2")
        self.assertTrue(same_event(self.item("Once a $2.3 Billion Network, Ethereum Layer-2 Blast Is Shutting Down", "blast"), self.item("Once a $2 billion Ethereum layer-2, Blast is shutting down after assets plunge 98%", "blast-other")))
        self.assertTrue(same_event(self.item("Once a $2.3 Billion Network, Ethereum Layer-2 Blast Is Shutting Down", "blast"), self.item("Blast将停止运营，建议用户在10月26日之前将资产提现到以太坊主网", "blast-cn")))
        self.assertTrue(same_event(self.item("Once a $2.3 Billion Network, Ethereum Layer-2 Blast Is Shutting Down", "blast"), self.item("Blast to wind down Ethereum L2 after costs outpace revenue", "blast-third")))
        self.assertFalse(same_event(self.item("OpenAI shuts down GPT-4o", "gpt4o-end"), self.item("OpenAI shuts down Sora", "sora-end")))
        self.assertTrue(same_event(self.item("Anchorage Digital 据报裁员 17%，估值 42 亿美元", "layoff-cn"), self.item("$4.2B crypto bank Anchorage Digital cuts 17% of workforce: Report", "layoff-en")))

    def test_generic_stock_whale_and_monthly_recaps_are_not_preferred_hotspots(self):
        items = [self.item("英伟达股价再创历史新高，市值逼近6万亿美元", "stock"), self.item("数据：某巨鲸清仓DeFi代币，亏损961万美元", "whale", "crypto"), self.item("The latest AI news we announced in September 2026", "recap")]
        selector = self.selector()
        selector.prepare(selector.group_raw(items), community_signals=[])
        self.assertEqual(selector.candidates, [])

    def test_community_discussion_is_observable_and_cannot_refresh_old_news(self):
        first = self.item("OpenAI releases GPT-6", "gpt6")
        old = self.item("Google releases Gemini-3", "old", age=30)
        signals = [{"platform": "Hacker News", "title": first["title"], "url": first["url"], "points": 230, "comments": 75, "discussion_url": "https://news.ycombinator.com/item?id=1"}, {"platform": "Hacker News", "title": old["title"], "url": old["url"], "points": 900, "comments": 500}]
        selector = self.selector()
        selector.prepare(selector.group_raw([first, old]), community_signals=signals)
        self.assertEqual(len(selector.candidates), 1)
        self.assertIn("230票 / 75评论", selector.candidates[0]["attention_evidence"])
        self.assertNotIn("X", selector.candidates[0]["attention_evidence"])
        self.assertEqual(selector.candidates[0]["published_at"], first["published_at"])

    def test_multiple_feeds_of_same_publisher_only_count_once(self):
        self.feeds.append({"id": "ai-two", "category": "ai", "publisher": "ai-news"})
        items = [self.item("OpenAI releases GPT-6", "gpt6"), self.item("OpenAI发布GPT-6", "gpt6-other", feed_id="ai-two")]
        selector = self.selector()
        selector.prepare(selector.group_raw(items), community_signals=[])
        self.assertEqual(len(selector.candidates), 1)
        self.assertEqual(selector.candidates[0]["coverage_publishers"], ["ai-news"])
        self.assertFalse(selector.candidates[0]["attention_verified"])

    def test_history_excludes_successful_event_but_allows_a_new_development(self):
        first = self.item("OpenAI releases GPT-6", "gpt6")
        selector = self.selector()
        selector.prepare(selector.group_raw([first]), community_signals=[])
        result = selector.complete(AIAnalysisResult(success=False))
        selector.archive(result, {"bark": True}, [first["url"]])
        another_source = self.item("OpenAI发布GPT-6", "other")
        new_event = self.item("OpenAI GPT-6 outage", "outage")
        second = self.selector()
        second.prepare(second.group_raw([another_source, new_event]), community_signals=[])
        self.assertEqual([x["url"] for x in second.candidates], [new_event["url"]])
        self.assertEqual(second.diagnostics["counts"]["previously_delivered_events"], 1)

    def test_model_shortage_uses_qualified_distinct_candidates_and_archives_exact_body(self):
        items = [self.item(f"{name} releases {product}", product) for name, product in (("OpenAI", "Atlas"), ("Google", "Orion"), ("Nvidia", "Spectra"))]
        items += [self.item(f"{name} launches {product}", product, "crypto") for name, product in (("Coinbase", "Vault"), ("Solana", "Nova"), ("Aave", "Prism"))]
        selector = self.selector()
        selector.prepare(selector.group_raw(items), community_signals=[])
        result = selector.complete(AIAnalysisResult(success=False, error="model unavailable"))
        self.assertEqual(len(_parse_creator_topic_cards(result.core_trends)), 3)
        self.assertEqual(len(_parse_creator_topic_cards(result.sentiment_controversy)), 3)
        selector.archive(result, dry_run=True)
        audit = json.loads(selector.audit_path.read_text(encoding="utf-8"))
        self.assertEqual(audit["content"]["ai"], result.core_trends)
        self.assertEqual(len(audit["selected"]), 6)
        self.assertFalse(selector.history_path.exists())
        self.assertEqual(result.signals, "")

    def test_partial_bark_success_records_only_accepted_card_urls(self):
        accepted, rejected = Mock(status_code=200), Mock(status_code=503)
        accepted.json.return_value = {"code": 200}
        delivered = []
        cards = [{"headline": "AI A", "body": "来源：https://example.com/a"}, {"headline": "AI B", "body": "来源：https://example.com/b"}]
        with patch("trendradar.notification.senders.requests.post", side_effect=[accepted, rejected]), patch("trendradar.notification.senders.time.sleep"):
            success = _send_creator_topics_to_bark("https://bark.example/push", "test", None, cards, [], batch_interval=0, delivered_urls=delivered)
        self.assertFalse(success)
        self.assertEqual(delivered, ["https://example.com/b"])

    def test_hn_fetch_rejects_old_and_future_posts(self):
        payload = {"nbPages": 1, "hits": [{"objectID": "1", "title": "fresh", "created_at": self.now.isoformat(), "points": 20, "num_comments": 10}, {"objectID": "2", "created_at": (self.now - timedelta(hours=25)).isoformat()}, {"objectID": "3", "created_at": (self.now + timedelta(hours=1)).isoformat()}]}
        response = Mock()
        response.json.return_value = payload
        with patch("trendradar.creator.requests.get", return_value=response):
            signals = fetch_hn_signals(self.now)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["points"], 20)

    def test_dry_run_disables_notifications_and_model_calls(self):
        with patch.dict("os.environ", {"CREATOR_DRY_RUN": "true", "AI_API_KEY": ""}):
            config = load_config()
        self.assertFalse(config["ENABLE_NOTIFICATION"])
        self.assertTrue(config["AI_ANALYSIS"]["RSS_ONLY_FALLBACK"])


if __name__ == "__main__":
    unittest.main()
