# 미국판 자막 엔진 비교

## 결론

미국판은 일본판 음성 위에 단순한 문자열 패치를 한 것이 아니다. 장면 코드가
자막 이벤트 ID를 선택하고, 파일에 추가된 시간표와 문장을 읽어 각 장면의 올바른
그리기 순서에서 출력하는 전용 자막 엔진을 포함한다.

일본판 한글 자막의 주 경로는 기존 `Key On -> 사용자 정의 OT 오버레이`를 더
확장하는 방식보다 **미국판의 이벤트 선택·시간표·장면별 렌더 호출 구조를
일본판에 이식하고, 마지막 영문 글자 출력부만 한글 아틀라스 출력부로 교체하는
방식**이 적합하다.

## 원본 식별

| 구분 | 일본판 | 미국판 |
|---|---|---|
| BIN | `Bushido Blade (Japan).bin` | `Bushido Blade (USA).bin` |
| BIN 크기 | 510,021,792 | 582,258,768 |
| BIN SHA-256 | `7D89B9254C5001AD7FC4F0C2FC3CDA9600417D075292C34D3B60AA3B600DBA96` | `C5CBA1E3E8BC68F15E32492907AC37ADD7F8E8D3E10DDB36742A729CE1A970BF` |
| 실행 파일 | `SLPM_860.20` | `SCUS_941.80` |
| 실행 파일 크기 | 604,160 | 595,968 |
| 실행 파일 SHA-256 | `0164189A870BA87495A3C0010ADB85535AE5A798BBD0DD276B88BF24ECC3CEDA` | `3DB8EB15607E5F67A5EFBF52143E57515BBFBFC3976E1CA6EF9150E67094E264` |
| RAM 적재 주소 | `0x80010000` | `0x80010000` |

양쪽에 공통으로 존재하는 파일은 334개이고 그중 306개가 바이트 단위로 같다.
변경 공통 파일은 28개다. `SE0`~`SE5` 음원 파일은 변경 목록에 하나도 없으므로
미국판의 VAB 대사 음원은 일본판과 같다. 미국판은 동일한 일본어 음성을 유지하며
영문 자막 경로를 추가한 판본으로 사용할 수 있다.

미국판에만 있는 핵심 파일은 다음과 같다.

- `DATA3/O20.DAT`: 4,202바이트 자막 데이터 블록
- `SCUS_941.80`: 미국판 자막 엔진과 영문 글꼴 호출부
- `BIN1/SCEAP.TIM`: 미국판 추가 그래픽

`DATA3/O20.DAT` 전체는 `DATA3/O14.DAT`의 오프셋 `0xF314`에서 다시 발견되며,
O14에는 그 뒤 2바이트만 더 있다. O20은 O14에 포함된 자막 블록을 별도 장면에서
재사용하도록 복제한 파일이다.

## 공식 자막 저장 위치

화면에서 확인된 문장을 논리 파일에 대조했다.

| 문장 | 파일 | 파일 오프셋 |
|---|---|---:|
| `For the first time in my life,` | `DATA7/V00.DAT` | `0x35FC4` |
| `I've taken a life.` | `DATA7/V00.DAT` | `0x35FE3` |
| `I can't believe Meikyokan is ...` | `DATA3/O05.DAT` | `0x7F9B9` 부근 |
| `Kage...and Meikyokan have survived ...` | `DATA3/O14.DAT` | `0x101AA` 부근 |
| `Let's have a clean fight!` | `SCUS_941.80` | `0x1AC0` 부근 |
| `TATSUMI` | `SCUS_941.80` | `0x16EC` |

실제 자막 후보는 다음 파일군에 모여 있다.

- 스토리/오프닝 계열: `DATA3/O05.DAT`, `DATA3/O14.DAT`, `DATA3/O20.DAT`
- 전투 전후 계열: `DATA7/V00.DAT`~`DATA7/V08.DAT`
- 공통 전투 대사와 캐릭터 이름: `SCUS_941.80`

`work/analysis/usa_subtitle_comparison.json`은 이 파일들의 문장, 오프셋,
제어값을 기계 판독 형식으로 보존한다.

## DATA7 컨테이너 차이

`V00.DAT`는 미국판이 일본판 원본 컨테이너에 자막 섹션 하나를 추가한 형태다.

| 항목 | 일본판 V00 | 미국판 V00 |
|---|---:|---:|
| 파일 크기 | `0x35EE0` | `0x36060` |
| 최상위 섹션 수 | `0x13` | `0x14` |
| 마지막 원본 경계 | `0x35EE0` | 원본 데이터가 헤더 확장으로 `0x35EE4`까지 이동 |
| 새 자막 블록 | 없음 | `0x35EE4`~`0x3605F` |

미국판은 최상위 오프셋 표에 4바이트를 넣어 섹션 수를 19에서 20으로 늘리고,
기존 섹션 오프셋을 4씩 보정한 뒤 자막 블록을 마지막에 붙인다. 따라서 V 파일의
자막은 원본 그래픽/모델 바이트 안에 우연히 섞인 문자열이 아니라 로더가 인식하는
정식 추가 섹션이다.

## 자막 블록 형식

미국판 함수 `0x8003C920`은 새 섹션을 다음 구조로 재배치한다.

```text
u32 group_count
u32 group_first_entry[group_count]  # 엔트리 인덱스, 실행 중 포인터로 변환
u32 entry_count
u32 string_offset[entry_count]      # 문자열 기준 상대 오프셋, 포인터로 변환
char strings[]
```

문장 제어부는 5자리 ASCII다.

```text
SSSSL본문\0
```

- `SSSS`: 장면 시작 뒤 표시 시작 프레임
- `L`: 줄 수. `1`, `2` 등이 실제 줄 수이며 `0`이면 종료 시각 토큰이다.
- 본문 안의 NUL은 다음 줄을 나누는 데도 사용된다.

두 문장 사이에서는 이전 문장의 종료 토큰과 다음 문장의 시작 토큰이 붙어
`0180000841...`처럼 10자리로 보일 수 있다. 이는 `01800`과 `00841` 두 토큰이다.
번역 시 숫자열을 본문의 일부로 취급하거나 다시 조판하면 안 된다.

## 미국판 실행 파일의 자막 엔진

| 역할 | 미국판 RAM 주소 |
|---|---:|
| 이벤트 ID 선택 | `0x8003C4D8` |
| 그룹/엔트리 선택 | `0x8003C578` |
| 프레임 갱신 및 자막 표시 | `0x8003C5A8` |
| 한 줄 조판 래퍼 | `0x8003C868` |
| 자막 섹션 포인터 재배치 | `0x8003C920` |
| 기존 ASCII 텍스트 렌더러 | `0x80036EF4` |

상태는 미국판의 다음 `gp` 상대 위치에 있다.

- 현재 자막 프레임: `gp+0x420`
- 현재 자막 디스크립터: `gp+0x424`
- 이벤트 초기화 플래그: `gp+0x428`

확인된 외부 호출 수는 다음과 같다.

- 이벤트 선택기: 4개 호출점
- 장면별 자막 갱신/표시: 4개 호출점
- 새 파일 섹션 재배치: 4개 호출점
- ASCII 텍스트 렌더러: 19개 호출점

`0x8003C5A8`은 문장의 시작 프레임과 줄 수를 읽고, 한 줄이면 화면 중앙
`y=200`, 두 줄이면 `y=192/208` 부근에 배치한다. 중요한 점은 이 함수가
단일 글로벌 GPU 훅이 아니라 네 장면 경로에서 각 장면의 정상 렌더 순서에 맞춰
호출된다는 것이다. 사용자 화면에서 자막 시점과 전경 우선순위가 정상인 이유다.

