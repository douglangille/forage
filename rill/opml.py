from xml.etree import ElementTree as ET
from xml.sax.saxutils import quoteattr


def parse_opml(data: bytes) -> list[dict]:
    root = ET.fromstring(data)
    out: list[dict] = []

    def walk(node, folder: str):
        for o in node.findall("outline"):
            url = o.get("xmlUrl")
            if url:
                out.append({"url": url, "title": o.get("title") or o.get("text") or "", "folder": folder})
            else:
                walk(o, o.get("title") or o.get("text") or folder)

    body = root.find("body")
    if body is not None:
        walk(body, "")
    return out


def build_opml(feeds) -> str:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<opml version="2.0"><head><title>rill</title></head><body>']
    by_folder: dict[str, list] = {}
    for f in feeds:
        by_folder.setdefault(f["folder"], []).append(f)

    def line(f, pad):
        return (f'{pad}<outline type="rss" text={quoteattr(f["title"] or f["url"])} '
                f'title={quoteattr(f["title"] or f["url"])} xmlUrl={quoteattr(f["url"])} '
                f'htmlUrl={quoteattr(f["site_url"] or "")}/>')

    for folder, items in sorted(by_folder.items()):
        if folder:
            lines.append(f"  <outline text={quoteattr(folder)} title={quoteattr(folder)}>")
            lines += [line(f, "    ") for f in items]
            lines.append("  </outline>")
        else:
            lines += [line(f, "  ") for f in items]
    lines.append("</body></opml>")
    return "\n".join(lines) + "\n"
