import json
import unittest

from trendradar.ai.analyzer import AIAnalyzer


class AIAnalyzerResponseTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = AIAnalyzer.__new__(AIAnalyzer)

    def test_parses_normal_report_json(self):
        result = self.analyzer._parse_response(
            json.dumps(
                {
                    "core_trends": "AI 选题（1/5）\n1. 新模型发布\n来源：https://example.com/ai",
                    "sentiment_controversy": "币圈选题（1/5）\n1. 协议更新\n来源：https://example.com/crypto",
                    "signals": "优先发布 AI 选题",
                    "rss_insights": "核验发布时间",
                    "outlook_strategy": "先发 AI，再发币圈",
                    "standalone_summaries": {},
                },
                ensure_ascii=False,
            )
        )

        self.assertTrue(result.success)
        self.assertIn("新模型发布", result.core_trends)
        self.assertIn("协议更新", result.sentiment_controversy)

    def test_unwraps_provider_result_object(self):
        result = self.analyzer._parse_response(
            json.dumps(
                {
                    "core_trends": "",
                    "sentiment_controversy": "",
                    "result": {
                        "core_trends": "AI 候选卡",
                        "sentiment_controversy": "币圈候选卡",
                    },
                },
                ensure_ascii=False,
            )
        )

        self.assertTrue(result.success)
        self.assertEqual(result.core_trends, "AI 候选卡")
        self.assertEqual(result.sentiment_controversy, "币圈候选卡")

    def test_rejects_empty_report_instead_of_marking_success(self):
        result = self.analyzer._parse_response(
            json.dumps(
                {
                    "core_trends": "",
                    "sentiment_controversy": "",
                    "signals": "",
                    "rss_insights": "",
                    "outlook_strategy": "",
                    "standalone_summaries": {},
                }
            )
        )

        self.assertFalse(result.success)
        self.assertIn("日报正文为空", result.error)

    def test_creator_daily_requires_both_topic_sections(self):
        self.analyzer.analysis_config = {
            "PROMPT_FILE": "config/creator_daily_prompt.txt"
        }
        result = self.analyzer._parse_response(
            json.dumps(
                {
                    "core_trends": "AI 选题（1/5）：候选内容",
                    "sentiment_controversy": "",
                    "signals": "今天先发 AI",
                    "rss_insights": "核验来源",
                    "outlook_strategy": "",
                    "standalone_summaries": {},
                },
                ensure_ascii=False,
            )
        )

        self.assertFalse(result.success)
        self.assertIn("币圈选题", result.error)


if __name__ == "__main__":
    unittest.main()