일본판의 대응 장면 초기화 코드에는 미국판의 이벤트 선택 호출이 없다. 예를 들어
미국판 `0x800390B4`는 `jal 0x8003C4D8`로 이벤트를 선택하지만, 정규화한 주변
명령이 대응하는 일본판 `0x8003AB74` 부근은 해당 호출 없이 다음 상태로 진행한다.
따라서 일본판에서 자막이 나오지 않는 것은 문장 데이터만 빠진 것이 아니라
이벤트 선택·갱신·표시 호출 전체가 빠졌기 때문이다.

## 캐릭터 선택 이름

캐릭터 이름은 음성 자막 표와 별도다. 미국판 포인터 표 `0x80080F18`에는 다음
명령 문자열 여섯 개가 있다.

```text
#X#Y#FRED SHADOW
#X#Y#FTATSUMI
#X#Y#FMIKADO
#X#Y#FBLACK LOTUS
#X#Y#FUTSUSEMI
#X#Y#FKANNUKI
```

캐릭터 선택 코드 `0x8003294C`와 `0x80032BB4`가 ASCII 렌더러
`0x80036EF4`를 호출한다. 따라서 캐릭터 이름은 공식 자막 이벤트 엔진을 거치지
않고 선택 화면 UI 호출부를 따로 이식해야 한다.

## 일본판 이식 전략

### 권장안: 미국판 제어 엔진 + 한글 출력기

1. 미국판 `0x8003C4D8`~`0x8003C9B4`의 역할을 일본판 코드 케이브에 재구현한다.
2. 미국판 네 종류의 호출점을 일본판 대응 장면 함수에 매핑한다.
   - 자막 섹션 등록
   - 이벤트 선택
   - 프레임 갱신/표시
   - 장면 종료/교체
3. `V00` 하나를 미국판과 같은 20섹션 컨테이너로 만들어 한 문장 표만 넣는다.
4. 미국판의 ASCII 출력기 자체는 이식하지 않는다. `0x8003C868`의 마지막 출력만
   기존 `subtitle_pack.bin`의 한글 아틀라스 출력기로 바꾼다.
5. 공식 장면 호출 위치에서 프리미티브를 제출해 캐릭터에 가려지거나 첫 조작 때까지
   지연되는 기존 PoC 문제를 제거한다.

이 방식은 미국판이 이미 해결한 세 가지를 그대로 얻는다.

- 대사 종류별 정확한 이벤트 선택
- 장면 시작 기준 표시/종료 프레임
- 오프닝·전투·승리 장면별 정상 그리기 순서

### 대안: 미국판을 패치 기반으로 사용

미국판을 한국어판의 실행 기반으로 삼으면 자막 엔진 이식 비용은 가장 낮다.
특히 `SE0`~`SE5`가 일본판과 동일하므로 일본어 VAB 음성은 그대로 유지된다.
다만 디스크 ID, STR/XA 경로, 일본판에서 이미 수정한 타이틀·엔딩·로딩 그래픽을
미국판 파일 구조에 다시 적용해야 한다. 현재 목표가 일본판 `SLPM_860.20` 유지이므로
이 경로는 이식 PoC가 막힐 때의 대안으로 둔다.

## 첫 PoC 통과 조건

첫 대상은 이미 번역과 플레이 위치가 확인된 `DATA7/V00.DAT`의 다음 문장으로 한다.

```text
For the first time in my life,
I've taken a life.
```

통과 조건은 다음과 같다.

1. 해당 음성 장면에서만 한국어 한 줄 또는 두 줄이 나온다.
2. 타츠미 첫 조작까지 기다리지 않고 음성과 함께 나온다.
3. 캐릭터 스프라이트가 자막을 가리지 않는다.
4. 다음 대사와 장면 전환에서 즉시 교체 또는 제거된다.
5. 로딩, 전투 진입, 음성 재생에 변화가 없다.
6. 클린 부팅과 재진입에서 같은 결과가 나온다.

이 PoC가 통과한 뒤 `DATA7/V00`~`V08`, `O05`, `O14/O20`, 실행 파일 고정 대사를
순서대로 옮긴다. 캐릭터 선택 이름은 별도 UI PoC로 처리한다.

## V00 장면 호출 PoC

미국판 `V00.DAT` 자막 블록의 그룹 시작 엔트리는
`[0, 2, 4, 6, 8, 11, 13]`이다. `For the first time in my life,`가 속한
엔트리 쌍은 그룹 `3`에서 시작한다. 미국판은 V00 장면 초기화 중
`0x800390B4`에서 이 그룹 번호를 `0x8003C4D8`에 전달한다.

정규화한 동일 함수에서 일본판 대응 위치는 `0x8003AB74`다. 일본판에는 자막 호출이
없고 `j 0x8003AB98; nop`으로 바로 진행한다. v1은 이 두 명령을 코드 케이브 래퍼로
바꾸고, 장면 그룹 값이 `3`일 때만 `처음으로…` 표시 타이머를 켠 뒤 원래 목적지로
복귀한다. 따라서 SPU Key On, 음색 번호, 첫 조작 상태에는 의존하지 않는다.

- 빌더: `workspace/scripts/build_subtitle_poc.py --event-hook v00-scene`
- 이벤트 훅: `0x8003AB74` -> 코드 케이브 -> `0x8003AB98`
- 대상 미국판 자막 그룹: `3`
- 화면 제출: 기존 검증용 `DrawOTag` 래퍼
- 정적 상태: 원본/실행 파일 해시, expected-write, Mode2 Form 1 EDC/ECC,
  디스크 크기, 실행 파일 크기, 코드 케이브 범위, 재추출 일치 통과
- v1 런타임 결과: 대상 대사에서 미출력. 미국판의 프레임 갱신 단계를 생략한
  즉시 타이머 방식은 공식 구조의 이식으로 인정할 수 없어 폐기했다.

v2는 미국판 `0x8003989C`의 자막 갱신 호출에 대응하는 일본판
`0x8003B348`을 추가로 연결한다. 기존 일본판 호출 `jal 0x8003BAB0`은 래퍼 안에서
그대로 재생하며, V00 그룹 3이 선택된 뒤 장면 프레임을 센다. 공식 제어값
`00602`에 맞춰 60프레임째 `처음으로…` 타이머를 켠다.

- 결과: `build/subtitle_usa_scene_poc_v2/`
- 장면 갱신 훅: `0x8003B348` -> 코드 케이브 -> 원래 `0x8003BAB0`
- 진단 상태: 이벤트 호출 관측, 그룹 일치/대기, 장면 프레임을 별도 RAM 값으로 보존
- 정적 상태: 모든 빌드 게이트 통과
- 런타임 상태: 클린 부팅 DuckStation 확인 대기

v2 런타임에서는 대상 대사까지 진행해도 출력되지 않았고, 사후 상태는
`event_seen=1`, `group_armed=0`, `scene_frame=0`이었다. 지속 GDB 브레이크포인트
추적은 사용 중인 DuckStation에서 BIOS/타이틀 실행을 정지시켜 폐기했다.

v3는 플레이 중 디버거를 연결하지 않는다. 이벤트 훅 자체가 전달값 0~7을
`event_seen` 바이트의 비트마스크로 누적한다. 정상 플레이가 끝난 뒤 GDB로 한 번만
RAM을 읽고 즉시 연결을 끊어 실제 이벤트 번호를 판정한다.

- 결과: `build/subtitle_usa_scene_poc_v3/`
- BIN SHA-256: `138AFBAB6742524DD0C99012795E0723F7A46F31CD62BF8F6A9A0E06E8ED28A7`
- 플레이 중 GDB 연결: 없음
- 런타임 결과: 대상 구간 완료 뒤 마스크 `0x45`(`0, 2, 6`)가 기록됐으나
  그룹 3은 선택되지 않아 미출력

v3 결과로 일본판 지역 삭제 구간에서 자막 함수뿐 아니라 인수 설정도 함께 사라졌다는
점을 확인했다. 미국판 `jal 0x8003C4D8`의 지연 슬롯은 `move a0,s1`이지만 일본판
대응 위치 `0x8003AB74`는 `j 0x8003AB98; nop`이다. 정규화한 일본판 함수에서 같은
이벤트 값은 `s2`에 있으므로, v3이 기록한 값은 이벤트 ID가 아니라 남아 있던 `a0`다.

