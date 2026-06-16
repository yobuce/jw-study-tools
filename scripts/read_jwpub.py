#!/usr/bin/env python3
"""JWPUB 파일에서 콘텐츠를 추출하는 스크립트.

사용법:
    python scripts/read_jwpub.py <번호>           # S-34 강연 번호 (예: 35)
    python scripts/read_jwpub.py <파일명>          # JWPUB 파일명 (예: CO-tk26_KO_031)
    python scripts/read_jwpub.py <경로>            # JWPUB 파일 경로
    python scripts/read_jwpub.py <번호> --html     # HTML 원본 출력
    python scripts/read_jwpub.py <번호> --text     # 텍스트만 출력 (기본)
    python scripts/read_jwpub.py <번호> --text-only # 본문만 (성구/출판물/연구자료 생략)
    python scripts/read_jwpub.py <번호> --refs     # 본문 + 성구 참조 + 참조 출판물
    python scripts/read_jwpub.py <번호> --extracts          # 참조 자료(Extract)만 출력
    python scripts/read_jwpub.py <번호> --extracts "키워드"  # 키워드 필터링
    python scripts/read_jwpub.py --list            # 사용 가능한 파일 목록
"""

import zipfile
import os
import sys
import tempfile
import sqlite3
import zlib
import hashlib
import json
import re
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

# 하드코딩된 XOR 상수 (JW Library 앱에서 추출)
XOR_CONST = bytes([
    0x11, 0xCB, 0xB5, 0x58, 0x7E, 0x32, 0x84, 0x6D,
    0x4C, 0x26, 0x79, 0x0C, 0x63, 0x3D, 0xA2, 0x89,
    0xF6, 0x6F, 0xE5, 0x84, 0x2A, 0x3A, 0x58, 0x5C,
    0xE1, 0xBC, 0x3A, 0x29, 0x4A, 0xF5, 0xAD, 0xA7
])

def _resolve_data_dir():
    """JWPUB/DB 데이터 폴더를 결정한다.

    우선순위:
      1) 환경변수 JW_DATA_DIR (커맨드가 ${user_config.data_dir} 등으로 주입)
      2) 플러그인 영구 데이터 폴더 ${CLAUDE_PLUGIN_DATA}/JWPUB (업데이트에도 유지)
      3) 로컬 개발 폴백: 스크립트 상위의 JWPUB 폴더
    """
    env = os.environ.get("JW_DATA_DIR")
    if env:
        return Path(env)
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if plugin_data:
        return Path(plugin_data) / "JWPUB"
    return Path(__file__).parent.parent / "JWPUB"


JWPUB_DIR = _resolve_data_dir()
RSG_JWPUB_PATH = JWPUB_DIR / "rsg19_KO.jwpub"

# JW Library 앱의 mepsunit.db 경로 (성구 매핑용)
MEPSUNIT_DB_PATH = Path(
    r"C:\Program Files\WindowsApps"
    r"\WatchtowerBibleandTractSo.45909CDBADF3C_15.7.36.0_x64__5rz59y55nfz3e"
    r"\Data\mepsunit.db"
)

# 성경 DB 경로 (성구 본문 조회용, JWPUB 디렉토리에 복사본 사용)
BIBLE_DB_PATH = JWPUB_DIR / "nwtsty_KO.db"
BIBLE_MANIFEST_PATH = JWPUB_DIR / "nwtsty_KO_manifest.json"


# ---------------------------------------------------------------------------
# 암호화/복호화
# ---------------------------------------------------------------------------

def derive_key_iv(password: str) -> tuple[bytes, bytes]:
    """비밀번호에서 AES 키와 IV를 파생."""
    sha = hashlib.sha256(password.encode('utf-8')).digest()
    key = bytes([sha[i] ^ XOR_CONST[i] for i in range(16)])
    iv = bytes([sha[i + 16] ^ XOR_CONST[i + 16] for i in range(16)])
    return key, iv


def decrypt_blob(blob: bytes, password: str) -> bytes:
    """암호화된 BLOB을 복호화하고 압축을 해제."""
    key, iv = derive_key_iv(password)
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    decrypted = decryptor.update(blob) + decryptor.finalize()

    # PKCS5 패딩 제거
    pad_len = decrypted[-1]
    if 1 <= pad_len <= 16 and all(b == pad_len for b in decrypted[-pad_len:]):
        decrypted = decrypted[:-pad_len]

    # zlib 압축 해제
    return zlib.decompress(decrypted)


