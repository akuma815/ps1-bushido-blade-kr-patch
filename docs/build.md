# 빌드 계획

생산 빌드는 아직 없으며, 대표 그래픽 경로용 PoC 빌드가 있다.

## 옵션 헤더 PoC

진입점:

```powershell
python .\workspace\scripts\build_options_poc.py `
  '.\original\Bushido Blade (Japan).bin' `
  --font '..\..\Font\Galmuri11-Condensed.ttf' `
  --output-dir '.\build\poc_options'
```

이 빌드는 일본판 BIN과 폰트의 SHA-256을 고정하고
`DATA3/O04.DAT`의 `0x3CCC` TIM만 같은 크기로 바꾼다. TIM 왕복,
팔레트·CLUT·크기, Expected Write, Mode 2 Form 1 EDC/ECC,
수정본 디스크 재파싱을 모두 통과해야 결과를 만든다.

현재 정적 검증 BIN SHA-256:
`1CBB3136DBE5F4D179C9DFA3C023017D5F42B2804A7B195D51A09BF937FEBCF4`

상세 판정은 [poc-options.md](poc-options.md)와
`build/poc_options/build_report.json`을 본다.

## 타이틀 로고 PoC

```powershell
python .\workspace\scripts\build_title_poc.py `
  '.\original\Bushido Blade (Japan).bin' `
  --edited-png '.\workspace\graphics\title\title_ko_edit.png' `
  --output-dir '.\build\poc_title'
```

이 빌드는 깨끗한 일본판 원본에서 `DATA3/O12.DAT`의 타이틀 TIM만
교체한다. 사용자 PNG를 16색 PS1 BGR555로 변환하고 CLUT·이미지 payload,
Expected Write, Mode 2 Form 1 EDC/ECC와 수정본 재파싱을 검증한다.

정적 검증 BIN SHA-256:
`969D40AC7D8DFAD2123FB68D37088A93A9B63473DD42975DA14322EBCAF5D7F8`

상세 판정은 [poc-title.md](poc-title.md)와
`build/poc_title/build_report.json`을 본다.

## 로딩 로고 경로 진단

```powershell
python .\workspace\scripts\build_loading_logo_probe.py `
  '.\original\Bushido Blade (Japan).bin' `
  --output-dir '.\build\poc_loading_logo'
```

이 빌드는 깨끗한 일본판 원본에서 `DATA3/O01.DAT @ 0x7CB18`의 픽셀
인덱스는 보존하고 CLUT 32바이트만 진단색으로 바꾼다. 출력 CUE는
`build/poc_loading_logo/Bushido Blade (Japan) [Loading Logo Probe].cue`다.
상세 판정은 [poc-loading-logo.md](poc-loading-logo.md)와
`build/poc_loading_logo/build_report.json`을 본다.

초기 빌드 진입점은 다음 책임을 한 번에 수행하도록 만들 예정이다.

1. 일본판 BIN SHA-256 확인
2. 수정 대상 원본 파일·블록 해시와 예상 바이트 확인
3. 깨끗한 추출본에서 수정 파일 생성
4. 파일 크기와 LBA 불변 검사
5. raw Mode 2/2352 BIN에 허용된 sector만 적용
6. Form 1 EDC/ECC와 XA/STR 비변경 범위 검사
7. 결과 BIN/CUE와 SHA-256 생성
8. xdelta와 배포 README 생성

파일 성장이나 이동이 필요해질 때만 전체 `dumpsxiso`/`mkpsxiso` 재빌드
경로를 승인한다.

## 전체 그래픽 통합 빌드

남은 UI 편집 배치와 기존 검증 완료 그래픽을 깨끗한 일본판 BIN에 한 번에
적용하는 진입점은 다음과 같다.

```powershell
python .\workspace\scripts\build_full_graphics_patch.py `
  '.\original\Bushido Blade (Japan).bin' `
  --ui-catalog '.\workspace\graphics\ui_translation_batch\edit_catalog.json' `
  --title-png '.\workspace\graphics\title\title_ko_edit.png' `
  --loading-png '.\workspace\graphics\loading\edited\SLPM_86020_0007657C.png' `
  --ending-dir '.\workspace\graphics\ending\edited' `
  --font '..\..\Font\Galmuri11-Condensed.ttf' `
  --output-dir '.\build\full_graphics_ko'