v4는 훅 점프의 지연 슬롯을 `move a0,s2`로 바꿔 미국판의 호출 계약을 복원한다.

- 결과: `build/subtitle_usa_scene_poc_v4/`
- 이벤트 훅 최종 바이트: `j 0x800118B4; move a0,s2`
- BIN SHA-256: `129776A133BD5B9603FA3684C22D4AC8CADB5C97DE5571C87C2CF9379907E291`
- 정적 상태: 모든 빌드 게이트 통과
- 런타임 상태: 확인 대기

이 PoC는 공식 이벤트 선택 및 갱신 위치가 일본판에서도 올바른 타이밍을 제공하는지
확인하는 중간 게이트다. 타이밍이 통과하면 다음 빌드에서 미국판 장면별 그리기
레이어까지 이식해 캐릭터보다 앞쪽 레이어와 프레임 기반 교체/종료를 구현한다.

### v4 강제 표시 및 깜빡임 진단

v4 실행 중 `0x800A2E90`의 표시 타이머를 강제로 설정하자 `처음으로…`가 실제로
출력됐다. 따라서 `DrawOTag 0x80067B54` 훅과 TILE 패킷 자체는 유효하다. 다만
문구가 계속 반짝였으며, 기존 래퍼에는 같은 프레임의 여러 `DrawOTag` 호출을
구분하는 장치가 없었다. 고정된 더블 버퍼 패킷 세트를 같은 OT에 반복 삽입하면
GPU 연결 태그가 재연결되거나 자기 참조가 생길 수 있으므로 이 중복 삽입을
깜빡임의 우선 원인으로 판정했다.

v6은 다음 항목을 결합한다.

- 미국판 V00 이벤트 그룹 `3` 감지
- 일본판 레지스터 대응 `move a0,s2`
- 장면 갱신 래퍼의 R3000A load-delay NOP
- `DrawOTag` 호출 시 프레임 패리티를 `0x800A2E94`에 기록
- 동일 패리티에서는 패킷 삽입과 타이머 감소를 생략해 프레임당 한 번만 출력

결과물: `build/subtitle_usa_scene_poc_v6/`

BIN SHA-256:
`AA49878B0E45614EE07385087245E1343358B6446C50858103CC81F2F931BF67`

정적 검증은 원본 해시, expected-write, Mode2 Form 1 EDC/ECC, 디스크 크기,
실행 파일 크기, 코드 케이브 범위 및 실행 파일 왕복 추출을 모두 통과했다.
실기동에서 깜빡임 해소 여부와 공식 이벤트 타이밍은 별도로 확인한다.

### v7 전경 레이어 대응

v6 강제 표시 사진에서는 자막이 정상 생성됐지만, 같은 프레임의 후속 OT가 그리는
하단 전경 그래픽 뒤로 일부 또는 전부 가려졌다. 이는 자막 패킷의 불안정이 아니라
여러 `DrawOTag` 호출 사이의 제출 순서 문제다.

v7은 게임 OT에 패킷을 삽입하지 않는다. 원래 `DrawOTag` 본체
`0x80067B5C`를 호출해 현재 게임 OT를 먼저 제출한 뒤, 검은 배경과 흰 글자를
연결한 독립 자막 OT를 즉시 추가 제출한다. 이 동작을 타이머가 켜진 동안 모든
게임 OT 제출 뒤에 반복하므로 마지막 전경/UI OT 뒤에도 자막이 다시 그려진다.
표시 수명은 프레임 패리티가 바뀔 때만 감소시켜 여러 OT 호출 때문에 짧아지지
않도록 유지한다.

- 결과물: `build/subtitle_usa_scene_poc_v7/`
- BIN SHA-256:
  `E5066804FD835AC9A2AF5D8461DF7C9C51EA033D2257C2D3C29F4B017424CA5B`
- 강제 표시 진단: 6초 동안 타이머 `3600 -> 3418`, 약 30회/초로 정상 감소
- 프로세스 상태: 강제 표시 후 정상 응답
- 시각 검증: 별도 OT의 연속 DMA 제출에서는 자막이 나오지 않아 이 방식은 폐기

### v8 교대 OT 안전 영역 및 load-delay 수정

런타임 추적 결과 DrawOTag는 한 프레임에 여러 번 호출되는 것이 아니라 두 OT
주소 `0x80135C80`, `0x80131BFC`가 프레임마다 교대로 한 번씩 제출됐다. v6 사진의
교대 잘림은 후속 전경 OT가 아니라 자막 원점 `y=202`가 한쪽 버퍼의 하단 그리기
영역을 넘은 현상으로 재분류했다. OT 연결 탐색의 `lw` 직후에도 R3000A
load-delay NOP가 빠져 있어 함께 수정했다.

v8 변경점:

- 실제 표시됐던 게임 OT 말단 패킷 삽입 방식으로 복귀
- 자막 원점 `y=202 -> 174`
- 타이머, 프레임 패리티, OT 연결 태그 로드 뒤 load-delay NOP 보장
- 미국판 V00 그룹 3 및 장면 갱신 타이밍 로직 유지

결과물: `build/subtitle_usa_scene_poc_v8/`

BIN SHA-256:
`999D8E5FDA3E2CC3AAA1B051EF5CAB25D4B10065BEE7F13C63849495097326E4`

강제 타이머 런타임 결과는 전투 시작, 오프닝 컷, 전투 종료 장면 모두에서 검은
자막 상자와 `처음으로…` 문구가 잘리지 않고 캐릭터 및 배경보다 앞에 표시됐다.
따라서 렌더 경로, 레이어 우선순위 및 안전 Y좌표 PoC는 통과했다. 이 검증은
강제 `3600`프레임 표시이므로 공식 이벤트의 자동 시작/종료 타이밍 검증과는
구분한다.

### v8 자동 타이밍 결과와 v9 장면 ID 수정

강제 타이머 없이 v8을 실행하자 `처음으로…`가 스토리 오프닝부터 출력됐다.
따라서 미국판 자막 데이터 내부의 그룹 번호 `3`과 `0x8003C4D8`에 전달되는
장면 선택 ID를 같은 번호로 취급한 기존 가정은 기각한다. 미국판 선택 함수는
입력 ID 범위에 따라 런타임 포인터 표 0, 1, 2 또는 정적 표를 고른 뒤 장면별
자막 묶음을 선택한다. 입력값 자체가 묶음 내부 그룹 번호는 아니다.

일본판 런타임에서 관측한 선택 순서 `2, 3, 6` 중 ID 3은 오프닝 장면으로
확정됐다. v9은 다음 전투/승리 장면 ID 6을 대상으로 바꾼다. 또한 상태 바이트를
`0=비활성`, `1=장면 선택 후 대기`, `2=표시 활성`으로 구분한다. 렌더러는 상태
2와 0이 아닌 타이머가 동시에 성립할 때만 패킷을 삽입하므로 초기 RAM의 타이머
값이나 다른 장면 선택만으로는 자막이 나오지 않는다.

- 결과물: `build/subtitle_usa_scene_poc_v9/`
- BIN SHA-256:
  `328B73AF80E98FBB368D68A42192F098EE18852CDFD45BE2851A2C9A92B29689`
- 클린 타이틀 상태: 12바이트 자막 상태가 모두 0
- 현재 검증 기준: 오프닝에서는 미출력, ID 6 장면 진입 60프레임 뒤 출력

### 순서 로거 결과와 v10 실제 V00 그룹 호출 이식

`0x8003AB74`에 설치한 순서 로거를 타츠미 스토리 시작부터 첫 전투 종료 뒤까지
실행한 결과, 이 위치는 전체 구간에서 단 한 번만 호출됐다.