# ---------------------------------------------------------------------------
# JWPUB 파일 처리
# ---------------------------------------------------------------------------

def parse_manifest(jwpub_path: str) -> dict:
    """JWPUB 파일에서 manifest.json을 읽어 메타데이터 반환."""
    with zipfile.ZipFile(jwpub_path, 'r') as z:
        manifest = json.loads(z.read('manifest.json'))
    return manifest


def build_password(manifest: dict) -> str:
    """manifest에서 복호화 비밀번호를 생성."""
    pub = manifest['publication']
    lang = pub['language']
    symbol = pub['symbol']
    year = pub['year']
    issue = pub.get('issueNumber', pub.get('issueId', 0))

    if issue and issue != 0:
        return f"{lang}_{symbol}_{year}_{issue}"
    else:
        return f"{lang}_{symbol}_{year}"


def build_password_candidates(manifest: dict) -> list[str]:
    """manifest에서 복호화 비밀번호 후보 목록을 생성.

    issueNumber와 issueId가 다른 경우(CO-tk26 등) 둘 다 시도한다.
    """
    pub = manifest['publication']
    lang = pub['language']
    symbol = pub['symbol']
    year = pub['year']

    candidates = []
    issue_id = pub.get('issueId', 0)
    issue_number = pub.get('issueNumber', 0)

    if issue_id and issue_id != 0:
        candidates.append(f"{lang}_{symbol}_{year}_{issue_id}")
    if issue_number and issue_number != 0:
        pw = f"{lang}_{symbol}_{year}_{issue_number}"
        if pw not in candidates:
            candidates.append(pw)
    if not candidates:
        candidates.append(f"{lang}_{symbol}_{year}")

    return candidates


def find_working_password(manifest: dict, sample_blob: bytes) -> str:
    """sample_blob으로 복호화를 시도하여 올바른 비밀번호를 찾는다."""
    candidates = build_password_candidates(manifest)
    for pw in candidates:
        try:
            decrypt_blob(sample_blob, pw)
            return pw
        except Exception:
            continue
    return candidates[0]


def extract_db(jwpub_path: str) -> str:
    """JWPUB에서 SQLite DB를 임시 디렉토리에 추출하고 경로 반환."""
    tmp_dir = tempfile.mkdtemp()
    with zipfile.ZipFile(jwpub_path, 'r') as z:
        z.extractall(tmp_dir)
    contents_path = os.path.join(tmp_dir, 'contents')
    inner_dir = os.path.join(tmp_dir, 'inner')
    os.makedirs(inner_dir, exist_ok=True)
    with zipfile.ZipFile(contents_path, 'r') as z2:
        z2.extractall(inner_dir)

    for f in os.listdir(inner_dir):
        if f.endswith('.db'):
            return os.path.join(inner_dir, f)
    raise FileNotFoundError("DB file not found in JWPUB")


# ---------------------------------------------------------------------------
# 성경 구절 ID 매핑
# ---------------------------------------------------------------------------

_bible_ranges = None
_bible_book_names = None
# nwtsty BibleVerse.Label 기반 절 번호 매핑 (시편 표제 등 비정규 번호 정확 처리)
_id_to_verse = None       # {BibleVerseId: (book_num, chapter, verse)}
_verse_to_id = None       # {(book_num, chapter, verse): BibleVerseId}


def _parse_verse_label(label: str):
    """BibleVerse.Label에서 실제 절 번호를 추출.

    '<span class="vl">10</span>' → 10 (일반 절)
    '<span class="cl">90</span>' → 1  (장 첫 절은 장 번호를 표시)
    빈 문자열                    → 0  (시편 표제 등 superscription)
    """
    if not label:
        return 0
    m = re.search(r'class="vl">(\d+)', label)
    if m:
        return int(m.group(1))
    if 'class="cl"' in label:
        return 1
    return None


