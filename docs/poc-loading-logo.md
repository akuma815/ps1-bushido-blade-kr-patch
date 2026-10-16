# 로딩 로고 경로 진단

## 관찰

타이틀 로고를 한글로 교체한 빌드에서도 로딩 화면 우하단에는 작은 일본어
`ブシドーブレード` 로고가 남았다. 따라서 타이틀의
`DATA3/O12.DAT @ 0x1CC`를 공유한다는 가설은 기각했다.

## 기각된 후보: DATA3/O01.DAT

정적 그림이 유사한 `DATA3/O01.DAT @ 0x7CB18`의 552x144, 4bpp TIM을
두 방식으로 시험했다.

1. `BBJ-POC-GFX-003-CLUT`: 내부 16색 CLUT 32바이트를 진단색으로 교체
2. `BBJ-POC-GFX-003-PIXELS`: CLUT는 보존하고 전체 픽셀 인덱스를 반전

두 빌드 모두 원본과 화면이 동일했다. 픽셀 payload 39,744바이트의 원시
복사본과 니블 반전 복사본도 전 파일에서 검색했지만 O01 내부 한 곳만
나왔다. 그러므로 O01 TIM의 실제 로딩 화면 소비 가설은 기각한다.

- CLUT 무효 증거:
  `work/screenshots/poc_loading_logo/ブシドーブレード 2026-07-29-22-50-33.png`
- 픽셀 무효 증거:
  `work/screenshots/poc_loading_logo_pixels/ブシドーブレード 2026-07-29-22-57-42.png`
- 원시 payload 검색:
  `work/analysis/loading_logo_raw_payload_search.json`

## 실제 텍스처 연결

DuckStation의 텍스처 덤프를 켜고 로딩 화면을 다시 통과해 실제 렌더링에
사용된 136x32 P4 텍스처를 확보했다.

- 런타임 덤프:
  `work/graphics/duckstation_loading_dump/runtime_loading_logo_136x32.png`
- 런타임 PNG SHA-256:
  `7D1A3B737FB01ED4A07B784E0D66D9621407C67E15A894FB3F37AF46147F12F9`
- 정적 목록의 유일한 136x32 TIM:
  `SLPM_860.20 @ 0x7657C`
- 실행 파일 SHA-256:
  `0164189A870BA87495A3C0010ADB85535AE5A798BBD0DD276B88BF24ECC3CEDA`
- TIM 형식: 136x32, 4bpp, 16색 CLUT, 총 2,240바이트
- 원본 PNG:
  `workspace/graphics/loading/source/SLPM_86020_0007657C.png`
- 편집 PNG:
  `workspace/graphics/loading/edited/SLPM_86020_0007657C.png`

런타임 덤프와 정적 PNG는 크기·글자 형태·16색 구성이 일치한다. 472픽셀의
색상값 차이 중 450개는 PS1 BGR555를 8비트로 확장하는 방식에 따른 채널값
1 차이이며, 나머지 22픽셀은 덤프 시점의 텍스처 상태 차이로 남긴다.

## 현재 PoC: BBJ-POC-GFX-004

`SLPM_860.20 @ 0x7657C`의 CLUT와 TIM 헤더는 보존하고 4bpp 픽셀 인덱스만
모두 `15-index`로 반전했다.

- 실행 CUE:
  `build/poc_loading_logo_actual/Bushido Blade (Japan) [Actual Loading Logo Pixel Probe].cue`
- 출력 BIN SHA-256:
  `C4B32F07DC66A091A74C58D35C709092C63088101372BAC0B18C70C271A041DE`
- 변경된 파일 바이트: 2,176
- 수정 섹터: Mode2 Form1 2개
- 정적 판정: 통과
- 런타임 판정: 최종 통과

실제 로딩 화면은 검은 바탕과 흰 로고로 반전됐다. 따라서
`SLPM_860.20 @ 0x7657C`가 런타임 소비 자산임을 확인했다.

- 런타임 증거:
  `work/screenshots/poc_loading_logo_actual/ブシドーブレード 2026-07-29-23-13-01.png`
- 증거 SHA-256:
  `89FE65B0D3C32AA8AB21C6E3482451A4F33B8C2D928535E9A053039008765861`

사용자가 변경된 화면을 확인하고 스크린샷을 남겼으므로 자산 도달성 검증인
`BBJ-POC-GFX-004`를 최종 통과로 종료한다. 타이틀 메뉴 회귀 여부는 별도
최종 빌드 QA 항목으로 유지한다.
