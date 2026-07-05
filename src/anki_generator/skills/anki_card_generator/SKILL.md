---
name: "anki_card_generator"
description: "일본어 입력 문장이나 단어로부터 핵심 학습용 Anki 카드를 생성하고 검증, TTS 합성 및 Anki 앱과 로컬 DB로 동기화합니다."
---

# Anki 카드 자동 생성 에이전트 스킬 가이드라인

본 스킬은 고급 일본어 학습자(JLPT N1 ~ 비즈니스 수준)를 위해 에이전트가 직접 구동하는 자동화 파이프라인 명세서입니다. 사용자가 일본어 단어, 활용형, 또는 완성된 문장을 던져주면 에이전트는 아래의 단계에 따라 카드를 생성하고 로컬 스크립트를 조율하여 작업을 완료해야 합니다.

---

## 🧠 에이전트 수행 워크플로우 (The Orchestration Flow)

사용자가 일본어 입력 텍스트를 제공하면, 에이전트는 다음 단계를 순서대로 수행합니다.

### [1단계] 핵심 타겟 추출 및 중복 검사 (Routing & Dedup)
1. 사용자가 문장을 던졌다면, 에이전트 자체의 지식으로 문맥상 학습 가치가 높은 고급 어휘(N2~N1 수준 또는 비즈니스 단어/관용구)를 추출하여 타겟 단어 리스트로 만듭니다. (예: `奔走する`, `妥協`)
2. 추출된 각 타겟 단어에 대해 로컬 DB 헬퍼를 실행하여 기등록 여부를 확인합니다.
   * **명령어**: `uv run python src/anki_generator/skills/anki_card_generator/scripts/db_helper.py --check "<추출된단어>"`
3. DB 헬퍼의 응답 JSON에 `exists: true`가 찍혀 있다면:
   * 다의어로 새 카드를 추가 생성할지, 혹은 이번 단어는 건너뛸지 유저에게 의사를 확인합니다.
   * 중복이 없고 `exists: false`라면 카드를 생성하기 위해 다음 단계로 넘어갑니다.

### [2단계] 마스터 룰 기반 JSON 카드 생성 (Generation)
1. 아래 **[카드 생성 4대 원칙]** 및 **[JSON Output Schema]**를 엄격히 준수하여 카드의 구조화 데이터를 추론 및 작성합니다.
2. 작성된 임시 카드 데이터를 프로젝트 내 `temp_card.json` 파일에 저장합니다.

### [3단계] 자가 검증 및 수정 루프 (Validation & Self-Reflection)
1. `validator.py`를 실행하여 생성된 JSON이 규칙에 맞는지 기계적 및 형태소 검증을 수행합니다.
   * **명령어**: `uv run python src/anki_generator/skills/anki_card_generator/scripts/validator.py temp_card.json`
2. 만약 오류가 반환되어 검증 실패(`valid: false`)가 뜬 경우:
   * 오류 메시지를 확인하고 JSON 데이터를 수정한 뒤 `temp_card.json`을 다시 덮어씁니다.
   * 성공할 때까지 검증 스크립트를 재호출합니다. (최대 3회 반복)

### [4단계] 예문 오디오 합성 (TTS Generation)
1. 검증이 완료된 카드의 `front` 필드 예문 텍스트를 바탕으로 원어민 TTS 음성 mp3 파일을 생성합니다.
   * **명령어**: `uv run python src/anki_generator/skills/anki_card_generator/scripts/tts_helper.py --text "<앞면에 들어갈 일본어 예문 전체>"`
2. 성공 응답으로 받은 JSON 결과 중 `"output_path"`를 추출합니다.
3. `temp_card.json` 내 해당 카드의 `audio_path` 필드값에 해당 경로를 업데이트하여 저장합니다.

### [5단계] Anki 동기화 및 로컬 DB 영구 기록 (Export & DB Write)
1. 생성된 카드를 데스크톱 Anki 앱에 직접 주입하기 위해 커넥터를 실행합니다.
   * **명령어**: `uv run python src/anki_generator/skills/anki_card_generator/scripts/anki_connector.py temp_card.json`
2. 마지막으로 지식의 원천인 로컬 DB에 카드를 안전하게 등록합니다.
   * **명령어**: `uv run python src/anki_generator/skills/anki_card_generator/scripts/db_helper.py --insert temp_card.json`
3. 처리가 끝난 임시 파일 `temp_card.json`을 삭제하고, 사용자에게 최종 성공 결과를 리포트합니다.

---

## 🏛️ 카드 생성 4대 원칙