def _load_verse_labels():
    """nwtsty_KO.db의 BibleVerse.Label에서 ID↔(책,장,절) 매핑을 구축.

    BibleChapter가 (책,장)→ID 범위를, BibleVerse.Label이 각 ID의 실제 절 번호를
    제공한다. 단순 산술(ID - 장첫ID + 1)은 시편 표제가 첫 ID를 차지해 절 번호가
    +1 밀리므로, 표시 라벨을 진실의 원천으로 삼아 정확히 매핑한다.
    """
    global _id_to_verse, _verse_to_id
    if _id_to_verse is not None:
        return
    _id_to_verse, _verse_to_id = {}, {}
    if not BIBLE_DB_PATH.exists():
        return
    try:
        conn = sqlite3.connect(str(BIBLE_DB_PATH))
        cur = conn.cursor()
        labels = dict(cur.execute(
            'SELECT BibleVerseId, Label FROM BibleVerse'
        ).fetchall())
        chapters = cur.execute(
            'SELECT BookNumber, ChapterNumber, FirstVerseId, LastVerseId '
            'FROM BibleChapter '
            'WHERE FirstVerseId IS NOT NULL AND LastVerseId IS NOT NULL'
        ).fetchall()
        conn.close()
    except Exception:
        return
    for book, ch, first_id, last_id in chapters:
        for vid in range(first_id, last_id + 1):
            verse = _parse_verse_label(labels.get(vid))
            if verse is None:
                continue
            _id_to_verse[vid] = (book, ch, verse)
            _verse_to_id.setdefault((book, ch, verse), vid)


def verse_ref_to_id(book_num: int, chapter: int, verse: int):
    """(책번호, 장, 절) → BibleVerseId. 라벨 매핑에 없으면 None."""
    _load_verse_labels()
    return _verse_to_id.get((book_num, chapter, verse))


def _load_bible_data():
    """BibleVerseId → 책/장/절 매핑 데이터를 로드.

    1순위: JW Library 앱의 mepsunit.db (설치돼 있으면 표준 약어 사용)
    2순위: 프로젝트 nwtsty_KO.db의 BibleChapter/BibleBook (JW Library 미설치 폴백)

    mepsunit.db가 없으면 매핑이 0건이 되어 모든 성구가 "[verseId N]"으로
    폴백되던 문제가 있어, nwtsty_KO.db로 동일한 매핑을 구성하도록 보강했다.
    """
    global _bible_ranges, _bible_book_names

    if _bible_ranges is not None:
        return

    # 1순위: JW Library mepsunit.db
    if MEPSUNIT_DB_PATH.exists():
        conn = sqlite3.connect(str(MEPSUNIT_DB_PATH))
        cursor = conn.cursor()

        # BibleRange: verseId → 책/장/절 매핑
        cursor.execute('''
            SELECT BookNumber, ChapterNumber, FirstBibleVerseId, LastBibleVerseId
            FROM BibleRange
            WHERE BibleInfoId = 2 AND BookNumber IS NOT NULL AND ChapterNumber IS NOT NULL
            ORDER BY FirstBibleVerseId
        ''')
        _bible_ranges = cursor.fetchall()

        # 한국어 성경 책 이름
        cursor.execute('''
            SELECT bn.BookNumber, bn.StandardBookAbbreviation
            FROM BibleBookName bn
            JOIN BibleCluesInfo ci ON bn.BibleCluesInfoId = ci.BibleCluesInfoId
            JOIN Language l ON ci.LanguageId = l.LanguageId
            WHERE l.Symbol = 'KO' AND ci.BibleInfoId = 2
            ORDER BY bn.BookNumber
        ''')
        _bible_book_names = {r[0]: r[1] for r in cursor.fetchall()}
        conn.close()

        if _bible_ranges:
            return  # mepsunit 매핑 확보 성공

    # 2순위 폴백: 프로젝트 nwtsty_KO.db (BibleChapter/BibleBook)
    if BIBLE_DB_PATH.exists():
        try:
            conn = sqlite3.connect(str(BIBLE_DB_PATH))
            cur = conn.cursor()
            # BibleChapter: 장별 verseId 범위 (BookNumber, ChapterNumber, First/LastVerseId)
            cur.execute('''
                SELECT BookNumber, ChapterNumber, FirstVerseId, LastVerseId
                FROM BibleChapter
                WHERE FirstVerseId IS NOT NULL AND LastVerseId IS NOT NULL
                ORDER BY FirstVerseId
            ''')
            _bible_ranges = cur.fetchall()
            # BibleBook: BibleBookId == BookNumber, BookDisplayTitle = "마태복음" 등
            cur.execute('SELECT BibleBookId, BookDisplayTitle FROM BibleBook')
            _bible_book_names = {r[0]: r[1] for r in cur.fetchall()}
            conn.close()
            if _bible_ranges:
                return
        except Exception:
            pass

    _bible_ranges = []
    _bible_book_names = {}