```text
count=1
sequence=0
a0=3, a1=0x8000, a2=2, a3=0xFFFFFFFF
s0=0x801CB784, s1=0, s2=3
```

따라서 이 위치는 개별 대사의 자막 그룹 선택기가 아니라 스토리 전체 초기화
호출이다. v9의 이벤트 비트마스크도 `lbu` 직후 값을 사용한 R3000A load-delay
오류로 오염됐으므로, 여기서 추정한 ID 6은 타이밍 근거로 사용할 수 없다.
원시 로그는 `work/analysis/v00_event_context_log_run1.json`에 보존했다.

미국판과 일본판의 정규화된 함수 본문을 다시 비교한 결과, V00 그룹 3을 넘기는
실제 미국판 호출은 `0x800386B8: jal 0x8003C578`이며 delay slot에서 `s2`를
인자로 사용한다. 일본판 대응 함수는 호출을 삭제하고 곧바로
`0x8003A1A0`의 에필로그로 진입한다. 같은 그룹을 프레임마다 갱신하는 미국판
호출 `0x80038DA4: jal 0x8003C5A8`도 일본판에서는 삭제됐으며, 대응 위치는
`0x8003A8A8`이다.

v10은 삭제된 두 호출 위치를 훅으로 사용한다.

- 그룹 선택 훅: `0x8003A1A0`, live `s2 == 3`일 때만 대기 상태 설정
- 그룹 갱신 훅: `0x8003A8A8`, 60프레임 뒤 표시 활성화
- 렌더 훅: 검증된 `0x80067B54` DrawOTag 말단 삽입
- 화면 원점: `(278,174)`
- 결과물: `build/subtitle_usa_group_poc_v10/`
- BIN SHA-256:
  `E12C3566AA3ED2A94101AD3AE25EB296E919169A85C87DD7EDDF4A38BCF967E0`
- 정적 검증: 원본 해시, 두 Expected Write, Mode2/Form1 무결성, 디스크 크기,
  EXE 왕복 및 산출물 해시 일치
- 런타임 통과 기준: 오프닝에서는 미출력, 첫 전투 후 해당 대사에서만 출력

2026-08-01 클린 재기동 후 목표 전투 장면에서 `처음으로…` 출력에 성공했다.
검은 배경과 흰 글자는 캐릭터 및 배경보다 앞쪽에 정상 표시됐다. 이 결과로
실제 V00 그룹 선택 훅, 60프레임 갱신 훅, DrawOTag 말단 렌더 경로의 연결은
통과했다. 표시 종료 시점, 재진입, 후속 대사 교체 및 오프닝 미출력은 아직
전체 타이밍 검증 항목으로 남긴다.

### v11 표시 수명 수정

v10 런타임에서 목표 장면 출력은 성공했지만 자막이 사라지지 않고 다음 전투까지
유지됐다. 렌더 래퍼는 `FRAME_PARITY`가 이전 값과 달라질 때만 표시 타이머를
감소시켰으나, 해당 전투 후 경로에서는 이 값이 고정돼 타이머가 첫 1회 이후
감소하지 않았다.

기존 런타임 추적에서 공통 `DrawOTag` 훅은 완성된 화면 프레임마다 한 번 호출되는
것이 확인됐으므로, v11은 parity-change guard를 제거한다. 활성 자막은 DrawOTag
호출마다 타이머를 1씩 감소시키며, 0이 되면 표시 phase도 0으로 해제한다. 화면
패킷의 이중 버퍼 선택에는 기존 parity 값을 그대로 사용한다.

- 결과물: `build/subtitle_usa_group_poc_v11/`
- 표시 시간: `180`프레임
- BIN SHA-256:
  `53C94AB719FBA7A059B37B757C43D8AC2377029DCB7449A96F1C5453425E889A`
- 정적 검증: 전체 통과
- 런타임 통과 기준: 목표 장면에서 출력된 뒤 약 180프레임 후 사라지고 다음
  전투까지 남지 않음

### 미국판 공식 시작·종료 로직 확정

고정 표시 시간을 장면마다 수작업으로 맞추는 방식은 사용하지 않는다. 미국판
`0x8003C5A8`의 실제 갱신 함수를 역어셈블한 결과, 자막 데이터의 5자리 ASCII
토큰이 타임라인 자체다.

```text
FFFFL
```

- `FFFF`: 장면 그룹 선택 뒤 경과 프레임
- `L`: 표시할 줄 수
- `L > 0`: 해당 프레임부터 뒤따르는 NUL 구분 문자열을 표시
- `L == 0`: 해당 프레임부터 표시하지 않는 종료 토큰

미국판 엔진은 그룹 선택 함수 `0x8003C578`에서 `gp+0x420`의 프레임을 0으로
초기화하고 `gp+0x424`에 그룹 디스크립터를 저장한다. 갱신 함수
`0x8003C5A8`은 현재 토큰의 프레임과 현재 프레임을 비교해 표시하고, 호출마다
프레임을 1 증가시킨다. 다음 토큰의 프레임에 도달하면 디스크립터를 다음
포인터로 진행시킨다. 다음 토큰의 줄 수가 0이면 자연스럽게 렌더가 중단된다.
종료 토큰이 없는 그룹은 다음 장면 또는 그룹 선택 시 프레임과 디스크립터가
교체되면서 종료된다.

타츠미의 첫 살인 대사는 원본 `DATA7/V00.DAT`에서 다음 타임라인을 사용한다.

```text
00602 For the first time in my life,\0I've taken a life.\0
01800
```

따라서 공식 시작은 60프레임, 공식 종료는 180프레임이며 실제 표시 길이는
120프레임이다. 앞으로는 미국판 토큰을 전 대사의 기준 타임라인으로 추출하고,
한국어 렌더 자산만 같은 디스크립터에 연결한다. 음성 VAG의 샘플 길이는 미국판
타임라인이 없는 일본판 고유 대사 또는 누락 항목에만 보조 종료 기준으로 사용한다.

`workspace/scripts/analyze_usa_subtitles.py`는 각 레코드에 `end_frame`,
`duration_frames`, `end_mode`를 함께 기록한다.

### v13 미국판 타임라인 구동 PoC

v13은 고정 표시 타이머를 제거하고 `v00-timeline` 모드를 추가한다. 빌더가
`work/analysis/usa_subtitle_comparison.json`에서 파일 경로와 미국판 문장으로
레코드를 정확히 하나 선택하며, 해당 레코드의 `start_frame`과 `end_frame`을
런타임 래퍼에 주입한다.

동작 순서는 미국판 `0x8003C5A8`과 동일하다.

1. V00 그룹 3 선택 시 `scene_frame=0`, phase=대기
2. 갱신 호출에서 현재 scene frame을 시작·종료 토큰과 먼저 비교
3. `60 <= scene_frame < 180`이면 phase=표시
4. `scene_frame >= 180`이면 phase=비활성
5. 상태 판정 뒤 scene frame을 1 증가
6. DrawOTag 렌더러는 phase=표시일 때만 패킷을 연결하며 타이머를 변경하지 않음
7. 다른 그룹이 선택되면 phase와 scene frame을 즉시 초기화

- 결과물: `build/subtitle_usa_timeline_poc_v13/`
- 대상 레코드: `DATA7/V00.DAT / For the first time in my life,`
- 공식 표시 범위: frame `60`~`179`
- 공식 종료: frame `180`, zero-line token
- BIN SHA-256:
  `894CA8E73350956E0FDE3047133137F0477D9A8E1D5AA3D6647F89366A3B7EC4`
- 정적 검증: 원본 해시, Expected Write 3개, wrapper 크기, Mode2/Form1,
  디스크 크기, EXE 왕복, 산출물 해시 모두 통과
- 런타임 상태: 검증 대기

