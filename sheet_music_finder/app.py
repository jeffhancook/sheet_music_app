import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# In-memory cache: key -> (timestamp, results)
_cache = {}
CACHE_TTL = 300  # 5 minutes

executor = ThreadPoolExecutor(max_workers=2)

IMSLP_API = "https://imslp.org/api.php"
IMSLP_WIKI = "https://imslp.org/wiki/"
HEADERS = {"User-Agent": "SheetMusicFinder/1.0"}


def _search_imslp(query, limit=20):
    """Search IMSLP via its MediaWiki API."""
    results = []
    try:
        resp = requests.get(IMSLP_API, params={
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": limit,
            "format": "json",
        }, headers=HEADERS, timeout=15)
        resp.raise_for_status()

        for item in resp.json().get("query", {}).get("search", []):
            title = item["title"]
            snippet = re.sub(r"<[^>]+>", "", item.get("snippet", ""))
            url = IMSLP_WIKI + title.replace(" ", "_")
            results.append({
                "title": title,
                "url": url,
                "description": snippet[:200] if snippet else "",
                "source": "IMSLP",
            })
    except Exception:
        pass
    return results


def _search_ddg(query, limit=15):
    """Best-effort DuckDuckGo search for MuseScore and other sheet music sites."""
    results = []
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            for item in ddgs.text(f"{query} sheet music", max_results=limit):
                url = item.get("href", "")
                # Only keep results from sheet music sites
                if "musescore.com" in url:
                    source = "MuseScore"
                elif "8notes.com" in url:
                    source = "8notes"
                elif "musicnotes.com" in url:
                    source = "Musicnotes"
                elif "imslp.org" in url:
                    continue  # Skip — already covered by IMSLP API
                else:
                    continue
                results.append({
                    "title": item.get("title", ""),
                    "url": url,
                    "description": item.get("body", "")[:200],
                    "source": source,
                })
    except Exception:
        pass
    return results


def _get_results(query):
    """Search IMSLP (reliable) and DuckDuckGo (best-effort) in parallel, with caching."""
    cache_key = query.strip().lower()
    now = time.time()

    if cache_key in _cache:
        ts, cached = _cache[cache_key]
        if now - ts < CACHE_TTL:
            return cached

    imslp_future = executor.submit(_search_imslp, query)
    ddg_future = executor.submit(_search_ddg, query)

    results = imslp_future.result(timeout=30) + ddg_future.result(timeout=30)

    _cache[cache_key] = (now, results)
    return results


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/search")
def api_search():
    query = request.args.get("q", "").strip()

    if not query:
        return jsonify({"error": "No search query provided"}), 400
    if len(query) > 200:
        return jsonify({"error": "Query too long (max 200 characters)"}), 400

    try:
        results = _get_results(query)
        return jsonify({"results": results, "query": query})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
