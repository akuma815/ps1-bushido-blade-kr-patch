# 남은 UI 이미지 편집 배치

일본판 DATA3에서 추출한 다음 한글화 대상 103개를 파일별로 정리했다.

| 폴더 | 용도 | 수량 |
| --- | --- | ---: |
| `character_weapon_select` | 캐릭터·무기·결투 장소 선택 | 24 |
| `character_names` | 캐릭터 선택 화면 세로 이름패 | 6 |
| `options_system` | 옵션 및 메모리 카드 시스템 문구 | 36 |
| `main_menu` | 메인 메뉴 선택 항목 | 9 |
| `versus_stage_settings` | 대전·스테이지 설정 | 4 |
| `character_profile` | 인물 이력·명칭 | 24 |

## 편집 방법

1. `source`는 일본판 보존 원본이므로 수정하지 않는다.
2. 같은 상대 경로의 `edited` PNG만 수정한다.
3. 파일명과 캔버스 크기는 바꾸지 않는다.
4. 투명 영역과 원래 배경·장식은 가능한 한 보존한다.
5. 수정이 끝나면 `edit_catalog.csv`의 `korean_text`와 `status`를 작성한다.

`contact_sheets`는 전체 이미지를 빠르게 확인하기 위한 참고용이며 빌드
입력이 아니다. 실제 빌드 입력은 `edited` 아래 PNG다.

이미 완료된 `DATA3/O12.DAT @ 0x1CC` 타이틀과
`DATA3/O04.DAT @ 0x3CCC` 옵션 헤더는 이 배치에서 제외했다.

`options_system`에는 메뉴에서 직접 선택하는 저장·불러오기 항목이 아니라
자동 저장, 자동 불러오기, 메모리 카드 초기화 및 오류 안내 이미지가 포함돼
있다.
