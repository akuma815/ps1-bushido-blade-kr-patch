# V0X 대사 장면 ↔ 자막 매핑 (2026-08-01 확정)

일본판 스토리 대사 장면과 한국어 자막의 완전 매핑. 근거는 라이브 추적
(`work/analysis/jp_scene_signature_catalog.json`)과 정적 파싱
(`work/analysis/jp_subtitle_scene_schedule.json`)이다.

## 1. 파일 구조 (JP/USA 공통)

`DATA7/V00.DAT`~`V08.DAT` 9개. 각 파일:

- `+0`: u32 섹션 수 (JP `0x13`=19, USA `0x14`=20)
- `+4`: u32 섹션 오프셋 배열
  - 섹션 0: 그룹 비교 테이블 (그룹당 8바이트 `{slot_word, length_frames}`)
  - 섹션 13~18: 그룹 0~5의 장면 레코드
    (`{stream_count, n1, n2}` 헤더 + 입모양/타이밍 스트림. 텍스트/음성 ID 없음)
  - **USA만** 섹션 19: 자막 타임라인 (아래)
- 모든 파일이 정확히 그룹 6개. JP/USA 그룹 길이 완전 일치 (54개 장면).
- 파일별 헤더 워드 지문은 유일 (`jp_v0x_scene_map.json`의 `fingerprint_unique`).

## 2. USA 자막 섹션 (섹션 19)

```text
u32 record_count
u32 boundaries[]     그룹별 시작 토큰 인덱스, 마지막 = 전체 토큰 수
u32 token_offsets[]  이 배열 시작 기준 상대 오프셋 (마지막은 EOF 센티널 가능)
텍스트 블롭          토큰 = 4자리 프레임 + 1자리 줄 수 + NUL 종료 줄들
                     줄 수 0 = 자막 소거 토큰
```

79개 텍스트 레코드 = CSV(`usa_subtitle_timeline.csv`) 77 + CSV 분석기가 놓친
말없음 자막 2 (V02 g0 f150 `` `..." ``, V04 g1 f190 `` `......" ``).

## 3. 런타임 장면 선택 체인 (일본판, 역공학 확정)

- 호출부 `0x800483EC` → 장면 시작 함수 `0x80039F6C(a0=stage, a1=group, a2=base)`
  - `stage = [0x800A3074](h) - 1`
  - `group = [0x800A30A0](h) % 6`
  - `base = 0x8014C000` (transient 디스크 버퍼, **장면 재생 중 V0X 파일 상주**)
  - 직전에 `jal 0x80045544` (스토리 VAB 로더, `docs/voice-runtime-trace.md`)
- `0x80039F6C` 내부:
  - `[gp+0x2F8] = base + [base+4] + group*8` (비교 엔트리)
  - `[gp+0x304] = base + [base + 0x38 + group*4]` (장면 레코드)
  - `[gp+0x310] = length`, `[gp+0x31C]/[gp+0x320]` = 스트림 커서(재생 중 전진)
- 프레임 갱신 `0x8003A874~`: `[gp+0x314]` 1→length 증가, `0x8003A88C`에서
  마지막 프레임 비교 (v35 훅 지점, `HANDOFF_2026-08-01.md` 15절)

## 4. 자막 엔진용 런타임 키 (영구 상태 0바이트)

장면 재생 중 매 프레임, 게임 소유 메모리만으로:

| 항목 | 방법 |
|---|---|
| 파일 | `0x8014C000` 헤더 워드 지문 대조 (9개 파일 유일) |
| 그룹 | `([gp+0x2F8] − 0x8014C000 − [0x8014C004]) ÷ 8` |
| 프레임 | `0x8003A88C`의 `a0` (= `[gp+0x314]`, 1..length) |
| 자막 | `jp_subtitle_scene_schedule.json`의 (파일, 그룹) 스케줄 |

검증: 첫 살인 장면 라이브 값 `cmp_ptr=0x8014C06C` → `(0x6C−0x54)÷8 = 그룹 3`,
JP V00 그룹 3 레코드 헤더 `{2,23,46}` = 라이브 소스 바이트와 일치,
스케줄 = frame 60 표시 / frame 180 소거 = 미국판 런타임 확인 기록과 일치.

## 5. 주의사항

- **장면 레코드 헤더 `{2, n1, 2·n1}`은 대사 고유 키가 아니다.** 길이가 같은
  장면끼리 동일하다 (180프레임 장면 전부 `{2,23,46}`). v35의 게이트는 이
  비고유 서명을 쓰므로 모든 180프레임 장면에서 발화한다. 다음 빌드는 위
  런타임 키로 교체할 것.
- `cmp` 테이블의 `slot_word`(6 또는 7)는 파일 데이터다. V00에서는 그룹 3만
  6이므로 보조 판별자로 쓸 수 있으나 파일마다 분포가 다르다.
- V0X는 전투 전후 대사 장면만 담당한다. 오프닝/엔딩 컷신 자막
  (`DATA3/O05·O14·O20`, 163 레코드)과 EXE 내장 문자열(19)은 별도 경로다.
- 스크립트: `workspace/scripts/build_v0x_subtitle_schedule.py`,
  `workspace/scripts/build_v0x_scene_map.py`,
  `workspace/scripts/trace_jp_scene_catalog.py`
