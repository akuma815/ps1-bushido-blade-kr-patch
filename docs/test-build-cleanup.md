# 테스트 빌드 정리 기록

2026-07-31에 `build` 아래의 기존 테스트 결과물을 모두 삭제했다.

삭제한 디렉터리:

- `full_graphics_ko`
- `loading_ending_ko`
- `loading_logo_ko`
- `loading_logo_right_guard_probe`
- `poc_loading_logo`
- `poc_loading_logo_actual`
- `poc_loading_logo_pixels`
- `poc_options`
- `poc_title`

삭제 후 `build`의 남은 항목은 0개다. 삭제 대상은 생성된 BIN/CUE와 보고서,
중간 결과물이며, 다음 항목은 보존했다.

- `original`의 일본판 원본
- `workspace/graphics`의 원본 및 수정 PNG
- `workspace/scripts`의 조사/빌드 스크립트
- `work/analysis`와 `work/screenshots`의 조사 증거
- `docs` 문서

필요한 빌드는 `docs/build.md`의 명령으로 다시 생성한다.
