"""JW 방송 자막 추출기.

jw.org 동영상 URL을 받아 한국어 VTT 자막을 다운로드하고
docs/JW방송/YYYY-MM.md 형식(제목 → 빈 줄 → 자막 본문)으로 저장합니다.

지원 URL 형식:
  1) lank / pub-…_VIDEO 형식 → mediator API로 직접 조회
     "https://www.jw.org/finder?srcid=share&wtlocale=KO&lank=pub-jwb-134_9_VIDEO"
  2) docid 형식 → pub-media API 직접 조회, 실패 시 finder 리다이렉트를
     따라가 페이지의 pub·제목으로 자막을 해석
     "https://www.jw.org/finder?wtlocale=KO&docid=503000104&srcid=share"

사용 예:
  python -X utf8 scripts/extract_jwb_subtitle.py \
    "https://www.jw.org/finder?srcid=share&wtlocale=KO&lank=pub-jwb-134_9_VIDEO"
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://data.jw-api.org/mediator/v1/media-items"
PUBMEDIA_BASE = "https://b.jw-cdn.org/apis/pub-media/GETPUBMEDIALINKS"
DEFAULT_LANG = "KO"
DEFAULT_OUT_DIR = os.path.join("docs", "JW방송")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
VIDEO_FORMATS = "M4V,MP4"


def http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def http_get_with_url(url: str) -> tuple[bytes, str]:
    """본문과 (리다이렉트 후) 최종 URL을 함께 반환."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read(), r.geturl()


# ---------------------------------------------------------------------------
# URL 파싱
# ---------------------------------------------------------------------------
def try_extract_lank(url: str) -> str | None:
    """URL에서 lank ID를 추출. 없으면 None."""
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    if "lank" in qs:
        return qs["lank"][0]
    m = re.search(r"(pub-[\w-]+_VIDEO)", url)
    if m:
        return m.group(1)
    m = re.search(r"mediaitems/[^/]+/([^/?#]+)", url)
    if m:
        return m.group(1)
    return None


def extract_docid(url: str) -> str | None:
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    if "docid" in qs:
        return qs["docid"][0]
    m = re.search(r"[?&]docid=(\d+)", url)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# mediator API (lank 경로)
# ---------------------------------------------------------------------------
def fetch_media_info(lank: str, lang: str) -> dict:
    api_url = f"{API_BASE}/{lang}/{lank}?clientType=www"
    raw = http_get(api_url)
    return json.loads(raw.decode("utf-8"))


def find_subtitle_url(info: dict) -> str:
    for item in info.get("media", []):
        for f in item.get("files", []):
            subs = f.get("subtitles") or {}
            if subs.get("url"):
                return subs["url"]
    raise ValueError("이 미디어에 자막 URL이 없습니다.")


def get_title(info: dict) -> str:
    media = info.get("media", [])
    return media[0].get("title", "") if media else ""


# ---------------------------------------------------------------------------
# pub-media API (docid 경로)
# ---------------------------------------------------------------------------
def fetch_pubmedia(lang: str, *, docid: str | None = None, pub: str | None = None) -> object:
    """pub-media GETPUBMEDIALINKS 호출. 정상 시 dict, 404 등은 list 반환."""
    params = {
        "output": "json",
        "fileformat": VIDEO_FORMATS,
        "alllangs": "0",
        "langwritten": lang,
        "txtCMSLang": lang,
    }
    if docid:
        params["docid"] = docid
    if pub:
        params["pub"] = pub
    url = f"{PUBMEDIA_BASE}?{urllib.parse.urlencode(params)}"
    try:
        raw = http_get(url)
    except urllib.error.HTTPError:
        # docid/pub 조합이 없으면 404 — 데이터 없음으로 처리
        return []
    return json.loads(raw.decode("utf-8"))


def pick_subtitle_entry(data: object, lang: str, match_title: str | None = None) -> tuple[str, str] | None:
    """pub-media 응답에서 (자막URL, 제목) 선택. 없으면 None.

    match_title 지정 시 제목이 일치하는 항목을 우선(정확→끝일치→포함).
    자막이 있는 항목(화면 해설본 등 자막 없는 변형 제외)만 후보로 본다.
    """
    if not isinstance(data, dict):
        return None
    files = data.get("files", {}).get(lang, {})
    candidates: list[tuple[str, str]] = []
    for items in files.values():
        for f in items:
            sub = (f.get("subtitles") or {}).get("url")
            if sub:
                candidates.append((f.get("title", ""), sub))
    if not candidates:
        return None
    if match_title:
        matchers = (
            lambda t: t == match_title,
            lambda t: t.endswith("—" + match_title) or t.endswith("-" + match_title),
            lambda t: match_title in t,
        )
        for matcher in matchers:
            for title, sub in candidates:
                if matcher(title):
                    return sub, title
        return None
    title, sub = candidates[0]
    return sub, title


def scrape_pub(html: str) -> str | None:
    """페이지의 data-jsonurl 중 동영상 포맷을 가리키는 항목에서 pub 추출."""
    jsonurls = [m.group(1).replace("&amp;", "&") for m in re.finditer(r'data-jsonurl="([^"]+)"', html)]
    # 동영상(M4V/MP4) 포맷을 포함한 jsonurl 우선
    for u in jsonurls:
        if "GETPUBMEDIALINKS" in u and ("M4V" in u or "MP4" in u):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(u).query)
            if "pub" in qs:
                return qs["pub"][0]
    for u in jsonurls:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(u).query)
        if "pub" in qs:
            return qs["pub"][0]
    return None


