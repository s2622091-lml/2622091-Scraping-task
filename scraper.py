"""同一ホストのHTMLページを幅優先探索し、URLとtitleを辞書・CSVに保存する。"""

import argparse
import codecs
import csv
import json
import re
import time
from collections import deque
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

START_URL = "https://www.musashino-u.ac.jp/"
USER_AGENT = "UniversitySitemapStudent/1.0"
NON_HTML = {
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
    ".css", ".js", ".zip", ".mp4", ".mp3", ".woff", ".woff2", ".ttf",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".xml",
}


def normalize_url(href, base=START_URL):
    """相対URLを絶対URLに変換。外部ホスト、添付ファイル、アンカーを除外する。"""
    if not href or not href.strip():
        return None
    try:
        url = urlsplit(urljoin(base, href.strip()))
        origin = urlsplit(START_URL)
        if url.scheme not in ("http", "https") or url.hostname != origin.hostname:
            return None
        if url.username or url.password or url.port not in (None, 80, 443):
            return None
        path = url.path or "/"
        if Path(path.lower()).suffix in NON_HTML:
            return None
        query = [(k, v) for k, v in parse_qsl(url.query, keep_blank_values=True)
                 if not k.lower().startswith("utm_") and k.lower() not in {"gclid", "fbclid"}]
        return urlunsplit((url.scheme.lower(), url.hostname,
                           quote(path, safe="/%:@!$&'()*+,;=-._~"), urlencode(query), ""))
    except ValueError:
        return None


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.title_parts = []
        self.links = []
        self.base = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title":
            self.in_title = True
        elif tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        elif tag == "base" and self.base is None:
            self.base = attrs.get("href")

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, text):
        if self.in_title:
            self.title_parts.append(text)

    @property
    def title(self):
        return " ".join("".join(self.title_parts).split())


def decode_html(body, headers):
    # HTTPヘッダー、HTMLのmeta、UTF-8の順に文字コードを確認する。
    meta = re.search(br'charset\s*=\s*["\']?\s*([A-Za-z0-9_-]+)', body[:8192], re.I)
    candidates = [headers.get_content_charset(), meta[1].decode("ascii") if meta else None, "utf-8", "cp932"]
    for encoding in candidates:
        if not encoding:
            continue
        try:
            codecs.lookup(encoding)
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def save_csv(pages, path):
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["url", "title"])
        writer.writerows(sorted(pages.items()))
    temporary.replace(path)


