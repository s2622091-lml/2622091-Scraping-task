"""外部サイトにアクセスせずに、URL処理・HTML解析・探索・再開を確認する。"""

import argparse
import csv
import io
import json
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from scraper import START_URL, PageParser, crawl, decode_html, normalize_url, save_csv


class ScraperTests(unittest.TestCase):
    def test_links_and_fragments(self):
        self.assertEqual(normalize_url("../about.html#top", START_URL + "guide/"),
                         START_URL + "about.html")
        self.assertEqual(normalize_url("#main"), START_URL)

    def test_scope_and_files(self):
        for url in ["https://example.com/", "https://dl.musashino-u.ac.jp/",
                    "mailto:test@example.com", "/file.PDF", "javascript:void(0)"]:
            self.assertIsNone(normalize_url(url))

    def test_meaningful_query_is_preserved(self):
        self.assertEqual(normalize_url("/news/?page=2&utm_source=test#top"),
                         START_URL + "news/?page=2")

    def test_title_and_links(self):
        parser = PageParser()
        parser.feed('<title> 大学 &amp;\n 学び </title><a href="/guide/">案内</a>'
                    '<script>const fake = "<a href=bad>";</script>')
        self.assertEqual(parser.title, "大学 & 学び")
        self.assertEqual(parser.links, ["/guide/"])

    def test_legacy_encoding(self):
        headers = Message()
        body = '<meta charset="shift_jis"><title>大学</title>'.encode("shift_jis")
        self.assertIn("大学", decode_html(body, headers))

    def test_csv_handles_japanese_and_commas(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sitemap.csv"
            save_csv({START_URL: '大学, "学び"'}, path)
            with path.open(encoding="utf-8-sig", newline="") as file:
                self.assertEqual(list(csv.reader(file)),
                                 [["url", "title"], [START_URL, '大学, "学び"']])

    def test_crawl_resumes_and_deduplicates(self):
        html_pages = {
            START_URL: '<title>大学</title><a href="/a/">A</a><a href="/a/#x">A</a>'
                       '<a href="https://example.com/">外部</a><a href="/missing/">404</a>',
            START_URL + "a/": '<title>A</title><a href="/">Home</a>',
        }
        visited = []

        class Response(io.BytesIO):
            def __init__(self, url):
                super().__init__(html_pages[url].encode("utf-8"))
                self.url = url
                self.headers = Message()
                self.headers["Content-Type"] = "text/html; charset=utf-8"

            def geturl(self):
                return self.url

        class FakeCrawler:
            def __init__(self, delay):
                self.robots = self

            def load_robots(self):
                pass

            def can_fetch(self, agent, url):
                return True

            def fetch(self, url):
                visited.append(url)
                if url not in html_pages:
                    raise HTTPError(url, 404, "Not Found", {}, None)
                return Response(url)

        with tempfile.TemporaryDirectory() as tmp, patch("scraper.Crawler", FakeCrawler):
            root = Path(tmp)
            args = argparse.Namespace(delay=1, max_pages=1, fresh=False,
                                      state=root / "state.json", output=root / "sitemap.csv",
                                      report=root / "report.json")
            self.assertEqual(crawl(args), 2)  # ページ制限で中断
            args.max_pages = 0
            self.assertEqual(crawl(args), 0)  # 続きから最後まで
            state = json.loads(args.state.read_text())
            self.assertEqual(state["pages"], {START_URL: "大学", START_URL + "a/": "A"})
            self.assertEqual(visited.count(START_URL), 1)
            self.assertEqual(visited.count(START_URL + "a/"), 1)
            self.assertIn(START_URL + "missing/", state["errors"])

    def test_unexpected_failure_keeps_inflight_url(self):
        fake = Mock()
        fake.robots.can_fetch.return_value = True
        fake.fetch.side_effect = RuntimeError("Unexpected interruption")
        with tempfile.TemporaryDirectory() as tmp, patch("scraper.Crawler", return_value=fake):
            root = Path(tmp)
            args = argparse.Namespace(delay=1, max_pages=0, fresh=False,
                                      state=root / "state.json", output=root / "sitemap.csv",
                                      report=root / "report.json")
            with self.assertRaises(RuntimeError):
                crawl(args)
            state = json.loads(args.state.read_text())
            self.assertEqual(state["queue"], [START_URL])
            self.assertFalse(json.loads(args.report.read_text())["traversal_complete"])


if __name__ == "__main__":
    unittest.main()
