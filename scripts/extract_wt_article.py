#!/usr/bin/env python3
"""파수대 JWPUB에서 연구 기사를 항별로 구조화하여 추출하는 스크립트.

사용법:
    python scripts/extract_wt_article.py <JWPUB|식별자> --list       # 기사 목록 (날짜+제목)
    python scripts/extract_wt_article.py <JWPUB|식별자> --article N  # N번째 기사 추출
    python scripts/extract_wt_article.py <JWPUB|식별자> --date YYYYMMDD  # 날짜로 기사 검색
    python scripts/extract_wt_article.py <JWPUB|식별자> --json       # JSON 구조화 출력
"""

import sys
import os
import sqlite3
import re
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from read_jwpub import (
    parse_manifest, extract_db, build_password, find_working_password,
    decrypt_blob, html_to_text, format_verse_range, get_bible_verse_text,
    extract_bible_citations, extract_publications, JWPUB_DIR,
)

WT_DIR = JWPUB_DIR / "파수대"


# ---------------------------------------------------------------------------
# JWPUB 파일 찾기
# ---------------------------------------------------------------------------

def find_wt_jwpub(identifier: str) -> Path:
    """식별자로 파수대 JWPUB 파일 경로를 찾는다."""
    p = Path(identifier)
    if p.suffix == '.jwpub' and p.exists():
        return p
    # JWPUB/파수대/ 내에서 검색
    if WT_DIR.exists():
        candidate = WT_DIR / f"{identifier}.jwpub"
        if candidate.exists():
            return candidate
    # JWPUB/ 내에서 검색
    candidate = JWPUB_DIR / f"{identifier}.jwpub"
    if candidate.exists():
        return candidate
    raise FileNotFoundError(f"JWPUB 파일을 찾을 수 없습니다: {identifier}")


# ---------------------------------------------------------------------------
# 기사 목록 조회
# ---------------------------------------------------------------------------

def list_study_articles(jwpub_path: str) -> list[dict]:
    """JWPUB에서 연구 기사 목록을 반환한다.

    Returns:
        list of {doc_id, title, context_title, date_start, date_end, article_index}
    """
    manifest = parse_manifest(jwpub_path)
    db_path = extract_db(jwpub_path)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Class=40 문서가 연구 기사
    cur.execute('''
        SELECT DocumentId, Title, ContextTitle
        FROM Document
        WHERE Class = 40
        ORDER BY DocumentId
    ''')
    articles_raw = cur.fetchall()

    # DatedText에서 날짜 정보 가져오기
    cur.execute('''
        SELECT DatedTextId, FirstDateOffset, LastDateOffset
        FROM DatedText
        ORDER BY DatedTextId
    ''')
    dated_rows = cur.fetchall()

    conn.close()

    articles = []
    for idx, (doc_id, title, ctx_title) in enumerate(articles_raw):
        date_start = None
        date_end = None
        if idx < len(dated_rows):
            date_start = str(dated_rows[idx][1])
            date_end = str(dated_rows[idx][2])

        articles.append({
            'doc_id': doc_id,
            'title': title,
            'context_title': ctx_title or '',
            'date_start': date_start,
            'date_end': date_end,
            'article_index': idx + 1,
        })

    return articles


# ---------------------------------------------------------------------------
# HTML에서 개별 요소 추출
# ---------------------------------------------------------------------------

