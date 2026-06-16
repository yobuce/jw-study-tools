# jw.org 동영상 자막 추출

jw.org 동영상 URL에서 한국어 VTT 자막을 받아 시간정보·태그를 제거한 본문 텍스트로 정리합니다.

## 입력

$ARGUMENTS (jw.org 동영상 URL. `?lank=…` 쿼리스트링 또는 경로 내 `pub-…_VIDEO` 형식 모두 인식)

## 실행

이 기능은 JWPUB 데이터가 필요 없습니다(웹에서 직접 받음). 다음을 실행:

```bash
python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/extract_jwb_subtitle.py" "$ARGUMENTS"
```

- 기본 저장 위치: 현재 작업 폴더의 `docs/JW방송/YYYY-MM.md`
- 저장 위치를 바꾸려면 `--out-dir <폴더>` 또는 `--out <파일경로>` 추가
- 언어 변경: `--lang EN` (기본 KO)
- 제목 직접 지정: `--title "..."`

## 동작

1. URL에서 lank ID 자동 추출
2. `data.jw-api.org/mediator` API에서 한국어 메타데이터 조회 → VTT 다운로드
3. 시간정보·태그 제거 → "제목 → 빈 줄 → 자막 본문" 형식의 md로 저장
4. 저장 경로와 글자 수를 사용자에게 보고

자막이 없는 영상이거나 lank ID를 찾지 못하면 그 사실을 사용자에게 알리세요.