def verse_id_to_ref(vid: int) -> tuple:
    """BibleVerseId를 (책이름, 장, 절) 튜플로 변환."""
    _load_bible_data()
    # 1순위: BibleVerse.Label 기반 정확한 절 번호 (시편 표제 등 비정규 처리)
    _load_verse_labels()
    if vid in _id_to_verse:
        book, ch, verse = _id_to_verse[vid]
        return _bible_book_names.get(book, f'책{book}'), ch, verse
    # 2순위 폴백: 장 범위 + 산술
    for book, ch, first_id, last_id in _bible_ranges:
        if first_id <= vid <= last_id:
            verse = vid - first_id + 1
            book_name = _bible_book_names.get(book, f'책{book}')
            return book_name, ch, verse
    return None, None, None


def format_verse_range(first_id: int, last_id: int) -> str:
    """성구 범위를 사람이 읽을 수 있는 문자열로 변환."""
    b1, c1, v1 = verse_id_to_ref(first_id)
    if b1 is None:
        return f"[verseId {first_id}]"

    if first_id == last_id:
        return f"{b1} {c1}:{v1}"

    b2, c2, v2 = verse_id_to_ref(last_id)
    if b1 == b2 and c1 == c2:
        return f"{b1} {c1}:{v1}-{v2}"
    elif b1 == b2:
        return f"{b1} {c1}:{v1}~{c2}:{v2}"
    else:
        return f"{b1} {c1}:{v1}~{b2} {c2}:{v2}"


# ---------------------------------------------------------------------------
# 성경 본문 조회
# ---------------------------------------------------------------------------

_bible_conn = None
_bible_pw = None


def _get_bible_db():
    """성경 DB 연결과 비밀번호를 반환 (lazy init)."""
    global _bible_conn, _bible_pw
    if _bible_conn is not None:
        return _bible_conn, _bible_pw
    if not BIBLE_DB_PATH.exists():
        return None, None
    if BIBLE_MANIFEST_PATH.exists():
        manifest = json.loads(BIBLE_MANIFEST_PATH.read_text(encoding='utf-8'))
        _bible_pw = build_password(manifest)
    else:
        _bible_pw = '129_신세연_2023'  # fallback
    _bible_conn = sqlite3.connect(str(BIBLE_DB_PATH))
    return _bible_conn, _bible_pw


def get_bible_verse_text(first_id: int, last_id: int) -> str:
    """nwtsty_KO.db에서 BibleVerseId 범위의 본문을 조회."""
    conn, pw = _get_bible_db()
    if conn is None:
        return ''
    cur = conn.cursor()
    cur.execute(
        'SELECT Label, Content FROM BibleVerse '
        'WHERE BibleVerseId BETWEEN ? AND ? ORDER BY BibleVerseId',
        (first_id, last_id)
    )
    parts = []
    for label, blob in cur.fetchall():
        if not blob:
            continue
        try:
            html = decrypt_blob(blob, pw).decode('utf-8')
            text = re.sub(r'<[^>]+>', '', html).strip()
            label_num = re.sub(r'<[^>]+>', '', label).strip() if label else ''
            if label_num:
                parts.append(f'{label_num} {text}')
            else:
                parts.append(text)
        except Exception:
            pass
    return ' '.join(parts)


# ---------------------------------------------------------------------------
# 연구 자료 찾아보기 (rsg19_KO)
# ---------------------------------------------------------------------------

_rsg_conn = None
_rsg_pw = None
_rsg_db_path = None


def _get_rsg_db():
    """연구 자료 찾아보기 DB 연결과 비밀번호를 반환 (lazy init)."""
    global _rsg_conn, _rsg_pw, _rsg_db_path
    if _rsg_conn is not None:
        return _rsg_conn, _rsg_pw
    if not RSG_JWPUB_PATH.exists():
        return None, None
    manifest = parse_manifest(str(RSG_JWPUB_PATH))
    _rsg_pw = build_password(manifest)
    _rsg_db_path = extract_db(str(RSG_JWPUB_PATH))
    _rsg_conn = sqlite3.connect(_rsg_db_path)
    return _rsg_conn, _rsg_pw


