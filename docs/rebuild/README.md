# 무사도의 검 최종판 재빌드

미국판 SCUS-941.80과 일본판 SLPM-860.20은 각각 별도의 원본과 빌드 경로를 사용합니다. 공개 소스에 필요한 분석 JSON, 번역 원장, 편집용 참조·수정 PNG와 Galmuri 폰트를 보완했습니다. 과거 `work`·`build`·`extracted` 폴더는 최종 재빌드 입력으로 사용하지 않습니다.

## 준비

- 이 저장소의 최신 `main`
- 지원 원본 BIN: 사용할 지역의 것
- 별도 자막 폰트 `솔뫼 김대건 Medium.ttf`
- Python 3.12, `requirements-rebuild.txt`에 고정한 Pillow 12.3.0과 Keystone 0.9.2

자막 폰트 파일은 저장소에 포함하지 않습니다. 기존 공용 `Font` 폴더에 보관한 파일을 그대로 사용할 수 있습니다. 필요한 폰트의 출처와 해시는 [외부 자료 안내](../../THIRD_PARTY_NOTICES.md) 및 [INPUTS.json](INPUTS.json)을 참고하세요. 원본 BIN·BIOS·출력 BIN은 Git에 올리지 않습니다.

## 실행

저장소 최상위에서 실행합니다. 출력 폴더는 아직 존재하지 않는 새 경로여야 합니다.

```powershell
python -m pip install -r requirements-rebuild.txt
python -X utf8 -B scripts/rebuild_final.py --region USA --source-bin "미국판 원본.bin" --subtitle-font "솔뫼 김대건 Medium.ttf" --output-dir build/usa_restored
python -X utf8 -B scripts/rebuild_final.py --region JP --source-bin "일본판 원본.bin" --subtitle-font "솔뫼 김대건 Medium.ttf" --output-dir build/jp_restored
```

두 명령 중 필요한 지역만 실행하면 됩니다. 경로는 실제 보관 경로를 사용하세요. 원본의 크기·해시, 자막 폰트 및 공개 입력 229개의 해시를 먼저 확인합니다. 출력 `final` 폴더의 BIN/CUE를 사용하며, 전체 결과는 `REBUILD_RESULT.json`에 기록합니다. 각 하위 빌더의 출력은 같은 출력 폴더의 로그에 기록됩니다.

| 지역 | 원본 BIN SHA-256 | 최종 BIN SHA-256 |
| --- | --- | --- |
| USA | `c5cba1e3e8bc68f15e32492907ac37add7f8e8d3e10ddb36742a729ce1a970bf` | `323c1a47604310b98a37f3711a16379bda7014272a325f28d4023078078141e5` |
| JP | `7d89b9254c5001ad7fc4f0c2fc3cda9600417d075292c34d3b60aa3b600dba96` | `17f137839c0bcc27782a70599b7b1b1bebc8596a01391e2bf8ba8187e8b50ead` |

## 빌드 순서와 편집

미국판은 공식 자막 구조의 한글 자막·이름판 → 배드엔딩 6장 → 타이틀·로딩 순서입니다. 일본판은 그래픽 112대상 → V00~V08 자막 재배치 → 갤러리 해금 순서입니다. 다른 버전의 완성 BIN을 입력으로 사용하지 않고 해당 지역의 불변 원본에서 시작합니다. 일본판 중간 BIN의 해시는 계산해 전달하지만 EXE 예상 해시·케이브·Expected Write 검사는 유지합니다.

이 진입점은 최종 배포본의 동결된 입력을 검사하는 재현 도구입니다. 새 문안·그래픽 작업에는 `workspace/text/source`의 원장과 `workspace/graphics`의 편집 PNG를 수정하고 해당 하위 빌더들을 새 출력 경로에서 사용하세요. 원문 참조 PNG는 그래픽 빌더의 원본 픽셀 대조에 필요하므로 수정하지 않습니다.

검증 기록은 [VERIFICATION_2026-10-16.json](VERIFICATION_2026-10-16.json)에 있습니다. 원본 일치, 재빌드 해시, 공개 릴리즈 패치 적용은 실제 수행한 범위로 기록합니다. 새 에뮬레이터 장면 검수·전체 플레이는 이번에 수행하지 않습니다. 과거 세이브·실행 덤프·중간 버전 전체를 복원하는 백업은 이 저장소의 범위가 아닙니다.

배포 ZIP을 새로 포장하는 `make_release_dist.py`에는 별도의 xdelta3 및 이전 배포 템플릿 경로가 필요합니다. 위 최종 BIN 재빌드에는 해당 배포 도구나 템플릿이 필요하지 않습니다.
