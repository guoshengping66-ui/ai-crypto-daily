# coding=utf-8
"""
AI 分析器模块

调用 AI 大模型对热点新闻进行深度分析
基于 LiteLLM 统一接口，支持 100+ AI 提供商
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, NamedTuple, Optional
from urllib.parse import urlparse

from trendradar.ai.client import AIClient
from trendradar.ai.prompt_loader import load_prompt_template


@dataclass
class AIAnalysisResult:
    """AI 分析结果"""
    # 新版 5 核心板块
    core_trends: str = ""                # 核心热点与舆情态势
    sentiment_controversy: str = ""      # 舆论风向与争议
    signals: str = ""                    # 异动与弱信号
    rss_insights: str = ""               # RSS 深度洞察
    outlook_strategy: str = ""           # 研判与策略建议
    standalone_summaries: Dict[str, str] = field(default_factory=dict)  # 独立展示区概括 {源ID: 概括}

    # 基础元数据
    raw_response: str = ""               # 原始响应
    success: bool = False                # 是否成功
    skipped: bool = False                # 是否因无内容跳过（非失败）
    error: str = ""                      # 错误信息

    # 新闻数量统计
    total_news: int = 0                  # 总新闻数（热榜+RSS）
    analyzed_news: int = 0               # 实际分析的新闻数
    max_news_limit: int = 0              # 分析上限配置值
    hotlist_count: int = 0               # 热榜新闻数（总数）
    rss_count: int = 0                   # RSS 新闻数（总数）
    hotlist_analyzed: int = 0            # 热榜实际分析数
    rss_analyzed: int = 0               # RSS 实际分析数
    standalone_analyzed: int = 0        # 独立展示区实际分析数
    ai_mode: str = ""                    # AI 分析使用的模式 (daily/current/incremental)
    include_rss: bool = True             # 是否启用 RSS 分析
    include_standalone: bool = False     # 是否启用独立展示区分析
    fallback_used: bool = False          # 是否使用无模型 RSS 原始候选模式
    creator_delivered_urls: List[str] = field(default_factory=list)


class PreparedNewsContent(NamedTuple):
    news_content: str
    rss_content: str
    hotlist_total: int
    rss_total: int
    analyzed_count: int
    hotlist_analyzed: int
    rss_analyzed: int


class AIAnalyzer:
    """AI 分析器"""

    def __init__(
        self,
        ai_config: Dict[str, Any],
        analysis_config: Dict[str, Any],
        get_time_func: Callable,
        debug: bool = False,
    ):
        """
        初始化 AI 分析器

        Args:
            ai_config: AI 模型配置（LiteLLM 格式）
            analysis_config: AI 分析功能配置（language, prompt_file 等）
            get_time_func: 获取当前时间的函数
            debug: 是否开启调试模式
        """
        self.ai_config = ai_config
        self.analysis_config = analysis_config
        self.get_time_func = get_time_func
        self.debug = debug

        # 创建 AI 客户端（基于 LiteLLM）
        self.client = AIClient(ai_config)

        # 验证配置
        valid, error = self.client.validate_config()
        if not valid:
            print(f"[AI] 配置警告: {error}")

        # 从分析配置获取功能参数
        self.max_news = analysis_config.get("MAX_NEWS_FOR_ANALYSIS", 50)
        self.include_rss = analysis_config.get("INCLUDE_RSS", True)
        self.include_rank_timeline = analysis_config.get("INCLUDE_RANK_TIMELINE", False)
        self.include_standalone = analysis_config.get("INCLUDE_STANDALONE", False)
        self.language = analysis_config.get("LANGUAGE", "Chinese")

        # 加载提示词模板
        self.system_prompt, self.user_prompt_template = load_prompt_template(
            analysis_config.get("PROMPT_FILE", "ai_analysis_prompt.txt"),
            label="AI",
        )

    def analyze(
        self,
        stats: List[Dict],
        rss_stats: Optional[List[Dict]] = None,
        report_mode: str = "daily",
        report_type: str = "当日汇总",
        platforms: Optional[List[str]] = None,
        keywords: Optional[List[str]] = None,
        standalone_data: Optional[Dict] = None,
    ) -> AIAnalysisResult:
        """
        执行 AI 分析

        Args:
            stats: 热榜统计数据
            rss_stats: RSS 统计数据
            report_mode: 报告模式
            report_type: 报告类型
            platforms: 平台列表
            keywords: 关键词列表

        Returns:
            AIAnalysisResult: 分析结果
        """

        if (
            self.analysis_config.get("RSS_ONLY_FALLBACK", False)
            and self._is_creator_daily_prompt()
        ):
            print("[AI] 使用 RSS 免费兜底模式：不调用 AI 模型")
            return self._build_creator_rss_fallback(stats, rss_stats, report_mode)

        # 打印配置信息方便调试
        model = self.ai_config.get("MODEL", "unknown")
        api_key = self.client.api_key or ""
        api_base = self.ai_config.get("API_BASE", "")
        masked_key = f"{api_key[:5]}******" if len(api_key) >= 5 else "******"
        model_display = model.replace("/", "/\u200b") if model else "unknown"

        print(f"[AI] 模型: {model_display}")
        print(f"[AI] Key : {masked_key}")

        if api_base:
            print(f"[AI] 接口: 存在自定义 API 端点")

        timeout = self.ai_config.get("TIMEOUT", 120)
        max_tokens = self.ai_config.get("MAX_TOKENS", 5000)
        print(f"[AI] 参数: timeout={timeout}, max_tokens={max_tokens}")

        if not self.client.api_key:
            return AIAnalysisResult(
                success=False,
                error="未配置 AI API Key，请在 config.yaml 或环境变量 AI_API_KEY 中设置"
            )

        # 准备新闻内容并获取统计数据
        prepared = self._prepare_news_content(stats, rss_stats)
        total_news = prepared.hotlist_total + prepared.rss_total

        if not prepared.news_content and not prepared.rss_content:
            return AIAnalysisResult(
                success=False,
                skipped=True,
                error="本轮无新增热点内容，跳过 AI 分析",
                total_news=total_news,
                hotlist_count=prepared.hotlist_total,
                rss_count=prepared.rss_total,
                analyzed_news=0,
                max_news_limit=self.max_news
            )

        if self._is_creator_daily_prompt() and not prepared.rss_content:
            print("[AI] 严格24小时筛选后没有带原文时间和链接的 RSS 候选，跳过生成，避免从热榜线索编造热点")
            return AIAnalysisResult(
                core_trends="AI热点（0/3）",
                sentiment_controversy="Web3热点（0/3）",
                success=True,
                skipped=True,
                error="严格24小时筛选后没有可核验的 RSS 候选",
                total_news=total_news,
                hotlist_count=prepared.hotlist_total,
                rss_count=prepared.rss_total,
                analyzed_news=prepared.analyzed_count,
                max_news_limit=self.max_news,
                hotlist_analyzed=prepared.hotlist_analyzed,
                rss_analyzed=prepared.rss_analyzed,
                include_rss=self.include_rss,
                include_standalone=self.include_standalone,
            )

        # 构建提示词
        current_time = self.get_time_func().strftime("%Y-%m-%d %H:%M:%S")

        # 提取关键词
        if not keywords:
            keywords = [s.get("word", "") for s in stats if s.get("word")] if stats else []

        # 使用安全的字符串替换，避免模板中其他花括号（如 JSON 示例）被误解析
        user_prompt = self.user_prompt_template
        user_prompt = user_prompt.replace("{report_mode}", report_mode)
        user_prompt = user_prompt.replace("{report_type}", report_type)
        user_prompt = user_prompt.replace("{current_time}", current_time)
        user_prompt = user_prompt.replace("{news_count}", str(prepared.hotlist_total))
        user_prompt = user_prompt.replace("{rss_count}", str(prepared.rss_total))
        user_prompt = user_prompt.replace("{platforms}", ", ".join(platforms) if platforms else "多平台")
        user_prompt = user_prompt.replace("{keywords}", ", ".join(keywords[:20]) if keywords else "无")
        user_prompt = user_prompt.replace("{news_content}", prepared.news_content)
        user_prompt = user_prompt.replace("{rss_content}", prepared.rss_content)
        user_prompt = user_prompt.replace("{language}", self.language)

        # 构建独立展示区内容
        standalone_content = ""
        standalone_count = 0
        if self.include_standalone and standalone_data:
            standalone_content, standalone_count = self._prepare_standalone_content(standalone_data)
        user_prompt = user_prompt.replace("{standalone_content}", standalone_content)

        if self.debug:
            print("\n" + "=" * 80)
            print("[AI 调试] 发送给 AI 的完整提示词")
            print("=" * 80)
            if self.system_prompt:
                print("\n--- System Prompt ---")
                print(self.system_prompt)
            print("\n--- User Prompt ---")
            print(user_prompt)
            print("=" * 80 + "\n")

        # 调用 AI API（使用 LiteLLM）
        try:
            response = self._call_ai(user_prompt)
            result = self._parse_response(response)

            # JSON 解析失败时的重试兜底（仅重试一次）
            if result.error and "JSON 解析错误" in result.error:
                print(f"[AI] JSON 解析失败，尝试让 AI 修复...")
                retry_result = self._retry_fix_json(response, result.error)
                if retry_result and retry_result.success and not retry_result.error:
                    print("[AI] JSON 修复成功")
                    retry_result.raw_response = response
                    result = retry_result
                else:
                    print("[AI] JSON 修复失败，拒绝将损坏内容当作日报发送")
                    result.success = False
                    result.core_trends = ""
                    result.error = "AI 返回内容不是有效日报 JSON，自动修复也未成功；请重试或检查模型兼容性"

            # 创作者日报：允许真实的不足3条结果，避免为凑数反复要求模型编新闻。
            if self._is_creator_daily_prompt():
                required_creator_fields = {
                    "core_trends": "AI 热点",
                    "sentiment_controversy": "Web3 热点",
                }

                def creator_card_count(section: str) -> int:
                    return len(
                        re.findall(
                            r"(?m)^[ \t]*\d+[.)、][ \t]*(?:\*\*)?【", section
                        )
                    )

                def declares_creator_partial(section: str) -> bool:
                    lines = str(section or "").splitlines()
                    if not lines:
                        return False
                    declared_count = re.search(r"\b([0-2])\s*/\s*3\b", lines[0])
                    return bool(
                        declared_count
                        and int(declared_count.group(1)) == creator_card_count(section)
                    )

                def merge_creator_retry(field: str, retry_value: str) -> None:
                    retry_value = str(retry_value or "").strip()
                    if not retry_value:
                        return
                    current = str(getattr(result, field, "") or "").strip()
                    current_count = creator_card_count(current)
                    retry_count = creator_card_count(retry_value)
                    if retry_count > current_count or (
                        retry_count == current_count
                        and declares_creator_partial(retry_value)
                    ):
                        setattr(result, field, retry_value)
                    elif (
                        current_count < 3
                        and declares_creator_partial(retry_value)
                        and not declares_creator_partial(current)
                    ):
                        label = "AI热点" if field == "core_trends" else "Web3热点"
                        lines = current.splitlines()
                        partial_header = f"{label}（{current_count}/3）"
                        if lines:
                            lines[0] = partial_header
                        else:
                            lines.append(partial_header)
                        setattr(result, field, "\n".join(lines).strip())

                creator_card_counts = {
                    field: creator_card_count(str(getattr(result, field, "") or ""))
                    for field in required_creator_fields
                }
                missing_creator_fields = [
                    field
                    for field in required_creator_fields
                    if creator_card_counts[field] < 3
                    and not declares_creator_partial(
                        str(getattr(result, field, "") or "")
                    )
                ]
                if missing_creator_fields:
                    missing_labels = [
                        f"{required_creator_fields[field]}（{creator_card_counts[field]}/3）"
                        for field in missing_creator_fields
                    ]
                    print(
                        "[AI] 创作者日报缺少必需分区，尝试定向补全："
                        + "、".join(missing_labels)
                    )
                    retry_schema = json.dumps(
                        {field: "请返回简洁热点卡片" for field in missing_creator_fields},
                        ensure_ascii=False,
                    )
                    retry_prompt = (
                        user_prompt
                        + "\n\n【补全缺失分区】上一轮没有完整生成必需分区："
                        + "、".join(missing_labels)
                        + "。请根据上方同一批候选材料，只返回一个有效 JSON 对象，"
                        + "且字段名必须与下列示例完全一致，不要省略字段：\n"
                        + retry_schema
                        + "\n只按主提示词筛选并输出热点卡片，不写文章、帖子或运营建议。"
                        + "每个所需分区最多3条，符合条件有几条就写几条；标题数量必须与卡片数一致。"
                        + "只使用滚动24小时内有明确原文发布时间和来源URL的候选，不得编造或用旧闻补数。"
                    )
                    retry_response = self._call_ai(retry_prompt)
                    retry_result = self._parse_response(retry_response)
                    for field in missing_creator_fields:
                        merge_creator_retry(
                            field, getattr(retry_result, field, "")
                        )

                    still_missing_fields = []
                    for field in missing_creator_fields:
                        section = str(getattr(result, field, "") or "")
                        count = creator_card_count(section)
                        if count < 3 and not declares_creator_partial(section):
                            still_missing_fields.append(field)

                    if still_missing_fields:
                        retry_labels = []
                        for field in still_missing_fields:
                            section = str(getattr(result, field, "") or "")
                            count = creator_card_count(section)
                            retry_labels.append(
                                f"{required_creator_fields[field]}（{count}/3）"
                            )
                        completion_schema = json.dumps(
                            {
                                field: "请仅返回符合条件的热点；不足3条就按实际数量"
                                for field in still_missing_fields
                            },
                            ensure_ascii=False,
                        )
                        completion_prompt = (
                            user_prompt
                            + "\n\n【再次定向补全】以下分区仍不足3条："
                            + "、".join(retry_labels)
                            + "。请只返回以下字段的有效JSON对象；每个字段最多3条，符合条件有几条就写几条，"
                            + "标题数量必须与卡片数一致。只用滚动24小时内有明确原文时间与来源URL的候选，"
                            + "不得编造或用旧闻补数：\n"
                            + completion_schema
                        )
                        completion_result = self._parse_response(
                            self._call_ai(completion_prompt)
                        )
                        for field in still_missing_fields:
                            merge_creator_retry(
                                field, getattr(completion_result, field, "")
                            )

                    remaining_fields = []
                    for field in missing_creator_fields:
                        section = str(getattr(result, field, "") or "")
                        count = creator_card_count(section)
                        if count < 3 and not declares_creator_partial(section):
                            remaining_fields.append(
                                f"{required_creator_fields[field]}（{count}/3）"
                            )
                    if remaining_fields:
                        result.success = False
                        result.error = (
                            "AI 日报缺少必需分区，自动补全后仍缺少："
                            + "、".join(remaining_fields)
                        )
                        print(f"[AI] 定向补全失败：{'、'.join(remaining_fields)}")
                    else:
                        result.success = True
                        result.error = ""
                        if any(
                            creator_card_count(str(getattr(result, field, "") or "")) < 3
                            for field in required_creator_fields
                        ):
                            print("[AI] 创作者日报按24小时规则保留实际合格热点")
                        else:
                            print("[AI] 创作者日报缺失分区补全成功")

                allowed_candidates = self._creator_rss_evidence(prepared.rss_content)
                result.core_trends, rejected_ai = self._validate_creator_topic_section(
                    result.core_trends, "AI", "ai", allowed_candidates
                )
                result.sentiment_controversy, rejected_web3 = self._validate_creator_topic_section(
                    result.sentiment_controversy, "Web3", "crypto", allowed_candidates
                )
                rejected_total = rejected_ai + rejected_web3
                if rejected_total:
                    print(
                        "[AI] 日报来源校验：剔除 "
                        f"{rejected_total} 条未匹配到本轮24小时 RSS 原文的模型卡片"
                    )
                print(
                    "[AI] 日报来源校验后保留："
                    f"AI={self._creator_card_count(result.core_trends)}，"
                    f"Web3={self._creator_card_count(result.sentiment_controversy)}"
                )

            # 如果配置未启用 RSS 分析，强制清空 AI 返回的 RSS 洞察
            if not self.include_rss:
                result.rss_insights = ""

            # 如果配置未启用 standalone 分析，强制清空
            if not self.include_standalone:
                result.standalone_summaries = {}

            if result.success and self._is_creator_daily_prompt():
                print(
                    "[AI] 日报正文长度: "
                    f"AI={len(result.core_trends)}，"
                    f"币圈={len(result.sentiment_controversy)}，"
                    f"优先发布={len(result.signals)}"
                )

            # 填充统计数据
            result.total_news = total_news
            result.hotlist_count = prepared.hotlist_total
            result.rss_count = prepared.rss_total
            result.analyzed_news = prepared.analyzed_count
            result.hotlist_analyzed = prepared.hotlist_analyzed
            result.rss_analyzed = prepared.rss_analyzed
            result.standalone_analyzed = standalone_count
            result.max_news_limit = self.max_news
            result.include_rss = self.include_rss
            result.include_standalone = self.include_standalone
            return result
        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)

            # 截断过长的错误消息
            if len(error_msg) > 200:
                error_msg = error_msg[:200] + "..."
            friendly_msg = f"AI 分析失败 ({error_type}): {error_msg}"

            return AIAnalysisResult(
                success=False,
                error=friendly_msg
            )

    def _prepare_news_content(
        self,
        stats: List[Dict],
        rss_stats: Optional[List[Dict]] = None,
    ) -> PreparedNewsContent:
        news_lines = []
        rss_lines = []
        news_count = 0
        rss_count = 0
        creator_daily = self._is_creator_daily_prompt()

        if creator_daily and rss_stats:
            current_time = self.get_time_func()
            fresh_stats = []
            rejected_rss = {"missing": 0, "invalid": 0, "future": 0, "older_than_24h": 0}
            for stat in rss_stats:
                fresh_titles = []
                for item in stat.get("titles", []):
                    published_at = item.get("published_at") if isinstance(item, dict) else None
                    status = self._creator_timestamp_status(published_at, current_time)
                    if status == "fresh":
                        fresh_titles.append(item)
                    else:
                        rejected_rss[status] += 1
                if fresh_titles:
                    fresh_stats.append(
                        {**stat, "titles": fresh_titles, "count": len(fresh_titles)}
                    )
            rss_stats = fresh_stats
            rejected_total = sum(rejected_rss.values())
            passed_total = sum(len(stat.get("titles", [])) for stat in fresh_stats)
            print(
                "[AI] 创作者日报24小时校验："
                f"通过 {passed_total} 条；剔除 {rejected_total} 条 "
                f"（缺时间 {rejected_rss['missing']}、格式无效 {rejected_rss['invalid']}、"
                f"超过24小时 {rejected_rss['older_than_24h']}、未来时间 {rejected_rss['future']}）"
            )

        # 计算总新闻数
        hotlist_total = sum(len(s.get("titles", [])) for s in stats) if stats else 0
        rss_total = sum(len(s.get("titles", [])) for s in rss_stats) if rss_stats else 0

        # 热榜内容
        if stats:
            creator_hotlist_limit = (
                min(12, max(6, self.max_news // 5))
                if creator_daily and self.max_news > 0
                else 12 if creator_daily else self.max_news
            )
            for stat in stats:
                if creator_daily and news_count >= creator_hotlist_limit:
                    break
                word = stat.get("word", "")
                titles = stat.get("titles", [])
                if word and titles:
                    section_note = (
                        "热榜讨论线索（上榜时间不等于发布时间）"
                        if creator_daily
                        else word
                    )
                    news_lines.append(f"\n**{section_note}** ({len(titles)}条)")
                    for t in titles:
                        if creator_daily and news_count >= creator_hotlist_limit:
                            break
                        if not isinstance(t, dict):
                            continue
                        title = t.get("title", "")
                        if not title:
                            continue

                        # 来源
                        source = t.get("source_name", t.get("source", ""))

                        # 构建行
                        if source:
                            line = f"- [{source}] {title}"
                        else:
                            line = f"- {title}"

                        # 始终显示简化格式：排名范围 + 时间范围 + 出现次数
                        ranks = t.get("ranks", [])
                        if ranks:
                            min_rank = min(ranks)
                            max_rank = max(ranks)
                            rank_str = f"{min_rank}" if min_rank == max_rank else f"{min_rank}-{max_rank}"
                        else:
                            rank_str = "-"

                        first_time = t.get("first_time", "")
                        last_time = t.get("last_time", "")
                        time_str = self._format_time_range(first_time, last_time)

                        appear_count = t.get("count", 1)

                        time_label = "上榜观察时间" if creator_daily else "时间"
                        line += f" | 排名:{rank_str} | {time_label}:{time_str} | 出现:{appear_count}次"

                        # 开启完整时间线时，额外添加轨迹
                        if self.include_rank_timeline:
                            rank_timeline = t.get("rank_timeline", [])
                            timeline_str = self._format_rank_timeline(rank_timeline)
                            line += f" | 轨迹:{timeline_str}"

                        source_url = t.get("url") or t.get("mobile_url")
                        if source_url:
                            line += f" | 链接:{source_url}"

                        news_lines.append(line)

                        news_count += 1
                        if not creator_daily and news_count >= self.max_news:
                            break
                if not creator_daily and news_count >= self.max_news:
                    break

        # RSS 内容（仅在启用时构建）
        if self.include_rss and rss_stats:
            creator_hotlist_budget = (
                min(12, max(6, self.max_news // 5))
                if creator_daily and self.max_news > 0
                else 12
            )
            remaining = (
                max(12, self.max_news - creator_hotlist_budget)
                if creator_daily
                else self.max_news - news_count
            )
            if creator_daily:
                # RSS groups are ordered by config priority. Round-robin
                # selection prevents the first domain from consuming the
                # entire creator-report context limit.
                rss_stats = self._interleave_rss_groups(rss_stats, remaining)
            for stat in rss_stats:
                if rss_count >= remaining:
                    break
                word = stat.get("word", "")
                titles = stat.get("titles", [])
                if word and titles:
                    rss_lines.append(f"\n**{word}** ({len(titles)}条)")
                    for t in titles:
                        if not isinstance(t, dict):
                            continue
                        title = t.get("title", "")
                        if not title:
                            continue

                        # 来源
                        source = t.get("source_name", t.get("feed_name", ""))

                        # 发布时间
                        time_display = t.get("time_display", "")
                        published_at = str(t.get("published_at") or "").strip()

                        # 构建行：[来源] 标题 | 发布时间
                        if source:
                            line = f"- [{source}] {title}"
                        else:
                            line = f"- {title}"
                        if creator_daily:
                            line += f" | 原文发布时间:{published_at}"
                            if t.get("event_id"):
                                line += f" | 事件ID:{t['event_id']} | 候选分:{t.get('score', 0)}"
                            if t.get("attention_evidence"):
                                line += f" | 关注依据:{t['attention_evidence']}"
                        elif time_display:
                            line += f" | {time_display}"
                        summary = str(t.get("summary") or "").strip()
                        if summary:
                            summary_limit = 240 if creator_daily else 320
                            line += f" | 摘要:{summary[:summary_limit]}"
                        source_url = t.get("url")
                        if source_url:
                            line += f" | 链接:{source_url}"
                        rss_lines.append(line)

                        rss_count += 1
                        if rss_count >= remaining:
                            break

        news_content = "\n".join(news_lines) if news_lines else ""
        rss_content = "\n".join(rss_lines) if rss_lines else ""
        total_count = news_count + rss_count

        return PreparedNewsContent(
            news_content=news_content,
            rss_content=rss_content,
            hotlist_total=hotlist_total,
            rss_total=rss_total,
            analyzed_count=total_count,
            hotlist_analyzed=news_count,
            rss_analyzed=rss_count,
        )

    @staticmethod
    def _creator_timestamp_status(value: Any, now: datetime) -> str:
        """Classify source timestamps for the creator digest's rolling 24-hour gate."""
        raw_value = str(value or "").strip()
        if not raw_value:
            return "missing"
        try:
            published_at = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
        except ValueError:
            return "invalid"

        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        age = now.astimezone(timezone.utc) - published_at.astimezone(timezone.utc)
        if age < timedelta(0):
            return "future"
        if age > timedelta(hours=24):
            return "older_than_24h"
        return "fresh"

    @staticmethod
    def _creator_timestamp_is_within_24h(value: Any, now: datetime) -> bool:
        """Creator candidates require a parseable original timestamp in the rolling window."""
        return AIAnalyzer._creator_timestamp_status(value, now) == "fresh"

    @staticmethod
    def _creator_card_count(section: str) -> int:
        return len(re.findall(r"(?m)^[ \t]*\d+[.)、][ \t]*(?:\*\*)?【", section or ""))

    @staticmethod
    def _creator_rss_evidence(rss_content: str) -> Dict[str, Dict[str, str]]:
        """Build an allowlist from the exact fresh RSS items shown to the model."""
        evidence = {}
        group_name = ""
        for line in (rss_content or "").splitlines():
            group_match = re.match(r"^\*\*(.+?)\*\*\s*\(\d+条\)", line.strip())
            if group_match:
                group_name = group_match.group(1).strip()
                continue

            if not line.lstrip().startswith("-"):
                continue
            time_match = re.search(r"\|\s*原文发布时间:([^|]+)", line)
            url_match = re.search(r"\|\s*链接:(https?://\S+)", line)
            if not time_match or not url_match:
                continue

            published_at = time_match.group(1).strip()
            url = url_match.group(1).strip().rstrip(".,;，。；)")
            parsed_url = urlparse(url)
            if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
                continue

            title_part = line.split("| 原文发布时间:", 1)[0].strip()
            title_part = re.sub(r"^-\s*\[[^]]+\]\s*", "", title_part)
            title = title_part.strip()
            category = AIAnalyzer._rss_fallback_category(group_name, title)
            if not category:
                continue

            evidence[url] = {
                "published_at": published_at,
                "category": category,
                "event_id": (re.search(r"\|\s*事件ID:([^|]+)", line).group(1).strip() if re.search(r"\|\s*事件ID:([^|]+)", line) else url),
            }
        return evidence

    @staticmethod
    def _validate_creator_topic_section(
        section: str,
        label: str,
        category: str,
        allowed_candidates: Dict[str, Dict[str, str]],
    ) -> tuple[str, int]:
        """Keep only model cards linked to eligible, in-prompt RSS candidates."""
        section = str(section or "")
        start_pattern = re.compile(
            r"(?m)^[ \t]*\d+[.)、][ \t]*(?:\*\*)?【(?P<kind>[^】]*)】(?P<title>[^\r\n]*)"
        )
        matches = list(start_pattern.finditer(section or ""))
        accepted = []
        rejected = 0
        seen_urls = set()
        seen_events = set()

        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
            block = section[match.start():end]
            source_match = re.search(
                r"(?m)^\s*(?:来源|原文|链接)\s*[：:]\s*(https?://[^\s|<>]+)",
                block,
            )
            summary_match = re.search(
                r"(?m)^\s*简述(?:（[^）]*）)?\s*[：:]\s*(.+?)\s*$", block
            )
            title = re.sub(r"\*+\s*$", "", match.group("title")).strip()
            if not source_match or not summary_match or not title:
                rejected += 1
                continue

            url = source_match.group(1).rstrip(".,;，。；)")
            candidate = allowed_candidates.get(url)
            if (
                not candidate
                or candidate.get("category") != category
                or url in seen_urls
                or candidate.get("event_id", url) in seen_events
                or len(accepted) >= 3
            ):
                rejected += 1
                continue

            summary = re.sub(r"\s+", " ", summary_match.group(1)).strip()
            if not summary:
                rejected += 1
                continue

            seen_urls.add(url)
            seen_events.add(candidate.get("event_id", url))
            accepted.append(
                {
                    "title": title,
                    "summary": summary,
                    "published_at": candidate["published_at"],
                    "url": url,
                }
            )

        lines = [f"{label}热点（{len(accepted)}/3）"]
        for index, card in enumerate(accepted, 1):
            lines.extend(
                [
                    f"{index}. 【热点】{card['title']}",
                    f"简述：{card['summary']}",
                    f"时间：{card['published_at']}",
                    f"来源：{card['url']}",
                ]
            )
        return "\n".join(lines), rejected

    @staticmethod
    def _interleave_rss_groups(
        rss_stats: List[Dict], limit: int
    ) -> List[Dict]:
        """Select RSS candidates round-robin across configured topic groups."""
        if limit <= 0:
            return []

        selected = [[] for _ in rss_stats]
        total = 0
        while total < limit:
            added = False
            for index, stat in enumerate(rss_stats):
                titles = stat.get("titles", [])
                if len(selected[index]) < len(titles):
                    selected[index].append(titles[len(selected[index])])
                    total += 1
                    added = True
                    if total >= limit:
                        break
            if not added:
                break

        return [
            {**stat, "titles": selected[index]}
            for index, stat in enumerate(rss_stats)
            if selected[index]
        ]

    @staticmethod
    def _rss_fallback_category(group_name: str, title: str) -> str:
        """Return the creator RSS category, preferring its configured group."""
        group = str(group_name or "").casefold()
        if any(token in group for token in ("币圈", "加密", "crypto", "bitcoin", "web3")):
            return "crypto"
        if any(token in group for token in ("ai", "人工智能", "大模型")):
            return "ai"

        text = str(title or "").casefold()
        crypto_pattern = r"\bcrypto\b|\bbitcoin\b|\bbtc\b|\beth\b|\bethereum\b|比特币|以太坊|加密货币|稳定币|区块链|web3|defi|代币"
        ai_pattern = r"\bai\b|\bllm\b|\bopenai\b|\bchatgpt\b|\bclaude\b|\bgemini\b|人工智能|大模型|智能体|机器人"
        if re.search(crypto_pattern, text, re.IGNORECASE):
            return "crypto"
        if re.search(ai_pattern, text, re.IGNORECASE):
            return "ai"
        return ""

    @staticmethod
    def _clean_rss_fallback_text(value: Any, limit: int = 360) -> str:
        """Flatten publisher-controlled text before placing it in the digest."""
        text = re.sub(r"<[^>]*>", " ", str(value or ""))
        text = re.sub(r"\s+", " ", text).strip()
        return text[:limit] + ("…" if len(text) > limit else "")

    def _build_creator_rss_fallback(
        self,
        stats: Optional[List[Dict]],
        rss_stats: Optional[List[Dict]],
        report_mode: str,
    ) -> AIAnalysisResult:
        """Build source-linked RSS candidate cards without calling a model."""
        selected = {"ai": [], "crypto": []}
        seen_urls = set()
        seen_titles = set()
        current_time = getattr(
            self, "get_time_func", lambda: datetime.now(timezone.utc)
        )()

        for stat in rss_stats or []:
            group_name = stat.get("word", "")
            for item in stat.get("titles", []):
                if not isinstance(item, dict):
                    continue
                published_at = item.get("published_at")
                if not self._creator_timestamp_is_within_24h(
                    published_at, current_time
                ):
                    continue
                title = self._clean_rss_fallback_text(item.get("title"), 240)
                url = str(item.get("url") or "").strip()
                parsed_url = urlparse(url)
                if not title or parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
                    continue

                category = self._rss_fallback_category(group_name, title)
                if not category or len(selected[category]) >= 3:
                    continue

                normalized_url = url.rstrip("/").casefold()
                normalized_title = title.casefold()
                if normalized_url in seen_urls or normalized_title in seen_titles:
                    continue
                seen_urls.add(normalized_url)
                seen_titles.add(normalized_title)

                selected[category].append(
                    {
                        "title": title,
                        "source": self._clean_rss_fallback_text(
                            item.get("source_name") or item.get("feed_name") or "RSS 来源", 100
                        ),
                        "time": self._clean_rss_fallback_text(item.get("time_display"), 80)
                        or self._clean_rss_fallback_text(published_at, 80),
                        "summary": self._clean_rss_fallback_text(item.get("summary"), 200)
                        or "RSS 未提供摘要；请打开原文核实。",
                        "url": url,
                    }
                )

        def render_cards(category: str, label: str) -> str:
            cards = selected[category]
            lines = [f"{label}热点（{len(cards)}/3）"]
            for index, card in enumerate(cards, 1):
                lines.extend(
                    [
                        f"{index}. 【热点】{card['title']}",
                        f"简述（RSS摘要）：{card['summary']}",
                        f"时间：{card['time']}",
                        f"来源：{card['source']}｜{card['url']}",
                    ]
                )
            return "\n".join(lines)

        ai_count = len(selected["ai"])
        crypto_count = len(selected["crypto"])
        rss_count = sum(len(stat.get("titles", [])) for stat in (rss_stats or []))
        hotlist_count = sum(len(stat.get("titles", [])) for stat in (stats or []))
        analyzed_count = ai_count + crypto_count
        return AIAnalysisResult(
            core_trends=render_cards("ai", "AI"),
            sentiment_controversy=render_cards("crypto", "Web3"),
            signals="",
            rss_insights="",
            outlook_strategy="",
            success=True,
            total_news=hotlist_count + rss_count,
            analyzed_news=analyzed_count,
            max_news_limit=10,
            hotlist_count=hotlist_count,
            rss_count=rss_count,
            hotlist_analyzed=0,
            rss_analyzed=analyzed_count,
            ai_mode=report_mode,
            include_rss=True,
            fallback_used=True,
        )

    def _call_ai(self, user_prompt: str) -> str:
        """调用 AI API（使用 LiteLLM）"""
        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        return self.client.chat(messages)

    def _retry_fix_json(self, original_response: str, error_msg: str) -> Optional[AIAnalysisResult]:
        """
        JSON 解析失败时，请求 AI 修复 JSON（仅重试一次）

        使用轻量 prompt，不重复原始分析的 system prompt，节省 token。

        Args:
            original_response: AI 原始响应（JSON 格式有误）
            error_msg: JSON 解析的错误信息

        Returns:
            修复后的分析结果，失败时返回 None
        """
        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个 JSON 修复助手。用户会提供一段格式有误的 JSON 和错误信息，"
                    "你需要修复 JSON 格式错误并返回正确的 JSON。\n"
                    "常见问题：字符串值内的双引号未转义、缺少逗号、字符串未正确闭合等。\n"
                    "只返回纯 JSON，不要包含 markdown 代码块标记（如 ```json）或任何说明文字。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"以下 JSON 解析失败：\n\n"
                    f"错误：{error_msg}\n\n"
                    f"原始内容：\n{original_response}\n\n"
                    f"请修复以上 JSON 中的格式问题（如值中的双引号改用中文引号「」或转义 \\\"、"
                    f"缺少逗号、不完整的字符串等），保持原始内容语义不变，只修复格式。"
                    f"直接返回修复后的纯 JSON。"
                ),
            },
        ]

        try:
            response = self.client.chat(messages)
            return self._parse_response(response)
        except Exception as e:
            print(f"[AI] 重试修复 JSON 异常: {type(e).__name__}: {e}")
            return None

    def _format_time_range(self, first_time: str, last_time: str) -> str:
        """格式化时间范围（简化显示，只保留时分）"""
        def extract_time(time_str: str) -> str:
            if not time_str:
                return "-"
            # 尝试提取 HH:MM 部分
            if " " in time_str:
                parts = time_str.split(" ")
                if len(parts) >= 2:
                    time_part = parts[1]
                    if ":" in time_part:
                        return time_part[:5]  # HH:MM
            elif ":" in time_str:
                return time_str[:5]
            # 处理 HH-MM 格式
            result = time_str[:5] if len(time_str) >= 5 else time_str
            if len(result) == 5 and result[2] == '-':
                result = result.replace('-', ':')
            return result

        first = extract_time(first_time)
        last = extract_time(last_time)

        if first == last or last == "-":
            return first
        return f"{first}~{last}"

    def _format_rank_timeline(self, rank_timeline: List[Dict]) -> str:
        """格式化排名时间线"""
        if not rank_timeline:
            return "-"

        parts = []
        for item in rank_timeline:
            time_str = item.get("time", "")
            if len(time_str) == 5 and time_str[2] == '-':
                time_str = time_str.replace('-', ':')
            rank = item.get("rank")
            if rank is None:
                parts.append(f"0({time_str})")
            else:
                parts.append(f"{rank}({time_str})")

        return "→".join(parts)

    def _prepare_standalone_content(self, standalone_data: Dict) -> tuple:
        """
        将独立展示区数据转为文本，注入 AI 分析 prompt

        Args:
            standalone_data: 独立展示区数据 {"platforms": [...], "rss_feeds": [...]}

        Returns:
            tuple: (格式化的文本内容, 独立展示区条目数)
        """
        lines = []

        # 热榜平台
        for platform in standalone_data.get("platforms", []):
            platform_id = platform.get("id", "")
            platform_name = platform.get("name", platform_id)
            items = platform.get("items", [])
            if not items:
                continue

            lines.append(f"### [{platform_name}]")
            for item in items:
                title = item.get("title", "")
                if not title:
                    continue

                line = f"- {title}"

                # 排名信息
                ranks = item.get("ranks", [])
                if ranks:
                    min_rank = min(ranks)
                    max_rank = max(ranks)
                    rank_str = f"{min_rank}" if min_rank == max_rank else f"{min_rank}-{max_rank}"
                    line += f" | 排名:{rank_str}"

                # 时间范围
                first_time = item.get("first_time", "")
                last_time = item.get("last_time", "")
                if first_time:
                    time_str = self._format_time_range(first_time, last_time)
                    line += f" | 时间:{time_str}"

                # 出现次数
                count = item.get("count", 1)
                if count > 1:
                    line += f" | 出现:{count}次"

                # 排名轨迹（如果启用）
                if self.include_rank_timeline:
                    rank_timeline = item.get("rank_timeline", [])
                    if rank_timeline:
                        timeline_str = self._format_rank_timeline(rank_timeline)
                        line += f" | 轨迹:{timeline_str}"

                lines.append(line)
            lines.append("")

        # RSS 源
        for feed in standalone_data.get("rss_feeds", []):
            feed_id = feed.get("id", "")
            feed_name = feed.get("name", feed_id)
            items = feed.get("items", [])
            if not items:
                continue

            lines.append(f"### [{feed_name}]")
            for item in items:
                title = item.get("title", "")
                if not title:
                    continue

                line = f"- {title}"
                published_at = item.get("published_at", "")
                if published_at:
                    line += f" | {published_at}"

                lines.append(line)
            lines.append("")

        standalone_count = sum(
            len(p.get("items", [])) for p in standalone_data.get("platforms", [])
        ) + sum(
            len(f.get("items", [])) for f in standalone_data.get("rss_feeds", [])
        )
        return "\n".join(lines), standalone_count

    def _parse_response(self, response: str) -> AIAnalysisResult:
        """解析 AI 响应"""
        result = AIAnalysisResult(raw_response=response)

        if not response or not response.strip():
            result.error = "AI 返回空响应"
            return result

        # 提取 JSON 文本（去掉 markdown 代码块标记）
        json_str = response

        if "```json" in response:
            parts = response.split("```json", 1)
            if len(parts) > 1:
                code_block = parts[1]
                end_idx = code_block.find("```")
                if end_idx != -1:
                    json_str = code_block[:end_idx]
                else:
                    json_str = code_block
        elif "```" in response:
            parts = response.split("```", 2)
            if len(parts) >= 2:
                json_str = parts[1]

        json_str = json_str.strip()
        if not json_str:
            result.error = "提取的 JSON 内容为空"
            result.core_trends = response[:500] + "..." if len(response) > 500 else response
            result.success = True
            return result

        # 创作者日报使用纯文本分区，避免长内容被模型生成成不完整 JSON。
        if self._is_creator_daily_prompt() and not json_str.lstrip().startswith(("{", "[")):
            return self._parse_creator_daily_text(json_str, response)

        # 第一步：标准 JSON 解析
        data = None
        parse_error = None

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            parse_error = e

        # 第二步：json_repair 本地修复
        if data is None:
            try:
                from json_repair import repair_json
                repaired = repair_json(json_str, return_objects=True)
                if isinstance(repaired, dict):
                    data = repaired
                    print("[AI] JSON 本地修复成功（json_repair）")
            except Exception:
                pass

        # 两步都失败，记录错误（后续由 analyze 方法的重试机制处理）
        if data is None:
            if parse_error:
                error_context = json_str[max(0, parse_error.pos - 30):parse_error.pos + 30] if json_str and parse_error.pos else ""
                result.error = f"JSON 解析错误 (位置 {parse_error.pos}): {parse_error.msg}"
                if error_context:
                    result.error += f"，上下文: ...{error_context}..."
            else:
                result.error = "JSON 解析失败"
            # 兜底：使用已提取的 json_str（不含 markdown 标记），避免推送中出现 ```json
            result.core_trends = json_str[:500] + "..." if len(json_str) > 500 else json_str
            result.success = True
            return result

        # 有些兼容接口会把 JSON 包在 result/data/output 等对象里。
        # 优先找包含实际日报内容的对象，避免顶层空壳遮住内层结果。
        data = self._find_analysis_payload(data)
        if not isinstance(data, dict):
            result.error = "AI 返回的 JSON 不是日报对象"
            return result

        # 解析成功，提取字段
        try:
            def report_value(*aliases):
                """兼容模型把日报区块名直接用作 JSON 字段名。"""
                normalize = lambda value: re.sub(
                    r"[\s_\-【】\[\]：:（）()]", "", str(value)
                ).lower()
                for alias in aliases:
                    normalized_alias = normalize(alias)
                    for key, value in data.items():
                        normalized_key = normalize(key)
                        if normalized_key == normalized_alias or normalized_key.startswith(normalized_alias):
                            if self._content_to_text(value):
                                return value
                return ""

            result.core_trends = self._content_to_text(
                report_value(
                    "core_trends", "AI选题", "AI今日选题", "AI热点",
                    "人工智能选题", "人工智能热点", "ai_topics", "ai_news"
                )
            )
            result.sentiment_controversy = self._content_to_text(
                report_value(
                    "sentiment_controversy", "币圈选题", "币圈今日选题",
                    "币圈热点", "加密货币选题", "加密货币热点", "加密热点",
                    "加密行业选题", "加密行业热点", "crypto_topics", "crypto_news"
                )
            )
            result.signals = self._content_to_text(
                report_value(
                    "signals", "优先发布顺序", "今日优先发布", "优先发布",
                    "今日主推", "今日推荐", "精选选题", "重点选题",
                    "priority_posts", "priority_topics", "today_priority",
                    "today_top_3", "top_3", "top3", "top_picks", "publish_order"
                )
            )
            result.rss_insights = self._content_to_text(
                report_value("rss_insights", "核查提醒", "信息核查")
            )
            result.outlook_strategy = self._content_to_text(
                report_value("outlook_strategy", "运营建议", "账号运营建议")
            )

            # 解析独立展示区概括
            summaries = report_value("standalone_summaries", "独立源点概括") or {}
            if isinstance(summaries, dict):
                result.standalone_summaries = {
                    str(k): self._content_to_text(v)
                    for k, v in summaries.items()
                    if self._content_to_text(v)
                }

            if self._is_creator_daily_prompt():
                missing = []
                if not result.core_trends.strip():
                    missing.append("AI 选题")
                if not result.sentiment_controversy.strip():
                    missing.append("币圈选题")

                if missing:
                    result.error = "模型没有生成必需分区：" + "、".join(missing)
                    print(f"[AI] 日报缺少必需分区: {'、'.join(missing)}")
                    return result

            visible_fields = (
                result.core_trends,
                result.sentiment_controversy,
                result.signals,
                result.rss_insights,
                result.outlook_strategy,
                *result.standalone_summaries.values(),
            )
            if not any(value.strip() for value in visible_fields):
                result.error = (
                    "模型返回了可解析的 JSON，但日报正文为空；"
                    "请检查模型输出或重试，避免把空报告误当成成功"
                )
                print("[AI] JSON 结构可解析，但所有可展示字段均为空")
                return result

            result.success = True
        except (KeyError, TypeError, AttributeError) as e:
            result.error = f"字段提取错误: {type(e).__name__}: {e}"
            result.core_trends = json_str[:500] + "..." if len(json_str) > 500 else json_str
            result.success = True

        return result

    def _parse_creator_daily_text(
        self, content: str, raw_response: str = ""
    ) -> AIAnalysisResult:
        """将固定标题的创作者日报纯文本映射回统一结果结构。"""
        aliases = {
            "ai选题": "core_trends",
            "ai今日选题": "core_trends",
            "ai热点": "core_trends",
            "人工智能选题": "core_trends",
            "人工智能热点": "core_trends",
            "人工智能相关热点": "core_trends",
            "币圈选题": "sentiment_controversy",
            "币圈今日选题": "sentiment_controversy",
            "币圈热点": "sentiment_controversy",
            "加密货币选题": "sentiment_controversy",
            "加密货币热点": "sentiment_controversy",
            "加密热点": "sentiment_controversy",
            "加密行业选题": "sentiment_controversy",
            "加密行业热点": "sentiment_controversy",
            "今日优先发布": "signals",
            "今日优先发布建议": "signals",
            "信息核查": "rss_insights",
            "账号运营建议": "outlook_strategy",
        }
        sections = {field: [] for field in aliases.values()}
        current_field = None

        for line in content.splitlines():
            heading = re.sub(r"^\s*#{1,6}\s*", "", line).strip()
            heading = heading.replace("**", "").replace("__", "")
            heading = re.sub(r"^\s*(?:[-*•·]|\d+[.)、])\s*", "", heading)
            heading = heading.strip("【】[]:： *_`")
            heading = re.sub(
                r"^(?:第)?[一二三四五六七八九十0-9]+(?:部分)?[、.．:：\s-]+",
                "",
                heading,
            )
            heading = re.sub(r"[（(].*?[）)]", "", heading)
            normalized = re.sub(r"\s+", "", heading).lower()
            field = aliases.get(normalized)
            if field is None:
                field = next(
                    (
                        target
                        for alias, target in aliases.items()
                        if normalized.startswith(alias)
                    ),
                    None,
                )
            if field:
                current_field = field
                continue
            if current_field:
                sections[current_field].append(line)

        result = AIAnalysisResult(raw_response=raw_response or content)
        result.core_trends = "\n".join(sections["core_trends"]).strip()
        result.sentiment_controversy = "\n".join(
            sections["sentiment_controversy"]
        ).strip()
        result.signals = "\n".join(sections["signals"]).strip()
        result.rss_insights = "\n".join(sections["rss_insights"]).strip()
        result.outlook_strategy = "\n".join(
            sections["outlook_strategy"]
        ).strip()

        missing = []
        if not result.core_trends:
            missing.append("AI 选题")
        if not result.sentiment_controversy:
            missing.append("币圈选题")
        if missing:
            found_sections = [
                name for name, lines in sections.items() if any(line.strip() for line in lines)
            ]
            marker_flags = []
            for label, pattern in (
                ("AI", r"AI|人工智能|大模型"),
                ("币圈", r"币圈|加密|crypto|bitcoin|区块链|web3"),
            ):
                marker_flags.append(
                    f"{label}关键词={'是' if re.search(pattern, content, re.I) else '否'}"
                )
            result.error = (
                "模型纯文本日报缺少必需分区："
                + "、".join(missing)
                + f"（响应 {len(content)} 字符，识别到区块："
                + ("、".join(found_sections) if found_sections else "无")
                + "；"
                + "，".join(marker_flags)
                + "）"
            )
            print(f"[AI] 纯文本日报缺少必需分区: {'、'.join(missing)}")
            return result

        result.success = True
        return result

    @staticmethod
    def _content_to_text(value: Any) -> str:
        """将模型字段安全地归一为可展示文本。"""
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            parts = []
            for item in value:
                text = AIAnalyzer._content_to_text(item)
                if text:
                    parts.append(text)
            return "\n".join(parts)
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False)
        return str(value).strip()

    @classmethod
    def _find_analysis_payload(cls, data: Any) -> Any:
        """在常见 API 包装对象中寻找实际日报字段。"""
        report_keys = {
            "core_trends",
            "sentiment_controversy",
            "signals",
            "rss_insights",
            "outlook_strategy",
            "standalone_summaries",
        }
        creator_report_keys = {
            "ai选题", "ai今日选题", "ai热点", "人工智能选题", "人工智能热点",
            "ai_topics", "ai_news",
            "币圈选题", "币圈今日选题", "币圈热点", "加密货币选题",
            "加密货币热点", "加密热点", "加密行业选题", "加密行业热点",
            "crypto_topics", "crypto_news",
            "今日优先发布", "优先发布顺序", "优先发布", "今日主推",
            "今日推荐", "精选选题", "重点选题",
            "priority_posts", "priority_topics", "today_priority",
            "today_top_3", "top_3", "top3", "top_picks", "publish_order",
            "信息核查", "核查提醒", "账号运营建议", "运营建议", "独立源点概括",
        }
        normalize = lambda value: re.sub(
            r"[\s_\-【】\[\]：:（）()]", "", str(value)
        ).lower()
        recognized_keys = {
            normalize(key) for key in report_keys | creator_report_keys
        }
        queue = [data]
        fallback = None
        visited = set()

        while queue:
            candidate = queue.pop(0)
            if not isinstance(candidate, dict) or id(candidate) in visited:
                continue
            visited.add(id(candidate))

            candidate_report_keys = {
                key for key in candidate
                if normalize(key) in recognized_keys
                or any(normalize(key).startswith(alias) for alias in recognized_keys)
            }
            if candidate_report_keys:
                if fallback is None:
                    fallback = candidate
                for key in candidate_report_keys:
                    value = candidate.get(key)
                    if isinstance(value, dict):
                        if any(cls._content_to_text(v) for v in value.values()):
                            return candidate
                    elif cls._content_to_text(value):
                        return candidate

            # 递归兼容 result/data/output/response 等常见包装字段，
            # 同时兼容提供商自定义包装名。
            queue.extend(value for value in candidate.values() if isinstance(value, dict))

        return fallback if fallback is not None else data

    def _is_creator_daily_prompt(self) -> bool:
        """判断当前提示词是否要求 AI 与币圈双分区选题日报。"""
        analysis_config = getattr(self, "analysis_config", {}) or {}
        prompt_file = str(analysis_config.get("PROMPT_FILE", "")).replace("\\", "/")
        return prompt_file.rsplit("/", 1)[-1] == "creator_daily_prompt.txt"