v13 런타임에서는 다음 전투에서도 자막이 남았고, 그 시점의 상태는
`scene_frame=180`, `phase=2`였다. 일본판 대응 갱신 훅은 프레임 180으로
증가한 호출을 마지막으로 장면에서 빠져나가므로, 다음 호출에서 `frame>=180`을
판정하는 순서는 종료 토큰을 처리할 기회를 잃는다.

미국판은 갱신 함수 내부에서 현재 자막을 그린 뒤 프레임을 증가시키고 다음
디스크립터로 진행하지만, PoC의 GPU 패킷은 갱신 함수 이후 공통 DrawOTag에서
제출된다. 따라서 동일한 화면 표시 구간을 만들려면 PoC 갱신 래퍼는 프레임을
먼저 증가시킨 뒤 새 값에 대해 `[start,end)`를 판정해야 한다. 새 값이 60이면
표시하고, 새 값이 180이면 같은 호출에서 phase를 0으로 해제한다.

v14는 이 순서를 적용한다. 정적 역어셈블에서 `scene_frame` 증가·저장이
시작/종료 비교보다 앞에 있으며, 새 frame 180에서 `sb zero,phase`가 같은 호출
안에 실행되는 것을 확인했다.

- 결과물: `build/subtitle_usa_timeline_poc_v14/`
- scene update wrapper: `0x80011934`, 108바이트
- BIN SHA-256:
  `23FBF57434253666A42E78EFE89909B68E08E3D3EEBF5B4CF12D88022FCB6DB9`
- 정적 검증: 전체 통과
- 런타임 판정: 목표 장면 뒤 RAM이 `scene_frame=180, phase=0`이어야 함

v14 런타임에서 다음 전투까지 화면상 자막이 남았지만, 같은 시점 RAM은 실제로
`scene_frame=180`, `phase=0`이었다. 따라서 공식 타임라인과 상태 해제는
통과했으며, 잔류 원인은 GPU OT 연결이었다. 기존 렌더 래퍼는 게임 OT의 원래
종료 태그를 자막 패킷 체인으로 바꾸지만 phase가 비활성화된 뒤에는 렌더 경로를
즉시 건너뛰어 그 연결을 `0xFFFFFF`로 복원하지 않았다. 교대 OT가 재사용될 때
과거 자막 체인도 계속 제출된 것이다.

v15 렌더 래퍼는 phase 판정보다 먼저 현재 OT를 순회한다. 현재 링크가 두 자막
버퍼의 체인 헤드 `0x00122310` 또는 `0x00122630`이면, 태그의 상위 바이트를
보존하고 하위 24비트를 `0xFFFFFF`로 복원한다. 그 뒤 phase=2일 때만 새 자막
체인을 다시 연결한다. 따라서 활성 중에는 매 프레임 안전하게 재연결되고,
비활성화된 첫 프레임에는 기존 연결이 제거된다.

- 결과물: `build/subtitle_usa_timeline_poc_v15/`
- render wrapper: `0x800119B4`, 484바이트
- BIN SHA-256:
  `769086EB2CD64AC6123AE684F5C0B6B4CEBC39354A61F7A5670B9D81ED378C19`
- 정적 검증 및 역어셈블 검증: 전체 통과
- 런타임 판정: frame 180 이후 자막 체인이 분리되고 다음 전투에 남지 않음

2026-08-01 클린 실행에서 사용자가 목표 장면의 자막 출력 후 정상 소멸과 다음
전투 미잔류를 확인했다. 따라서 V00 그룹 3 선택, 미국판 frame 60 시작 토큰,
frame 180 zero-line 종료 토큰, phase 해제, 교대 OT 자막 체인 분리까지 포함한
데이터 구동형 단일 자막 E2E PoC를 최종 통과로 판정한다.

## 재현 도구와 산출물

- 분석기: `workspace/scripts/analyze_usa_subtitles.py`
- 기계 판독 보고서: `work/analysis/usa_subtitle_comparison.json`
- 미국판 추출본: `work/extracted/usa_compare/`
- 일본판 비교 추출본: `work/extracted/japan_compare/`

재실행:

```powershell
python workspace/scripts/analyze_usa_subtitles.py
```

## Official timeline catalog

The single-subtitle gate is now converted into reproducible multi-record input.

- Analyzer: `workspace/scripts/analyze_usa_subtitles.py`
- Confirmed mapping input: `workspace/config/usa_subtitle_voice_mapping.json`
- Catalog builder: `workspace/scripts/build_usa_subtitle_timeline_catalog.py`
- Editable UTF-8 BOM CSV: `workspace/text/source/usa_subtitle_timeline.csv`
- Validation report: `work/analysis/usa_subtitle_timeline_catalog.json`

Regenerate it with:

```powershell
python workspace\scripts\analyze_usa_subtitles.py
python workspace\scripts\build_usa_subtitle_timeline_catalog.py
```

The analyzer now reads every NUL-terminated string declared by `line_count`.
This preserves official two-line subtitles instead of retaining only the first
line.  The inventory contains 259 records.  The 73 records in `DATA3/O20.DAT`
are marked as duplicates of the copy embedded in `DATA3/O14.DAT`, leaving 186
canonical records.  The story-battle files `DATA7/V00.DAT` through `V08.DAT`
contain 77 records.

`usa/data7/v00/003` is the first and currently only `runtime_confirmed` mapping.
It combines these two Japanese voice records into the official USA two-line
timeline at group 3, frames 60 through 179:

- `voice/se4/tat3win/02/p00/t00/v01` -> `처음으로…`
- `voice/se4/tat3win/02/p00/t01/v02` -> `사람을 베었어…`

USA subtitle records and Japanese voice records are not consistently 1:1, so
the remaining rows intentionally stay `unmapped`.  Only audio- or
runtime-confirmed entries should be added to the mapping JSON.  Rebuilding then
fills `voice_subtitle_ids`, `korean_translation`, `usa_group`, and
`mapping_status` in the CSV.

The next runtime milestone is a two-group dispatcher driven by this catalog.
Each runtime entry uses `(source_path, usa_group, start_frame, end_frame,
Korean asset)`.  A `zero_line_token` entry clears at `end_frame`; a
`scene_or_group_change` entry clears at the next group selection.  The complete
106-entry bitmap pack does not fit the EXE code cave, so the safe order is a
two-group transition PoC followed by a disc asset loader.

## v16 two-group dispatcher

`build/subtitle_usa_multi_poc_v16/` is the first two-group build.  It keeps the
v15 group-selection, update, and DrawOTag-terminal hooks while selecting timing
and render assets by the live USA group ID.

- group 3: `For the first time in my life,` -> `처음으로...`
  - start 60, end 180 (`zero_line_token`)
  - mapping status: `runtime_confirmed` from v15
- group 4: `Mikado...` -> `미카도 씨를...`
  - start 30, clear on scene/group change
  - mapping status: `candidate`; do not promote until runtime timing is observed
- event wrapper: 120 bytes
- timeline update wrapper: 184 bytes
- render wrapper: 620 bytes at `0x800119F4`
- code cave usage: 1,460 / 2,076 bytes
- output SHA-256:
  `8B2C0CE59212A470664280D81F992F35FE6383057B40A71572EF9AB8D766A519`

Static checks passed for baseline hashes, all three Expected Writes,
Mode2/Form1 EDC/ECC, unchanged disc and executable sizes, code-cave bounds, and
disc executable roundtrip.  Runtime remains pending.  Required observations:

1. group 3 still appears at frame 60 and disappears at frame 180;
2. group 4 shows the distinct `미카도 씨를...` asset at frame 30;
3. selecting a different group removes the previous group-4 subtitle;
4. neither subtitle survives into the next battle unless its group is selected.

Runtime result: v16 failed before game entry.  DuckStation mounted the CUE and
showed the game title, but did not advance from BIOS into the executable.  The
process remained responsive, so this is recorded as a boot-path regression,
not a CUE/BIN creation failure.

