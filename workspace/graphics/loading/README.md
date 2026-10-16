# 로딩 로고 자산

실제 로딩 화면 우하단의 `ブシドーブレード` 로고는 실행 파일에 포함된
`SLPM_860.20 @ 0x7657C`의 136x32, 4bpp TIM이다.

- `source/SLPM_86020_0007657C.png`: 원본 TIM 디코드
- `edited/SLPM_86020_0007657C.png`: 한글 편집용 복사본
- `work/graphics/duckstation_loading_dump/runtime_loading_logo_136x32.png`:
  DuckStation에서 실제 로딩 중 덤프한 텍스처 증거

`DATA3/O01.DAT @ 0x7CB18`의 552x144 TIM은 모양이 비슷한 별도 자산이다.
CLUT 교체와 전체 픽셀 반전 PoC 모두 실제 로딩 화면에 변화가 없었으므로
로딩 화면 소비 후보에서 제외했다. 기존 `O01_0007CB18.png` 파일은 기각된
가설의 재현 자료로 보존한다.

`BBJ-POC-GFX-004` 픽셀 반전 화면이 실제 로딩 중 출력되어 실행 파일 TIM의
런타임 도달성을 확인했다. 다음 작업은
`edited/SLPM_86020_0007657C.png`에 한글 로고를 넣고 동일 크기·16색 조건으로
재삽입하는 것이다.

## 편집 규칙

- 크기: 정확히 136x32
- 투명도: 사용하지 않음, 완전 불투명
- 권장 색상: 회색조 16색 이하
- 원본 파일과 `source` PNG는 수정하지 않음

재삽입기는 원본 TIM의 16색 CLUT를 그대로 보존하고 편집 PNG를 가장 가까운
기존 회색조 색인으로 변환한다. 편집본이 원본과 동일하면 실수로 빈 빌드를
만들지 않고 오류로 중단한다.

```powershell
python workspace/scripts/build_loading_logo_patch.py `
  "original/Bushido Blade (Japan).bin" `
  --edited-png "workspace/graphics/loading/edited/SLPM_86020_0007657C.png" `
  --output-dir "build/loading_logo_ko"
```

정상 빌드 결과는 `build/loading_logo_ko` 아래의 CUE/BIN, PS1 변환 미리보기,
패치된 `SLPM_860.20`, `build_report.json`이다.
