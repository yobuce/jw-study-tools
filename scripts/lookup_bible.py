#!/usr/bin/env python3
"""성구 참조 문자열로 성경 본문과 연구 자료를 조회하는 스크립트.

사용법:
    python -X utf8 scripts/lookup_bible.py "요한 3:16"              # 본문만
    python -X utf8 scripts/lookup_bible.py "창세기 1:1-3"           # 범위
    python -X utf8 scripts/lookup_bible.py "로마서 8:28; 요한 3:16" # 복수 성구
    python -X utf8 scripts/lookup_bible.py "요한 3:16" --study      # 연구 자료만
    python -X utf8 scripts/lookup_bible.py "요한 3:16" --all        # 본문 + 모든 연구 자료
    python -X utf8 scripts/lookup_bible.py --books                  # 책 이름 목록
"""

import sys
import os
import re
import sqlite3
import json
from pathlib import Path

# read_jwpub에서 필요한 함수/상수 재사용
sys.path.insert(0, str(Path(__file__).parent))
from read_jwpub import (
    decrypt_blob, html_to_text, _get_bible_db, _get_rsg_db,
    get_bible_verse_text, get_study_references, format_verse_range,
    verse_id_to_ref, verse_ref_to_id, BIBLE_DB_PATH, MEPSUNIT_DB_PATH,
    build_password, BIBLE_MANIFEST_PATH,
)


def _resolve_verse_id(book_num, chapter, verse, first_ch_id):
    """(책,장,절)→ID. BibleVerse.Label 매핑 우선, 없으면 장첫ID 산술 폴백."""
    vid = verse_ref_to_id(book_num, chapter, verse)
    return vid if vid is not None else first_ch_id + (verse - 1)

# 단장 책 (장이 1개뿐인 책)
SINGLE_CHAPTER_BOOKS = {31, 57, 63, 64, 65}  # 오바댜, 빌레몬서, 요한 2서, 요한 3서, 유다서


# ---------------------------------------------------------------------------
# 책 이름 매핑
# ---------------------------------------------------------------------------

_book_name_map = None  # {이름: 책번호}


def build_book_name_map() -> dict[str, int]:
    """성경 책 이름/약어 → 책 번호 매핑을 구축."""
    global _book_name_map
    if _book_name_map is not None:
        return _book_name_map

    name_map = {}

    # 1) nwtsty_KO.db BibleBook에서 정식 이름 로드
    if BIBLE_DB_PATH.exists():
        conn = sqlite3.connect(str(BIBLE_DB_PATH))
        cur = conn.cursor()
        cur.execute(
            'SELECT BibleBookId, BookDisplayTitle FROM BibleBook '
            'ORDER BY BibleBookId'
        )
        for book_id, title in cur.fetchall():
            if title:
                name_map[title] = book_id
        conn.close()

    # 2) mepsunit.db BibleBookName에서 약어 로드
    if MEPSUNIT_DB_PATH.exists():
        try:
            conn = sqlite3.connect(str(MEPSUNIT_DB_PATH))
            cur = conn.cursor()
            cur.execute('''
                SELECT bn.BookNumber,
                       bn.StandardBookName,
                       bn.StandardBookAbbreviation,
                       bn.OfficialBookAbbreviation,
                       bn.StandardSingularBookName,
                       bn.StandardSingularBookAbbreviation
                FROM BibleBookName bn
                JOIN BibleCluesInfo ci ON bn.BibleCluesInfoId = ci.BibleCluesInfoId
                JOIN Language l ON ci.LanguageId = l.LanguageId
                WHERE l.Symbol = 'KO' AND ci.BibleInfoId = 2
                ORDER BY bn.BookNumber
            ''')
            for row in cur.fetchall():
                book_num = row[0]
                for name in row[1:]:
                    if name and name.strip():
                        name_map[name.strip()] = book_num
            conn.close()
        except Exception:
            pass

    # 3) 하드코딩 약어 fallback (mepsunit.db에 없는 추가 약어)
    _extra_abbrevs = {
        '창': 1, '창세': 1,
        '출': 2, '출애굽': 2,
        '레': 3, '레위': 3,
        '민': 4, '민수': 4,
        '신': 5, '신명': 5,
        '수': 6,
        '삿': 7, '사사': 7,
        '룻': 8,
        '삼상': 9, '사무엘상': 9,
        '삼하': 10, '사무엘하': 10,
        '왕상': 11, '열왕기상': 11,
        '왕하': 12, '열왕기하': 12,
        '대상': 13, '역대기상': 13, '역대상': 13,
        '대하': 14, '역대기하': 14, '역대하': 14,
        '라': 15,
        '느': 16,
        '더': 17,
        '욥': 18,
        '시': 19,
        '잠': 20,
        '전': 21, '전도': 21,
        '아': 22, '솔로몬': 22, '아가': 22,
        '사': 23, '이사야': 23,
        '렘': 24,
        '애': 25, '애가': 25,
        '겔': 26,
        '단': 27, '다니엘': 27,
        '호': 28,
        '욜': 29,
        '암': 30,
        '옵': 31, '오바댜': 31,
        '욘': 32,
        '미': 33,
        '나': 34,
        '합': 35, '하박국': 35,
        '습': 36,
        '학': 37,
        '슥': 38,
        '말': 39, '말라기': 39,
        '마': 40, '마태': 40, '마태복음': 40, '맛': 40,
        '막': 41, '마가': 41, '마가복음': 41,
        '눅': 42, '누가': 42, '누가복음': 42,
        '요': 43, '요한': 43, '요한복음': 43,
        '행': 44, '사도': 44, '사도행전': 44,
        '롬': 45, '로마': 45, '로마서': 45,
        '고전': 46, '고린도전서': 46,
        '고후': 47, '고린도후서': 47,
        '갈': 48, '갈라디아': 48,
        '엡': 49, '에베소': 49,
        '빌': 50, '빌립보': 50,
        '골': 51, '골로새': 51,
        '살전': 52,
        '살후': 53,
        '딤전': 54,
        '딤후': 55,
        '딛': 56, '디도': 56,
        '몬': 57, '빌레몬': 57,
        '히': 58, '히브리': 58,
        '약': 59, '야고보': 59,
        '벧전': 60,
        '벧후': 61,
        '요1': 62, '요일': 62,
        '요2': 63, '요이': 63,
        '요3': 64, '요삼': 64,
        '유': 65, '유다': 65,
        '계': 66, '계시록': 66,
    }
    for name, num in _extra_abbrevs.items():
        if name not in name_map:
            name_map[name] = num

    _book_name_map = name_map
    return _book_name_map