```

2026-07-30 정적 빌드 결과:

- UI 배치 97개 중 수정 96개 적용
- `O14.DAT @ 0x1B68`의 `????????` 자산 1개는 원본 유지
- 기존 타이틀·옵션 헤더·로딩·엔딩을 포함한 TIM 대상 106개
- 변경 Form 1 섹터 145개
- BIN 크기 불변: 510,021,792바이트
- BIN SHA-256:
  `BC346F722D1152028742519E3534022D03F31D6AA6F7BE1C17337227D893FC6F`

Expected Write, TIM 원본 라운드트립, 파일 크기, Mode 2 Form 1 EDC/ECC,
최종 디스크 파일·TIM 재파싱은 모두 통과했다. 런타임 전수 검수는 아직
진행 전이다. 상세 결과는 `build/full_graphics_ko/build_report.json`이다.

2026-07-31 런타임 검수에서 캐릭터 선택 세로 이름패 6개가 최초 97개
모집단에서 누락됐음을 확인했다. 따라서 위 SHA-256 빌드는 기존 선언 범위의
정적 검증에는 통과했지만 전체 그래픽 릴리스 후보로는 폐기한다. 추가된
`character_names` 6개 편집 후 같은 진입점으로 새 통합판을 생성한다.

## 최종 빌드 (2026-08-04)

두 판은 **각각 별개의 디스크**다. 한쪽에 합치는 것이 아니다.

### 미국판 — `build/usa_final`

```powershell
python .\workspace\scriptsuild_usa_subtitle.py `
  --output-dir build/usa_final --base-name "Bushido Blade (USA) [KO Final]"
```

| 항목 | 값 |
| --- | --- |
| 자막 | 파일 11개 / 167 레코드 + EXE 내장 19 레코드 |
| 캐릭터 선택 이름판 | 6개 |
| 갤러리 | 7/7 상시 해제 |
| 배드엔딩 이미지 | **미적용** (사용자 지시) |
| 풀 | `0x801FB000..0x801FDF2C` (여유 4308 B) |

배드엔딩 이미지를 넣으려면 같은 BIN에 한 줄 더 돌리면 된다:

```powershell
python .\workspace\scriptsuild_usa_tim_patch.py `
  --in-place "build/usa_final/Bushido Blade (USA) [KO Final].bin"
```

### 일본판 — `build/jp_final` (그래픽 → 자막 2단 체인)

1단, 그래픽 통합 (`build/jp_final_graphics`,
SHA-256 `69D60C075C677F9EA489E88E61731650A924B01F9E8C43A39AC3E9D4B04FA90D`):

```powershell
python .\workspace\scriptsuild_full_graphics_patch.py `
  "original/Bushido Blade (Japan).bin" `
  --ui-catalog "workspace/graphics/ui_translation_batch/edit_catalog.json" `
  --title-png "workspace/graphics/title/title_ko_edit.png" `
  --loading-png "workspace/graphics/loading/edited/SLPM_86020_0007657C.png" `
  --ending-dir "workspace/graphics/ending/edited" `
  --font "../../Font/Galmuri11-Condensed.ttf" `
  --output-dir build/jp_final_graphics
```

UI 102/103 적용(`O14.DAT @0x1B68`의 `????????` 1개는 원본 유지), TIM 대상
112개, 변경 섹터 149개. **캐릭터 이름판 6개가 이번엔 포함**됐다
(이전 통합본은 96/97·106개였고 그래서 폐기됐었다).

2단, 자막 (`build/jp_final`, SHA-256
`D3709408ABB2826CD04CB9E0C4346E1543A1DBE8A9A210C7E9C51ABE2EB9C33D`):