def get_study_references(first_id: int, last_id: int) -> list[dict]:
    """rsg19_KO에서 BibleVerseId 범위에 대한 연구 자료를 조회.

    VerseCommentary HTML에서 data-xtid를 추출하고,
    Extract 테이블에서 실제 자료 내용을 가져온다.
    """
    conn, pw = _get_rsg_db()
    if conn is None:
        return []
    cur = conn.cursor()
    cur.execute('''
        SELECT vcm.BibleVerseId, vc.Content
        FROM VerseCommentaryMap vcm
        JOIN VerseCommentary vc ON vcm.VerseCommentaryId = vc.VerseCommentaryId
        WHERE vcm.BibleVerseId BETWEEN ? AND ?
        ORDER BY vcm.BibleVerseId
    ''', (first_id, last_id))
    results = []
    seen_xtids = set()
    for vid, blob in cur.fetchall():
        if not blob:
            continue
        try:
            html = decrypt_blob(blob, pw).decode('utf-8')
        except Exception:
            continue
        ref = format_verse_range(vid, vid)
        # HTML에서 data-xtid 추출
        xtids = re.findall(r'data-xtid="(\d+)"', html)
        # 중복 제거 (같은 xtid가 여러 절에 나올 수 있음)
        unique_xtids = []
        for xt in xtids:
            if xt not in seen_xtids:
                seen_xtids.add(xt)
                unique_xtids.append(int(xt))
        if not unique_xtids:
            continue
        # Extract 테이블에서 실제 자료 조회
        placeholders = ','.join('?' * len(unique_xtids))
        cur2 = conn.cursor()
        cur2.execute(f'''
            SELECT ExtractId, Caption, Content
            FROM Extract
            WHERE ExtractId IN ({placeholders})
            ORDER BY ExtractId
        ''', unique_xtids)
        extracts = []
        for ext_id, caption, ext_blob in cur2.fetchall():
            clean_caption = re.sub(r'<[^>]+>', '', caption).strip() if caption else ''
            ext_text = ''
            if ext_blob:
                try:
                    ext_html = decrypt_blob(ext_blob, pw).decode('utf-8')
                    ext_text = html_to_text(ext_html)
                except Exception:
                    pass
            extracts.append({'caption': clean_caption, 'content': ext_text})
        if extracts:
            results.append({'reference': ref, 'extracts': extracts})
    return results


# ---------------------------------------------------------------------------
# 참조 데이터 추출
# ---------------------------------------------------------------------------

def _extract_html_bible_refs(html: str) -> dict[int, list[str]]:
    """HTML <a data-bid> 태그에서 성구 참조 표시 텍스트를 문단별로 추출."""
    refs_by_para = {}
    for p_match in re.finditer(
        r'<p[^>]*\bdata-pid="(\d+)"[^>]*>(.*?)</p>', html, re.DOTALL
    ):
        pid = int(p_match.group(1))
        p_content = p_match.group(2)
        bid_parts = {}
        for a_match in re.finditer(
            r'<a[^>]*\bdata-bid="(\d+)-\d+"[^>]*>(.*?)</a>',
            p_content, re.DOTALL
        ):
            group = int(a_match.group(1))
            text = re.sub(r'<[^>]+>', '', a_match.group(2))
            if group not in bid_parts:
                bid_parts[group] = []
            bid_parts[group].append(text)
        if bid_parts:
            refs_by_para[pid] = [
                ''.join(parts).strip().rstrip(';').strip()
                for parts in (bid_parts[g] for g in sorted(bid_parts.keys()))
            ]
    return refs_by_para


def extract_bible_citations(cursor, doc_id: int, html: str) -> list[dict]:
    """BibleCitation에서 verse ID + HTML에서 표시 텍스트 + 성경 본문을 추출."""
    html_refs = _extract_html_bible_refs(html)

    cursor.execute('''
        SELECT ParagraphOrdinal, FirstBibleVerseId, LastBibleVerseId
        FROM BibleCitation
        WHERE DocumentId = ?
        ORDER BY ParagraphOrdinal, BibleCitationId
    ''', (doc_id,))

    citations = []
    para_counter = {}
    for para, first_id, last_id in cursor.fetchall():
        if last_id is None:
            last_id = first_id

        idx = para_counter.get(para, 0)
        para_counter[para] = idx + 1

        # HTML 표시 텍스트 (fallback: verse ID 변환)
        if para in html_refs and idx < len(html_refs[para]):
            ref = html_refs[para][idx]
        else:
            ref = format_verse_range(first_id, last_id)

        verse_text = get_bible_verse_text(first_id, last_id)
        citations.append({
            'paragraph': para,
            'reference': ref,
            'verse_text': verse_text,
            'first_id': first_id,
            'last_id': last_id,
        })
    return citations


