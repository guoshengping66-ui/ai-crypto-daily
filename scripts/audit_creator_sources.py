"""Check public RSS endpoints without calling a model or sending notifications."""

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trendradar.crawler.rss.parser import RSSParser


def check(feed):
    try:
        response = requests.get(feed["url"], timeout=15, headers={"User-Agent": "TrendRadar RSS Reader"})
        response.raise_for_status()
        items = RSSParser().parse(response.text, feed["url"])
        latest = max((item.published_at or "" for item in items), default="")
        return {"id": feed["id"], "items": len(items), "dated": sum(bool(i.published_at) for i in items), "latest": latest, "url": response.url}
    except Exception as exc:
        return {"id": feed["id"], "error": str(exc)[:160]}


if __name__ == "__main__":
    import json

    config = yaml.safe_load(Path("config/config.yaml").read_text(encoding="utf-8"))
    feeds = config["rss"]["feeds"]
    with ThreadPoolExecutor(max_workers=6) as pool:
        for result in pool.map(check, feeds):
            print(json.dumps(result, ensure_ascii=False))
