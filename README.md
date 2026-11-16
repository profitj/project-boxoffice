# 영화 상영 횟수 조정 도우미

'AI개발환경의 이해' 기말 팀 프로젝트입니다.

**경영 질문**: 멀티플렉스 편성 담당자가 개봉 2주차에 어느 영화의 상영 횟수를 줄이고, 어느 영화를 유지할까?

## 데이터

| 데이터 | 출처 | 결합 기준 |
|---|---|---|
| 일별 박스오피스 (관객 수·매출액·스크린 수·상영 횟수) | 영화진흥위원회 오픈API | 날짜, 영화코드 |
| 공휴일 | 한국천문연구원 특일 정보 API (공공데이터포털) | 날짜 |
| 영화 상세정보 (장르·관람등급) | 영화진흥위원회 오픈API | 영화코드 |

행 하나 = 날짜 × 영화. 핵심 지표는 상영 1회당 관객 수(관객 수 ÷ 상영 횟수)입니다.

## 설치

```bash
git clone https://github.com/profitj/project-boxoffice.git
cd project-boxoffice
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
```

## 실행

인증키가 없어도 `data/sample/`의 샘플 데이터로 실행됩니다.

```bash
uvicorn api:app --reload        # API 서버
streamlit run app.py            # 화면 (다른 터미널에서)
```

직접 수집하려면 `.env.example`을 복사해 `.env`로 이름을 바꾸고 키를 넣은 뒤 실행합니다.

```bash
python collect.py
python clean.py
```

## 폴더 구조

```
collect.py        수집 (박스오피스·공휴일·상세정보)
clean.py          정리·결합
api.py            FastAPI 서버
app.py            Streamlit 화면
data/sample/      인증키 없이 실행할 때 쓰는 샘플 데이터
reports/          건수 검증표 (count_check.md)
docs/prompts.md   AI와 작업한 과정 기록
tests/            테스트
```

## 팀

| 이름 | 담당 |
|---|---|
| 이윤정 | 수집, 배포, README |
| 이승주 | 정리·결합, 건수 검증표, 테스트 |
| 김유라 | 서버, 화면 |
