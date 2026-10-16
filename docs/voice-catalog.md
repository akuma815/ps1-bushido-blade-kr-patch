# 스토리 음성 추출 및 카탈로그

## 결과

일본판 원본의 `SE3`과 `SE4`에 있는 wrapped VAB를 분석해 다음 결과물을
생성했다. 원본 BIN은 변경하지 않았다.

| 항목 | 결과 |
| --- | ---: |
| 스토리 VAB 뱅크 | 54 |
| 프로그램/톤 기반 카탈로그 행 | 106 |
| 고유 ADPCM 파형 | 99 |
| `wav_note60` | 106 |
| `wav_spu44100` | 106 |
| `vag_note60` | 106 |

뱅크에 들어 있는 VAG 순서와 프로그램의 톤 순서가 항상 같지는 않다. 예를 들어
톤 1이 VAG 4를 참조할 수 있다. 따라서 카탈로그는 `(bank, program, tone,
vag)`를 모두 기록한다.

## 결과물 위치

- 편집용 SSOT: `workspace/text/source/voice_catalog.csv`
- 기술 보고서: `work/analysis/story_voice_extraction.json`
- 청취용 추출물: `extracted/audio/story_voice`
- 재생성 스크립트: `workspace/scripts/extract_story_vab_audio.py`

CSV는 UTF-8 BOM이며 다음 사용자 편집 필드를 포함한다.

- `japanese_transcript`
- `korean_translation`
- `status`
- `notes`

나머지 좌표·해시·음성 속성 필드는 추출기가 소유한다. 받아쓰기 작업 중에는
행을 재정렬하거나 `subtitle_id`, `bank_path`, 프로그램/톤/VAG 인덱스를
변경하지 않는다.

## 어느 WAV를 들어야 하는가

### `wav_note60`

우선 청취할 파일이다. Psy-Q의 일반적인 기준음 60으로 Key On한다고 가정하고,
VAB 톤의 `center_note`와 `fine_shift`로 재생률을 계산했다. 현재 파일들의 계산
결과는 15,107Hz 또는 16,005Hz이며, 음성 길이 중앙값은 약 1.49초다.

이 속도 계산은 아직 게임의 실제 Key On 인수로 검증되지 않았다. 사람이 듣기에
자연스러운지 확인하는 1차 작업용이다.

### `wav_spu44100`

피치 보정을 적용하지 않고 PS1 SPU 기준률 44,100Hz로 디코드한 대조군이다.
`wav_note60`의 목소리가 지나치게 느리거나 낮게 들리면 이 파일과 비교한다.
실제 게임 재생률은 DuckStation 디버거에서 Key On 직전의 음정 인수를 잡아
확정한다.

### `vag_note60`

표준 48바이트 `VAGp` 헤더를 붙인 파일이다. ADPCM 본문은 원본 VAB에서 그대로
복사했으며 헤더의 표본율만 `wav_note60`과 같은 추정값을 사용한다.

## 검증

- CSV BOM: `EF BB BF`
- 카탈로그 ID: 106개, 중복 0개
- 세 출력 종류: 각각 106개
- 모든 WAV: 모노, 16-bit PCM
- 모든 VAG: `VAGp` 헤더의 ADPCM 크기와 실제 본문 크기 일치
- 모든 106개 음성: SPU ADPCM 종료 플래그 확인
- 각 원본 VAB: 크기 테이블 합계와 샘플 본문 크기 일치

각 음성은 종료 플래그 뒤에 게임 원본의 16바이트 종료용 블록 하나를 더 가진다.
WAV 디코드는 첫 종료 플래그가 붙은 프레임까지 포함하고 그 뒤 블록은 재생하지
않는다. VAG에는 가역 보존을 위해 원본 ADPCM 전체를 넣었다.

## 재생성

프로젝트 루트에서 실행한다.

```powershell
python .\workspace\scripts\extract_story_vab_audio.py `
  '.\original\Bushido Blade (Japan).bin' `
  --inspector '.\workspace\scripts\inspect_psx_disc.py' `
  --output-dir '.\extracted\audio\story_voice' `
  --catalog '.\workspace\text\source\voice_catalog.csv' `
  --report '.\work\analysis\story_voice_extraction.json'