`build/subtitle_usa_multi_poc_v17/` is the first bisection build.  It preserves
the v16 two-group selector and timing update but disables dynamic render-asset
selection and uses the already proven primary v15 DrawOTag-terminal renderer
for both groups.  A successful boot isolates the regression to the v16 dynamic
renderer; another boot failure isolates it to the multi-group event/update
path.  v17 SHA-256 is
`9646138EE4ED55B31CB7485CCDEEA832B833AA40965D780970F1D2EE20050E91`.

Runtime result: v17 also failed to advance from BIOS.  Because v17 removed the
dynamic asset selector, that selector is rejected as the sole cause.  v18 is a
clean single-group bisection using the v15 `v00-timeline` event/update path and
the original render-wrapper address `0x800119B4`.  It retains only group 3,
start 60, end 180.  v18 SHA-256 is
`0C1E5F81CDB91FE7A35B6AE46A405581E8CE787B5905A0D4D200A7D6D9D0BE64`.
If v18 boots, the regression is confined to the multi-group event/update path
or the v16/v17 wrapper relocation.  If v18 fails, the current builder output
must be compared against the formerly runtime-passed v15 payload before any
further multi-group work.

v18 also failed to boot, but it was not byte-equivalent to the passed v15:
the 18px asset produced 56 rectangles and therefore changed the primitive OT
head addresses inspected by the unconditional stale-chain sanitizer.  Rebuilding
the translation directly from `voice_catalog.csv` established the exact v15
render condition as Malgun Gothic Bold 16px, `처음으로…`, 49 rectangles,
84x21 pixels, and a 1,220-byte payload.

`build/subtitle_usa_timeline_poc_v19/` reproduces those conditions.  Its full
disc SHA-256 is
`769086EB2CD64AC6123AE684F5C0B6B4CEBC39354A61F7A5670B9D81ED378C19`,
which is byte-identical to the former runtime-passed v15 disc.  This rejects a
general builder or disc-layout regression.  The leading hypothesis for v18 is
an early-boot false positive in the sanitizer: a changed primitive-head value
can equal a legitimate boot OT link before any subtitle has ever been attached.
The next robust multi-render revision must gate stale-chain detachment on an
explicit `subtitle_attached` state rather than matching head addresses
unconditionally.

Runtime result: v19 booted successfully, confirming the exact v15 baseline and
the 49-rectangle condition.  v20 implements the proposed fix for multi-render
boot safety.  `TIMER_RAM` byte 0 is reused as `subtitle_attached`; this byte was
unused by timeline mode and zero in the verified baseline state.

- when `attached=0` and phase is inactive, the DrawOTag hook returns without
  traversing or comparing any OT link;
- first activation traverses only to find the terminal link and does not detach
  address matches;
- after attaching a subtitle the hook sets `attached=1`;
- only a later frame with `attached=1` may compare and detach the four known
  primary/secondary parity heads;
- group changes preserve `attached` until the next render call removes the old
  chain, then the flag is cleared before optional reattachment.

v20 uses group 3 `처음으로…` and candidate group 4 `미카도 씨를…`, both at
the baseline 16px size.  Static checks passed; payload usage is 1,412/2,076
bytes and the output SHA-256 is
`2B3A472A2EB8932CE3354AAB717B10E5A74BB2ABD4F9536739655EFC15AC1BD1`.
Runtime boot and both group transitions remain pending.

Runtime result: v20 still failed after BIOS, so a single `attached` byte was not
a sufficient initialization contract.  v21 adds a second-byte magic value
`0xA5`.  On the first DrawOTag call without that magic, it clears `attached`,
sets the magic, and returns directly to the original renderer without walking
the OT.  Only later calls with initialized state can traverse or compare links.
The multi render wrapper begins at `0x800119EC`, immediately after the 184-byte
update wrapper, and occupies the full 712-byte reserved interval.  All static
checks passed.  v21 SHA-256 is
`14A96C3FDC1CF344BD3CFE5FD65F4763E6578669B57D8F9579FF040B73D2CF09`;
runtime boot remains pending.

Runtime result: v21 also failed after BIOS, rejecting the uninitialized-state
explanation.  v22 is a strict one-variable bisection: it uses the byte-identical
v19/v15 group-3 timeline, 49-rectangle asset, and original single renderer, but
moves only that renderer from `0x800119B4` to the multi-build address
`0x800119EC`.  Its SHA-256 is
`BD3C8D026447DB74D25D5D4347FD2475820BA7D186C4AF101AD3A485162D7D38`.
A v22 boot failure proves the relocation is unsafe; a successful boot proves
the multi event/update path is responsible.

Runtime result: v22 booted and reached gameplay.  The post-fight dialogue
displayed only the first Korean test subtitle (`For the first time...`).  This
proves that relocating the otherwise byte-equivalent single renderer from
`0x800119B4` to `0x800119EC` is safe.  The v16/v17/v20/v21 boot regression is
therefore confined to the multi-group event/update path, not the renderer
address.  It also confirms that the current group-3 PoC selects only one fixed
record; the second translated voice line has not yet been connected to a
distinct runtime event.

The next investigation uses a DuckStation GDB breakpoint at Japanese
`0x8003A1A0`, the epilogue corresponding to the USA subtitle-group call.  The
older `0x8003AB74` trace point is retained only as historical evidence: it ran
once at story initialization and is not the per-record selector.  No further
multi-group disc build should be made until the live `s2` group and surrounding
register context are captured at boot/title and at the relevant story lines.

The live trace completed after the target post-fight dialogue with exactly one
hit at `0x8003A1A0`: `s2=3`, `s0=3`, `ra=0x8003A120`.  No second group-selection
call occurred for the second spoken line.  This confirms the USA data model:
`For the first time...` and `I've taken a life.` are two simultaneous lines in
one group-3 record, not two independently selected subtitle events.

`build/subtitle_usa_timeline_poc_v23/` therefore keeps the runtime-passed v22
single-group event, update, relocated renderer, and frame 60-180 timeline.  Its
only functional change is a single two-line Korean bitmap sourced from catalog
row `usa/data7/v00/003`:

```text
처음으로…
사람을 베었어…
```

The verified primitive region cannot hold this bitmap at 16px (125 rectangles,
4,032 double-buffered bytes).  Adjacent live RAM is occupied by game data.  v23
uses 9px for the semantic two-line gate, producing 57 rectangles and 1,856
bytes within the existing 1,952-byte limit.  Static checks passed; code-cave
usage is 1,252/2,076 bytes and the output SHA-256 is
`86BC1306AF64A5242FB3A2B7973B74B92A27E9043061235150CC15B3DB47542E`.
After this confirms two-line selection and lifetime at runtime, production
quality should move to a textured subtitle asset loader instead of accumulating
TILE rectangles or shrinking every translation.

## Subtitle font rule

Starting with the build after v23, Korean subtitle assets use
`G:\Downloads\Korean Traslate Project\Font\솔뫼 김대건 Medium.ttf`.  The subtitle
builder now uses this file as its default `--font`; an explicit `--font` remains
available only for controlled diagnostics.  v23 itself retains Malgun Gothic
because it had already been built before this rule was requested.

The production subtitle style omits the USA-style black letterbox and draws
only white Korean glyphs with a 1px black outline.  Removing the current black
background TILE saves only one 16-byte packet per buffer (32 bytes total), so
it does not materially increase the rectangle budget.  A true two-colour
outline in the current TILE-run renderer would require separate black-outline
and white-fill runs and would roughly double packet use.  Consequently this
style is a requirement for the planned textured subtitle renderer, not an
optimization of the temporary rectangle PoC.

Runtime result: v23 repeatedly failed to enter the game after BIOS even with
all prior DuckStation processes closed.  This reproduces the v18 failure class:
changing the rectangle count changes the two primitive-head addresses, and the
unconditional DrawOTag sanitizer can mistake an ordinary boot OT link for a
previous subtitle chain before any subtitle has been attached.

