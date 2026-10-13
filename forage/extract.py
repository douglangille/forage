"""Turn an article page (or feed-supplied HTML) into clean reader HTML and clip markdown."""
import re
from html import unescape
from urllib.parse import urljoin

import nh3
import trafilatura
from markdownify import markdownify

ALLOWED_TAGS = {
    "a", "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "blockquote",
    "pre", "code", "em", "strong", "b", "i", "u", "s", "sub", "sup", "img", "figure",
    "figcaption", "table", "thead", "tbody", "tr", "th", "td", "dl", "dt", "dd", "details", "summary",
}
ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "th": {"colspan", "rowspan"},
    "td": {"colspan", "rowspan"},
}


def sanitize(html: str, base_url: str = "") -> str:
    if base_url:
        html = _absolutize(html, base_url)
    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer",
        strip_comments=True,
    )


_URL_ATTR = re.compile(r'(\s(?:href|src)\s*=\s*)(["\'])(.*?)\2', re.I | re.S)


def _absolutize(html: str, base: str) -> str:
    def fix(m):
        val = m.group(3).strip()
        if val.startswith(("#", "data:", "mailto:", "javascript:")):
            return m.group(0)
        return f"{m.group(1)}{m.group(2)}{urljoin(base, val)}{m.group(2)}"

    return _URL_ATTR.sub(fix, html)


def extract_article(page_html: str, url: str) -> str | None:
    """Readable article HTML from a full page, or None if nothing usable was found."""
    out = trafilatura.extract(
        page_html,
        url=url,
        output_format="html",
        include_images=True,
        include_links=True,
        include_formatting=True,
        include_tables=True,
        favor_recall=True,
    )
    if not out:
        return None
    # trafilatura wraps in <html><body>…; sanitize drops the wrappers, keeps the content.
    cleaned = sanitize(out, url).strip()
    return cleaned if len(_text(cleaned)) > 200 else None


def _text(html: str) -> str:
    return unescape(re.sub(r"<[^>]+>", "", html))


def to_markdown(html: str) -> str:
    md = markdownify(html, heading_style="ATX", bullets="-", strip=["script", "style"])
    md = re.sub(r"[ \t]+\n", "\n", md)
    return re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"


def looks_full(feed_html: str, summary: str) -> bool:
    """Heuristic: the feed already carries the whole article, so skip fetching the page."""
    t = len(_text(feed_html))
    return t > 1500 and t > 1.5 * max(len(summary), 1)


def strip_leading_title(html: str, title: str) -> str:
    """Extractors often start with the article's own heading; the reader already shows it."""
    m = re.match(r"\s*<h[1-3][^>]*>(.*?)</h[1-3]>", html, re.S | re.I)
    if m and re.sub(r"\W+", "", _text(m.group(1))).lower() == re.sub(r"\W+", "", title).lower():
        return html[m.end():].lstrip()
    return html