# ---------------------------------------------------------------------------
# BibleChapter 캐시
# ---------------------------------------------------------------------------

_chapter_cache = None  # {(book, chapter): (first_verse_id, last_verse_id)}


def _load_chapter_cache():
    """BibleChapter 테이블에서 FirstVerseId/LastVerseId를 로드."""
    global _chapter_cache
    if _chapter_cache is not None:
        return _chapter_cache

    _chapter_cache = {}
    if not BIBLE_DB_PATH.exists():
        return _chapter_cache

    conn = sqlite3.connect(str(BIBLE_DB_PATH))
    cur = conn.cursor()
    cur.execute(
        'SELECT BookNumber, ChapterNumber, FirstVerseId, LastVerseId '
        'FROM BibleChapter ORDER BY BookNumber, ChapterNumber'
    )
    for book, ch, first_id, last_id in cur.fetchall():
        _chapter_cache[(book, ch)] = (first_id, last_id)
    conn.close()
    return _chapter_cache


# ---------------------------------------------------------------------------
# 참조 문자열 파싱
# ---------------------------------------------------------------------------

def parse_reference(ref_str: str) -> list[tuple[int, int]]:
    """성구 참조 문자열을 파싱하여 [(first_verse_id, last_verse_id), ...] 반환.

    지원 형식:
        "요한 3:16"        → 단일 구절
        "창세기 1:1-3"     → 절 범위
        "유다서 3"          → 단장 책
        "유다서 3-5"        → 단장 책 범위
        "요한 3:16; 로마 8:28"  → 세미콜론 구분 복수 성구
    """
    name_map = build_book_name_map()
    chapters = _load_chapter_cache()

    results = []
    # 세미콜론으로 분리
    parts = [p.strip() for p in ref_str.split(';') if p.strip()]

    for part in parts:
        parsed = _parse_single_reference(part, name_map, chapters)
        if parsed:
            results.append(parsed)
        else:
            print(f"경고: '{part}'를 파싱할 수 없습니다.", file=sys.stderr)

    return results