```powershell
python .\workspace\scriptsuild_subtitle_v37.py `
  "build/jp_final_graphics/Bushido Blade (Japan) [Korean Graphics].bin" `
  --output-dir build/jp_final --base-name "Bushido Blade (Japan) [KO Final]" `
  --allow-source-sha 69D60C075C677F9EA489E88E61731650A924B01F9E8C43A39AC3E9D4B04FA90D `
  --allow-exe-sha 909CBD027BAA13A6C0FA2B86ADF90FEA6606102EFF2F5A00DB052ED4DB2103AB `
  --files DATA7/V00.DAT DATA7/V01.DAT DATA7/V02.DAT DATA7/V03.DAT DATA7/V04.DAT `
          DATA7/V05.DAT DATA7/V06.DAT DATA7/V07.DAT DATA7/V08.DAT
```

V00~V08 재배치 9개 전부 `directory_lba_ok / directory_size_ok / form1 /
content_ok` 통과, `exe_roundtrip=True`.

#### 체인하려면 게이트를 명시적으로 열어야 한다

`build_subtitle_v37.py`는 소스 BIN 해시와 `SLPM_860.20` 해시를 고정하고
있었는데, 그래픽 통합본은 **둘 다** 바꾼다 — 로딩 로고가 EXE 내부
(`SLPM_86020 @0x7657C`) 이미지이기 때문이다. 게이트를 없애지 않고
`--allow-source-sha` / `--allow-exe-sha` 허용 목록을 추가했다. 호출자가
해시를 직접 적어야 하므로 엉뚱한 입력은 여전히 거부되고, 케이브 소스 해시와
expected-write 검사는 그대로 남는다.

**순서를 뒤집지 말 것**: 자막 빌드가 V0X를 이미지 끝으로 재배치하므로,
그래픽을 나중에 올리면 그래픽 빌더가 자기 해시 게이트에서 막힌다.

## 미국판 UI 자산 추출 (2026-08-04)

```powershell
python .\workspace\scripts\extract_usa_ui_assets.py
```

출력은 `workspace/graphics/usa/`. 일본판 규약과 동일하게 자산마다 두 장을
쓴다 — `*_original.png`는 매번 새로 덮어쓰는 참조본, `*_ko_edit.png`는
편집 대상이고 이미 있으면 **덮어쓰지 않는다**.

| 자산 | 위치 | 크기 | TIM | CLUT | 투명 |
| --- | --- | --- | --- | --- | --- |
| 타이틀 로고 | `DATA3/O12.DAT +0x0001CC` | 480×128 4bpp | 30,784 B | 16색 | 없음 |
| 로딩 로고 | `SCUS_941.80 +0x074A84` | 176×16 4bpp | 1,472 B | 16색 | 인덱스 15 |

- **타이틀은 일본판과 같은 파일·같은 오프셋·같은 크기**다
  (일본판 `DATA3/O12.DAT @0x1CC`, 480×128). 내용은 카타카나 로고 +
  `BUSHIDO BLADE` + TM.
- **로딩 로고는 일본판과 다르다.** 일본판은 EXE `SLPM_86020 @0x7657C`의
  136×32 카타카나였고, 미국판은 실행 파일 안의 **유일한 TIM**
  (`SCUS_941.80 @0x74A84`)인 176×16 영문 `BUSHIDO BLADE`다.

편집 제약:

- 캔버스 크기를 유지할 것. 픽셀 payload를 그대로 덮어쓰는 방식이라
  크기가 바뀌면 TIM 전체를 다시 만들어야 한다.
- 색은 기존 16색 CLUT 안에서 쓰는 것이 가장 안전하다. 벗어나면 재삽입
  단계에서 최근접 색으로 양자화된다(`build_usa_tim_patch.py`와 동일 방식).
- 로딩 로고는 인덱스 15가 투명이다. 배경은 투명으로 남길 것.
- 타이틀에는 투명색이 없다. 로고 밖 영역은 원본 배경색을 유지할 것.

재삽입 스크립트는 편집이 끝난 뒤 작성한다(`build_usa_tim_patch.py`의
양자화 로직을 그대로 재사용하면 된다).

## 최종 빌드 갱신 (2026-08-04 저녁)

### 미국판 — UI 자산 재삽입 추가

