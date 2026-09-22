import json
import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from trendradar.ai.client import AIClient
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

    def test_parses_creator_daily_plain_text_sections(self):
        self.analyzer.analysis_config = {
            "PROMPT_FILE": "config/creator_daily_prompt.txt"
        }
        result = self.analyzer._parse_response(
            """【AI选题】
AI 选题（1/5）
1. 事件：新模型发布
推文草稿：关注它的实际用途。
来源：https://example.com/ai

【币圈选题】
币圈选题（1/5）
1. 事件：协议升级
推文草稿：升级会改变哪些用户体验？
来源：https://example.com/crypto

【今日优先发布】
先发布 AI 选题。

【信息核查】
核实发布时间。

【账号运营建议】
先发 AI，再发币圈。"""
        )

        self.assertTrue(result.success)
        self.assertIn("新模型发布", result.core_trends)
        self.assertIn("协议升级", result.sentiment_controversy)
        self.assertIn("先发布 AI", result.signals)

    def test_interleaves_creator_rss_groups_before_applying_context_limit(self):
        selected = AIAnalyzer._interleave_rss_groups(
            [
                {"word": "币圈热点", "titles": [{"title": f"crypto-{i}"} for i in range(4)]},
                {"word": "AI热点", "titles": [{"title": f"ai-{i}"} for i in range(4)]},
            ],
            5,
        )

        self.assertEqual(
            [item["title"] for item in selected[0]["titles"]],
            ["crypto-0", "crypto-1", "crypto-2"],
        )
        self.assertEqual(
            [item["title"] for item in selected[1]["titles"]],
            ["ai-0", "ai-1"],
        )

    def test_accepts_markdown_and_common_topic_heading_variants(self):
        self.analyzer.analysis_config = {
            "PROMPT_FILE": "config/creator_daily_prompt.txt"
        }
        result = self.analyzer._parse_response(
            """### **AI 热点（2/5）**
1. 一个 AI 事件

- 【加密货币选题】
1. 一个币圈事件"""
        )

        self.assertTrue(result.success)
        self.assertIn("AI 事件", result.core_trends)
        self.assertIn("币圈事件", result.sentiment_controversy)

    def test_accepts_chinese_numbered_section_headings(self):
        self.analyzer.analysis_config = {
            "PROMPT_FILE": "config/creator_daily_prompt.txt"
        }
        result = self.analyzer._parse_response(
            """第一部分：人工智能相关热点
1. 模型更新
第二部分：加密行业热点
1. 协议更新"""
        )

        self.assertTrue(result.success)
        self.assertIn("模型更新", result.core_trends)
        self.assertIn("协议更新", result.sentiment_controversy)


class AIClientParameterTests(unittest.TestCase):
    @patch("trendradar.ai.client.completion")
    def test_passes_configured_provider_parameters(self, completion):
        completion.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
        )
        client = AIClient(
            {
                "MODEL": "openai/example/model",
                "API_KEY": "test-key",
                "TEMPERATURE": 1.0,
                "EXTRA_PARAMS": {
                    "top_p": 0.95,
                    "extra_body": {"enable_thinking": False},
                },
            }
        )

        self.assertEqual(client.chat([{"role": "user", "content": "test"}]), "ok")
        self.assertEqual(completion.call_args.kwargs["top_p"], 0.95)
        self.assertEqual(
            completion.call_args.kwargs["extra_body"], {"enable_thinking": False}
        )
        self.assertEqual(completion.call_args.kwargs["temperature"], 1.0)

    @patch("trendradar.ai.client.completion")
    def test_extracts_only_final_suffix_from_reasoning_field(self, completion):
        completion.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="repetition",
                    message=SimpleNamespace(
                        content="",
                        reasoning_content="hidden reasoning</think>{\"result\":\"daily topics\"}",
                    ),
                )
            ]
        )
        client = AIClient({"MODEL": "openai/example/model"})

        answer = client.chat([{"role": "user", "content": "test"}])

        self.assertEqual(answer, '{"result":"daily topics"}')

    @patch("trendradar.ai.client.completion")
    def test_never_returns_reasoning_without_final_separator(self, completion):
        completion.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="repetition",
                    message=SimpleNamespace(
                        content=None,
                        reasoning_content="private reasoning, no final answer",
                    ),
                )
            ]
        )
        client = AIClient({"MODEL": "openai/example/model"})
        output = io.StringIO()

        with redirect_stdout(output):
            answer = client.chat([{"role": "user", "content": "test"}])

        self.assertEqual(answer, "")
        self.assertNotIn("private reasoning", output.getvalue())
        self.assertIn("final_separator=False", output.getvalue())

    @patch("trendradar.ai.client.completion")
    def test_strips_thinking_prefix_from_content(self, completion):
        completion.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        content="<think>private reasoning</think>{\"report\":\"ready\"}"
                    ),
                )
            ]
        )
        client = AIClient({"MODEL": "openai/example/model"})

        answer = client.chat([{"role": "user", "content": "test"}])

        self.assertEqual(answer, '{"report":"ready"}')


if __name__ == "__main__":
    unittest.main()