def extract_publications(cursor, doc_id: int, password: str) -> list[dict]:
    """Extract + RefPublication 테이블에서 참조 출판물과 본문을 추출."""
    cursor.execute('''
        SELECT de.BeginParagraphOrdinal,
               e.Caption,
               e.Content,
               rp.ShortTitle,
               rp.Symbol,
               rp.Title
        FROM DocumentExtract de
        JOIN Extract e ON de.ExtractId = e.ExtractId
        LEFT JOIN RefPublication rp ON e.RefPublicationId = rp.RefPublicationId
        WHERE de.DocumentId = ?
        ORDER BY de.BeginParagraphOrdinal, de.DocumentExtractId
    ''', (doc_id,))

    pubs = []
    for para, caption, blob, short_title, symbol, full_title in cursor.fetchall():
        clean_caption = re.sub(r'<[^>]+>', '', caption) if caption else ''
        content_text = ''
        if blob:
            try:
                html = decrypt_blob(blob, password).decode('utf-8')
                content_text = html_to_text(html)
            except Exception:
                pass
        pubs.append({
            'paragraph': para,
            'caption': clean_caption,
            'symbol': symbol or '',
            'short_title': short_title or '',
            'full_title': full_title or '',
            'content': content_text,
        })
    return pubs


# ---------------------------------------------------------------------------
# Extract 전용 조회
# ---------------------------------------------------------------------------

def read_extracts_only(jwpub_path: str, keyword: str = None) -> str:
    """JWPUB 파일에서 참조 자료(Extract)만 추출하여 반환.

    Args:
        jwpub_path: JWPUB 파일 경로
        keyword: 필터 키워드 (캡션 또는 본문에 포함된 것만 출력)
    """
    manifest = parse_manifest(jwpub_path)
    db_path = extract_db(jwpub_path)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 비밀번호 도출
    cursor.execute('SELECT Content FROM Document WHERE Content IS NOT NULL LIMIT 1')
    row = cursor.fetchone()
    if row:
        password = find_working_password(manifest, row[0])
    else:
        cursor.execute('SELECT Content FROM Extract WHERE Content IS NOT NULL LIMIT 1')
        row = cursor.fetchone()
        if row:
            password = find_working_password(manifest, row[0])
        else:
            password = build_password(manifest)

    # 모든 Extract 조회
    cursor.execute('''
        SELECT DISTINCT e.ExtractId,
               e.Caption,
               e.Content,
               rp.ShortTitle,
               rp.Symbol,
               rp.Title
        FROM Extract e
        LEFT JOIN RefPublication rp ON e.RefPublicationId = rp.RefPublicationId
        ORDER BY e.ExtractId
    ''')
    rows = cursor.fetchall()
    conn.close()

    try:
        os.unlink(db_path)
    except OSError:
        pass

    # 복호화 + 필터링
    entries = []
    for ext_id, caption, blob, short_title, symbol, full_title in rows:
        clean_caption = re.sub(r'<[^>]+>', '', caption).strip() if caption else ''
        content_text = ''
        if blob:
            try:
                html = decrypt_blob(blob, password).decode('utf-8')
                content_text = html_to_text(html)
            except Exception:
                content_text = '[복호화 실패]'

        # 키워드 필터링
        if keyword:
            search_text = (clean_caption + ' ' + content_text).lower()
            if keyword.lower() not in search_text:
                continue

        entries.append({
            'id': ext_id,
            'caption': clean_caption,
            'symbol': symbol or '',
            'short_title': short_title or '',
            'content': content_text,
        })

    return _format_extracts_output(entries, keyword)


def _format_extracts_output(entries: list[dict], keyword: str = None) -> str:
    """참조 자료 목록을 텍스트로 포맷."""
    if not entries:
        if keyword:
            return f"'{keyword}' 키워드에 해당하는 참조 자료가 없습니다."
        return "참조 자료가 없습니다."

    lines = []
    header = f"참조 자료 ({len(entries)}건)"
    if keyword:
        header += f" — 필터: '{keyword}'"
    lines.append("═" * 50)
    lines.append(header)
    lines.append("═" * 50)

    for i, e in enumerate(entries, 1):
        label = f"「{e['symbol']}」 " if e['symbol'] else ''
        lines.append(f"\n[{i}] {label}{e['caption']}")
        if e['content']:
            lines.append("─" * 40)
            for content_line in e['content'].splitlines():
                if content_line.strip():
                    lines.append(f"  {content_line.strip()}")

    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# 참조 섹션 포맷팅