```powershell
python .\workspace\scriptsuild_usa_ui_patch.py `
  --in-place "build/usa_final/Bushido Blade (USA) [KO Final].bin"
```

`build_usa_tim_patch.py`와 같은 계약이다 — 편집 PNG를 원본 16색 CLUT로
재양자화해 **픽셀 payload만** 덮어쓴다. 크기·CLUT가 안 바뀌므로 뒤따르는
데이터가 움직이지 않는다. CLUT는 **대상 이미지에서** 읽는다(로딩 로고가
`SCUS_941.80` 안에 있고 자막 빌더가 그 실행 파일을 재배치·증가시키기
때문). 값이 `0x0000`인 CLUT 엔트리는 투명이며 불투명 픽셀에는 절대 고르지
않는다.

되읽기 검증: `extract_usa_ui_assets.py <빌드 bin> --output-dir
work/analysis/usa_ui_verify` → 타이틀 `무사도의 검`, 로딩 `무 사 도 의 검`
확인.

**미국판 최종본 `build/usa_final` 현재 내용**: 자막(파일 167 + EXE 19) +
캐릭터 선택 이름판 6개 + 배드엔딩 이미지 6장 + 타이틀 + 로딩 로고 +
갤러리 상시 해제.

미국판 빌드 순서는 반드시 이 순서다:

1. `build_usa_subtitle.py` (디스크 생성 — EXE 재배치가 여기서 일어난다)
2. `build_usa_tim_patch.py --in-place` (배드엔딩 이미지)
3. `build_usa_ui_patch.py --in-place` (타이틀·로딩)

### 일본판 — 캐릭터 프로필 편집 반영 재빌드

`character_profile` 8장(`DATA3/O14.DAT`) 수정 후 2단 체인을 다시 돌렸다.
해시는 매 재빌드마다 바뀌므로 2단의 `--allow-source-sha`에 1단 결과를
그대로 넣어야 한다. EXE 해시는 로딩 로고를 안 건드리는 한 그대로다.

| 단계 | 산출물 | SHA-256 |
| --- | --- | --- |
| 1단 그래픽 | `build/jp_final_graphics` | `CE31E87E48FEA5CF5CD05206288BE44E5E9ECCE05307D54272876F32C4C289B6` |
| 1단 EXE | `SLPM_860.20` | `909CBD027BAA13A6C0FA2B86ADF90FEA6606102EFF2F5A00DB052ED4DB2103AB` |
| 2단 자막 | `build/jp_final` | `E88330A30EC16518A4DDA324BC3A876CCE7A1C25BD9CB85989F55277664848A8` |

**주의**: 대상 BIN을 DuckStation이 열고 있으면 2단이
`PermissionError`로 죽는다. 에뮬레이터를 닫고 돌릴 것.

## 배포 빌드 (2026-08-04)

```powershell
python .\workspace\scripts\make_release_dist.py
```

출력: `dist/BushidoBlade_KR_Patch_20260804/`. Prinny 배포물과 같은 구조다
(xdelta + apply 도우미 + tools + SHA256.txt + release_report.json).

| 패치 | 크기 | 역검증 |
| --- | --- | --- |
| `BushidoBlade_SCUS94180_KR_20260804.xdelta` | 1,699,363 B | PASS |
| `BushidoBlade_SLPM86020_KR_20260804.xdelta` | 1,155,560 B | PASS |

역검증은 만든 패치를 **원본에 되적용해 결과 해시를 빌드 해시와 대조**하는
것이다. 패치를 재현할 수 없는 패치는 깨진 패치이고, 그건 사용자 PC가 아니라
여기서 잡아야 한다.

적용 스크립트도 실제로 돌려 확인했다(경로 오타 같은 건 이 방법 말고는
안 잡힌다):

```
apply_usa.ps1 "original/Bushido Blade (USA).bin"    -> PASS (bin + cue)
apply_jp.ps1  "original/Bushido Blade (Japan).bin"  -> PASS (bin + cue)
```

PS1은 cue가 필요하므로 적용 스크립트가 `_KR.cue`(단일 MODE2/2352 트랙)를
BIN 옆에 같이 만든다. Prinny(단일 ISO)와 다른 점이다.

**아직 남은 것**: 이 빌드 쌍의 런타임 확인. 특히 미국판 캐릭터 선택
이름판(d3 구성)은 정적 검증만 통과했고 사용자 실행 확인이 없다.
`release_report.json`의 `runtime.user_runtime_confirmation`이 `PENDING`인
이유다.

## 일본판 갤러리 해금 (2026-08-04)

```powershell
python .\workspace\scriptsuild_jp_gallery_unlock.py `
  --in-place "build/jp_final/Bushido Blade (Japan) [KO Final].bin"
```

