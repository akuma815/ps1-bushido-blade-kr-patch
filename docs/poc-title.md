# BBJ-POC-GFX-002 타이틀 로고 한글 표시

## 목적과 범위

사용자가 편집한 `무사도의 검` 타이틀 PNG를 일본판의 실제
`DATA3/O12.DAT` 4bpp TIM 경로로 되삽입한다. 이 PoC는 타이틀 로고 한
장만 대상으로 하며 옵션 헤더 PoC는 포함하지 않는다.

## 입력

| 항목 | 값 |
| --- | --- |
| 편집 PNG | `workspace/graphics/title/title_ko_edit.png` |
| 편집 PNG SHA-256 | `2748EC82C9A4EE8390D6A133442966A3EDE7674318626919A766FC88590AB4F5` |
| 캔버스 | 480×128 RGBA, 완전 불투명 |
| 입력 고유 색상 | 585 |
| 원본 자산 | `DATA3/O12.DAT` 오프셋 `0x1CC` |
| 원본 형식 | 4bpp indexed TIM, 16색 |

## 정적 빌드 결과

- Pillow MAXCOVERAGE, 디더링 없음으로 16색을 생성했다.
- 각 색은 PS1 BGR555로 변환했고 원본 CLUT와 같이 bit 15를 유지했다.
- TIM 블록 헤더, 캔버스, 전체 TIM 크기와 `O12.DAT` 크기는 불변이다.
- 변경은 TIM의 32바이트 CLUT와 이미지 payload 안으로 제한했다.
- raw Mode 2 Form 1 섹터 16개만 다시 썼고 각 섹터의 EDC/ECC를
  재생성·재검증했다.
- 수정 BIN에서 `O12.DAT`와 TIM을 다시 파싱해 팔레트와 픽셀 인덱스가
  빌드 결과와 같은지 확인했다.

정적 검증 BIN SHA-256:
`969D40AC7D8DFAD2123FB68D37088A93A9B63473DD42975DA14322EBCAF5D7F8`

PS1 색상 변환 미리보기:
`build/poc_title/title_ko_psx_preview_2x.png`

## 런타임 게이트

- 환경: DuckStation 0.1.11621
- 대상 CUE:
  `build/poc_title/Bushido Blade (Japan) [KO Title PoC].cue`
- 실행 시각: 2026-07-29 21:54 KST
- 현재 판정: **최종 통과**
- 확인 항목:
  - [x] `무사도의 검`이 잘림 없이 표시된다.
  - [x] `BUSHIDO BLADE`와 `TM`, 흰 배경이 유지된다.
  - [x] 하단 메인 메뉴 프레임과 기본 선택 항목이 정상 표시된다.
  - [x] 메뉴 선택 이동과 복귀가 정상이다.

런타임 캡처:
`work/screenshots/poc_title/ブシドーブレード 2026-07-29-21-55-42.png`

캡처 SHA-256:
`CEEA68AC9C1BA135D8910FBCBD3C8736985429363B1FCD23E8DD44DD96FA6907`

HITL 회귀 확인: 2026-07-29 사용자 응답 `타이틀 메뉴 정상`

판정: `BBJ-POC-GFX-002`는 통과했다. 증명 범위는
`DATA3/O12.DAT`의 480×128 4bpp TIM에서 CLUT와 이미지 payload를 같은
크기로 교체하는 경로다. 로딩 화면의 소형 일본어 로고는 별도 자산 또는
별도 소비 변형일 가능성이 있어 이 판정에 포함하지 않고 전체 그래픽
추출·분류 단계로 넘긴다.

상세 Expected Write와 섹터 해시는
`build/poc_title/build_report.json`이 소유한다.
