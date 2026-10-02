"""Source-backed daily topic selection, attention signals and delivery history.

Scores rank editorial candidates; they are not predicted impressions or X metrics.
Only dated RSS articles establish the event's freshness. Community timestamps
describe discussion activity, never the publication date of a linked article.
"""

import hashlib
import html
import json
import math
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests


AI_PATTERN = re.compile(
    r"\b(?:ai|llms?|agi|openai|chatgpt|claude|gemini|deepmind|deepseek|qwen|kimi|"
    r"anthropic|nvidia|copilot|gpt[-\s]?\d|hugging\s*face)\b|artificial intelligence|"
    r"machine learning|人工智能|大模型|智能体|多模态|生成式|具身智能|机器人|算力|通义|豆包|智谱|千问",
    re.I,
)
CRYPTO_PATTERN = re.compile(
    r"\b(?:crypto(?:currency)?|bitcoin|btc|ethereum|eth|solana|web3|defi|nfts?|"
    r"stablecoins?|blockchain|coinbase|binance|uniswap|aave|tether|usdc|usdt|"
    r"polymarket|hyperliquid|tokenization|rwa)\b|比特币|以太坊|加密货币|加密资产|"
    r"稳定币|区块链|链上|币安|代币|空投|质押|公链|清算",
    re.I,
)
PROMO_PATTERN = re.compile(
    r"sponsored|press release|price prediction|best (?:crypto|coins)|presale|"
    r"how to buy|next (?:100x|1000x)|don['’]t miss|expo.{0,30}pass|"
    r"(?:conference|disrupt).{0,50}(?:ticket|pass)|early[- ]bird|register now|"
    r"价格预测|买入指南|广告|推广合作|预售代币|活动报名|购票|门票", re.I
)
ROUNDUP_PATTERN = re.compile(
    r"daily (?:digest|roundup)|weekly (?:digest|roundup)|week in review|"
    r"monthly (?:digest|roundup|highlights)|latest ai news we announced|"
    r"每日早报|晚间必读|今日必读|一周回顾|周报|日报|融资周报|每日行情|月度回顾", re.I
)
IMPACT_PATTERN = re.compile(
    r"\b(?:launch\w*|releas\w*|introduc\w*|unveil\w*|open[- ]source|"
    r"ban\w*|lawsuit|sue\w*|copyright|leak\w*|hack\w*|exploit\w*|breach|"
    r"outage|shutdown|regulat\w*|approv\w*|etf|acquir\w*|billion|million|"
    r"funding|layoff\w*|mainnet|upgrade\w*|vulnerab\w*)\b|"
    r"发布|上线|开源|封禁|诉讼|起诉|版权|泄露|攻击|被盗|漏洞|宕机|监管|"
    r"获批|收购|融资|裁员|主网|升级|突破|亿|百万", re.I
)
ROUTINE_PATTERN = re.compile(
    r"\b(?:tutorial|guide|how to|opinion|price analysis|price forecast|"
    r"partnership|webinar|sponsor)\b|教程|入门|价格分析|行情分析|合作伙伴|活动报名|"
    r"(?:bitcoin|btc|ether|ethereum|eth|solana|xrp).{0,40}(?:price|rises|falls|gains|drops|slips|rallies)|"
    r"(?:比特币|以太坊).{0,20}(?:上涨|下跌|反弹|回落|价格)|全网合约爆仓|"
    r"过去24小时.{0,25}爆仓|\bliquidations\b", re.I
)
ALIASES = {
    "人工智能": " ai ", "比特币": " bitcoin ", "btc": "bitcoin",
    "以太坊": " ethereum ", "eth": "ethereum", "币安": " binance ",
    "英伟达": " nvidia ", "谷歌": " google ", "微软": " microsoft ",
    "通义千问": " qwen ", "千问": " qwen ", "稳定币": " stablecoin ",
    "发布": " launch ", "上线": " launch ", "推出": " launch ",
    "releases": "launch", "released": "launch", "release": "launch",
    "launches": "launch", "launched": "launch", "unveils": "launch",
    "introduces": "launch", "开源": " opensource ", "open-source": "opensource",
    "被盗": " hack ", "攻击": " hack ", "hacked": "hack", "hacks": "hack",
    "漏洞": " exploit ", "宕机": " outage ", "版权": " copyright ",
    "起诉": " lawsuit ", "诉讼": " lawsuit ", "监管": " regulation ",
    "融资": " funding ", "收购": " acquisition ", "acquires": "acquisition",
    "裁员": " layoff ", "workforce": "layoff", "layoffs": "layoff",
    "shutting down": "shutdown", "shuts down": "shutdown", "关闭": " shutdown ",
    "wind down": "shutdown", "winds down": "shutdown", "winding down": "shutdown",
    "停止运营": " shutdown ", "终止运营": " shutdown ",
}
STOPWORDS = set("a an the to of for in on at with and or is are its it as by from has have new ai crypto web3 news says said more model models token tokens today launch company companies bitcoin ethereum openai google nvidia microsoft anthropic binance solana".split())
STOPWORDS.update("report reported reports now stock price bank data first using just million billion dollar dollars percent after once cuts layer network".split())
ENTITIES = ("openai", "anthropic", "google", "deepseek", "qwen", "kimi", "nvidia", "microsoft", "bitcoin", "ethereum", "solana", "binance", "coinbase", "tether", "aave", "polymarket", "hyperliquid")