# ---------------------------------------------------------------------------

def format_references_section(citations: list[dict], publications: list[dict],
                              study_refs: list[dict] = None) -> str:
    """참조 데이터를 텍스트 섹션으로 포맷."""
    lines = []

    if citations:
        # 중복 성구 제거 (동일 reference는 한 번만)
        seen = set()
        unique = []
        for c in citations:
            if c['reference'] not in seen:
                seen.add(c['reference'])
                unique.append(c)

        lines.append("─" * 40)
        lines.append("성구 참조")
        lines.append("─" * 40)
        for c in unique:
            lines.append(f"\n  ■ {c['reference']}")
            if c.get('verse_text'):
                lines.append(f"    {c['verse_text']}")

    if publications:
        lines.append("")
        lines.append("─" * 40)
        lines.append("참조 출판물")
        lines.append("─" * 40)
        for p in publications:
            lines.append(f"\n  ■ 「{p['symbol']}」 {p['caption']}")
            if p.get('content'):
                # 본문을 들여쓰기하여 표시
                for content_line in p['content'].splitlines():
                    if content_line.strip():
                        lines.append(f"    {content_line.strip()}")

    if study_refs:
        lines.append("")
        lines.append("─" * 40)
        lines.append("연구 자료 찾아보기")
        lines.append("─" * 40)
        for sr in study_refs:
            lines.append(f"\n  ■ {sr['reference']}")
            for ext in sr.get('extracts', []):
                if ext.get('caption'):
                    lines.append(f"\n    ▸ {ext['caption']}")
                if ext.get('content'):
                    for content_line in ext['content'].splitlines():
                        if content_line.strip():
                            lines.append(f"      {content_line.strip()}")

    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# HTML → 텍스트 변환
# ---------------------------------------------------------------------------

def html_to_text(html: str) -> str:
    """HTML에서 텍스트만 추출. <p> 태그의 class에 따라 들여쓰기 반영."""
    text = re.sub(r'<br\s*/?>', '\n', html)

    # <p> 태그의 class에 따라 들여쓰기 + 레벨 태그 삽입
    # HTML 클래스 → 레벨 매핑: se=1, s5=2, s6=3, s7=4, s8=5
    level_map = {'se': 1, 's5': 2, 's6': 3, 's7': 4, 's8': 5}
    def replace_p_tag(m):
        cls = m.group(1) if m.group(1) else ''
        if cls in level_map:
            lvl = level_map[cls]
            indent = '  ' * (lvl - 1)
            return '\n' + indent + '[s' + str(lvl) + '] '
        return '\n'
    text = re.sub(r'<p\b[^>]*\bclass="(\w+)"[^>]*>', replace_p_tag, text)

    # class 없는 <p> 태그 처리
    text = re.sub(r'<p\b[^>]*>', '\n', text)

    # h2 → 섹션 제목 구분자
    text = re.sub(r'<h2[^>]*>(.*?)</h2>', lambda m: '\n\n' + re.sub(r'<[^>]+>', '', m.group(1)).strip() + '\n', text, flags=re.DOTALL)

    # 나머지 블록 태그
    text = re.sub(r'</?(h[1-6]|div|header|li|ul|ol|blockquote)[^>]*>', '\n', text)
    # 인라인 태그 제거
    text = re.sub(r'<[^>]+>', '', text)
    # HTML 엔티티
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'&#\d+;', '', text)
    text = re.sub(r'\u200b', '', text)  # zero-width space 제거
    text = text.replace('\r\n', '\n').replace('\r', '\n')  # CR 제거
    text = re.sub(r'[ \t]+$', '', text, flags=re.MULTILINE)  # 줄 끝 공백 제거
    text = re.sub(r'\n{3,}', '\n\n', text)  # 연속 빈 줄 축소
    return text.strip()


# ---------------------------------------------------------------------------
# 메인 읽기 함수
# ---------------------------------------------------------------------------