def _parse_html_elements(html: str) -> dict:
    """기사 HTML에서 개별 요소를 data-pid 기준으로 추출한다.

    Returns:
        dict: {pid: {'type': str, 'text': str, 'pnum': int|None, 'html': str}}
    """
    elements = {}

    # h1 제목 (data-pid 포함)
    for m in re.finditer(
        r'<h1[^>]*\bdata-pid="(\d+)"[^>]*>(.*?)</h1>', html, re.DOTALL
    ):
        pid = int(m.group(1))
        text = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        elements[pid] = {'type': 'title', 'text': text, 'pnum': None}

    # h2 소제목
    for m in re.finditer(
        r'<h2[^>]*\bid="p(\d+)"[^>]*\bdata-pid="(\d+)"[^>]*>(.*?)</h2>',
        html, re.DOTALL
    ):
        pid = int(m.group(2))
        text = re.sub(r'<[^>]+>', '', m.group(3)).strip()
        elements[pid] = {'type': 'heading', 'text': text, 'pnum': None}

    # h2 without data-pid but with id
    for m in re.finditer(
        r'<h2[^>]*\bid="p(\d+)"[^>]*>(.*?)</h2>', html, re.DOTALL
    ):
        pid = int(m.group(1))
        if pid not in elements:
            text = re.sub(r'<[^>]+>', '', m.group(2)).strip()
            elements[pid] = {'type': 'heading', 'text': text, 'pnum': None}

    # p 요소
    for m in re.finditer(
        r'<p[^>]*\bdata-pid="(\d+)"([^>]*)>(.*?)</p>', html, re.DOTALL
    ):
        pid = int(m.group(1))
        if pid in elements:
            continue  # h1/h2로 이미 처리됨
        attrs = m.group(2)
        body = m.group(3)
        text = re.sub(r'<[^>]+>', '', body).strip()
        # 영폭 공백 제거
        text = text.replace('\u200b', '').replace('\u00a0', ' ')

        pnum_match = re.search(r'data-pnum="(\d+)"', body)
        pnum = int(pnum_match.group(1)) if pnum_match else None

        if 'class="qu"' in attrs or 'class="qu "' in attrs:
            el_type = 'question'
        elif 'data-rel-pid' in attrs:
            el_type = 'content'
        elif 'themeScrp' in attrs:
            el_type = 'theme_scripture'
        elif 'pubRefs' in attrs:
            el_type = 'pub_ref'
        else:
            el_type = 'other'

        elements[pid] = {'type': el_type, 'text': text, 'pnum': pnum}

    return elements


def _ordered_html_bible_refs(html: str) -> dict:
    """문단별 <a data-bid> 성구 표시 텍스트를 '그룹 결합 없이' 등장 순서대로 반환.

    반환: {para_ordinal: [{'text': 표시라벨, 'read_aloud': bool}, ...]}

    성구마다 <a> 태그 1개가 나오므로 BibleCitation 테이블의 행(범위마다 1행)과
    1:1·동일 순서로 정렬된다. 인덱스로 안전하게 짝지을 수 있어, 기사의 실제 표시
    라벨('마태 24:8' 등)을 본문과 정확히 대응시킬 수 있다. (그룹 결합형
    _extract_html_bible_refs는 복합 인용을 1개로 합쳐 DB 행 수와 어긋난다.)

    [낭독] 성구는 특정 <a data-bid="G-N"> 바로 뒤에 '낭독' 표시가 오는 것으로
    판별한다(예: "…전도서 7:13, 14</a> <strong>낭독)</strong>"). 같은 그룹 G에
    속한 태그들이 한 낭독 묶음이므로(예: '히브리서 12:7, 11'의 12-1·12-2) 그룹
    단위로 표시한다. 표시 형식이나 절 번호 매핑에 의존하지 않아 시편 표제 등에서도
    안전하다.
    """
    refs_by_para = {}
    a_tag = re.compile(
        r'<a[^>]*\bdata-bid="(\d+)-\d+"[^>]*>(.*?)</a>', re.DOTALL
    )
    bare_verse = re.compile(r'^[\d,\s–\-]+$')   # 절 번호만(쉼표/범위 포함)
    book_chapter = re.compile(r'^(.+?)\s+(\d+):')    # "책이름 장:" 접두 추출
    for p_match in re.finditer(
        r'<p[^>]*\bdata-pid="(\d+)"[^>]*>(.*?)</p>', html, re.DOTALL
    ):
        pid = int(p_match.group(1))
        body = p_match.group(2)
        entries = []
        read_groups = set()
        group_prefix = {}  # group -> "책이름 장" (복합 인용의 책·장 문맥)
        for a_match in a_tag.finditer(body):
            group = a_match.group(1)
            text = re.sub(r'<[^>]+>', '', a_match.group(2)).strip(' ;,\t\n')
            if not text:
                continue
            # 복합 인용은 '히브리서 12:7,' + '11'처럼 쪼개진다. 첫 조각에서 책·장을
            # 확보해 두고, 책·장이 없는 절 전용 조각엔 그 접두를 붙여 복원한다.
            bc = book_chapter.match(text)
            if bc:
                group_prefix[group] = f"{bc.group(1)} {bc.group(2)}"
            elif bare_verse.match(text) and group in group_prefix:
                text = f"{group_prefix[group]}:{text}"
            # 닫는 </a> 직후 짧은 구간에 '낭독'이 오면 그 그룹 전체가 낭독 성구
            if '낭독' in body[a_match.end():a_match.end() + 20]:
                read_groups.add(group)
            entries.append((text, group))
        if entries:
            refs_by_para[pid] = [
                {'text': t, 'read_aloud': g in read_groups} for t, g in entries
            ]
    return refs_by_para


