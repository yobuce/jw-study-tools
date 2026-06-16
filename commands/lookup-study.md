# 연구 자료 조회

성구 참조를 입력받아 연구 노트, 각주, 연구 자료 찾아보기를 조회합니다.

## 입력

$ARGUMENTS (성구 참조, 예: "요한 3:16", "창세기 1:1-3")

## 실행 절차

```bash
# 데이터 폴더 결정 (설정값 우선, 없으면 플러그인 영구 데이터 폴더)
JW_DATA_DIR="${user_config.data_dir}"
[ -z "$JW_DATA_DIR" ] && JW_DATA_DIR="${CLAUDE_PLUGIN_DATA}/JWPUB"
export JW_DATA_DIR
python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/lookup_bible.py" $ARGUMENTS --all
```

1. 위 명령으로 본문 + 모든 연구 자료를 조회
2. 연구 노트만 필요한 경우 `--all` 대신 `--study` 사용
3. 조회된 자료를 사용자에게 출력

## 조회 내용

- **연구 노트**: 신세계역 연구용 성경(nwtsty)의 구절별 주석
- **각주**: 구절에 달린 번역/원어 각주
- **연구 자료 찾아보기**: rsg19(연구 자료 찾아보기) 출판물의 관련 자료

## 옵션

- `--study`: 연구 자료만 (본문 제외)
- `--all`: 본문 + 모든 연구 자료 (기본)

## 데이터 요구

`$JW_DATA_DIR/nwtsty_KO.db` 가 필요하고, 연구 자료 찾아보기에는 `$JW_DATA_DIR/rsg19_KO.jwpub` 이 있으면 함께 조회됩니다.