def scrape_title(html: str) -> str | None:
    m = re.search(r'property=["\']og:title["\']\s+content=["\']([^"\']+)', html)
    if m:
        return m.group(1).strip()
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    return m.group(1).strip() if m else None


def resolve_via_docid(url: str, docid: str, lang: str) -> tuple[str, str]:
    """docid 기반으로 (자막URL, 전체제목) 해석."""
    # 1) pub-media에 docid 직접 질의 (일부 출판물은 바로 응답)
    hit = pick_subtitle_entry(fetch_pubmedia(lang, docid=docid), lang)
    if hit:
        sub, title = hit
        return sub, title
    # 2) finder 리다이렉트를 따라가 페이지의 pub·제목으로 매칭
    html_bytes, _final = http_get_with_url(url)
    html = html_bytes.decode("utf-8", errors="replace")
    pub = scrape_pub(html)
    episode = scrape_title(html)
    if not pub:
        raise ValueError(f"docid {docid}: 페이지에서 pub 식별자를 찾지 못했습니다.")
    print(f"[pub] {pub} / [episode] {episode}", file=sys.stderr)
    hit = pick_subtitle_entry(fetch_pubmedia(lang, pub=pub), lang, match_title=episode)
    if hit:
        sub, title = hit
        return sub, title
    raise ValueError(f"docid {docid}: 자막을 찾지 못했습니다 (pub={pub}, episode={episode!r}).")


def resolve_subtitle(url: str, lang: str) -> tuple[str, str]:
    """URL에서 (자막URL, 전체제목)을 해석. lank → mediator, docid → pub-media."""
    lank = try_extract_lank(url)
    if lank:
        print(f"[lank] {lank}", file=sys.stderr)
        info = fetch_media_info(lank, lang)
        return find_subtitle_url(info), get_title(info)
    docid = extract_docid(url)
    if docid:
        print(f"[docid] {docid}", file=sys.stderr)
        return resolve_via_docid(url, docid, lang)
    raise ValueError(f"URL에서 lank 또는 docid를 추출할 수 없습니다: {url}")


# ---------------------------------------------------------------------------
# VTT 파싱 / 출력 경로
# ---------------------------------------------------------------------------
def parse_vtt(content: str) -> list[str]:
    blocks = re.split(r"\r?\n\s*\r?\n", content)
    out: list[str] = []
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        head = lines[0]
        if head.startswith(("WEBVTT", "NOTE", "STYLE", "REGION", "Kind:", "Language:")):
            continue
        time_idx = -1
        for i, ln in enumerate(lines):
            if "-->" in ln:
                time_idx = i
                break
        if time_idx < 0:
            continue
        for txt in lines[time_idx + 1:]:
            cleaned = re.sub(r"<[^>]+>", "", txt).strip()
            cleaned = (
                cleaned.replace("&amp;", "&")
                .replace("&lt;", "<")
                .replace("&gt;", ">")
                .replace("&nbsp;", " ")
                .replace("&#39;", "'")
                .replace("&quot;", '"')
            )
            if cleaned:
                out.append(cleaned)
    return out


def slugify(title: str) -> str:
    """제목을 파일명으로. 공백→_, 대시류→-, 금지문자 제거."""
    s = title.strip()
    s = s.replace("—", "-").replace("–", "-")
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r'[\\/:*?"<>|]', "", s)
    return s


def derive_output_path(title: str, out_dir: str) -> tuple[str, str]:
    """제목에서 저장경로와 표시제목 결정.

    'YYYY년 M월'이 있으면 월별 방송으로 보고 'YYYY-MM.md' + 'JW 방송—YYYY년 M월'.
    없으면(시리즈 동영상 등) 제목 슬러그로 파일명을 만들고 제목을 그대로 쓴다.
    """
    m = re.search(r"(\d{4})\s*년\s*(\d{1,2})\s*월", title)
    if m:
        year, month = m.group(1), m.group(2).zfill(2)
        filename = f"{year}-{month}.md"
        short_title = f"JW 방송—{year}년 {int(month)}월"
        return os.path.join(out_dir, filename), short_title
    return os.path.join(out_dir, slugify(title) + ".md"), title


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", help="jw.org 동영상 URL (lank 또는 docid 형식)")
    ap.add_argument("--lang", default=DEFAULT_LANG, help=f"언어 코드 (기본 {DEFAULT_LANG})")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help=f"출력 폴더 (기본 {DEFAULT_OUT_DIR})")
    ap.add_argument("--out", help="출력 파일 경로 직접 지정 (--out-dir보다 우선)")
    ap.add_argument("--title", help="첫 줄 제목 직접 지정 (생략 시 제목에서 자동 추출)")
    args = ap.parse_args()

    vtt_url, full_title = resolve_subtitle(args.url, args.lang)
    print(f"[제목] {full_title}", file=sys.stderr)
    print(f"[자막] {vtt_url}", file=sys.stderr)

    vtt_text = http_get(vtt_url).decode("utf-8-sig")
    texts = parse_vtt(vtt_text)

    if args.out:
        out_path = args.out
        short_title = args.title or full_title
    else:
        out_path, short_title = derive_output_path(full_title, args.out_dir)
        if args.title:
            short_title = args.title

    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(short_title + "\n\n")
        for t in texts:
            f.write(t + "\n")

    size = os.path.getsize(out_path)
    print(f"저장 완료: {out_path} ({len(texts)}줄, {size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
