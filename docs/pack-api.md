# 학습 자료 내려받기 규격 (앱 연동)

앱이 서버에서 학습 자료를 받아 **오프라인으로** 쓰기 위한 규격입니다.
서버는 정적 파일만 내어 줍니다. 로그인도 API 키도 없습니다.

```
https://myvoice.901planner.cloud/packs/
├── index.json                    무엇이 있는지 · 판(version)
└── packs/
    ├── 양사_d001.zip              하루치
    ├── hsk1_d001.zip
    └── …                         모두 258개
```

자료는 서버에서 계속 고칩니다. 앱은 `index.json` 하나만 확인해
**바뀐 하루치만** 다시 받으면 됩니다.

---

## 1. index.json

`GET /packs/index.json` — 104 KB, `Cache-Control: no-cache`

```jsonc
{
  "version": 1,
  "data_version": "20260904-2103",       // 하나라도 고치면 바뀐다
  "per_day": 20,
  "audio": "mp3 64k mono",
  "voices": { "m": "남성 (김준용)", "f": "여성 (수민)" },
  "verified": {                          // 음성 검사 상태
    "양사": "낱글자 음성 전수검사 통과",
    "hsk1": "미검사", "hsk2": "미검사", …
  },
  "courses": [
    {
      "id": "양사",
      "label": "양사",
      "words": 73,
      "days": 4,
      "built": "2026-09-04",
      "verified": "낱글자 음성 전수검사 통과",
      "lessons": [
        {
          "day": 1,
          "week": 1,
          "file": "packs/양사_d001.zip",
          "words": 20,
          "examples": 66,
          "preview": ["个","位","口","名","本","张"],
          "bytes": 1572864,
          "sha256": "88160db0c7c6…",
          "rev": "88160db0c7c6",         // sha256 앞 12자 — 이것만 견주면 된다
          "built": "2026-09-04"
        }
      ]
    }
  ]
}
```

### 과정 목록

| id | 낱말 | 일수 | 크기 | 검사 |
|---|---|---|---|---|
| `양사` | 73 | 4 | 5.3 MB | 전수검사 통과 |
| `hsk1` | 150 | 8 | 2.3 MB | 미검사 |
| `hsk2` | 147 | 8 | 2.1 MB | 미검사 |
| `hsk3` | 298 | 15 | 4.5 MB | 미검사 |
| `hsk4` | 601 | 31 | 8.9 MB | 미검사 |
| `hsk5` | 1,339 | 67 | 18.9 MB | 미검사 |
| `hsk6` | 2,500 | 125 | 36.4 MB | 미검사 |

하루치는 평균 0.3 MB(양사는 예문이 많아 1.3 MB)입니다.

---

## 2. 하루치 zip

`GET /packs/packs/hsk1_d001.zip` — `Cache-Control: public, max-age=604800`

```
data.json
audio/w1234_m.mp3      낱말, 남성
audio/w1234_f.mp3      낱말, 여성
audio/e567_m.mp3       예문, 남성
audio/e567_f.mp3
```

### data.json

```jsonc
{
  "version": 1,
  "course": "양사", "label": "양사",
  "day": 1, "days": 4,
  "words": [
    {
      "id": 5624,
      "chinese": "张",
      "pinyin": "zhāng",
      "meaning_ko": "장 (종이·책상 등을 세는 말)",
      "senses_ko": "① … ② …",          // 사전식 여러 뜻, 없을 수 있음
      "hsk": 3,
      "set": "양사",
      "audio_say": null,                // ↓ 3절
      "audio": {
        "m": { "file": "audio/w5624_m.mp3", "sec": 1.53 },
        "f": { "file": "audio/w5624_f.mp3", "sec": 0.85 }
      },
      "examples": [
        {
          "id": 19,
          "chinese": "一张纸",
          "pinyin": "yī zhāng zhǐ",
          "meaning_ko": "종이 한 장",
          "audio": { "m": { "file": "…", "sec": 0.98 },
                     "f": { "file": "…", "sec": 0.94 } }
        }
      ]
    }
  ],
  "chars": {                            // ↓ 4절
    "纸": { "pinyin": "zhǐ", "ko": "종이" }
  }
}
```

`audio.m` / `audio.f` 는 **없을 수 있습니다**(`null`). 한쪽만 있으면 그쪽을 쓰세요.
`sec` 은 길이(초)입니다. 차례 재생의 사이 간격을 잡을 때 미리 알면 편합니다.

---

## 3. audio_say — 반드시 처리해야 합니다

양사 몇 개는 TTS 가 **글자 하나만으로는 성조를 제대로 못 냅니다**
(`页` 를 `耶` yé 로, `种` 을 `中` zhōng 으로 읽어 버립니다).

그런 낱말은 「一杯」처럼 **수사를 붙여** 녹음했고, 실제로 읽은 말을 `audio_say` 에 적었습니다.

```jsonc
{ "chinese": "杯", "pinyin": "bēi", "audio_say": "一杯" }
```

- `audio_say` 가 `null` → 그 글자만 읽은 소리
- `audio_say` 가 있으면 → 화면엔 `chinese`, 소리는 `audio_say`

화면에 「一杯 로 들려줍니다」처럼 곁들여 주면 배우는 이가 헷갈리지 않습니다.
양사 73개 중 21개가 여기 해당합니다.

