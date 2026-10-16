# TIM 그래픽 전수 목록

## 기준선과 분모

- 일본판 BIN SHA-256:
  `7D89B9254C5001AD7FC4F0C2FC3CDA9600417D075292C34D3B60AA3B600DBA96`
- 원본 TIM 스캔:
  `work/analysis/valid_tim_manifest.json`
- 검출 TIM: 3,696개
- 디코드 픽셀 기준 고유 화면: 2,261개
- 완전 중복 발생분: 1,435개

`workspace/scripts/build_tim_inventory.py`가 PNG 파일 바이트가 아니라 크기와
RGBA 디코드 픽셀을 해시해 중복을 묶는다. 결과의 정본은
`workspace/graphics/catalog/tim_inventory.json`이다.

## 검토 상태

| 그룹 | 고유 화면 | 연락시트 | 현재 상태 |
| --- | ---: | ---: | --- |
| DATA0 | 419 | 7 | 1차 시각 검토, 모델·의상 텍스처 |
| DATA1 | 38 | 1 | 1차 시각 검토, 모델·무기 텍스처 |
| DATA2 | 1,187 | 20 | 1차 시각 검토, 배경 글자 후보 28개 |
| DATA3 | 309 | 6 | 1차 시각 검토 |
| DATA4 | 191 | 4 | 1차 시각 검토, 모델·의상 텍스처 |
| DATA6 | 116 | 2 | 1차 시각 검토, 모델 텍스처 |
| SLPM_860.20 | 1 | 1 | 로딩 로고 확정, 런타임 PoC 통과 |

연락시트는 `work/graphics/tim_unique_contact_*` 아래에 있다. DATA3 1차
검토에서 일본어가 포함됐거나 글리프일 가능성이 있는 TIM 125개를 보수적으로
선정했다. 원본은 `workspace/graphics/text_candidates/source`, 편집 복사본은
`workspace/graphics/text_candidates/edited`, 기계 판독 목록은
`workspace/graphics/text_candidates/candidate_catalog.json`에 있다.

125개는 **확정 번역 분모가 아니다**. DATA3의 1차 후보 수이며, 각 TIM
안의 텍스트 블록 분리와 런타임 도달성 확인이 남아 있다.
통이미지·배경·초상은 텍스트 바깥의 보호 픽셀 마스크가 확정되기 전에는
편집 완료로 판정하지 않는다.

## DATA2 배경 글자 후보

DATA0/1/4/6의 연락시트에서는 UI 번역 대상으로 분류할 만한 텍스트를 찾지
못했다. 실행 파일의 유일한 TIM은 후속 런타임 덤프로 로딩 로고 후보임을
확인했다. 의상 위 한자와 문양은 캐릭터 원화의 일부로 보고
보호한다. DATA2에서는 간판·낙서·벽보 28개를 보수적으로 선정했다. 이 수는
같은 표지의 64/32/16픽셀 거리별 변형을 각각 포함한다.

- 선정 규칙:
  `workspace/graphics/catalog/environment_text_selection.json`
- 원본:
  `workspace/graphics/environment_text_candidates/source`
- 편집 복사본:
  `workspace/graphics/environment_text_candidates/edited`
- 기계 판독 목록:
  `workspace/graphics/environment_text_candidates/candidate_catalog.json`

대표적으로 `S09.DAT @ 0x1BFAC`의 `安全第一`, `S13.DAT @ 0x18B38`의
세로 간판, `S22.DAT @ 0x19DEC`의 벽면 한자, `S23.DAT @ 0x1DA1C`의
신문·벽보가 있다. 모두 배경과 글자가 합성된 보호 그래픽이며 런타임 위치와
텍스트 마스크를 확인하기 전에는 재삽입하지 않는다.

## DATA3 1차 분류

| 파일 | 역할 |
| --- | --- |
| `O00.DAT` | 글리프 표·숫자·전투 효과 문구 후보 |
| `O01.DAT` | 인명 스트립·별도 타이틀 로고·영문 UI |
| `O02.DAT` | 인물·무기·결투 장소 선택과 무기 정보 |
| `O04.DAT` | 옵션·시스템 메시지·선택 라벨 |
| `O12.DAT` | 일본판 타이틀·메인 메뉴 |
| `O13.DAT` | 대전·스테이지 설정 |
| `O14.DAT` | 인물 이력·명칭 |
| `O15.DAT` | 세로 엔딩 문구와 서명 |
| `O18.DAT` | 영문 크레디트, 번역보다 보호 검증 대상 |
| `O19.DAT` | 일본어가 포함된 640x240 통이미지 |