def clean(value, limit=500):
    value = html.unescape(str(value or ""))
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", value)).strip()[:limit]


def timestamp(value):
    try:
        result = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
        return (result if result.tzinfo else result.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def canonical_url(value):
    """Remove tracking and AMP variations while preserving meaningful query IDs."""
    try:
        parsed = urlsplit(str(value or "").strip())
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username:
            return ""
        host = parsed.hostname.casefold().removeprefix("www.")
        path = parsed.path.rstrip("/")
        if path.endswith("/amp"):
            path = path[:-4]
        query = sorted((k, v) for k, v in parse_qsl(parsed.query) if not k.casefold().startswith("utm_") and k.casefold() not in {"fbclid", "gclid", "ref", "source", "amp"})
        return urlunsplit(("https", host, path, urlencode(query), ""))
    except ValueError:
        return ""


def normalized_text(title):
    text = clean(title).casefold()
    for original, replacement in ALIASES.items():
        if original.isascii():
            text = re.sub(r"(?<!\w)" + re.escape(original) + r"(?!\w)", replacement, text)
        else:
            text = text.replace(original, replacement)
    # Keep specific versions as one distinctive token across languages.
    return re.sub(r"\b(gpt|claude|gemini|qwen|deepseek)[\s-]*(\d+(?:\.\d+)*)", r"\1\2", text)


def event_tokens(title):
    text = normalized_text(title)
    tokens = set(re.findall(r"[a-z][a-z0-9.]*|\d+(?:\.\d+)?", text)) - STOPWORDS
    for word in re.findall(r"[\u4e00-\u9fff]+", text):
        tokens.update(word[i:i + 2] for i in range(len(word) - 1))
    return tokens


def same_event(left, right):
    if left.get("category") != right.get("category"):
        return False
    a, b = canonical_url(left.get("url")), canonical_url(right.get("url"))
    if a and a == b:
        return True
    first, second = clean(left.get("title")).casefold(), clean(right.get("title")).casefold()
    if first and first == second:
        return True
    a, b = event_tokens(first), event_tokens(second)
    if not a or not b:
        return False
    normalized_a, normalized_b = normalized_text(first), normalized_text(second)
    entities_a = {x for x in ENTITIES if re.search(r"\b" + x + r"\b", normalized_a)}
    entities_b = {x for x in ENTITIES if re.search(r"\b" + x + r"\b", normalized_b)}
    if entities_a and entities_b and entities_a.isdisjoint(entities_b):
        return False
    actions = {"launch", "hack", "outage", "lawsuit", "acquisition", "funding", "regulation", "opensource", "layoff", "shutdown"}
    actions_a = set(re.findall(r"[a-z]+", normalized_a)) & actions
    actions_b = set(re.findall(r"[a-z]+", normalized_b)) & actions
    if actions_a and actions_b and actions_a.isdisjoint(actions_b):
        return False
    versions_a = {t for t in a if re.search(r"[a-z]\d", t)}
    versions_b = {t for t in b if re.search(r"[a-z]\d", t)}
    if versions_a and versions_b and versions_a.isdisjoint(versions_b):
        return False
    if versions_a & versions_b and entities_a & entities_b and actions_a & actions_b:
        return True
    common = a & b
    named_anchors = {x for x in common if x.isascii() and x[0].isalpha() and x not in actions}
    proper_a = {x.casefold() for x in re.findall(r"(?<![A-Za-z0-9])[A-Z][A-Za-z0-9]{2,}", clean(left.get("title")))} - STOPWORDS
    proper_b = {x.casefold() for x in re.findall(r"(?<![A-Za-z0-9])[A-Z][A-Za-z0-9]{2,}", clean(right.get("title")))} - STOPWORDS
    if "shutdown" in actions_a & actions_b and proper_a & proper_b & named_anchors:
        return True
    numbers = {x for x in common if re.fullmatch(r"\d+(?:\.\d+)?", x)}
    if len(named_anchors) >= 2 and numbers and actions_a & actions_b:
        return True
    return len(common) >= 2 and (len(common) / len(a | b) >= 0.55 or len(common) / min(len(a), len(b)) >= 0.8 or (len(common) >= 3 and len(common) / min(len(a), len(b)) >= 0.65))


def classify(item, feed):
    text = clean(item.get("title")) + " " + clean(item.get("summary"), 220)
    declared = feed.get("category", "mixed")
    if declared in ("ai", "crypto"):
        # A broad crypto publisher can also carry genuine AI news.
        if declared == "crypto" and AI_PATTERN.search(item.get("title", "")) and not CRYPTO_PATTERN.search(text):
            return "ai"
        return declared
    if CRYPTO_PATTERN.search(item.get("title", "")):
        return "crypto"
    if AI_PATTERN.search(item.get("title", "")):
        return "ai"
    if CRYPTO_PATTERN.search(text):
        return "crypto"
    if AI_PATTERN.search(text):
        return "ai"
    return ""


def fetch_hn_signals(now, timeout=12):
    """Read indexed public HN votes/comments; no X API or credentials are used."""
    signals = []
    cutoff = int((now - timedelta(hours=24)).timestamp())
    for page in range(3):
        try:
            response = requests.get(
                "https://hn.algolia.com/api/v1/search_by_date",
                params={"tags": "story", "numericFilters": f"created_at_i>={cutoff},points>=10", "hitsPerPage": 100, "page": page},
                timeout=timeout,
                headers={"User-Agent": "TrendRadar daily topic reader"},
            )
            response.raise_for_status()
            payload = response.json()
            for hit in payload.get("hits", []):
                created = timestamp(hit.get("created_at"))
                if not created or not timedelta(0) <= now - created <= timedelta(hours=24):
                    continue
                signals.append({
                    "platform": "Hacker News", "title": clean(hit.get("title")),
                    "url": hit.get("url") or "", "points": max(0, int(hit.get("points") or 0)),
                    "comments": max(0, int(hit.get("num_comments") or 0)),
                    "discussion_url": f"https://news.ycombinator.com/item?id={hit['objectID']}",
                    "observed_at": now.isoformat(), "created_at": created.isoformat(),
                })
            if page + 1 >= payload.get("nbPages", 1):
                break
        except (requests.RequestException, ValueError, KeyError, TypeError):
            print("[选题] Hacker News讨论信号暂时不可用，使用其他已取得的关注依据")
            break
    print(f"[选题] 获取 {len(signals)} 条24小时内HN讨论记录（仅用于热度匹配）")
    return signals


def fetch_farcaster_signals(now, api_key, timeout=12):
    """Read Neynar's public 24-hour trending Farcaster feed when configured."""
    if not api_key:
        return []
    try:
        response = requests.get(
            "https://api.neynar.com/v2/farcaster/feed/trending/",
            params={"time_window": "24h", "limit": 100},
            headers={"x-api-key": api_key, "User-Agent": "TrendRadar daily topic reader"},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError, TypeError):
        print("[选题] Farcaster趋势信号暂时不可用，继续使用其他公开热度依据")
        return []

    signals = []
    result = payload.get("result") or {}
    casts = payload.get("casts", []) or result.get("casts", [])
    for cast in casts:
        created = timestamp(cast.get("timestamp"))
        if not created or not timedelta(0) <= now - created <= timedelta(hours=24):
            continue
        reactions = cast.get("reactions") or {}
        likes = max(0, int(reactions.get("likes_count") or 0))
        recasts = max(0, int(reactions.get("recasts_count") or 0))
        replies = max(0, int((cast.get("replies") or {}).get("count") or 0))
        if likes + recasts + replies < 5:
            continue
        embeds = cast.get("embeds") or []
        linked_url = next((e.get("url") for e in embeds if isinstance(e, dict) and e.get("url")), "")
        username = clean((cast.get("author") or {}).get("username"), 80)
        cast_hash = clean(cast.get("hash"), 100)
        discussion_url = f"https://farcaster.xyz/{username}/{cast_hash}" if username and cast_hash else ""
        signals.append({
            "platform": "Farcaster", "title": clean(cast.get("text"), 500),
            "url": linked_url, "points": likes + recasts, "likes": likes,
            "recasts": recasts, "comments": replies, "discussion_url": discussion_url,
            "observed_at": now.isoformat(), "created_at": created.isoformat(),
        })
    print(f"[选题] 获取 {len(signals)} 条24小时内Farcaster趋势讨论（仅用于热点匹配）")
    return signals


class CreatorSelector:
    def __init__(self, config, feeds, now):
        self.config = config
        self.feeds = {f["id"]: f for f in feeds}
        self.now = timestamp(now.isoformat())
        self.history_path = Path(config.get("history_path", ".creator_state/history.json"))
        self.audit_path = Path(config.get("audit_path", "output/creator_daily/run.json"))
        self.history = self._load_history()
        self.candidates = []
        self.diagnostics = {}

    def _load_history(self):
        try:
            data = json.loads(self.history_path.read_text(encoding="utf-8"))
            entries = data.get("delivered", [])
            cutoff = self.now - timedelta(days=int(self.config.get("history_days", 7)))
            return [x for x in entries if isinstance(x, dict) and (timestamp(x.get("sent_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff]
        except FileNotFoundError:
            return []
        except (ValueError, TypeError, AttributeError):
            print("[选题] 推送历史格式异常，本次重新建立记录")
            return []

    def group_raw(self, items):
        """Classify using the feed's scope and headline/summary, retaining provenance."""
        groups = {"ai": [], "crypto": []}
        for original in items:
            feed = self.feeds.get(original.get("feed_id"), {})
            category = classify(original, feed)
            if category:
                groups[category].append({**original, "category": category, "source_name": original.get("feed_name", "RSS")})
        return [{"word": "AI热点" if key == "ai" else "Web3热点", "titles": values, "count": len(values)} for key, values in groups.items() if values]

    def prepare(self, rss_stats, hotlist_stats=(), community_signals=None):
        counters = Counter()
        source_counts = Counter()
        items = []
        for stat in rss_stats or []:
            for original in stat.get("titles", []):
                counters["input"] += 1
                published = timestamp(original.get("published_at"))
                if not published:
                    counters["missing_or_invalid_time"] += 1
                    continue
                age = self.now - published
                if age < timedelta(0) or age > timedelta(hours=24):
                    counters["future" if age < timedelta(0) else "older_than_24h"] += 1
                    continue
                title, url = clean(original.get("title"), 240), canonical_url(original.get("url"))
                if not title or not url:
                    counters["missing_title_or_url"] += 1
                    continue
                if PROMO_PATTERN.search(title) or ROUNDUP_PATTERN.search(title):
                    counters["promotion_or_roundup"] += 1
                    continue
                feed = self.feeds.get(original.get("feed_id"), {})
                category = original.get("category") or classify(original, feed)
                if category not in ("ai", "crypto"):
                    counters["irrelevant"] += 1
                    continue
                item = {**original, "title": title, "summary": clean(original.get("summary")), "url": str(original["url"]).strip(), "category": category, "published_at": published.isoformat(), "publisher": feed.get("publisher") or urlsplit(url).hostname, "source_kind": feed.get("kind", "media")}
                source_counts[item.get("feed_id") or item["publisher"]] += 1
                items.append(item)
        # Clustering precedes history filtering, so all articles for an already
        # delivered event are excluded together, including another language.
        clusters = []
        for item in sorted(items, key=lambda x: (x["source_kind"] == "official", x["published_at"]), reverse=True):
            group = next((g for g in clusters if any(same_event(item, other) for other in g)), None)
            if group is None:
                clusters.append([item])
            else:
                group.append(item)
                counters["same_event_merged"] += 1

        if community_signals is None:
            community_signals = fetch_hn_signals(self.now) if self.config.get("hn_enabled", True) else []
            neynar_key = os.environ.get("NEYNAR_API_KEY", "").strip()
            if neynar_key:
                community_signals.extend(fetch_farcaster_signals(self.now, neynar_key))
        hot_items = [t for stat in hotlist_stats or [] for t in stat.get("titles", [])]
        ranked = []
        for cluster in clusters:
            primary = cluster[0]
            if any(same_event(article, old) for article in cluster for old in self.history):
                counters["previously_delivered_events"] += 1
                continue
            publishers = sorted({x["publisher"] for x in cluster})
            event_id = hashlib.sha256((primary["category"] + canonical_url(primary["url"])).encode()).hexdigest()[:16]
            signals = []
            for signal in community_signals:
                if any(same_event(article, {**signal, "category": article["category"]}) for article in cluster):
                    signals.append(signal)
            for hot in hot_items:
                ranks = [x for x in hot.get("ranks", []) if isinstance(x, (int, float)) and x > 0]
                if ranks and any(same_event(article, {**hot, "category": article["category"]}) for article in cluster):
                    signals.append({"platform": hot.get("source_name", "热榜"), "rank": min(ranks), "url": hot.get("url", ""), "observed_at": self.now.isoformat()})

            impact = bool(IMPACT_PATTERN.search(primary["title"]))
            routine = bool(ROUTINE_PATTERN.search(primary["title"]))
            # Publisher breadth corroborates facts, but is not itself audience
            # attention. Keep it as a modest ranking feature, separate from
            # directly observed hot-list/community signals.
            coverage = min(12, max(0, len(publishers) - 1) * 6)
            community = max((min(28, math.log2(1 + x.get("points", 0)) * 2 + math.log2(1 + x.get("comments", 0)) * 3) if "points" in x else max(0, 20 - math.log2(1 + x["rank"]) * 2) for x in signals), default=0)
            age_hours = (self.now - timestamp(primary["published_at"])).total_seconds() / 3600
            freshness = max(0, 12 * (1 - age_hours / 24))
            authority = {"official": 12, "media": 8, "research": 3}.get(primary["source_kind"], 5)
            score = round(coverage + community + freshness + authority + (8 if impact else 4) - (14 if routine else 0), 1)
            # A monetary figure is not sufficient reason to select generic
            # equity prices or anonymous individual trading activity.
            noise = bool(re.search(r"某巨鲸|某鲸鱼|某地址|某交易员|anonymous whale|stock (?:price|rally)|股价|市值逼近", primary["title"], re.I))
            if noise:
                counters["market_or_whale_noise"] += 1
                continue
            if primary["source_kind"] == "research" and not signals:
                score -= 16
            evidence = [f"{len(publishers)}家独立来源报道（事实交叉验证，不代表平台热度）"] if len(publishers) > 1 else []
            for signal in signals:
                if signal.get("platform") == "Farcaster":
                    evidence.append(f"Farcaster {signal['likes']}赞/{signal['recasts']}转发/{signal['comments']}评论")
                elif signal.get("platform") == "Hacker News":
                    evidence.append(f"HN {signal['points']}票 / {signal['comments']}评论")
                else:
                    evidence.append(f"{signal['platform']}榜单第{signal['rank']}名")
            if not evidence:
                evidence.append("实质新进展；尚无匹配的社区讨论数据" if impact else "单一来源报道；讨论数据未核验")
            ranked.append({**primary, "event_id": event_id, "score": score, "discussion_score": round(community, 1), "coverage_score": coverage, "impact_flag": impact, "attention_evidence": "；".join(evidence), "attention_verified": bool(signals), "signals": signals, "coverage_publishers": publishers, "supporting_sources": [{"title": x["title"], "url": x["url"], "published_at": x["published_at"], "publisher": x["publisher"]} for x in cluster], "entity": next((x for x in ENTITIES if x in normalized_text(primary["title"])), "")})

        floor = float(self.config.get("min_score", 24))
        require_signal = self.config.get("require_platform_signal", True)
        counters["without_platform_signal"] = sum(not x["attention_verified"] for x in ranked)
        self.candidates = sorted((x for x in ranked if x["score"] >= floor and (not require_signal or x["attention_verified"])), key=lambda x: (x["score"], x["discussion_score"], x["published_at"]), reverse=True)
        counters["below_quality_floor"] = sum(x["score"] < floor for x in ranked)
        self.diagnostics = {"counts": dict(counters), "fresh_by_source": dict(source_counts), "community_records": len(community_signals), "community_by_platform": dict(Counter(x.get("platform", "unknown") for x in community_signals)), "eligible_ai": sum(x["category"] == "ai" for x in self.candidates), "eligible_web3": sum(x["category"] == "crypto" for x in self.candidates)}
        print("[选题] 候选检查：" + json.dumps(self.diagnostics, ensure_ascii=False))
        pool_size = int(self.config.get("pool_per_category", 20))
        return [{"word": "AI热点" if category == "ai" else "Web3热点", "titles": self.diverse(category, pool_size), "count": len(self.diverse(category, pool_size))} for category in ("ai", "crypto")]

    def diverse(self, category, limit=3, exclude=()):
        if limit <= 0:
            return []
        result, entities, publishers = [], Counter(), Counter()
        eligible = [x for x in self.candidates if x["category"] == category and x["event_id"] not in exclude]
        # Prefer different subjects/publishers, then relax diversity when the
        # evidence-backed pool is small. Freshness and quality never relax.
        for entity_limit, publisher_limit in ((1, 2), (2, 4), (limit, limit)):
            for item in eligible:
                if any(x["event_id"] == item["event_id"] for x in result):
                    continue
                if (item["entity"] and entities[item["entity"]] >= entity_limit) or publishers[item["publisher"]] >= publisher_limit:
                    continue
                result.append(item)
                entities[item["entity"]] += 1
                publishers[item["publisher"]] += 1
                if len(result) >= limit:
                    return result
        return result

    def complete(self, result):
        """Backfill only already qualified events if the model returns too few cards."""
        from trendradar.notification.senders import _parse_creator_topic_cards

        used = set()
        for field, category, label in (("core_trends", "ai", "AI"), ("sentiment_controversy", "crypto", "Web3")):
            cards = _parse_creator_topic_cards(getattr(result, field, "")) if result.success else []
            accepted = []
            for card in cards:
                urls = re.findall(r"https?://[^\s|<>]+", card.get("body", ""))
                candidate = next((x for x in self.candidates if x["category"] == category and any(canonical_url(u.rstrip(".,;，。；)")) == canonical_url(x["url"]) for u in urls)), None)
                if candidate and candidate["event_id"] not in used:
                    used.add(candidate["event_id"])
                    accepted.append(card)
            accepted = accepted[:3]
            for item in self.diverse(category, 3 - len(accepted), used) if len(accepted) < 3 else []:
                used.add(item["event_id"])
                accepted.append({"headline": item["title"], "body": f"简述（来源摘要）：{clean(item['summary'], 200) or item['title']}\n时间：{item['published_at']}\n来源：{item['url']}"})
                result.fallback_used = True
            lines = [f"{label}热点（{len(accepted)}/3）"]
            for index, card in enumerate(accepted, 1):
                lines.extend([f"{index}. 【热点】{card['headline']}", card["body"]])
            setattr(result, field, "\n".join(lines))
        result.success = True
        result.skipped = not used
        result.signals = result.rss_insights = result.outlook_strategy = ""
        ai_count = len(_parse_creator_topic_cards(result.core_trends))
        web3_count = len(_parse_creator_topic_cards(result.sentiment_controversy))
        print(f"[选题] 最终热点：AI={ai_count}/3，Web3={web3_count}/3")
        if ai_count < 3 or web3_count < 3:
            print("::warning::24小时内的合格热点不足，请查看creator-daily附件中的筛选诊断")
        return result

    def archive(self, result, delivery_results=None, delivered_urls=(), dry_run=False):
        text = result.core_trends + "\n" + result.sentiment_controversy
        chosen_urls = {canonical_url(u.rstrip(".,;，。；)")) for u in re.findall(r"https?://[^\s|<>]+", text)}
        selected = [x for x in self.candidates if canonical_url(x["url"]) in chosen_urls]
        audit = {"generated_at": self.now.isoformat(), "dry_run": dry_run, "diagnostics": self.diagnostics, "candidates": self.candidates, "selected": selected, "content": {"ai": result.core_trends, "web3": result.sentiment_controversy}, "delivery_results": delivery_results or {}, "delivered_urls": list(delivered_urls)}
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        self.audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        if not dry_run and delivered_urls:
            successful = {canonical_url(u) for u in delivered_urls}
            new_records = [{"title": x["title"], "url": x["url"], "category": x["category"], "event_id": x["event_id"], "sent_at": self.now.isoformat()} for x in selected if canonical_url(x["url"]) in successful]
            known = {canonical_url(x.get("url")) for x in self.history}
            self.history.extend(x for x in new_records if canonical_url(x["url"]) not in known)
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            self.history_path.write_text(json.dumps({"delivered": self.history}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[选题] 已保存可核对的日报内容与入选依据：{self.audit_path}")
