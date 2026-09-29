import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from trendradar.ai.analyzer import AIAnalyzer, PreparedNewsContent
from trendradar.notification.senders import (
    _parse_creator_topic_cards,
    _send_creator_topics_to_bark,
)


class CreatorDailyWorkflowTests(unittest.TestCase):
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

        self.assertEqual(prepared.hotlist_analyzed, 2)
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

    def test_explicit_shortage_is_accepted_without_retrying_or_inventing_news(self):
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
        ai_section = (
            "AI选题（2/3；24小时内合格素材不足）\n"
            "1. 【实测】AI话题一\n事实：新功能上线。\n原文：https://example.com/ai-1\n"
            "2. 【观点】AI话题二\n事实：论文公开。\n原文：https://example.com/ai-2"
        )
        crypto_section = (
            "Web3选题（3/3）\n"
            "1. 【快讯】币圈话题一\n事实：新提案发布。\n原文：https://example.com/w3-1\n"
            "2. 【快讯】币圈话题二\n事实：新规则公布。\n原文：https://example.com/w3-2\n"
            "3. 【观点】币圈话题三\n事实：安全公告更新。\n原文：https://example.com/w3-3"
        )
        analyzer._call_ai = Mock(
            return_value=json.dumps(
                {
                    "core_trends": ai_section,
                    "sentiment_controversy": crypto_section,
                    "signals": "",
                    "rss_insights": "",
                    "outlook_strategy": "",
                    "standalone_summaries": {},
                },
                ensure_ascii=False,
            )
        )

        result = analyzer.analyze([])

        self.assertTrue(result.success, result.error)
        self.assertIn("AI话题一", result.core_trends)
        self.assertEqual(analyzer._call_ai.call_count, 1)

    def test_retry_shortage_is_merged_into_the_section_header(self):
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

        def response(ai_section):
            return json.dumps(
                {
                    "core_trends": ai_section,
                    "sentiment_controversy": (
                        "Web3选题（3/3）\n"
                        "1. 【快讯】Web3一\n原文：https://example.com/w1\n"
                        "2. 【快讯】Web3二\n原文：https://example.com/w2\n"
                        "3. 【快讯】Web3三\n原文：https://example.com/w3"
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
                    "1. 【实测】AI话题一\n原文：https://example.com/ai-1\n"
                    "2. 【观点】AI话题二\n原文：https://example.com/ai-2"
                ),
                response(
                    "AI选题（2/3；24小时内合格素材不足）\n"
                    "未发现第三条符合条件的来源。\n"
                    "1. 【实测】AI话题一\n原文：https://example.com/ai-1\n"
                    "2. 【观点】AI话题二\n原文：https://example.com/ai-2"
                ),
            ]
        )

        result = analyzer.analyze([])

        self.assertTrue(result.success, result.error)
        self.assertIn("AI选题（2/3；24小时内合格素材不足）", result.core_trends)
        self.assertEqual(analyzer._call_ai.call_count, 2)

    @staticmethod
    def _accepted_response():
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"code": 200},
        )

    def test_partial_valid_cards_are_sent_with_a_shortage_notice(self):
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
        self.assertEqual(post.call_count, 4)
        self.assertEqual(
            post.call_args.kwargs["json"]["title"], "今日选题有缺额"
        )
        self.assertIn("AI 2/3", post.call_args.kwargs["json"]["markdown"])
        self.assertIn("Web3/币圈 1/3", post.call_args.kwargs["json"]["markdown"])


if __name__ == "__main__":
    unittest.main()
