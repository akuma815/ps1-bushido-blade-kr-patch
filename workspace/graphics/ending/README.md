# 엔딩 세로 문구 편집

## 폴더

- `source`: 일본판에서 추출한 보존 원본
- `edited`: 한글 이미지 편집용 복사본

`source`는 수정하지 않고 같은 이름의 `edited` PNG만 편집한다.

## DATA3/O15.DAT 자산

| 편집 PNG | TIM 오프셋 | 크기 | 비고 |
| --- | ---: | ---: | --- |
| `O15_00000148.png` | `0x148` | 32x216 | 세로 문구 |
| `O15_00000F08.png` | `0xF08` | 32x216 | 세로 문구 |
| `O15_00001CC8.png` | `0x1CC8` | 64x180 | 세로 문구 |
| `O15_00003388.png` | `0x3388` | 64x216 | 캡처한 엔딩 본문과 일치 |
| `O15_00004EC8.png` | `0x4EC8` | 64x204 | 세로 문구 |
| `O15_00006888.png` | `0x6888` | 24x40 | 캡처한 서명 `神和斎`와 일치 |

모두 4bpp indexed TIM이며 각 자산의 캔버스는 바꾸지 않는다. 글자는 세로로
배치하고 배경은 원본처럼 검정으로 유지한다. 재삽입 빌드는 편집본을 16색
PS1 BGR555로 변환하므로 안티앨리어싱을 과도하게 쓰지 않는 편이 안전하다.

최초 런타임 PoC는 실제 캡처와 연결된 `O15_00003388.png`를 대상으로 한다.
서명까지 번역 또는 교체하려면 `O15_00006888.png`도 함께 편집한다.

## 재삽입 빌드

6개 편집 PNG가 모두 준비되면 다음 명령으로 빌드한다.

```powershell
python workspace/scripts/build_ending_patch.py `
  "build/loading_logo_ko/Bushido Blade (Japan) [KO Loading Logo].bin" `
  --edited-dir "workspace/graphics/ending/edited" `
  --output-dir "build/loading_ending_ko"
```

재삽입기는 각 TIM의 원본 16색 CLUT와 헤더를 보존하고 이미지 payload만
교체한다. 현재 결과는 `build/loading_ending_ko`에 있으며 Mode2 Form1
EDC/ECC와 디스크 재파싱까지 정적 검증을 통과했다. 런타임 엔딩 확인은
별도로 필요하다.
