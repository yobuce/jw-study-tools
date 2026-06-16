# 성구 본문 조회

성구 참조를 입력받아 한국어 성경 본문(신세계역)을 조회합니다.

## 입력

$ARGUMENTS (성구 참조, 예: "요한 3:16", "창세기 1:1-3", "로마서 8:28; 요한 3:16")

## 실행 절차

먼저 데이터 폴더를 결정한 뒤 조회한다. 아래 bash를 그대로 실행:

```bash
# 데이터 폴더 결정 (설정값 우선, 없으면 플러그인 영구 데이터 폴더)
JW_DATA_DIR="${user_config.data_dir}"
[ -z "$JW_DATA_DIR" ] && JW_DATA_DIR="${CLAUDE_PLUGIN_DATA}/JWPUB"
export JW_DATA_DIR
python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/lookup_bible.py" $ARGUMENTS
```

1. 위 명령으로 본문을 조회해 사용자에게 출력
2. 성구를 찾을 수 없는 경우 같은 `JW_DATA_DIR` 환경에서 `python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/lookup_bible.py" --books` 로 사용 가능한 책 이름을 안내

## 지원 형식

- 단일 구절: `요한 3:16`
- 절 범위: `창세기 1:1-3`
- 복수 성구: `로마서 8:28; 요한 3:16` (세미콜론 구분)
- 약어 사용 가능: `고전 13:4-8`, `롬 8:28`
- 단장 책: `유다서 3` (장 번호 없이 절만)

## 데이터 요구

`$JW_DATA_DIR/nwtsty_KO.db` (신세계역 연구용 성경 DB)가 필요합니다. 없으면 README의 "데이터 준비"를 안내하세요.