# ---------------------------------------------------------------------------
# 기사 구조화 추출
# ---------------------------------------------------------------------------

def extract_article(jwpub_path: str, doc_id: int) -> dict:
    """단일 연구 기사를 항별로 구조화하여 추출한다.

    Returns:
        dict with keys: title, context_title, theme_scripture, paragraph_units
    """
    manifest = parse_manifest(jwpub_path)
    db_path = extract_db(jwpub_path)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # 문서 메타데이터
    cur.execute(
        'SELECT Title, ContextTitle, Content FROM Document WHERE DocumentId=?',
        (doc_id,)
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        raise ValueError(f"Document {doc_id} not found")

    title, ctx_title, blob = row
    pw = find_working_password(manifest, blob)
    html = decrypt_blob(blob, pw).decode('utf-8')

    # HTML 요소 파싱
    elements = _parse_html_elements(html)

    # 주제 성구 추출
    theme_scripture = ''
    for pid, el in elements.items():
        if el['type'] == 'theme_scripture':
            theme_scripture = el['text']
            break

    # DocumentParagraph (내용 항 매핑)
    cur.execute('''
        SELECT ParagraphIndex, ParagraphNumberLabel
        FROM DocumentParagraph
        WHERE DocumentId = ?
        ORDER BY ParagraphIndex
    ''', (doc_id,))
    doc_paras = cur.fetchall()
    # {ParagraphIndex: ParagraphNumberLabel}
    para_labels = {pi: pl for pi, pl in doc_paras}

    # Question 테이블
    cur.execute('''
        SELECT QuestionIndex, Content, ParagraphOrdinal,
               TargetParagraphOrdinal, TargetParagraphNumberLabel
        FROM Question
        WHERE DocumentId = ?
        ORDER BY QuestionIndex
    ''', (doc_id,))
    questions_raw = cur.fetchall()

    questions = []
    for qi, qblob, q_para_ord, target_ord, target_label in questions_raw:
        q_text = ''
        if qblob:
            try:
                q_html = decrypt_blob(qblob, pw).decode('utf-8')
                q_text = re.sub(r'<[^>]+>', '', q_html).strip()
                q_text = q_text.replace('\u200b', '')
            except Exception:
                pass
        questions.append({
            'index': qi,
            'text': q_text,
            'question_para_ord': q_para_ord,
            'target_para_ord': target_ord,
            'target_label': target_label,
        })

    # BibleCitation (항별 성구)
    cur.execute('''
        SELECT BibleCitationId, ParagraphOrdinal,
               FirstBibleVerseId, LastBibleVerseId
        FROM BibleCitation
        WHERE DocumentId = ?
        ORDER BY ParagraphOrdinal, BibleCitationId
    ''', (doc_id,))
    citations_raw = cur.fetchall()

    # 항별 성구 그룹핑
    # ref_display는 그룹 결합 없는 HTML <a> 라벨을 DB 행과 인덱스로 짝지어
    # 가져온다(기사 실제 표시 + 본문 정확 대응). 문단별 태그 수와 DB 행 수가
    # 어긋나는 예외적인 경우에만 format_verse_range로 폴백한다.
    ordered_refs = _ordered_html_bible_refs(html)
    db_counts = {}  # para_ord -> DB 인용 행 수
    for _, po, _, _ in citations_raw:
        db_counts[po] = db_counts.get(po, 0) + 1
    citations_by_para = {}
    para_cite_idx = {}
    for cid, para_ord, first_id, last_id in citations_raw:
        if last_id is None:
            last_id = first_id

        idx = para_cite_idx.get(para_ord, 0)
        para_cite_idx[para_ord] = idx + 1
        para_refs = ordered_refs.get(para_ord, [])
        # 태그 수와 DB 행 수가 일치할 때만 인덱스 정렬을 신뢰
        if len(para_refs) == db_counts[para_ord] and idx < len(para_refs):
            ref_display = para_refs[idx]['text']
            is_read_aloud = para_refs[idx]['read_aloud']
        else:
            ref_display = format_verse_range(first_id, last_id)
            is_read_aloud = False

        verse_text = get_bible_verse_text(first_id, last_id)

        cite = {
            'reference': ref_display,
            'verse_text': verse_text,
            'first_id': first_id,
            'last_id': last_id,
            'read_aloud': is_read_aloud,
        }
        citations_by_para.setdefault(para_ord, []).append(cite)

    # Footnote (항별 각주)
    cur.execute('''
        SELECT FootnoteId, Content, ParagraphOrdinal
        FROM Footnote
        WHERE DocumentId = ?
        ORDER BY FootnoteId
    ''', (doc_id,))
    footnotes_by_para = {}
    for fn_id, fn_blob, fn_para in cur.fetchall():
        fn_text = ''
        if fn_blob:
            try:
                fn_html = decrypt_blob(fn_blob, pw).decode('utf-8')
                fn_text = re.sub(r'<[^>]+>', '', fn_html).strip()
                fn_text = fn_text.replace('\u200b', '')
            except Exception:
                pass
        footnotes_by_para.setdefault(fn_para, []).append(fn_text)

    # DocumentExtract (항별 참조 출판물)
    cur.execute('''
        SELECT de.BeginParagraphOrdinal,
               e.Caption, e.Content, rp.ShortTitle, rp.Symbol
        FROM DocumentExtract de
        JOIN Extract e ON de.ExtractId = e.ExtractId
        LEFT JOIN RefPublication rp ON e.RefPublicationId = rp.RefPublicationId
        WHERE de.DocumentId = ?
        ORDER BY de.BeginParagraphOrdinal, de.DocumentExtractId
    ''', (doc_id,))
    extracts_by_para = {}
    for ext_para, caption, ext_blob, short_title, symbol in cur.fetchall():
        clean_caption = re.sub(r'<[^>]+>', '', caption).strip() if caption else ''
        ext_text = ''
        if ext_blob:
            try:
                ext_html = decrypt_blob(ext_blob, pw).decode('utf-8')
                ext_text = html_to_text(ext_html)
            except Exception:
                pass
        extracts_by_para.setdefault(ext_para, []).append({
            'caption': clean_caption,
            'symbol': symbol or '',
            'content': ext_text,
        })

    conn.close()

    # 소제목 위치 매핑 (data-pid → heading text)
    headings = {}
    for pid, el in elements.items():
        if el['type'] == 'heading':
            headings[pid] = el['text']

    # 항 단위(paragraph_unit) 구성
    paragraph_units = []

    for q_idx, q in enumerate(questions):
        target_label = q['target_label']

        # 이 질문이 다루는 항 번호 범위 결정
        if q_idx + 1 < len(questions):
            next_label = questions[q_idx + 1]['target_label']
            para_labels_covered = list(range(target_label, next_label))
        else:
            # 마지막 질문: 남은 번호가 있는 항까지
            max_label = max(
                (pl for pl in para_labels.values() if pl is not None),
                default=target_label
            )
            para_labels_covered = list(range(target_label, max_label + 1))

        # 해당 번호의 항 ordinal 찾기
        label_to_ord = {}
        for pi, pl in para_labels.items():
            if pl is not None and pl in para_labels_covered:
                label_to_ord[pl] = pi

        # 각 항의 데이터 수집
        paras = []
        for lbl in para_labels_covered:
            if lbl not in label_to_ord:
                continue
            ord_val = label_to_ord[lbl]

            # 항 본문 (HTML 요소에서)
            para_text = ''
            if ord_val in elements and elements[ord_val]['type'] == 'content':
                para_text = elements[ord_val]['text']

            paras.append({
                'number': lbl,
                'ordinal': ord_val,
                'text': para_text,
                'citations': citations_by_para.get(ord_val, []),
                'footnotes': footnotes_by_para.get(ord_val, []),
                'extracts': extracts_by_para.get(ord_val, []),
            })

        # 이 항 단위 앞의 소제목 확인
        subheading = None
        target_ord = q['target_para_ord']
        # target_ord 직전의 heading 찾기
        for pid in sorted(headings.keys()):
            if pid < target_ord:
                # 이전 항 단위의 target보다 크면 이 단위의 소제목
                prev_target = questions[q_idx - 1]['target_para_ord'] if q_idx > 0 else 0
                if pid > prev_target:
                    subheading = headings[pid]

        paragraph_units.append({
            'question_index': q['index'],
            'question_text': q['text'],
            'paragraphs': paras,
            'subheading': subheading,
        })

    return {
        'title': title,
        'context_title': ctx_title or '',
        'theme_scripture': theme_scripture,
        'paragraph_units': paragraph_units,
    }


# ---------------------------------------------------------------------------
# 출력 포맷팅
# ---------------------------------------------------------------------------

def format_article_list(articles: list[dict], jwpub_name: str) -> str:
    """기사 목록을 사람이 읽을 수 있는 형식으로 포맷."""
    lines = [f"파수대 연구 기사 목록 — {jwpub_name}", "=" * 50]
    for a in articles:
        date_str = ''
        if a['date_start'] and a['date_end']:
            ds = a['date_start']
            de = a['date_end']
            date_str = f"{ds[:4]}.{ds[4:6]}.{ds[6:8]}~{de[4:6]}.{de[6:8]}"
        lines.append(
            f"  {a['article_index']}. [{date_str}] {a['title']}"
        )
    return '\n'.join(lines)


def format_article_structured(article: dict) -> str:
    """구조화된 기사를 텍스트로 포맷."""
    lines = []
    lines.append("═" * 55)
    lines.append(f"파수대 연구 기사: {article['title']}")
    lines.append(f"연구 주간: {article['context_title']}")
    if article['theme_scripture']:
        lines.append(f"주제 성구: {article['theme_scripture']}")
    lines.append("═" * 55)

    current_subheading = None
    for unit in article['paragraph_units']:
        # 소제목
        if unit['subheading'] and unit['subheading'] != current_subheading:
            current_subheading = unit['subheading']
            lines.append(f"\n── {current_subheading} ──")

        # 질문 헤더
        para_nums = [str(p['number']) for p in unit['paragraphs']]
        para_str = ', '.join(para_nums) + '항'
        lines.append(
            f"\n▶ 질문 {unit['question_index']} ({para_str}): "
            f"{unit['question_text']}"
        )

        # 각 항 본문
        for p in unit['paragraphs']:
            lines.append(f"\n  [{p['number']}항 본문]")
            if p['text']:
                # 텍스트 줄바꿈 정리
                for tline in p['text'].split('\n'):
                    tline = tline.strip()
                    if tline:
                        lines.append(f"  {tline}")

            # 성구 참조
            if p['citations']:
                lines.append("  ┌ 성구 참조:")
                for c in p['citations']:
                    tag = ' [낭독]' if c.get('read_aloud') else ''
                    lines.append(f"  │ ■ {c['reference']}{tag}")
                    if c.get('verse_text'):
                        # 80자 너비로 줄바꿈
                        vt = c['verse_text']
                        lines.append(f"  │   {vt[:120]}")
                        if len(vt) > 120:
                            lines.append(f"  │   {vt[120:]}")
                lines.append("  └")

            # 각주
            if p['footnotes']:
                lines.append("  ┌ 각주:")
                for fn in p['footnotes']:
                    lines.append(f"  │ ▸ {fn}")
                lines.append("  └")

            # 참조 출판물
            if p['extracts']:
                lines.append("  ┌ 참조 출판물:")
                for ext in p['extracts']:
                    label = f"「{ext['symbol']}」 " if ext['symbol'] else ''
                    lines.append(f"  │ ▸ {label}{ext['caption']}")
                    if ext['content']:
                        for cl in ext['content'].split('\n')[:3]:
                            cl = cl.strip()
                            if cl:
                                lines.append(f"  │   {cl}")
                lines.append("  └")

        lines.append("─" * 55)

    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    # 옵션 파싱
    args = sys.argv[1:]
    identifier = None
    mode = None  # 'list', 'article', 'date'
    article_num = None
    date_str = None
    output_json = '--json' in args

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--list':
            mode = 'list'
        elif arg == '--article':
            mode = 'article'
            if i + 1 < len(args):
                article_num = int(args[i + 1])
                i += 1
        elif arg == '--date':
            mode = 'date'
            if i + 1 < len(args):
                date_str = args[i + 1]
                i += 1
        elif arg == '--json':
            pass  # 이미 처리
        elif identifier is None:
            identifier = arg
        i += 1

    if identifier is None:
        print("JWPUB 파일 식별자를 지정하세요.")
        return

    try:
        jwpub_path = find_wt_jwpub(identifier)
    except FileNotFoundError as e:
        print(str(e))
        return

    if mode == 'list' or mode is None and not article_num and not date_str:
        articles = list_study_articles(str(jwpub_path))
        if output_json:
            print(json.dumps(articles, ensure_ascii=False, indent=2))
        else:
            print(format_article_list(articles, jwpub_path.stem))
        return

    # 기사 선택
    articles = list_study_articles(str(jwpub_path))

    if mode == 'date' and date_str:
        # 날짜로 기사 찾기
        target_doc_id = None
        for a in articles:
            if a['date_start'] and a['date_end']:
                if int(a['date_start']) <= int(date_str) <= int(a['date_end']):
                    target_doc_id = a['doc_id']
                    break
        if target_doc_id is None:
            print(f"날짜 {date_str}에 해당하는 기사를 찾을 수 없습니다.")
            return
    elif mode == 'article' and article_num:
        if 1 <= article_num <= len(articles):
            target_doc_id = articles[article_num - 1]['doc_id']
        else:
            print(f"기사 번호 {article_num}이 범위를 벗어났습니다 (1~{len(articles)})")
            return
    else:
        print("--article N 또는 --date YYYYMMDD를 지정하세요.")
        print(format_article_list(articles, jwpub_path.stem))
        return

    # 기사 추출
    article = extract_article(str(jwpub_path), target_doc_id)
    if output_json:
        print(json.dumps(article, ensure_ascii=False, indent=2))
    else:
        print(format_article_structured(article))


if __name__ == '__main__':
    main()