def read_jwpub(jwpub_path: str, output_html: bool = False,
               include_refs: bool = True, include_study: bool = True) -> str:
    """JWPUB 파일에서 Document Content + 참조자료를 읽어 반환.

    Args:
        jwpub_path: JWPUB 파일 경로
        output_html: True면 HTML 원본, False면 텍스트만 반환
        include_refs: False면 성구 참조 + 참조 출판물 생략
        include_study: False면 연구 자료 찾아보기 생략
    """
    manifest = parse_manifest(jwpub_path)
    db_path = extract_db(jwpub_path)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Document 테이블에서 Content 읽기
    cursor.execute('SELECT DocumentId, Title, Content FROM Document ORDER BY DocumentId')
    rows = cursor.fetchall()

    # 첫 Content blob으로 비밀번호 자동 탐색
    sample_blob = next((blob for _, _, blob in rows if blob), None)
    if sample_blob:
        password = find_working_password(manifest, sample_blob)
    else:
        password = build_password(manifest)

    content_parts = []
    all_citations = []
    all_publications = []

    for doc_id, title, blob in rows:
        if blob:
            html = decrypt_blob(blob, password).decode('utf-8')
            if output_html:
                content_parts.append(html)
            else:
                content_parts.append(html_to_text(html))

            # 성구 참조 추출 (HTML에서 표시 텍스트 + 본문)
            if include_refs:
                citations = extract_bible_citations(cursor, doc_id, html)
                all_citations.extend(citations)

                # 참조 출판물 추출 (본문 포함)
                publications = extract_publications(cursor, doc_id, password)
                all_publications.extend(publications)

    conn.close()

    # 연구 자료 찾아보기 조회
    all_study_refs = []
    if include_refs and include_study:
        seen_vids = set()
        for c in all_citations:
            vid_key = (c['first_id'], c['last_id'])
            if vid_key not in seen_vids:
                seen_vids.add(vid_key)
                refs = get_study_references(c['first_id'], c['last_id'])
                all_study_refs.extend(refs)

    result = '\n\n'.join(content_parts)

    # 참조 섹션 추가 (텍스트 모드일 때만)
    if not output_html and (all_citations or all_publications or all_study_refs):
        ref_section = format_references_section(all_citations, all_publications, all_study_refs)
        result = result + '\n\n\n' + ref_section

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def list_jwpub_files():
    """사용 가능한 JWPUB 파일 목록 출력."""
    if not JWPUB_DIR.exists():
        print(f"JWPUB 디렉토리를 찾을 수 없습니다: {JWPUB_DIR}")
        return

    files = sorted(JWPUB_DIR.glob("*.jwpub"))
    if not files:
        print("JWPUB 파일이 없습니다.")
        return

    print(f"사용 가능한 JWPUB 파일 ({len(files)}개):")
    for f in files:
        try:
            manifest = parse_manifest(str(f))
            title = manifest['publication'].get('issueProperties', {}).get('coverTitle', '')
            if not title:
                title = manifest['publication'].get('title', '')
            print(f"  {f.stem}: {title}")
        except Exception as e:
            print(f"  {f.stem}: (읽기 오류: {e})")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    if sys.argv[1] == '--list':
        list_jwpub_files()
        return

    arg = sys.argv[1]
    output_html = '--html' in sys.argv
    text_only = '--text-only' in sys.argv
    refs_only = '--refs' in sys.argv

    # 추출 수준 결정
    if text_only:
        include_refs, include_study = False, False
    elif refs_only:
        include_refs, include_study = True, False
    else:
        include_refs, include_study = True, True

    # JWPUB 파일 찾기: 경로 → JWPUB 디렉토리 내 파일명 → S-34 번호 순서로 시도
    arg_path = Path(arg)
    if arg_path.suffix == '.jwpub' and arg_path.exists():
        jwpub_path = arg_path
    elif (JWPUB_DIR / f"{arg}.jwpub").exists():
        jwpub_path = JWPUB_DIR / f"{arg}.jwpub"
    else:
        jwpub_path = JWPUB_DIR / f"S-34_KO_{arg}.jwpub"

    if not jwpub_path.exists():
        print(f"파일을 찾을 수 없습니다: {jwpub_path}")
        return

    # --extracts 모드: 참조 자료만 추출
    if '--extracts' in sys.argv:
        keyword = None
        ext_idx = sys.argv.index('--extracts')
        if ext_idx + 1 < len(sys.argv) and not sys.argv[ext_idx + 1].startswith('--'):
            keyword = sys.argv[ext_idx + 1]
        result = read_extracts_only(str(jwpub_path), keyword=keyword)
        print(result)
        return

    result = read_jwpub(str(jwpub_path), output_html=output_html,
                        include_refs=include_refs, include_study=include_study)
    print(result)


if __name__ == '__main__':
    main()
