# 타이틀 로고 이미지

## 원본 자산

화면에 표시되는 `ブシドーブレード / BUSHIDO BLADE / TM` 로고는
`DATA3/O12.DAT` 파일 오프셋 `0x1CC`의 표준 PlayStation TIM이다.

| 항목 | 값 |
| --- | --- |
| 자산 ID | `BBJ-TITLE-LOGO-001` |
| 형식 | 4bpp indexed TIM |
| 캔버스 | 480×128 |
| 색상 수 | 16 |
| 투명색 | 없음 |
| 배경색 | 팔레트 인덱스 15, 불투명 흰색 |
| TIM 전체 크기 | 30,784바이트 |
| O12.DAT SHA-256 | `10FB76FA2D09F99E48CF3B12B26521C6FE9618F15E0EFD2DF72F4CB79025E83B` |

## 편집할 파일

`workspace/graphics/title/title_ko_edit.png`를 편집한다. 추출기를 다시
실행해도 이 파일이 이미 있으면 덮어쓰지 않는다.

편집 시 다음을 지킨다.

- 캔버스를 480×128로 유지한다.
- 로고 밖의 불투명 흰색 배경을 유지한다.
- 화면 아래쪽 메뉴 프레임은 이 자산에 포함되지 않는다.
- 원본은 16색이므로 최종 재삽입 단계에서 최대 16개 팔레트 인덱스로
  변환한다. 필요하면 기존 CLUT도 같은 TIM 크기 안에서 교체할 수 있다.
- `title_original.png`, `title_original_indexed.png`, `title_original.tim`은
  비교·재삽입 기준이므로 수정하지 않는다.

PNG 편집이 끝나면 `title_ko_edit.png`를 그대로 저장하고 알려 준다.
그 다음 무수정 TIM 왕복, 색상 양자화, Expected Write, BIN 재생성,
DuckStation 실제 표시 검증을 진행한다.

## 현재 편집본

- 문구: `무사도의 검`
- SHA-256:
  `2748EC82C9A4EE8390D6A133442966A3EDE7674318626919A766FC88590AB4F5`
- 상태: 16색 TIM 변환 및 정적 BIN 검증 통과
- 상세: [poc-title.md](poc-title.md)

## 재추출

프로젝트 루트에서 실행한다.

```powershell
python .\workspace\scripts\extract_title_asset.py `
  '.\extracted\DATA3\O12.DAT' `
  --output-dir '.\workspace\graphics\title'
```
