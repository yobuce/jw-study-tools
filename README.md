# JW 집회 준비 도구 (jw-study-tools)

JWPUB 파일에서 집회 준비에 필요한 자료를 추출·정리하는 Claude Code 플러그인입니다.

> ⚠️ **출판물 데이터는 포함되어 있지 않습니다.** 이 플러그인은 *도구*만 제공합니다. 성경 DB와 `.jwpub` 파일은 각자 JW Library에서 받아 본인 기기에 두고 사용하세요(아래 "데이터 준비").

## 제공 명령어

| 명령어 | 기능 | 필요 데이터 |
| --- | --- | --- |
| `/lookup-verse <성구>` | 신세계역 성경 본문 조회 | `nwtsty_KO.db` |
| `/lookup-study <성구>` | 연구 노트·각주·연구 자료 찾아보기 조회 | `nwtsty_KO.db` (+`rsg19_KO.jwpub`) |
| `/read-jwpub <식별자> [옵션]` | JWPUB 본문·성구·참조 출판물 추출 | `*.jwpub` (+DB) |
| `/extract-jwb-subtitle <jw.org URL>` | jw.org 동영상 한국어 자막 추출 | 없음 (웹) |
| `/meeting-workbook [날짜]` | 주중집회 전체 순서·참조 정리 | `집회교재/mwb_KO_*.jwpub` |
| `/wt-preview [날짜]` | 파수대 연구 예습자료(항별 질문·성구·참조) | `파수대/w_KO_*.jwpub` (+DB) |

설치된 플러그인의 명령어는 네임스페이스가 붙어 `/jw-study-tools:lookup-verse` 형태로 호출됩니다.

## 설치

### 1. 마켓플레이스 추가 후 설치

```
/plugin marketplace add https://github.com/yobuce/jw-study-tools
/plugin install jw-study-tools@jw-study-tools
```

### 2. 파이썬 의존성 설치

```bash
pip install -r requirements.txt
# 또는
pip install "cryptography>=42.0"
```

`python` 명령으로 Python 3.10+ 가 실행되어야 합니다.

## 데이터 준비

### 데이터 폴더 위치 정하기

플러그인을 활성화하면 **"JWPUB 데이터 폴더 경로"** 를 묻는 입력창이 뜹니다.

- **경로를 입력하면**: 그 폴더를 데이터 폴더로 사용합니다(이미 JWPUB 모음 폴더가 있으면 그 경로를 그대로 지정).
- **비워 두면**: `~/.claude/plugins/data/jw-study-tools.../JWPUB/` (플러그인 영구 데이터 폴더)를 사용합니다. 이 폴더는 플러그인을 업데이트해도 유지됩니다. 정확한 경로는 `/plugin` 화면에서 확인할 수 있습니다.

### 폴더 구성

지정한 데이터 폴더(이하 `<DATA>`) 안을 다음과 같이 채웁니다:

```
<DATA>/
├── nwtsty_KO.db               # 신세계역 연구용 성경 DB (성구·연구 노트·각주)
├── nwtsty_KO_manifest.json    # 위 DB 복호화용 manifest
├── rsg19_KO.jwpub             # (선택) 연구 자료 찾아보기
├── *.jwpub                    # (선택) 공개강연 등 임의 JWPUB
├── 집회교재/
│   └── mwb_KO_YYYYMM.jwpub    # /meeting-workbook 용 (격월 발행본)
└── 파수대/
    └── w_KO_YYYYMM.jwpub      # /wt-preview 용 (월별 연구판)
```

`nwtsty_KO.db` / `nwtsty_KO_manifest.json` 은 JW Library 앱의
`LocalState/Publications/nwtsty_KO/` 에서 복사할 수 있습니다.
`.jwpub` 파일은 JW Library에서 출판물을 내려받으면 생기는 파일입니다.

## 동작 방식 (경로 해석)

각 명령어는 데이터 폴더를 이 순서로 결정합니다:

1. 환경변수 `JW_DATA_DIR` (명령어가 설정값 `${user_config.data_dir}` 로 주입)
2. `${CLAUDE_PLUGIN_DATA}/JWPUB` (입력창을 비워 둔 경우의 기본값)
3. 로컬 개발 시: 스크립트 상위의 `JWPUB/` 폴더

스크립트(`scripts/*.py`)는 `${CLAUDE_PLUGIN_ROOT}` 안에 동봉되어 있고, 데이터는 위에서 정한 폴더에서 읽습니다. 즉 **코드와 데이터가 분리**되어 플러그인을 업데이트해도 데이터는 그대로 유지됩니다.

## 용어 처리

`/meeting-workbook`·`/wt-preview` 처럼 요약·정리 문장을 덧붙이는 명령은 `docs/용어가이드.md` 의 여호와의 증인 용어 규칙(통치체→중앙장로회, 형제님→형제, 사역→봉사, 교회→회중 등)을 **에이전트가 직접 쓴 문장에만** 적용합니다. 추출·인용한 출판물 원문은 그대로 보존합니다.

## 주의

- 이 도구는 개인 연구·집회 준비를 돕기 위한 것입니다. 추출한 출판물 본문을 재배포하지 마세요.
- `jw-study-tools`는 비공식 도구이며 Watch Tower Bible and Tract Society와 무관합니다.

## 라이선스

[PolyForm Noncommercial License 1.0.0](LICENSE) — **비상업 목적**에 한해 사용·수정·배포가 자유롭습니다(개인 연구·집회 준비 등). 상업적 이용은 허용되지 않습니다. 본 라이선스는 이 저장소의 **코드(스크립트·명령어)** 에만 적용되며, JWPUB 출판물 데이터는 Watch Tower의 저작물로 별개입니다.