---

## 4. chars — 글자 하나하나의 음과 뜻

쓰기 공부에서 예문을 한 글자씩 짚어 갈 때 씁니다.
그 하루치에 나오는 모든 한자가 들어 있습니다.

교재에 없는 글자는 CC-CEDICT 뜻을 한글로 옮겨 채웠습니다.
`咖啡` 의 `啡` 처럼 홀로 못 쓰는 글자는 「「咖啡(커피)」의 글자」로 적었습니다.

---

## 5. 앱이 도는 방식

```
1. index.json 받기                       (104 KB, 늘 새로)
2. data_version 이 저장해 둔 것과 같으면 → 끝
3. 다르면 lesson 마다 rev 견주기
4. rev 가 달라진 하루치만 zip 받아 덮어쓰기
5. 새 data_version 저장
```

`rev` 는 zip 의 sha256 앞 12자입니다. 자료를 고쳐 다시 내보내면 그 하루치의 `rev` 만 바뀝니다.

### 저장 자리

```
<앱문서폴더>/packs/<course>/d001/
    data.json
    audio/*.mp3
```

내려받아 푼 모양이 곧 zip 속 모양이므로, 앱 코드는 「하루치 폴더 하나」만 다루면 됩니다.

### 다트 보기

```dart
// ── 판 확인
final idx = jsonDecode(await http.read(
    Uri.parse('$base/packs/index.json'))) as Map<String, dynamic>;
if (idx['data_version'] == prefs.getString('data_version')) return;   // 그대로면 끝

// ── 바뀐 것만
for (final c in idx['courses']) {
  for (final l in c['lessons']) {
    final key = '${c['id']}_d${l['day']}';
    if (prefs.getString('rev_$key') == l['rev']) continue;            // 안 바뀜

    final bytes = await http.readBytes(Uri.parse('$base/packs/${l['file']}'));
    final dir = Directory('${docs.path}/packs/${c['id']}/d${l['day'].toString().padLeft(3,'0')}');
    if (dir.existsSync()) dir.deleteSync(recursive: true);
    dir.createSync(recursive: true);
    for (final f in ZipDecoder().decodeBytes(bytes)) {
      if (!f.isFile) continue;
      File('${dir.path}/${f.name}')..createSync(recursive: true)
                                   ..writeAsBytesSync(f.content as List<int>);
    }
    await prefs.setString('rev_$key', l['rev']);
  }
}
await prefs.setString('data_version', idx['data_version']);

// ── 재생 (파일 경로로)
final w = words[i];
final a = (voiceIsFemale ? w['audio']['f'] : w['audio']['m']) ?? w['audio']['m'];
await player.play(DeviceFileSource('${dir.path}/${a['file']}'));
```

필요한 패키지: `http`, `archive`(zip), `path_provider`, `audioplayers` 또는 `just_audio`,
`shared_preferences`.

### 챙길 것

- **끊긴 내려받기** — zip 을 받아 `sha256` 을 견주고 다르면 버리세요. 반쯤 받은 걸 풀면 깨집니다.
- **한 번에 다 받지 않기** — HSK 6급은 125일치입니다. 고른 주차만 받게 하세요.
- **`preview`** 로 목록에서 무슨 낱말이 들었는지 미리 보일 수 있습니다.
- **`verified`** 가 「미검사」인 과정은 음성에 성조 오류가 섞여 있을 수 있습니다(아래 8절).

---

## 6. 획순

이 꾸러미에는 없습니다. **hanzi-writer-data**(MIT)를 쓰세요 —
글자마다 획의 좌표가 든 JSON 이라 필요한 글자만 받아 `CustomPainter` 로 그리면 됩니다.

> 플러터용 기성 패키지가 있는지는 확인해 보지 않았습니다.
> 없으면 웹뷰에 hanzi-writer(JS)를 얹는 길도 있습니다.

---

## 7. 자료를 다시 만들려면 (서버 쪽)

```bash
# 고친 과정만 다시 내보내기
docker exec myvoice2-prod python3 /app/finetune_data/_wordapp/export_pack.py hsk3 --per 20

# 판 찍기 (data_version · rev · built)
docker exec myvoice2-prod python3 /app/finetune_data/_wordapp/export_stamp.py
```

- `--per 30` 하루 분량 바꾸기
- `--wav` 압축 없이 (12배 커집니다)
- 같은 과정을 다시 내보내면 `index.json` 의 그 과정만 갈아 끼웁니다

앱은 다음에 켤 때 바뀐 날짜만 받습니다. 앱 쪽 손볼 것은 없습니다.

---

## 8. 지금 알아 둘 한계

- **HSK 음성은 전수검사 전입니다.** 양사·접속사·의성의태 낱글자 162개만 받아적기로
  성조까지 확인했습니다. 그때 208개 중 77개가 틀렸던 걸 보면, HSK 쪽에도 섞여 있을 수 있습니다.
  특히 **낱글자**가 위험합니다. 여러 글자 낱말은 대체로 제대로 읽힙니다.
- **인증이 없습니다.** 주소를 아는 사람은 누구나 받습니다.
- **예문은 양사·접속사·의성의태에만** 있습니다(344개). HSK 낱말에는 아직 없습니다.
