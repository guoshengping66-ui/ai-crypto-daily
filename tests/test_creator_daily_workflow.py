import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from trendradar.ai.analyzer import AIAnalyzer, PreparedNewsContent
from trendradar.notification.senders import (
    _parse_creator_topic_cards,
    _send_creator_topics_to_bark,
)


def _rss_candidate_context(ai_urls=(), web3_urls=()):
    published_at = "2026-09-30T11:00:00+00:00"
    lines = []
    if ai_urls:
        lines.append(f"**AI热点** ({len(ai_urls)}条)")
        lines.extend(
            f"- [AI RSS] AI source candidate | 原文发布时间:{published_at} | 摘要:AI source summary | 链接:{url}"
            for url in ai_urls
        )
    if web3_urls:
        lines.append(f"**币圈热点** ({len(web3_urls)}条)")
        lines.extend(
            f"- [Crypto RSS] Web3 source candidate | 原文发布时间:{published_at} | 摘要:Web3 source summary | 链接:{url}"
            for url in web3_urls
        )
    return "\n".join(lines)


class CreatorDailyWorkflowTests(unittest.TestCase):
    def test_prompt_only_requests_ai_and_web3_hotspot_cards(self):
        prompt = (
            Path(__file__).resolve().parents[1]
            .joinpath("config", "creator_daily_prompt.txt")
            .read_text(encoding="utf-8")
        )

        self.assertIn("最多3条 AI 和最多3条 Web3 热点", prompt)
        self.assertIn("滚动24小时内", prompt)
        self.assertIn("可观察关注信号", prompt)
        self.assertIn("不能为了凑满6条降低时效或证据门槛", prompt)
        self.assertIn("简述：一句话", prompt)
        json_example = prompt.split("JSON 格式：\n", 1)[1].split(
            "\n\n只返回 JSON", 1
        )[0]
        self.assertEqual(
            set(json.loads(json_example)),
            {
                "core_trends",
                "sentiment_controversy",
                "signals",
                "rss_insights",
                "outlook_strategy",
            },
        )
        for unwanted in ("X中文稿：", "X英文稿：", "课程素材：", "适合账号："):
            self.assertNotIn(unwanted, prompt)

    def test_parser_keeps_numbered_howto_steps_inside_one_card(self):
        cards = _parse_creator_topic_cards(
            """AI选题（1/3）
1. 【实测】一个AI工具
事实：发布了新功能。
国内延展：可录屏演示。
操作步骤：
1. 打开设置。
2. 开启新功能。
X中文稿：可以试试这个变化。
原文：https://example.com/ai
2. 【观点】另一个事件
事实：发生了另一个变化。
原文：https://example.com/ai-2"""
        )

        self.assertEqual(len(cards), 2)
        self.assertIn("1. 打开设置。", cards[0]["body"])
        self.assertIn("2. 开启新功能。", cards[0]["body"])

    def test_creator_timestamp_gate_is_rolling_and_rejects_unknown_or_future(self):
        now = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)

        self.assertTrue(
            AIAnalyzer._creator_timestamp_is_within_24h(
                (now - timedelta(hours=24)).isoformat(), now
            )
        )
        self.assertFalse(
            AIAnalyzer._creator_timestamp_is_within_24h(
                (now - timedelta(hours=24, seconds=1)).isoformat(), now
            )
        )
        self.assertFalse(
            AIAnalyzer._creator_timestamp_is_within_24h(
                (now + timedelta(minutes=1)).isoformat(), now
            )
        )
        self.assertFalse(AIAnalyzer._creator_timestamp_is_within_24h("", now))
        self.assertFalse(
            AIAnalyzer._creator_timestamp_is_within_24h("yesterday", now)
        )
        self.assertEqual(AIAnalyzer._creator_timestamp_status("", now), "missing")
        self.assertEqual(
            AIAnalyzer._creator_timestamp_status("yesterday", now), "invalid"
        )
        self.assertEqual(
            AIAnalyzer._creator_timestamp_status(
                (now + timedelta(minutes=1)).isoformat(), now
            ),
            "future",
        )
        self.assertEqual(
            AIAnalyzer._creator_timestamp_status(
                (now - timedelta(hours=24, seconds=1)).isoformat(), now
            ),
            "older_than_24h",
        )

    def test_model_cards_must_match_fresh_rss_url_and_category(self):
        evidence = AIAnalyzer._creator_rss_evidence(
            _rss_candidate_context(
                ["https://example.com/ai"], ["https://example.com/web3"]
            )
        )
        section = (
            "AI热点（2/3）\n"
            "1. 【热点】AI事件\n简述：AI出现重要新进展。\n"
            "时间：错误时间\n来源：https://example.com/ai\n"
            "2. 【热点】无来源事件\n简述：未核验。\n来源：https://example.com/unknown"
        )

        clean, rejected = AIAnalyzer._validate_creator_topic_section(
            section, "AI", "ai", evidence
        )

        self.assertEqual(rejected, 1)
        self.assertIn("AI热点（1/3）", clean)
        self.assertIn("时间：2026-09-30T11:00:00+00:00", clean)
        self.assertIn("来源：https://example.com/ai", clean)
        self.assertNotIn("错误时间", clean)
        web3_clean, web3_rejected = AIAnalyzer._validate_creator_topic_section(
            section, "Web3", "crypto", evidence
        )
        self.assertEqual(AIAnalyzer._creator_card_count(web3_clean), 0)
        self.assertEqual(web3_rejected, 2)

    def test_hotlist_is_limited_and_rss_is_filtered_by_original_publication_time(self):
        analyzer = AIAnalyzer.__new__(AIAnalyzer)
        analyzer.analysis_config = {
            "PROMPT_FILE": "config/creator_daily_prompt.txt"
        }
        analyzer.max_news = 10
        analyzer.include_rss = True
        analyzer.include_rank_timeline = False
        analyzer.get_time_func = lambda: datetime(
            2026, 9, 30, 12, tzinfo=timezone.utc
        )
        analyzer._format_time_range = lambda first, last: f"{first}~{last}"

        hotlist = [
            {
                "title": f"Hot topic {index}",
                "first_time": "2026-09-30T10:00:00+00:00",
                "last_time": "2026-09-30T11:00:00+00:00",
                "count": 1,
            }
            for index in range(8)
        ]
        rss_titles = [
            {
                "title": "Fresh RSS story",
                "published_at": "2026-09-30T11:00:00+00:00",
                "time_display": "09-30 19:00",
                "url": "https://example.com/fresh",
            },
            {
                "title": "Stale RSS story",
                "published_at": "2026-09-28T11:00:00+00:00",
                "time_display": "09-28 19:00",
                "url": "https://example.com/stale",
            },
            {
                "title": "Undated RSS story",
                "time_display": "",
                "url": "https://example.com/undated",
            },
        ]

        prepared = analyzer._prepare_news_content(
            [{"word": "AI热榜", "titles": hotlist}],
            [{"word": "AI RSS", "titles": rss_titles}],
        )

        self.assertEqual(prepared.hotlist_analyzed, 6)
        self.assertEqual(prepared.rss_analyzed, 1)
        self.assertIn("上榜时间不等于发布时间", prepared.news_content)
        self.assertIn("原文发布时间:2026-09-30T11:00:00+00:00", prepared.rss_content)
        self.assertNotIn("Stale RSS story", prepared.rss_content)
        self.assertNotIn("Undated RSS story", prepared.rss_content)

    def test_rss_only_fallback_also_rejects_stale_and_undated_items(self):
        analyzer = AIAnalyzer.__new__(AIAnalyzer)
        analyzer.analysis_config = {
            "PROMPT_FILE": "config/creator_daily_prompt.txt"
        }
        analyzer.get_time_func = lambda: datetime(
            2026, 9, 30, 12, tzinfo=timezone.utc
        )
        result = analyzer._build_creator_rss_fallback(
            [],
            [
                {
                    "word": "AI热点",
                    "titles": [
                        {
                            "title": "Fresh AI release",
                            "published_at": "2026-09-30T11:00:00+00:00",
                            "url": "https://example.com/fresh",
                        },
                        {
                            "title": "Old AI release",
                            "published_at": "2026-09-28T11:00:00+00:00",
                            "url": "https://example.com/old",
                        },
                        {
                            "title": "Undated AI release",
                            "url": "https://example.com/undated",
                        },
                    ],
                }
            ],
            "daily",
        )

        self.assertIn("Fresh AI release", result.core_trends)
        self.assertNotIn("Old AI release", result.core_trends)
        self.assertNotIn("Undated AI release", result.core_trends)

    def test_hotlist_only_clues_do_not_generate_unverified_creator_cards(self):
        analyzer = AIAnalyzer.__new__(AIAnalyzer)
        analyzer.analysis_config = {
            "PROMPT_FILE": "config/creator_daily_prompt.txt",
            "RSS_ONLY_FALLBACK": False,
        }
        analyzer.ai_config = {"MODEL": "test-model", "TIMEOUT": 10, "MAX_TOKENS": 1000}
        analyzer.client = SimpleNamespace(api_key="test-key")
        analyzer.get_time_func = lambda: datetime(
            2026, 9, 30, 12, tzinfo=timezone.utc
        )
        analyzer.max_news = 10
        analyzer.include_rss = False
        analyzer.include_rank_timeline = False
        analyzer.include_standalone = False
        analyzer.language = "Chinese"
        analyzer.debug = False
        analyzer.system_prompt = ""
        analyzer.user_prompt_template = "{current_time}\n{news_content}\n{rss_content}"
        analyzer._prepare_news_content = Mock(
            return_value=PreparedNewsContent("news", "", 1, 0, 1, 1, 0)
        )
        analyzer._call_ai = Mock()

        result = analyzer.analyze([])

        self.assertTrue(result.success, result.error)
        self.assertTrue(result.skipped)
        self.assertEqual(AIAnalyzer._creator_card_count(result.core_trends), 0)
        self.assertEqual(AIAnalyzer._creator_card_count(result.sentiment_controversy), 0)
        analyzer._call_ai.assert_not_called()

    def test_retry_accepts_actual_partial_count_without_shortage_note(self):
        analyzer = AIAnalyzer.__new__(AIAnalyzer)
        analyzer.analysis_config = {
            "PROMPT_FILE": "config/creator_daily_prompt.txt",
            "RSS_ONLY_FALLBACK": False,
        }
        analyzer.ai_config = {"MODEL": "test-model", "TIMEOUT": 10, "MAX_TOKENS": 1000}
        analyzer.client = SimpleNamespace(api_key="test-key")
        analyzer.get_time_func = lambda: datetime(
            2026, 9, 30, 12, tzinfo=timezone.utc
        )
        analyzer.max_news = 10
        analyzer.include_rss = True
        analyzer.include_rank_timeline = False
        analyzer.include_standalone = False
        analyzer.language = "Chinese"
        analyzer.debug = False
        analyzer.system_prompt = ""
        analyzer.user_prompt_template = "{current_time}\n{news_content}\n{rss_content}"
        analyzer._prepare_news_content = Mock(
            return_value=PreparedNewsContent(
                "news",
                _rss_candidate_context(
                    ["https://example.com/ai-1", "https://example.com/ai-2"],
                    [
                        "https://example.com/w1",
                        "https://example.com/w2",
                        "https://example.com/w3",
                    ],
                ),
                1,
                5,
                6,
                1,
                5,
            )
        )

        def response(ai_section):
            return json.dumps(
                {
                    "core_trends": ai_section,
                    "sentiment_controversy": (
                        "Web3热点（3/3）\n"
                        "1. 【快讯】Web3一\n简述：安全公告更新。\n来源：https://example.com/w1\n"
                        "2. 【快讯】Web3二\n简述：新规则公布。\n来源：https://example.com/w2\n"
                        "3. 【快讯】Web3三\n简述：新提案发布。\n来源：https://example.com/w3"
                    ),
                    "signals": "",
                    "rss_insights": "",
                    "outlook_strategy": "",
                    "standalone_summaries": {},
                },
                ensure_ascii=False,
            )

        analyzer._call_ai = Mock(
            side_effect=[
                response(
                    "AI选题\n"
                    "1. 【实测】AI话题一\n简述：新功能上线。\n来源：https://example.com/ai-1\n"
                    "2. 【观点】AI话题二\n简述：论文公开。\n来源：https://example.com/ai-2"
                ),
                response(
                    "AI热点（2/3）\n"
                    "1. 【实测】AI话题一\n简述：新功能上线。\n来源：https://example.com/ai-1\n"
                    "2. 【观点】AI话题二\n简述：论文公开。\n来源：https://example.com/ai-2"
                ),
            ]
        )

        result = analyzer.analyze([])

        self.assertTrue(result.success, result.error)
        self.assertIn("AI热点（2/3）", result.core_trends)
        self.assertNotIn("缺额说明", result.core_trends)
        self.assertEqual(analyzer._call_ai.call_count, 2)

    @staticmethod
    def _accepted_response():
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"code": 200},
        )

    def test_partial_valid_cards_send_only_hotspot_messages(self):
        ai_cards = [
            {"post_type": "实测", "headline": f"AI {i}", "body": "内容"}
            for i in range(1, 3)
        ]
        crypto_cards = [
            {"post_type": "快讯", "headline": "Web3 1", "body": "内容"}
        ]

        with (
            patch(
                "trendradar.notification.senders.requests.post",
                return_value=self._accepted_response(),
            ) as post,
            patch("trendradar.notification.senders.time.sleep"),
        ):
            success = _send_creator_topics_to_bark(
                "https://bark.example/push",
                "test-key",
                None,
                ai_cards,
                crypto_cards,
                batch_interval=0,
            )

        self.assertTrue(success)
        self.assertEqual(post.call_count, 3)
        payloads = [call.kwargs["json"] for call in post.call_args_list]
        self.assertTrue(all("热点" in payload["title"] for payload in payloads))
        self.assertTrue(all(payload["markdown"] == "内容" for payload in payloads))
        self.assertFalse(any("缺额" in payload["title"] for payload in payloads))


if __name__ == "__main__":
    unittest.main()
