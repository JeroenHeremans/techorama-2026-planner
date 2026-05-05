#!/usr/bin/env python3
"""
Scrapes the Techorama BE schedule for Tuesday and Wednesday.
Writes data/tuesday.json, data/wednesday.json, and patches
the INLINE_DATA_START…INLINE_DATA_END block inside index.html
so the page works even when opened as a local file.
"""

import json, re, sys, datetime
from html.parser import HTMLParser
from pathlib import Path

try:
    import requests
    def fetch(url):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        return r.text
except ImportError:
    import urllib.request
    def fetch(url):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", errors="replace")

DAYS = {
    "tuesday":   "https://techorama.be/schedule/tuesday",
    "wednesday": "https://techorama.be/schedule/wednesday",
}

CAT_ALIASES = {
    "AI & Agents": "AI & Agents",
    "Architecture & Leadership": "Architecture & Leadership",
    "Cloud & DevOps": "Cloud & DevOps",
    "Data": "Data",
    "Dev": "Dev",
    "Workplace & Productivity": "Workplace & Productivity",
}


class ScheduleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.blocks = []
        self._cur_time = None
        self._cur_sessions = []
        self._cur_special = None
        self._in_strong = False
        self._strong_buf = ""
        self._in_session_a = False
        self._a_href = ""
        self._a_buf = []

    def _flush_block(self):
        if self._cur_time is None:
            return
        if self._cur_sessions:
            self.blocks.append({"time": self._cur_time, "sessions": self._cur_sessions})
        elif self._cur_special:
            self.blocks.append({"time": self._cur_time, "special": self._cur_special})
        self._cur_time = None
        self._cur_sessions = []
        self._cur_special = None

    def _parse_session(self, href, chunks):
        chunks = [c.strip() for c in chunks if c.strip()]
        if len(chunks) < 3:
            return None
        room = chunks[0]

        # Locate the category chunk by matching against known aliases.
        # Layouts seen in the wild:
        #   [room, title, speaker, "Category Level"]      (4 chunks, joined)
        #   [room, title, speaker, "Category", "Level"]   (5 chunks, separate)
        cat = level = ""
        cat_idx = -1
        for i in range(len(chunks) - 1, 0, -1):
            for c, alias in CAT_ALIASES.items():
                if chunks[i] == c or chunks[i].startswith(c + " "):
                    cat = alias
                    rest = chunks[i][len(c):].strip()
                    if rest:
                        level = rest
                    elif i + 1 < len(chunks):
                        level = " ".join(chunks[i + 1:])
                    cat_idx = i
                    break
            if cat_idx >= 0:
                break

        if cat_idx == -1:
            cat = chunks[-1]
            cat_idx = len(chunks) - 1

        middle = chunks[1:cat_idx]
        if len(middle) >= 2:
            speaker = middle[-1]
            title = " ".join(middle[:-1])
        elif len(middle) == 1:
            title = middle[0]
            speaker = ""
        else:
            title = ""
            speaker = ""

        m = re.search(r"/sessions/(\d+)", href)
        sid = m.group(1) if m else re.sub(r"\W+", "_", title[:30])
        return {"id": sid, "room": room, "title": title, "speaker": speaker,
                "cat": cat, "level": level, "url": f"https://techorama.be{href}"}

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "strong":
            self._in_strong = True
            self._strong_buf = ""
        elif tag == "a" and "/schedule/sessions/" in d.get("href", ""):
            self._in_session_a = True
            self._a_href = d["href"]
            self._a_buf = []

    def handle_endtag(self, tag):
        if tag == "strong":
            self._in_strong = False
            txt = self._strong_buf.strip()
            if re.match(r"\d{2}:\d{2}[–\-]\d{2}:\d{2}", txt):
                self._flush_block()
                self._cur_time = txt
        elif tag == "a" and self._in_session_a:
            self._in_session_a = False
            s = self._parse_session(self._a_href, self._a_buf)
            if s and self._cur_time:
                self._cur_sessions.append(s)
            self._a_buf = []

    def handle_data(self, data):
        if self._in_strong:
            self._strong_buf += data
        elif self._in_session_a:
            self._a_buf.append(data)
        else:
            txt = data.strip()
            if txt and self._cur_time and not self._cur_sessions:
                self._cur_special = txt if self._cur_special is None else f"{self._cur_special} {txt}"

    def finish(self):
        self._flush_block()
        return [b for b in self.blocks if b.get("sessions") or b.get("special")]


def scrape_day(day, url):
    print(f"  Fetching {url} ...", flush=True)
    html = fetch(url)
    p = ScheduleParser()
    p.feed(html)
    blocks = p.finish()
    print(f"  -> {len(blocks)} blocks for {day}", flush=True)
    return blocks


def patch_html(index_path, tue_data, wed_data):
    """Replace the INLINE_DATA block in index.html with fresh data."""
    text = index_path.read_text(encoding="utf-8")
    tue_json = json.dumps(tue_data, ensure_ascii=False, separators=(',', ':'))
    wed_json = json.dumps(wed_data, ensure_ascii=False, separators=(',', ':'))
    replacement = (
        "// INLINE_DATA_START\n"
        f"const FALLBACK = {{\n"
        f"  tuesday:   {tue_json},\n"
        f"  wednesday: {wed_json}\n"
        f"}};\n"
        "// INLINE_DATA_END"
    )
    new_text = re.sub(
        r"// INLINE_DATA_START.*?// INLINE_DATA_END",
        replacement,
        text,
        flags=re.DOTALL,
    )
    if new_text == text:
        print("  ! Warning: INLINE_DATA sentinel not found in index.html", file=sys.stderr)
    else:
        index_path.write_text(new_text, encoding="utf-8")
        print(f"  -> Patched {index_path}", flush=True)


def main():
    root = Path(__file__).parent
    out_dir = root / "data"
    out_dir.mkdir(exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    results = {}
    all_ok = True
    for day, url in DAYS.items():
        try:
            blocks = scrape_day(day, url)
            data = {"day": day, "blocks": blocks, "scraped_at": now}
            (out_dir / f"{day}.json").write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(f"  OK Wrote data/{day}.json", flush=True)
            results[day] = data
        except Exception as exc:
            print(f"  FAIL {day}: {exc}", file=sys.stderr)
            all_ok = False

    if len(results) == 2:
        patch_html(root / "index.html", results["tuesday"], results["wednesday"])

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