v24 retains the single group-3 selector and official frame 60-180 timeline but
adds a renderer-local initialization magic (`0xA5`) and attached flag.  The
first DrawOTag call initializes state and returns without traversing the OT.
Later calls compare primitive-head addresses only when this renderer previously
attached a subtitle.  Detaching or reaching an ordinary terminal clears the
flag; attaching the new subtitle sets it again.  No v16-style multi-group event
or update code is present.

v24 is also the first build using the new default Solmoe Kim Dae-geon Medium
font.  At 8px its two-line bitmap uses 45 rectangles and 1,472 double-buffered
bytes; payload usage is 1,204/2,076 bytes.  Static checks passed and the output
SHA-256 is
`A6FB7C4FBBA238A534DE4425C76E35E2D17E0F55758F52F82B90051BB4A9879F`.
The black background remains in this boot-safety diagnostic only.  The next
production renderer will remove it and use white textured glyphs with a true
1px black outline.
## Japanese official textured-font renderer route (v26)

The temporary TILE subtitle path is rejected for production use. It rasterizes a
Hangul bitmap into many one-pixel rectangles, so removing the letterbox does not
fix the loss of glyph shape at PS1 resolution. The USA release instead uses one
textured quad per glyph.

Static comparison confirms that the Japanese executable already contains the
same renderer family:

- USA renderer: `0x80036EF4`; glyph builder: `0x8003727C`
- Japan renderer: `0x80038B80`; glyph builder: `0x80038F3C`
- Japan glyph templates: `0x800858AC`, 39 entries x 16 bytes
- Japan width table: `0x80085B1C`, 39 signed halfwords
- Japan submission function: `0x80035854`
- Japan VRAM image upload (`LoadImage` ABI): `0x80069024`, `a0=RECT*`,
  `a1=pixel_data*`

Each 16-byte template begins with RGB plus GPU command `0x2C` (`POLY_FT4`),
followed by four UV pairs, CLUT, and texture-page fields. The Japanese `A`
slot (index 13) is:

`8080802cd8003878e7003e00d80fe70f`

It describes a 16x16 4bpp cell at tpage `0x003E`, UV `(216,0)` through
`(231,15)`. This resolves to physical VRAM rectangle `(950,256,4,16)` in
16-bit words. This exact packet format is shared with the USA uppercase font.

`workspace/scripts/build_official_glyph_poc.py` builds gate
`BBJ-POC-OFFICIAL-GLYPH-001`. It preserves the proven group-3 USA timeline
(frames 60-179), replaces only the Japanese `A` slot, uploads a 16x16 4bpp
Solmoe Kim Dae-geon Medium glyph and private CLUT at runtime, and invokes the
Japanese official renderer with format `#X#Y#w#h#S#CA`. No TILE primitives or
letterbox are used.

v26 static result:

- CUE: `build/subtitle_official_glyph_poc_v26/Bushido Blade (Japan) [KO Official Glyph PoC v26].cue`
- BIN SHA-256: `96236A528C5DE31F2C95645106C81280BDAF114EA3B9F84FB7A0AC2610BB6AAB`
- injected payload: 958 / 2076 verified cave bytes
- source hash, expected writes, Mode2/Form1 integrity, disc size, and executable
  roundtrip: passed
- runtime gate: pending; `처` must appear as one textured glyph only during
  group 3 frames 60-179 and disappear at frame 180

### v27 runtime isolation result

The live forced renderer displayed `처B`, proving all of the following at
runtime: the Japanese renderer entry is callable, the custom 4bpp glyph can be
uploaded, the patched A template selects it, and the resulting object reaches
the game display. A later scene showed `AB`. This is not an encoding failure:
it proves that a stock font-atlas upload later overwrites the A cell (and can
also overwrite the private CLUT), restoring the original ASCII texture.

The first v26 timing failure is separate. A live state force left
`active=1, frame=60` unchanged for one second, so the selected group update
function was no longer running in the current scene. The renderer and timing
lifecycle must therefore remain separate gates.

v28 is a diagnostic stability build. It re-uploads the one glyph and CLUT every
frame immediately before calling the official renderer, and uses direct
`#R/#G/#B` commands instead of scene-transformed `#C`. This is intentionally
not the production design; its purpose is to prove stable glyph/color output
before hooking the stock font-upload completion path.

- CUE: `build/subtitle_official_glyph_poc_v28/Bushido Blade (Japan) [KO Official Glyph Stable Diagnostic v28].cue`
- BIN SHA-256: `C1B0E7F6DBD7345AE4690DCD13DA29DCB8861AE9C59B9FC1726CBA3E7066A1FC`
- runtime status: passed; user confirmed `처B` is clean and clearly visible

### v29 stock-font reload hook

A filtered DuckStation breakpoint trace recorded 1,136 stock `LoadImage` calls
across a scene transition. Four completed uploads overlap the custom A/`처`
cell:

- `(950,256,8,208)`, twice
- `(926,256,30,60)`, twice
- queued `LoadImage` completion return: `0x80067864`

v29 patches that completion epilogue. The wrapper reads the retained `RECT*` in
`s0`; only when the half-open upload rectangle overlaps physical VRAM cell
`(950,256,4,16)` does it call low-level `LoadImage` twice to restore the custom
glyph and CLUT. The renderer still displays `처B` continuously for diagnosis,
but no longer uploads the glyph every frame.

- CUE: `build/subtitle_official_glyph_poc_v29/Bushido Blade (Japan) [KO Official Glyph Reload Hook v29].cue`
- BIN SHA-256: `CAF245736892BAD53F3006866649689A3CF2263C5AEC92399779981339EDC518`
- injected payload: 1233 / 2076 verified cave bytes
- runtime status: live-installed; persistence across the next scene transition
  remains to be verified

v29 runtime result: the glyph remained present across transitions, but the user
observed intermittent color changes and malformed glyph appearance. Forced
rendering on character-selection screens is outside the intended subtitle
context and explains the irrelevant position there. The battle-screen color
changes identify a separate lifetime issue: the private CLUT can be overwritten
without an upload overlapping the A/`처` texture cell.

### v30 glyph plus CLUT VRAM guard

v30 extends the same `0x80067864` completed-upload hook. It restores both
resources after an original upload overlaps either protected region:

- glyph: `(950,256,4,16)`
- private CLUT: `(1008,511,16,1)`

The global `처B` draw remains diagnostic only. Character-selection placement
is not a production-layout result; the runtime gate is stable glyph shape and
color during battle and across one subsequent scene transition.

- CUE: `build/subtitle_official_glyph_poc_v30/Bushido Blade (Japan) [KO Official Glyph VRAM Guard v30].cue`
- BIN SHA-256: `B228D3CCB16AD5518F963C8FEB69C82C8634CDEB1B8C1A2DC9EE0E762F2E1395`
- runtime status: live-installed, battle stability confirmation pending

v30 runtime result:

- character-selection screen: graphical glitch while the diagnostic renderer
  was forced globally
- replay: diagnostic glyph color changed to yellow
- battle and post-battle dialogue portions: glyph shape and color were normal

These results reject global rendering but accept the official renderer in the
actual subtitle contexts. The character-selection and replay failures are not
font-data failures; they occur only because the diagnostic draw was injected
into render states where subtitles do not belong.

### v31 timed official glyph gate

v31 removes the global render-callsite patch. It retains the glyph plus CLUT
VRAM guard and invokes the Japanese official renderer only from the group-3
USA subtitle timeline, frames 60-179. The timed diagnostic text is `처B`, using
direct `#R/#G/#B` color commands. Live verification confirmed that the global
render callsite returned to its original `jal 0x80070838` instruction.

- CUE: `build/subtitle_official_glyph_poc_v31/Bushido Blade (Japan) [KO Official Timed Glyph v31].cue`
- BIN SHA-256: `6C7013D9E45A80C826F9D7E8DA8D59207F81328AFB3319E420B5D77B6CB4071A`
- runtime status: live-installed; fresh group-3 timing verification pending