def _parse_single_reference(
    ref: str,
    name_map: dict[str, int],
    chapters: dict,
) -> tuple[int, int] | None:
    """단일 성구 참조를 파싱."""
    ref = ref.strip()
    if not ref:
        return None

    # longest-match-first로 책 이름 식별
    sorted_names = sorted(name_map.keys(), key=len, reverse=True)
    book_num = None
    remaining = ref

    for name in sorted_names:
        if ref.startswith(name):
            rest = ref[len(name):]
            # 이름 뒤에 숫자/공백/콜론이 오거나 문자열 끝이어야 함
            if not rest or rest[0] in ' \t:0123456789':
                book_num = name_map[name]
                remaining = rest.strip()
                break

    if book_num is None:
        return None

    # 남은 부분 파싱: "장:절", "장:절-절", "절"(단장), "절-절"(단장)
    if not remaining:
        # 책 전체 (첫 장 첫 절 ~ 마지막 장 마지막 절)
        first_ch = min(
            (ch for (b, ch) in chapters if b == book_num), default=None
        )
        last_ch = max(
            (ch for (b, ch) in chapters if b == book_num), default=None
        )
        if first_ch is None:
            return None
        first_id = chapters[(book_num, first_ch)][0]
        last_id = chapters[(book_num, last_ch)][1]
        return (first_id, last_id)

    # 장:절 패턴
    m = re.match(r'^(\d+)\s*:\s*(\d+)\s*(?:-\s*(\d+))?\s*$', remaining)
    if m:
        ch = int(m.group(1))
        v_start = int(m.group(2))
        v_end = int(m.group(3)) if m.group(3) else v_start

        key = (book_num, ch)
        if key not in chapters:
            print(f"경고: {book_num}권 {ch}장을 찾을 수 없습니다.", file=sys.stderr)
            return None

        first_ch_id, last_ch_id = chapters[key]
        first_id = _resolve_verse_id(book_num, ch, v_start, first_ch_id)
        last_id = _resolve_verse_id(book_num, ch, v_end, first_ch_id)
        # 범위 검증
        last_id = min(last_id, last_ch_id)
        return (first_id, last_id)

    # 단장 책: 절 번호만 (예: "3" 또는 "3-5")
    m = re.match(r'^(\d+)\s*(?:-\s*(\d+))?\s*$', remaining)
    if m:
        v_start = int(m.group(1))
        v_end = int(m.group(2)) if m.group(2) else v_start

        if book_num in SINGLE_CHAPTER_BOOKS:
            ch = 1
        else:
            # 단장 책이 아니면 장 번호로 해석 (장 전체)
            ch = v_start
            key = (book_num, ch)
            if key in chapters:
                first_id, last_id = chapters[key]
                if m.group(2):
                    # "3-5"를 장 범위로 해석
                    last_ch = int(m.group(2))
                    last_key = (book_num, last_ch)
                    if last_key in chapters:
                        last_id = chapters[last_key][1]
                return (first_id, last_id)
            return None

        key = (book_num, ch)
        if key not in chapters:
            return None

        first_ch_id, last_ch_id = chapters[key]
        first_id = _resolve_verse_id(book_num, ch, v_start, first_ch_id)
        last_id = _resolve_verse_id(book_num, ch, v_end, first_ch_id)
        last_id = min(last_id, last_ch_id)
        return (first_id, last_id)

    return None


# ---------------------------------------------------------------------------
# 연구 노트 조회 (nwtsty_KO.db VerseCommentary)
# ---------------------------------------------------------------------------

def get_verse_commentary(first_id: int, last_id: int) -> list[dict]:
    """nwtsty_KO.db에서 BibleVerseId 범위의 연구 노트를 조회."""
    conn, pw = _get_bible_db()
    if conn is None:
        return []

    cur = conn.cursor()
    cur.execute('''
        SELECT vcm.BibleVerseId, vc.Label, vc.Content
        FROM VerseCommentaryMap vcm
        JOIN VerseCommentary vc ON vcm.VerseCommentaryId = vc.VerseCommentaryId
        WHERE vcm.BibleVerseId BETWEEN ? AND ?
          AND vc.CommentaryType = 2
        ORDER BY vcm.BibleVerseId
    ''', (first_id, last_id))

    results = []
    for vid, label, blob in cur.fetchall():
        if not blob:
            continue
        try:
            html = decrypt_blob(blob, pw).decode('utf-8')
            text = html_to_text(html)
        except Exception:
            continue
        # Label에서 절 참조 추출
        label_text = re.sub(r'<[^>]+>', '', label).strip() if label else ''
        ref = format_verse_range(vid, vid)
        results.append({
            'reference': ref,
            'label': label_text,
            'content': text,
        })
    return results


# ---------------------------------------------------------------------------
# 각주 조회 (nwtsty_KO.db Footnote)
# ---------------------------------------------------------------------------