해금 비트마스크는 `[0x800A30B8]`. 2026-08-02 조사에서 분기 게이트
(`0x8004A51C`, `0x8004A578`) nop만으로는 메뉴가 잠긴 채였고 런타임 포크로만
열렸다 — 그래서 **마스크를 읽는 지점을 전부 `li $r,-1`로 강제**하는,
미국판에서 통한 방식을 그대로 썼다.

마스크를 건드리는 명령은 11곳이고 그중 **4곳은 의도적으로 남긴다**:

| 주소 | 역할 | 이유 |
| --- | --- | --- |
| `0x80049884` / `0x80049894` | 해금 세터 (`lw`/`or`/`sw`) | 로드를 강제하면 게임이 마스크에 -1을 되쓰고 세이브 경로를 통해 메모리카드까지 오염된다 |
| `0x80065050` / `0x800650F8` | 세이브 직렬화 | 같은 이유 |

남는 것이 정확히 **메뉴 게이트 로드 7곳** — 미국판이 필요로 했던 수와 같다
(미국판도 눈에 띄는 게이트 2곳만으로는 무효였다):

`0x8004A37C` `0x8004A410` `0x8004A440` `0x8004A510` `0x8004A56C`
`0x8004A674` `0x8004A6A4`

각 로드는 베이스와 목적지가 같은 레지스터(`lui $r,0x800a; lw $r,0x30b8($r)`)
라서 로드만 `li $r,-1`로 바꾸면 앞의 `lui`는 무해하게 남는다.

**일본판 빌드 순서**(미국판처럼 3단계가 됐다):

1. `build_full_graphics_patch.py` → `build/jp_final_graphics`
2. `build_subtitle_v37.py` (1단 해시를 `--allow-source-sha`/`--allow-exe-sha`로)
   → `build/jp_final`
3. `build_jp_gallery_unlock.py --in-place <bin>`

## 런타임 확인 완료 (2026-08-04)

사용자 실행 확인 **PASS**. `release_report.json`의
`runtime.user_runtime_confirmation`을 `PASS`로 올리고 배포 zip을 다시 만들었다.

갤러리 해금은 **빈 메모리카드 상태에서 대조 검증**했다. 처음 열려 보였던
것은 패치가 아니라 세이브 때문이었다 —
`H:\Duckstation\memcards\Bushido Blade (Japan)_1.mcd`에 진행 세이브가
있었고, 해금 마스크는 세이브에서 복원되는 값(`0x80065050` /
`0x800650F8` 경로)이라 **원본 디스크로 실행해도 갤러리가 열린다**.
DuckStation 설정이 `Card1Type = PerGameTitle`이라 원본과 패치본이 같은
카드를 공유하는 것도 원인이었다.

카드를 `Bushido Blade (Japan)_1.mcd.bak_20260804`로 비켜두고 다시 확인한
결과, 원본은 잠김 / 패치본은 열림 → **해금이 패치에서 온다는 것이 확정**.

교훈: 세이브에서 복원되는 상태(해금 플래그·진행도)를 검증할 때는 반드시
메모리카드를 비우고 대조군부터 돌린다. 그러지 않으면 패치가 하지도 않은
일을 했다고 착각한다.

## 미국판 자막 파일 오인식 수정 빌드 (2026-08-11)