```

스크립트는 기존 출력 폴더가 비어 있지 않으면 중단한다. 재생성할 때는 현재
카탈로그의 받아쓰기와 번역 필드를 먼저 별도로 보존해야 한다.

## 다음 게이트

카탈로그의 첫 음성을 실제 게임 화면에서 재생한 뒤 다음 정보를 기록한다.

1. 실제 재생 순서와 `subtitle_id`
2. 화자와 상황
3. `wav_note60` 속도가 실제 게임과 같은지
4. 음성 시작/종료 시점

이 매핑을 한 건 확보하면 해당 VAB의 LBA 읽기부터 SPU 전송과 상위 Key On
호출까지 추적해 자막 PoC 훅 위치를 찾는다.

## 번호 기반 사용자 선별

런타임에서 효과음과 대사를 한 호출씩 구분하는 방식은 공용 사운드 ID 때문에
중단했다. 대신 `prepare_voice_review.py`로 106개 `wav_note60`을 다음 위치에
번호순으로 평탄화했다.

- 청취 폴더: `extracted/audio/story_voice/review_flat`
- 연속 재생 목록: `review_flat/playlist.m3u8`
- 번호와 원본 좌표 대응표: `review_flat/INDEX.md`

사용자는 파일명 앞의 세 자리 `review_no`만으로 대사 여부를 지정할 수 있다.
예: `대사: 003, 017, 018`. 번호는 `subtitle_id`와 일대일로 연결되므로 이후 화자,
일본어 받아쓰기와 한국어 번역을 원본 `voice_catalog.csv`에 안전하게 병합한다.

### 사용자 청취 판정

사용자가 번호순 WAV 106개를 검토해 **전부 대사**라고 확인했다. 따라서
`voice_catalog.csv`의 106행 전체가 자막 대상이다. 이 판정의 기계 판독용 정본은
`work/analysis/story_voice_classification.json`이다.

CSV의 `status`는 대사 여부가 아니라 받아쓰기 진행 상태이므로 현재
`untranscribed`를 유지한다. 런타임에서는 공용 사운드 ID로 대사를 선별하지 않고,
카탈로그의 `(bank_path, program_index, tone_index, vag_index)` 전체를 자막 키로
사용한다.

### 한국어 번역 완료 검수 (2026-08-01)

사용자가 `korean_translation` 106행을 모두 작성했다. UTF-8 BOM, 106행,
25개 열과 `subtitle_id` 고유성은 유지됐고 한국어 빈칸은 0개다. 일본어
`japanese_transcript`는 106행 모두 비어 있으므로 `status=untranscribed`는
변경하지 않았다. 검수 정본은 `work/analysis/voice_translation_audit.json`이다.

임시 자막 글꼴 Malgun Gothic Bold 20px, 외곽선 1px 기준 최대 폭은 527px이며,
560px 한 줄 제한을 넘는 번역은 없다. 따라서 현재 번역은 전부 한 줄 자막으로
렌더링할 수 있다.

### 파생 자막 자산 생성 (2026-08-01)

`workspace/scripts/build_subtitle_assets.py`로 106개 번역을 모두 고정 크기 4bpp
자막 레코드로 생성했다. 출력 정본은 `build/subtitle_assets`에 있다.

- `subtitle_pack.bin`: 1,087,488바이트, SHA-256
  `045B1B141E4FB3C8B858C5FA9E76C923A5DA612CE27EDA122AB42853CBD89C91`
- `subtitle_manifest.json`: 전체 VAB 키, 표시 프레임, 팩 오프셋
- `subtitle_mapping.csv`: 사람이 검토할 수 있는 축약 매핑
- `previews`: 106개 PNG
- `contact_sheets`: 화자별 6개 접촉 시트

팩은 2,048바이트 전역 헤더 뒤에 자막당 10,240바이트(5섹터) 고정 레코드를
둔다. 각 레코드는 640x28 4bpp 마스크와
`(bank LBA, program, tone, VAG, 표시 프레임)`을 포함한다. 자체 파서로 106개
레코드와 원본 카탈로그의 키를 전부 역검증했다. 현재 상태는
`asset_ready_trigger_hook_pending`이며 아직 원본 디스크의 `DUMMY.DAT`에는 넣지
않았다.
