"""Scrape a job posting URL for the company name, job title and description text.

Strategy, in order of preference:
  1. schema.org JobPosting JSON-LD (emitted by Greenhouse, Lever, Workday,
     LinkedIn, Indeed and most ATS platforms) - most reliable.
  2. Open Graph / <title> / meta tags for the company + title.
  3. Largest visible text block on the page for the description.

Notes:
  - Many job boards' Terms of Service restrict automated access. Use this on
    postings you are entitled to read, keep the volume low (this tool does one
    posting per run), and prefer the employer's own careers page where possible.
  - You can always bypass scraping with --company / --title / a pasted
    description file (see main.py).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html import unescape

from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass
class JobPosting:
    url: str
    company: str
    title: str
    description: str

    def is_usable(self) -> bool:
        return bool(self.company and self.title and len(self.description) > 200)


def _clean(text: str) -> str:
    text = unescape(text or "")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _from_json_ld(soup: BeautifulSoup) -> dict | None:
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text() or ""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        if isinstance(data, dict) and "@graph" in data:
            candidates = data["@graph"]
        for node in candidates:
            if not isinstance(node, dict):
                continue
            node_type = node.get("@type", "")
            types = node_type if isinstance(node_type, list) else [node_type]
            if "JobPosting" in types:
                return node
    return None


def _description_fallback(soup: BeautifulSoup) -> str:
    for junk in soup(["script", "style", "nav", "header", "footer", "form", "noscript"]):
        junk.decompose()
    blocks = []
    for tag in soup.find_all(["div", "section", "article", "main"]):
        text = tag.get_text("\n", strip=True)
        if len(text) > 400:
            blocks.append(text)
    if blocks:
        return max(blocks, key=len)
    return soup.get_text("\n", strip=True)


def _company_fallback(soup: BeautifulSoup) -> str:
    for prop in ("og:site_name", "twitter:site"):
        tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        if tag and tag.get("content"):
            return tag["content"].strip().lstrip("@")
    title = (soup.title.string if soup.title else "") or ""
    # "Senior Engineer - Acme Corp" / "Senior Engineer at Acme Corp | Careers"
    m = re.split(r"\s[-|–]\s|\sat\s", title)
    if len(m) > 1:
        return m[-1].split("|")[0].strip()
    return ""


def _title_fallback(soup: BeautifulSoup) -> str:
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)
    tag = soup.find("meta", property="og:title")
    if tag and tag.get("content"):
        return tag["content"].strip()
    return (soup.title.string.strip() if soup.title and soup.title.string else "")


def parse_html(url: str, html: str) -> JobPosting:
    soup = BeautifulSoup(html, "lxml")

    company = title = description = ""
    node = _from_json_ld(soup)
    if node:
        title = _clean(str(node.get("title", "")))
        org = node.get("hiringOrganization")
        if isinstance(org, dict):
            company = _clean(str(org.get("name", "")))
        elif isinstance(org, str):
            company = _clean(org)
        raw_desc = node.get("description", "")
        if raw_desc:
            description = _clean(BeautifulSoup(raw_desc, "lxml").get_text("\n", strip=True))

    if not company:
        company = _clean(_company_fallback(soup))
    if not title:
        title = _clean(_title_fallback(soup))
    if not description or len(description) < 200:
        description = _clean(_description_fallback(soup))

    return JobPosting(url=url, company=company, title=title, description=description)


def scrape_job(url: str, timeout_ms: int = 45_000, headless: bool = True) -> JobPosting:
    """Load the URL in a real browser (handles JS-rendered postings) and parse it."""
    from playwright.sync_api import sync_playwright  # imported lazily

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(user_agent=USER_AGENT, locale="en-GB")
        page = context.new_page()
        try:
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=8_000)
            except Exception:
                pass  # networkidle is best-effort
            html = page.content()
        finally:
            context.close()
            browser.close()

    posting = parse_html(url, html)
    if not posting.company:
        posting.company = "Unknown Company"
    if not posting.title:
        posting.title = "Unknown Role"
    return posting