### 제1원칙: 데이터베이스 무결성과 타겟 분리 전략 (DB Integrity & Routing)
1. **다의어의 분할 (최소 정보의 원칙)**: 입력된 타겟이 다의어일 경우, 하나의 카드에 여러 뜻을 몰아넣지 않고, 대표적인 뜻 2~3개를 선정해 개별 독립 객체(카드)로 분리해 작성합니다.
2. **관용구(Idioms) vs 연어(Collocations) 구분**:
   * **관용구** (예: `腹を割る`, `水を差す`): 단어들이 결합해 완전히 새로운 뜻이 되는 고정 표현은 덩어리 전체를 하나의 `root_id`로 삼습니다. 단, `components` 필드에 구성 형태소를 쪼개어 배열로 저장합니다 (예: `["腹", "割る"]`).
   * **연어** (예: `妥協点を見出す`, `責任を追及する`): 본뜻이 유지되는 단순 짝꿍 표현은 핵심 고급 단어 하나만 `root_id`로 삼고, `collocations` 필드에 연어 덩어리를 배열로 수집합니다.

### 제2원칙: 최고 효율의 문장 생성 가이드라인 (Sentence Engineering)
1. **간결함**: 1~2개의 절, 40~50자 이내로 짧고 명료하게 작성합니다.
2. **맥락적 단서**: 문장 속 타겟 단어 자리가 비어 있어도 문맥만으로 어떤 단어(연어)가 오는지 유추할 수 있도록 촘촘하게 비즈니스 어휘를 주변에 배치합니다.
3. **시각적/감정적 생동감**: 비즈니스 위기 상황, 극적 협상, 정중한 사과 등 긴장감이 묘사되어 뇌에 강하게 각인되도록 상황을 부여합니다.
4. **대조를 통한 유의어 배치**: 유사 한자나 동음이의어가 있다면 한 문장 안에 두 단어를 교차 배치하여 자연스레 비교하게 만듭니다.

### 제3원칙: 형태소 환원 및 고유 ID 규칙 (Morphological Rules)
* **Root_ID 포맷**: 입력값이 활용형이더라도 반드시 Weblio/Goo 사전 기준 기본형으로 환원하여 `기본형한자(기본형요미가나)` 포맷으로 식별자를 만듭니다. (예: `躊躇った` -> `躊躇う(ためらう)`)
* **표기 통일**: 일본 상용한자표기 기준의 대표적 송리가나로 통일합니다.
* **표외 한자(Is_Hyogai)**: 상용한자 표 이외의 한자가 사용된 경우 `is_hyogai: true`로 설정합니다.

### 제4원칙: POS(품사) Enum 제한
* **Format**: `대분류(세부분류) - 활용/문법`
* **대분류**: 명사, 동사, い형용사, な형용사, 부사, 접속사, 연체사, 관용구
* **세부분류**: 1그룹, 2그룹, 3그룹, 자동사, 타동사, 대명사, 고유명사, 수사, 조동사적명사
* **활용/문법**: 수동, 사역, 사역수동, 가정, 명령, 존경어, 겸양어, 정중어, 활용 없음

---

## 📊 JSON Output Schema

```json
{
  "cards": [
    {
      "front": "타겟 단어가 반드시 <span style='color:blue'><b>단어</b></span> 태그로 감싸진 일본어 예문.",
      "back": "후리가나가 포함된 일본어 예문<br><br>[뜻] 해당 문맥에 맞는 한국어 뜻<br><br>[Tip] 헷갈리는 유의어와의 뉘앙스 차이점 설명",
      "target_word": "문장에 쓰인 타겟 단어의 실제 활용 형태 (예: 躊躇った)",
      "root_id": "기본형한자(기본형요미가나) (예: 躊躇う(ためらう))",
      "pos": "Enum 규칙에 맞춘 품사 정보 (예: 동사(1그룹/타동사) - 활용 없음)",
      "components": ["관용구일 경우 형태소 분리 배열"], 
      "collocations": ["연어가 있을 경우 배열 저장"], 
      "is_hyogai": false,
      "tags": ["비즈니스", "N1", "동사" 등 검색용 태그 배열],
      "audio_path": ""
    }
  ]
}
```

## ⚠️ 중요 주의사항 (CRITICAL)
- **언어 격리**: `front`, `target_word`, `root_id`, `components`, `collocations` 필드에는 절대 한글이나 한국어 한자(예: 壓, 賣 등)가 유입되어서는 안 되며, 반드시 일본어 신자체(Shinjitai, 예: 圧, 売) 및 가나로만 작성해야 합니다.
- **한국어 해설**: `back` 필드의 `[뜻]`과 `[Tip]` 설명 영역에 한해 한국어로 풍부하게 해설을 기재합니다.