기존 미국판은 파일 후보를 공통 `K1US` 한 단어만으로 판별해, V03 레코드 1이
V02 레코드 4 후보와 cross-file alias를 일으켰다. 수정본은 K1US 헤더를
`magic, crc32(source_path), record_count`로 확장하고 magic/path tag 두 단어를
모두 검사한다. 실패한 DMA wait 실험은 최종 코드에서 제거했다.

깨끗한 미국판 원본에서 다음 순서로 생성했다.

```powershell
python .\workspace\scripts\build_usa_subtitle.py `
  --output-dir build/usa_final_20260811 `
  --base-name "Bushido Blade (USA) [KO Final 20260811]"
python .\workspace\scripts\build_usa_tim_patch.py `
  --in-place "build/usa_final_20260811/Bushido Blade (USA) [KO Final 20260811].bin"
python .\workspace\scripts\build_usa_ui_patch.py `
  --in-place "build/usa_final_20260811/Bushido Blade (USA) [KO Final 20260811].bin"
```

| 항목 | 값 |
| --- | --- |
| BIN | `build/usa_final_20260811/Bushido Blade (USA) [KO Final 20260811].bin` |
| BIN SHA-256 | `323C1A47604310B98A37F3711A16379BDA7014272A325F28D4023078078141E5` |
| BIN 크기 | 586,647,600 B |
| 자막 | 파일 11개 / 167레코드 + EXE 19레코드, skipped 0 |
| 그래픽 체인 | 배드엔딩 6장 + 타이틀 + 로딩 로고 |
| K1US 독립 검사 | 11파일 / 고유 태그 11개 / Form 1 PASS |
| 래퍼 | 948 B, cave 여유 237 B |
| 런타임 | V03 레코드 1 `너도 카게에 들어와 있었다니…` PASS |

최종 BIN은 사용자가 런타임 확인한
`build/usa_subtitle_render_fix2/...bin`과 SHA-256이 같으므로 그 장면의
런타임 증거가 바이트 동일하게 적용된다. 기존 `build/usa_final`과 `dist/`는
아직 보존 상태이며 배포 갱신은 별도 단계다.

## 최종 배포 패치 (2026-08-11)

미국판 자막 cross-file alias 수정본과 기존 일본판 최종본을 각각 독립
xdelta 패치로 만들었다. 원본 게임 BIN/CUE/ISO/IMG/CHD는 배포물에 포함하지
않는다.

```powershell
python .\workspace\scripts\make_release_dist.py `
  --date 20260811 `
  --usa-target "build\usa_final_20260811\Bushido Blade (USA) [KO Final 20260811].bin" `
  --out "dist\BushidoBlade_KR_Patch_20260811" `
  --scratch "work\tmp_release_20260811"
```

| 항목 | 크기 | SHA-256 |
| --- | ---: | --- |
| USA xdelta | 1,699,354 B | `271909E08C9EFCB980258AFA058A46D0C67F10E0D81C184474F707B4969FA118` |
| JP xdelta | 1,155,798 B | `DDA3068A39D878282EDCBB0C6A4774CF643906A30124361BADAB2F35B29C5691` |
| 배포 ZIP | 3,017,290 B | `3805440045F722DD8EBB1F3DC5DE777C2248C87C2FBC7A90896CDB19F61C684B` |

검증 결과:

- USA/JP xdelta 역적용 결과가 각 최종 BIN과 바이트 단위로 일치
- 공백이 포함된 원본 경로에서 `apply_usa.ps1`, `apply_usa.cmd`,
  `apply_jp.ps1`, `apply_jp.cmd` 실제 실행 PASS
- 네 적용 경로 모두 목표 BIN SHA-256 재현 및 단일 트랙 CUE 생성 PASS
- `SHA256.txt`의 10개 항목과 폴더 파일 일치
- ZIP 내부 11개 파일과 배포 폴더 일치, 내부 파일 해시 PASS
- 배포물 내 원본 게임 이미지 없음

배포 폴더는 `dist/BushidoBlade_KR_Patch_20260811`, 최종 압축 파일은
`dist/BushidoBlade_KR_Patch_20260811.zip`이다. 이전 2026-08-04 배포물은
덮어쓰지 않고 보존했다.