v31 runtime result: no timed subtitle appeared. The selector trace still
recorded the intended event as `s2=3`, so the group identity was not the
failure. A post-test RAM read instead found the lifecycle block at
`0x800A2E90` as `000000000000000000400000`: `active` was clear and the frame
word had become `0x4000`, a value the subtitle wrapper never writes. This
proves that the provisional state address overlaps mutable game data.

### v32 code-cave lifecycle state

v32 moves the 12-byte active/frame state to `0x80011D94`, inside the verified
executable code cave and after the renderer assets. No renderer, glyph, CLUT,
group selector, or frame-window assumption changed. The current DuckStation
session was live-patched and the new state read back as twelve zero bytes,
while the unsafe old address still contained the foreign `0x4000` word.

- CUE: `build/subtitle_official_glyph_poc_v32/Bushido Blade (Japan) [KO Official Timed Glyph v32].cue`
- BIN SHA-256: `4D3AEE86D8CDE1A58EB05EB74EA1FA0272D8F47DC67105FB90CD25325FDD5F57`
- injected payload: 1260 / 2076 verified cave bytes
- runtime status: live-installed; requires replaying the group-3 story event
- decisive gate: after selector entry, `0x80011D97` must become 1 and the word
  at `0x80011D9C` must advance; the glyph must then appear only on frames
  60-179

Correction after clean boot: `0x80011D94` was not free state storage. v32
extended the payload from 1233 to 1260 bytes and overwrote live executable
bytes following the v31 payload. On a clean process, v32 remained in the BIOS
service path (`PC=0x000000A0`) and never entered the game. The apparent prior
RAM read only proved that a long-lived v26 process accepted the live writes;
it did not validate a clean v32 boot. v32 is rejected.

The first v32 test attempt was interrupted because the multi-breakpoint GDB
tracer paused the visible emulator at a hook while waiting for the client.
The user restarted the VM, which also discarded the live RAM patch. The tracer
connection was terminated, all breakpoints were cleared by the restart, and
v32 was live-installed again for that attempt. Subsequent verification uses
short state reads instead of a continuously armed breakpoint so normal
gameplay cannot remain paused.

Live installation was then abandoned for the next runtime gate because even a
brief GDB stop was disruptive. Process inspection showed that the long-lived
DuckStation instance had originally booted the v26 CUE and accumulated v27-v32
RAM patches. It was closed, and a clean process was launched directly with the
v32 CUE. Clean-test process PID: `4872`; the window title reached
`ブシドーブレード`, but later CPU sampling proved that this process was still
stuck in the BIOS service path. Window title alone is not a boot-success gate.

### v33 in-buffer lifecycle state

v33 places the 12-byte lifecycle block at `0x80011D78..0x80011D83`, in the
padding after the 19-byte timed format string and before the existing byte at
`0x80011D84`. The payload is therefore 1233 bytes, exactly the prior bootable
v31 size, and does not overwrite the executable bytes that v32 corrupted.

- CUE: `build/subtitle_official_glyph_poc_v33/Bushido Blade (Japan) [KO Official Timed Glyph v33].cue`
- BIN SHA-256: `CA9EEED750E5EA2EEDB4A89EFD9E5CDF50B70839A7EB2A7157109925BF0193D0`
- clean DuckStation process: PID `16364`
- clean-boot CPU samples: `0x800709F4`, `0x80043224`, `0x800709A8`
- state readback: `0x80011D78`, twelve zero bytes before the target event
- runtime status: clean boot passed; timed subtitle event pending

v33 runtime result: the post-fight subtitle did not appear. The 12-byte state
read after the event was `000000000000000000400000`: `active=0` and the frame
word was again the foreign value `0x4000`. The format-string padding is
therefore also game-owned at runtime and is rejected as persistent storage.

### Persistent-state allocation audit and build freeze

The repeated CPU-RAM failures were preventable. They were recorded
chronologically, but the builders did not consume an authoritative ownership
map. `CAVE_END_RAM=0x800120D0` only imposed an upper-bound check; it did not
prove that every byte below the bound was inert code space or writable state.
The additional wrapper-overlap check compared only our own injected ranges.
It could not detect an original game writer. Consequently static hash, disc
roundtrip, and `payload_end <= CAVE_END_RAM` checks incorrectly labeled unsafe
builds as fitting a "verified code cave."

Keep these failure classes separate:

- VRAM `(950,256,4,16)` and `(1008,511,16,1)`: stock glyph/CLUT uploads;
  expected resource-lifetime conflict, handled through the LoadImage path.
- CPU RAM `0x800A2E90..0x800A2E9B`: rejected; mutable game data overwrites the
  custom active/frame state.
- CPU RAM `0x80011D94..0x80011D9F`: rejected; live executable bytes, v32 boot
  failure.
- CPU RAM `0x80011D78..0x80011D83`: rejected; runtime game writer restores a
  foreign `0x4000` word, v33 timing failure.
- Global diagnostic render call: rejected lifecycle placement, not a storage
  overlap; it caused selection/replay artifacts.

No CPU RAM range is currently approved for persistent subtitle state. Do not
produce v34 by selecting another apparent zero/padding region. The next route
must remove the custom active/frame block and port the USA V00 lifecycle using
the existing game-owned group/frame state, or first prove a candidate field by
write-watch tracing across clean boot, title, selection, dialogue, replay, and
the following battle. A candidate becomes buildable only after that evidence
is added to a machine-enforced range manifest.

### v34 game-owned scene timeline

The USA comparison established that its subtitle engine owns separate globals
at `gp+0x420/0x424/0x428`. The Japanese scene player already owns a usable
timeline without those USA-only fields:

- `gp+0x304` (`0x800A2D88`): current Japanese scene source
- `gp+0x314` (`0x800A2D98`): scene frame, reset by scene initialization and
  incremented/capped by the original update function

After the target Tatsumi post-fight scene, the original Japanese frame read
`179`, matching the USA record's visible interval end. The target scene source
also exposed the stable diagnostic words `0x000C16E9` at offset 4 and
`0xACAF17DF` at offset 12.

v34 removes the group-selector patch and all custom active/frame writes. Its
only timing hook reads the target scene signature and the original Japanese
scene frame, rendering during `60..179`. The official renderer emits fresh GPU
primitives only while that update function runs, so the next scene naturally
stops submission without a persistent attached flag.

`workspace/config/subtitle_memory_map.json` is now the machine-readable range
authority. `build_official_glyph_poc.py` refuses the rejected persistent-state
mode, requires `--scene-frame-direct`, and limits the payload to the reduced
`0x800118B4..0x80011D85` allocation. v34 ends at `0x80011D77`.

- CUE: `build/subtitle_official_glyph_poc_v34/Bushido Blade (Japan) [KO Official JP Scene v34].cue`
- BIN SHA-256: `07EB32BA39BAC0BEA2F32EF4C5E30F41C5DC1CE5302002C6C5C8FF40D0B0B0C7`
- group hook `0x8003A1A0`: original instructions preserved
- update hook `0x8003A8A8`: direct jump to the read-only scene test
- custom persistent state: none
- clean DuckStation PID: `7184`
- clean-boot CPU samples: `0x80043224`, `0x800709F4`, `0x800709A8`
- runtime status: clean boot passed; target post-fight subtitle did not appear

The user completed the target fight on v34 and observed no subtitle. This
rejects v34 as an end-to-end solution but does not reintroduce a RAM ownership
failure: v34 has no custom persistent state. The next comparison must verify
the exact regional insertion point. USA calls its subtitle updater at
`0x80038DA4`, immediately after decrementing/storing the scene countdown and
before the final scene-frame comparison. The structurally corresponding Japan
location is around `0x8003A88C`, whereas v34 hooks the later epilogue at
`0x8003A8A8`. A successor should first capture source/frame values at the exact
pre-epilogue point and test the renderer there; do not allocate another state
block or broaden the payload range.