선정 규칙은
`workspace/graphics/catalog/data3_text_selection.json`에 보존한다.

## 남은 UI 편집 배치

DATA3 1차 후보에서 이미 완료된 타이틀·옵션 헤더·엔딩과 별도 진단 대상을
분리하고, 다음 수동 편집 대상으로 103개를 추출했다.

| 그룹 | 수량 |
| --- | ---: |
| 캐릭터·무기·결투 장소 선택 (`O02.DAT`) | 24 |
| 캐릭터 선택 세로 이름패 (`O02.DAT`) | 6 |
| 옵션·메모리 카드 시스템 문구 (`O04.DAT`) | 36 |
| 메인 메뉴 (`O12.DAT`) | 9 |
| 대전·스테이지 설정 (`O13.DAT`) | 4 |
| 인물 이력·명칭 (`O14.DAT`) | 24 |

편집 위치는 `workspace/graphics/ui_translation_batch/edited`, 원본 대조
위치는 같은 폴더의 `source`, 기계 판독 목록은 `edit_catalog.json`과
UTF-8 BOM `edit_catalog.csv`다. 파일별 연락시트도 `contact_sheets`에
생성했다.

`O04.DAT`에는 사용자가 직접 진입하는 저장·불러오기 메뉴와 별개로 자동
저장·불러오기, 메모리 카드 초기화와 오류 안내 이미지가 존재한다. 따라서
시스템 문구 번역 범위에는 유지한다.

초기 사용자 편집 완료 후 원본과 비교한 결과 기존 97개 중 96개가 변경됐다.
`O14.DAT @ 0x1B68`의 물음표 비공개 항목 1개만 원본과 동일하며, 통합
빌드에서 의도적으로 재작성하지 않고 보존했다. 수정본 연락시트는
`workspace/graphics/ui_translation_batch/edited_contact_sheets`에 있다.

2026-07-31 사용자 런타임 화면에서 캐릭터 초상화 위 세로 이름패 6개가
누락된 사실을 확인했다. 실제 자산은 `DATA3/O02.DAT @
0xE8EC/0xED64/0xF1DC/0xF654/0xFACC/0xFF44`의 연속된 40x54 4bpp
TIM이다. 이 6개를 `ui_translation_batch/source/character_names`와
`edited/character_names`에 추가했으며, 편집 전 상태다. 따라서 기존
97개 통합판은 전체 그래픽 완료판이 아니다.

## BBJ-LOAD-GFX-001

타이틀 PoC는 `DATA3/O12.DAT @ 0x1CC`를 한글로 바꿨지만 사용자가 관찰한
로딩 화면 우하단에는 일본어 로고가 남았다. 따라서 로딩 화면이 수정된
`O12` TIM을 그대로 공유한다는 가설은 기각한다.

DATA3 전수 목록에서 확인된 다른 전체 로고
`DATA3/O01.DAT @ 0x7CB18`은 CLUT 교체와 전체 픽셀 반전 PoC 모두
화면 변화가 없었다. 따라서 실제 로딩 소비 가설을 기각했다.

DuckStation 런타임 텍스처 덤프와 정적 TIM 목록을 대조해 다음 실제 후보를
연결했다.

| 항목 | 값 |
| --- | --- |
| 후보 | `SLPM_860.20 @ 0x7657C` |
| 형식 | 136x32, 4bpp TIM, 16색 CLUT |
| `SLPM_860.20` SHA-256 | `0164189A870BA87495A3C0010ADB85535AE5A798BBD0DD276B88BF24ECC3CEDA` |
| 런타임 덤프 PNG SHA-256 | `7D1A3B737FB01ED4A07B784E0D66D9621407C67E15A894FB3F37AF46147F12F9` |
| 현재 판정 | 런타임 덤프와 정적 자산 연결, 픽셀 반전 PoC 통과 |

편집 경로는
`workspace/graphics/loading/edited/SLPM_86020_0007657C.png`다.
`BBJ-POC-GFX-004`에서 이 TIM의 픽셀만 반전한 결과 실제 로딩 화면이 검은
배경과 흰 로고로 바뀌었다. 따라서 런타임 도달성을 확인했다.
