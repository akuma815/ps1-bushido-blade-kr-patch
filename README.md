# 무사도의 검 (Bushido Blade) — 플레이스테이션

플레이스테이션 **무사도의 검 (Bushido Blade)** 한글 번역 패치 프로젝트입니다. 검객들의 무기 대결을 다룬 대전 액션 게임입니다.

> **최종 배포본 · 2026-08-11**

## 다운로드 및 적용 방법

1. 아래 체크섬 또는 상세 안내와 일치하는 원본을 준비합니다.
2. [최종 배포 패치 다운로드](https://github.com/akuma815/ps1-bushido-blade-kr-patch/releases/tag/release-20260811)에서 ZIP을 받아 모두 압축 해제합니다.
3. 미국판 BIN은 `apply_usa.cmd`, 일본판 BIN은 `apply_jp.cmd`에 끌어다 놓습니다.
4. 생성된 `_KR.cue`를 에뮬레이터에서 엽니다.

## 주의사항

- 미국판 SCUS-941.80과 일본판 SLPM-860.20 패치는 서로 독립적입니다.
- 미국판은 컷신·엔딩 자막 중심, 일본판은 메뉴·UI 중심이며 번역 범위가 다릅니다.
- 원본 게임·BIOS 및 패치가 적용된 게임 이미지는 포함하지 않습니다.

## 체크섬

| 구분 | SHA-256 |
| --- | --- |
| 패치 파일 `BushidoBlade_SCUS94180_KR_20260811.xdelta` | `271909e08c9efcb980258afa058a46d0c67f10e0d81c184474f707b4969fa118` |
| 패치 파일 `BushidoBlade_SLPM86020_KR_20260811.xdelta` | `dda3068a39d878282edcbb0c6a4774cf643906a30124361badab2f35b29c5691` |
| 원본 USA | `c5cba1e3e8bc68f15e32492907ac37add7f8e8d3e10ddb36742a729ce1a970bf` |
| 패치 후 이미지 USA | `323c1a47604310b98a37f3711a16379bda7014272a325f28d4023078078141e5` |
| 원본 JP | `7d89b9254c5001ad7fc4f0c2fc3cda9600417d075292c34d3b60aa3b600dba96` |
| 패치 후 이미지 JP | `17f137839c0bcc27782a70599b7b1b1bebc8596a01391e2bf8ba8187e8b50ead` |
| 배포 압축 파일 | `34a3e2bba4f810dcb6b7ee56439f6fbf216f2cde9439b782acfdda9581c9e6a1` |

지원 원본의 세트·리비전 및 해시는 [상세 패치 안내](docs/PATCH_GUIDE.txt)를 확인하세요.

## 소스 및 작업 기록

[공개 소스 구성과 빌드 전제](docs/SOURCE_GUIDE.md) · [상세 패치 안내](docs/PATCH_GUIDE.txt)