class Crawler:
    def __init__(self, delay=1.0):
        self.delay = max(1.0, delay)
        self.last_request = None
        self.robots = None
        crawler = self

        class SameHostRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                target = normalize_url(newurl, req.full_url)
                if target is None:
                    raise URLError("External or non-HTML redirect skipped: " + newurl)
                if crawler.robots and not crawler.robots.can_fetch(USER_AGENT, target):
                    raise URLError("Redirect disallowed by robots.txt: " + target)
                crawler.wait()
                return super().redirect_request(req, fp, code, msg, headers, target)

        self.opener = build_opener(SameHostRedirect())

    def wait(self):
        if self.last_request is not None:
            time.sleep(max(0, self.delay - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()

    def fetch(self, url):
        self.wait()
        return self.opener.open(Request(url, headers={"User-Agent": USER_AGENT}), timeout=25)

    def load_robots(self):
        url = urljoin(START_URL, "robots.txt")
        robots = RobotFileParser(url)
        try:
            with self.fetch(url) as response:
                robots.parse(response.read().decode("utf-8", errors="replace").splitlines())
        except HTTPError as error:
            if error.code in (404, 410):
                robots.parse([])
                print("robots.txt: not found; using a 1+ second request interval", flush=True)
            else:
                raise RuntimeError("robots.txt could not be checked: HTTP " + str(error.code)) from error
        except (URLError, OSError) as error:
            raise RuntimeError("robots.txt could not be checked; try again later") from error
        interval = robots.crawl_delay(USER_AGENT) or 0
        rate = robots.request_rate(USER_AGENT)
        self.delay = max(self.delay, interval, rate.seconds / rate.requests if rate and rate.requests else 0)
        self.robots = robots


def crawl(args):
    pages = {}  # key: URL, value: <title>の文字列
    queue = deque([START_URL])
    seen = set()
    errors = {}
    excluded = []
    if args.state.exists() and not args.fresh:
        state = json.loads(args.state.read_text(encoding="utf-8"))
        if state["start_url"] != START_URL:
            raise ValueError("The checkpoint belongs to a different website")
        pages = state["pages"]
        queue = deque(state["queue"])
        seen = set(state["seen"])
        errors = state["errors"]
        excluded = state["excluded"]
    scheduled = seen | set(queue)
    crawler = Crawler(args.delay)
    crawler.load_robots()
    interrupted = False

    def save():
        save_csv(pages, args.output)
        state = dict(start_url=START_URL, pages=pages, queue=list(queue),
                     seen=sorted(seen), errors=errors, excluded=excluded)
        temporary = args.state.with_name(args.state.name + ".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(args.state)

    current = None
    try:
        while queue and (not args.max_pages or len(pages) < args.max_pages):
            current = queue.popleft()
            if current in seen:
                current = None
                continue
            if not crawler.robots.can_fetch(USER_AGENT, current):
                excluded.append(current)
                seen.add(current)
                current = None
                continue
            try:
                with crawler.fetch(current) as response:
                    final_url = normalize_url(response.geturl())
                    if final_url is None or response.headers.get_content_type() not in ("text/html", "application/xhtml+xml"):
                        seen.add(current)
                        current = None
                        continue
                    parser = PageParser()
                    parser.feed(decode_html(response.read(), response.headers))
                pages[final_url] = parser.title
                seen.add(current)
                seen.add(final_url)
                base = urljoin(final_url, parser.base) if parser.base else final_url
                for href in parser.links:
                    target = normalize_url(href, base)
                    if target and target not in scheduled and target not in seen:
                        queue.append(target)
                        scheduled.add(target)
                print(f"[{len(pages)} pages | {len(queue)} queued] {final_url} | {parser.title}", flush=True)
            except (HTTPError, URLError, OSError, ValueError) as error:
                if isinstance(error, HTTPError) and error.code in (429, 503):
                    queue.appendleft(current)
                    print(f"Server requested a pause (HTTP {error.code}); progress saved. Resume later.", flush=True)
                    current = None
                    break
                errors[current] = str(error)
                seen.add(current)
                print(f"SKIP {current}: {error}", flush=True)
            current = None
            save()
    except KeyboardInterrupt:
        if current and current not in seen:
            queue.appendleft(current)
        interrupted = True
        print("Interrupted; saving progress.", flush=True)
    finally:
        # 想定外の中断でも、処理途中のURLを次回のために残す。
        if current and current not in seen and current not in queue:
            queue.appendleft(current)
        save()
        report = dict(start_url=START_URL, traversal_complete=not queue,
                      page_count=len(pages), pending_count=len(queue),
                      errors=errors, robots_excluded=excluded)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {len(pages)} pages to {args.output}; pending={len(queue)}, errors={len(errors)}", flush=True)
    return 130 if interrupted else (2 if queue else 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--max-pages", type=int, default=0, help="0 = no page limit")
    parser.add_argument("--output", type=Path, default=Path("sitemap.csv"))
    parser.add_argument("--state", type=Path, default=Path(".crawl-state.json"))
    parser.add_argument("--report", type=Path, default=Path("crawl_report.json"))
    parser.add_argument("--fresh", action="store_true")
    args = parser.parse_args()
    if args.max_pages < 0 or args.delay < 0:
        parser.error("--max-pages and --delay must be nonnegative")
    return crawl(args)


if __name__ == "__main__":
    raise SystemExit(main())
