# JWPUB 텍스트 추출

JWPUB 파일에서 텍스트(본문·성구·참조 출판물·연구 자료 찾아보기)를 추출합니다.

## 입력

$ARGUMENTS (JWPUB 파일 식별자 + 옵션)

### 파일 식별자

- S-34 강연 번호: `35`
- JWPUB 파일명: `CO-tk26_KO_031`, `be_KO`
- JWPUB 파일 경로: 절대경로 또는 데이터 폴더 기준 상대경로

### 추출 수준 옵션

- `--text-only` — 본문만 (성구/출판물/연구자료 모두 생략)
- `--refs` — 본문 + 성구 참조 + 참조 출판물 (연구 자료 생략)
- `--extracts` — 참조 자료(Extract)만 출력
- `--extracts "키워드"` — 키워드로 참조 자료 필터링
- (옵션 없음) — 전체 추출: 본문 + 성구 + 출판물 + 연구 자료 찾아보기
- `--html` — HTML 원본 출력
- `--list` — 데이터 폴더 안의 사용 가능한 JWPUB 파일 목록

## 실행

```bash
# 데이터 폴더 결정 (설정값 우선, 없으면 플러그인 영구 데이터 폴더)
JW_DATA_DIR="${user_config.data_dir}"
[ -z "$JW_DATA_DIR" ] && JW_DATA_DIR="${CLAUDE_PLUGIN_DATA}/JWPUB"
export JW_DATA_DIR
python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/read_jwpub.py" $ARGUMENTS
```

결과를 사용자에게 보여주세요. 어떤 파일이 있는지 모르면 먼저 `--list` 로 목록을 확인하세요.

## 데이터 요구

추출할 `.jwpub` 파일이 `$JW_DATA_DIR/` 안에 있어야 합니다. 성구 본문을 함께 보려면 `$JW_DATA_DIR/nwtsty_KO.db` 도 필요합니다.