def get_footnotes(first_id: int, last_id: int) -> list[dict]:
    """nwtsty_KO.db에서 BibleVerseId 범위의 각주를 조회."""
    conn, pw = _get_bible_db()
    if conn is None:
        return []

    cur = conn.cursor()
    cur.execute('''
        SELECT FootnoteId, BibleVerseId, Content
        FROM Footnote
        WHERE BibleVerseId BETWEEN ? AND ?
        ORDER BY BibleVerseId, FootnoteId
    ''', (first_id, last_id))

    results = []
    for fid, vid, blob in cur.fetchall():
        if not blob:
            continue
        try:
            html = decrypt_blob(blob, pw).decode('utf-8')
            text = html_to_text(html)
        except Exception:
            continue
        ref = format_verse_range(vid, vid)
        results.append({
            'reference': ref,
            'content': text,
        })
    return results


# ---------------------------------------------------------------------------
# 출력 포맷팅
# ---------------------------------------------------------------------------

def print_verse_text(first_id: int, last_id: int):
    """성구 본문 출력."""
    ref = format_verse_range(first_id, last_id)
    text = get_bible_verse_text(first_id, last_id)
    if text:
        print(f"\n■ {ref}")
        print(f"  {text}")
    else:
        print(f"\n■ {ref}: 본문을 찾을 수 없습니다.")


def print_study_materials(first_id: int, last_id: int):
    """연구 자료 (연구 노트 + 각주 + 연구 자료 찾아보기) 출력."""
    ref = format_verse_range(first_id, last_id)

    # 연구 노트
    commentaries = get_verse_commentary(first_id, last_id)
    if commentaries:
        print(f"\n{'─' * 40}")
        print(f"연구 노트 — {ref}")
        print(f"{'─' * 40}")
        for c in commentaries:
            if c['label']:
                print(f"\n  ▸ {c['label']}")
            if c['content']:
                for line in c['content'].splitlines():
                    if line.strip():
                        print(f"    {line.strip()}")

    # 각주
    footnotes = get_footnotes(first_id, last_id)
    if footnotes:
        print(f"\n{'─' * 40}")
        print(f"각주 — {ref}")
        print(f"{'─' * 40}")
        for fn in footnotes:
            print(f"\n  ▸ {fn['reference']}")
            if fn['content']:
                for line in fn['content'].splitlines():
                    if line.strip():
                        print(f"    {line.strip()}")

    # 연구 자료 찾아보기 (rsg19)
    study_refs = get_study_references(first_id, last_id)
    if study_refs:
        print(f"\n{'─' * 40}")
        print(f"연구 자료 찾아보기 — {ref}")
        print(f"{'─' * 40}")
        for sr in study_refs:
            print(f"\n  ■ {sr['reference']}")
            for ext in sr.get('extracts', []):
                if ext.get('caption'):
                    print(f"\n    ▸ {ext['caption']}")
                if ext.get('content'):
                    for line in ext['content'].splitlines():
                        if line.strip():
                            print(f"      {line.strip()}")

    if not commentaries and not footnotes and not study_refs:
        print(f"\n{ref}에 대한 연구 자료가 없습니다.")


def print_books():
    """사용 가능한 책 이름 목록 출력."""
    name_map = build_book_name_map()

    # 책 번호 → 이름 목록 그룹핑
    book_names = {}
    for name, num in name_map.items():
        if num not in book_names:
            book_names[num] = []
        book_names[num].append(name)

    print("성경 66권 — 사용 가능한 이름/약어")
    print("=" * 50)
    for num in sorted(book_names.keys()):
        names = sorted(book_names[num], key=len, reverse=True)
        primary = names[0]
        aliases = ', '.join(names[1:]) if len(names) > 1 else ''
        marker = ' [단장]' if num in SINGLE_CHAPTER_BOOKS else ''
        print(f"  {num:>2}. {primary:<14s}  ({aliases}){marker}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    if sys.argv[1] == '--books':
        print_books()
        return

    # 옵션 파싱
    mode = 'text'  # text | study | all
    ref_parts = []
    for arg in sys.argv[1:]:
        if arg == '--study':
            mode = 'study'
        elif arg == '--all':
            mode = 'all'
        elif arg == '--books':
            print_books()
            return
        else:
            ref_parts.append(arg)

    if not ref_parts:
        print("성구 참조를 입력하세요. 예: \"요한 3:16\"")
        return

    ref_str = ' '.join(ref_parts)
    verse_ranges = parse_reference(ref_str)

    if not verse_ranges:
        print(f"'{ref_str}'를 파싱할 수 없습니다.")
        print("사용 가능한 책 이름을 확인하려면: --books")
        return

    for first_id, last_id in verse_ranges:
        if mode in ('text', 'all'):
            print_verse_text(first_id, last_id)

        if mode in ('study', 'all'):
            print_study_materials(first_id, last_id)

        if mode == 'text':
            pass  # 본문만

        print()


if __name__ == '__main__':
    main()
