# BBJ-POC-GFX-001 옵션 헤더 한글 표시

## 판정 목적

일본판 `SLPM-86020`의 실제 TIM 저장·적재·표시 경로에서 한글 그래픽이
도달하는지 확인한다. 표적은 `DATA3/O04.DAT` 파일 오프셋 `0x3CCC`의
232×16 4bpp TIM `オプション`이며 역문은 `옵션`이다.

## 사전 판정 기준

- 통과: 목표 화면에 `옵션`이 표시되고 주변 UI, 옵션 이동, 이탈·재진입이
  정상이다.
- 실패: 원문이 유지되거나 화면·팔레트·진입 동작이 손상된다.
- 불명확: 한글 표시 여부나 주변 회귀를 캡처로 판정할 수 없다.

통과해도 다른 DAT, 동적 문자열, 스토리 대사, 전체 그래픽 모집단은
증명하지 않는다.

## 빌드 기준선

| 항목 | 값 |
| --- | --- |
| 원본 BIN SHA-256 | `7D89B9254C5001AD7FC4F0C2FC3CDA9600417D075292C34D3B60AA3B600DBA96` |
| 원본 크기 | `510021792` |
| 대상 파일 | `DATA3/O04.DAT` |
| 대상 파일 LBA | `25849` |
| 대상 파일 SHA-256 | `DDED78FBE71C06711EA2F35E8F29D134DF18A21A1CF3FE6BCD0F0C1F62B5EE91` |
| TIM 오프셋·크기 | `0x3CCC`, `1920`바이트 |
| 이미지 payload | 파일 오프셋 `15628`, `1856`바이트 |
| 폰트 | `Galmuri11-Condensed.ttf` |
| 폰트 SHA-256 | `7B433B4A007C36DFB535FDEA11DE3E4F4C8B641AB591ED05D3BC0A4BBD75EB5F` |

## 정적 빌드 결과

- 무변경 4bpp 디코드→인코드가 원본 payload와 바이트 동일했다.
- 원본 TIM 헤더·CLUT·전체 길이와 `O04.DAT` 크기를 보존했다.
- 조판에는 원본 팔레트 인덱스 `0`, `2`, `6`, `14`만 사용했다.
- DAT 변경 594바이트는 TIM 이미지 payload 안에만 있다.
- raw BIN에서는 LBA `25856`, `25857`만 다시 썼다.
- 두 원본 섹터의 Mode 2 Form 1 EDC/ECC를 재계산해 원본과 일치함을
  먼저 확인했고, 수정 후 EDC/ECC도 다시 생성·검증했다.
- BIN 크기와 ISO 파일 extent를 유지하고 수정본에서 `O04.DAT`와 TIM을
  다시 파싱했다.
- 정적 검증 산출물 SHA-256:
  `1CBB3136DBE5F4D179C9DFA3C023017D5F42B2804A7B195D51A09BF937FEBCF4`

전체 Expected Write와 섹터 전후 해시는
`build/poc_options/build_report.json`이 소유한다.

## 재현

프로젝트 루트에서 실행한다.

```powershell
python .\workspace\scripts\build_options_poc.py `
  '.\original\Bushido Blade (Japan).bin' `
  --font '..\..\Font\Galmuri11-Condensed.ttf' `
  --output-dir '.\build\poc_options'
```

빌드는 원본·폰트 해시가 다르거나 TIM 구조, 왕복, Expected Write,
Mode 2 Form 1 무결성 검사가 실패하면 산출물을 승인하지 않는다.

## 런타임

- 환경: DuckStation 0.1.11621
- 대상 CUE:
  `build/poc_options/Bushido Blade (Japan) [KO Options PoC].cue`
- 실행 시각: 2026-07-29 21:11 KST
- 현재 상태: **최종 통과**
- 확인 결과:
  - 실제 게임의 원본 TIM 소비 경로에서 `옵션`이 판독 가능하게 표시됐다.
  - 목판형 옵션 탭과 배경에 눈에 띄는 손상이 없다.
  - DuckStation 프로세스가 응답 상태를 유지했다.
  - 사용자가 옵션에서 메인 메뉴로 복귀한 뒤 옵션에 다시 진입했고 정상
    동작을 확인했다.
- 런타임 증거:
  `work/screenshots/poc_options/ブシドーブレード 2026-07-29-21-12-25.png`
- 증거 SHA-256:
  `BAB7CABA114CF0DB70E35D63509B8C5427305C58B4AB95218BF1F49407642B27`
- HITL 회귀 확인: 2026-07-29 사용자 응답 `재진입 정상`
- 판정: `BBJ-POC-GFX-001`은 가시성 및 대표 그래픽 E2E 게이트를
  통과했다. 증명 범위는 `DATA3/O04.DAT`의 고정 크기 4bpp TIM 교체와
  동일 크기 raw BIN 제자리 패치이며, 다른 DAT와 동적 문자열 경로는
  별도 검증 대상이다.
