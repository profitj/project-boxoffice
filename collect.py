"""수집: 영화진흥위원회 일별 박스오피스를 날짜별로 받아 CSV로 저장한다.

사용법
    python collect.py                      # 그저께까지 최근 7일
    python collect.py 20260801 20261115    # 시작일 종료일 직접 지정

11주차 수업의 검수 기준을 따른다.
    - 인증키는 환경변수(.env)에서만 읽고, 화면·로그·파일 어디에도 출력하지 않는다.
    - HTTP 상태코드를 먼저 보고, 그다음 API가 돌려준 오류(faultInfo)를 본다.
    - 원본 응답(JSON)을 data/raw/json 에 남겨, CSV와 직접 비교할 수 있게 한다.
    - fetched_at(수집 시각)을 넣어, 다시 실행했을 때 새로 받은 것인지 확인한다.
"""
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

URL = "http://www.kobis.or.kr/kobisopenapi/webservice/rest/boxoffice/searchDailyBoxOfficeList.json"
OUT_DIR = Path("data/raw")        # 수집한 CSV (clean.py 가 여기서 먼저 찾음)
RAW_DIR = Path("data/raw/json")   # 원본 JSON (.gitignore 에 있어 올라가지 않음)

# API가 주는 이름 -> 우리가 쓰는 컬럼 이름 (11/16 회의에서 확정)
COLUMNS = {
    "rank": "rank",            # 그날 순위
    "movieCd": "movie_cd",     # 영화코드 (상세정보와 결합하는 기준)
    "movieNm": "movie_nm",     # 영화 이름
    "openDt": "open_dt",       # 개봉일
    "salesAmt": "sales_amt",   # 그날 매출액
    "audiCnt": "audi_cnt",     # 그날 관객 수
    "scrnCnt": "scrn_cnt",     # 그날 스크린 수
    "showCnt": "show_cnt",     # 그날 상영 횟수
}


def fetch_day(key: str, day: date, fetched_at: str) -> list[dict]:
    """하루치 박스오피스(상위 10편)를 받아 행 목록으로 돌려준다. 실패하면 빈 목록."""
    params = {"key": key, "targetDt": day.strftime("%Y%m%d")}

    # 1단계: 요청이 서버까지 갔는가 (오류 내용에는 키가 든 주소가 섞일 수 있어 종류만 출력)
    try:
        res = requests.get(URL, params=params, timeout=10)
    except requests.RequestException as e:
        print(f"  {day} 요청 실패: {type(e).__name__}")
        return []

    # 2단계: HTTP 상태코드 (200이 아니면 실패)
    if res.status_code != 200:
        print(f"  {day} HTTP 상태코드 {res.status_code}")
        return []

    # 3단계: 응답이 JSON인가
    try:
        data = res.json()
    except ValueError:
        print(f"  {day} 응답이 JSON이 아님")
        return []

    # 4단계: API가 돌려준 오류 (키가 틀리거나 한도를 넘으면 faultInfo가 온다)
    if "faultInfo" in data:
        print(f"  {day} API 오류: {data['faultInfo'].get('message')}")
        return []

    # 원본 응답을 그대로 저장 (응답에는 키가 들어 있지 않음)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_DIR / f"boxoffice_{day:%Y%m%d}.json"
    raw_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    movies = data.get("boxOfficeResult", {}).get("dailyBoxOfficeList", [])
    rows = []
    for m in movies:
        row = {"date": day.isoformat()}
        for api_name, our_name in COLUMNS.items():
            row[our_name] = m.get(api_name)
        row["fetched_at"] = fetched_at
        rows.append(row)
    return rows


def main() -> None:
    load_dotenv()
    key = os.getenv("KOBIS_API_KEY")
    if not key:
        sys.exit(".env 파일에 KOBIS_API_KEY가 없습니다. .env.example을 복사해 키를 넣으세요.")

    if len(sys.argv) == 3:
        start = datetime.strptime(sys.argv[1], "%Y%m%d").date()
        end = datetime.strptime(sys.argv[2], "%Y%m%d").date()
    else:
        # 전날 통계는 다음 날 오전까지 보정되므로 그저께까지만 받는다
        end = date.today() - timedelta(days=2)
        start = end - timedelta(days=6)

    fetched_at = datetime.now().isoformat(timespec="seconds")
    rows = []
    failed = []
    day = start
    while day <= end:
        day_rows = fetch_day(key, day, fetched_at)
        print(f"{day}: {len(day_rows)}건")
        if not day_rows:
            failed.append(day.isoformat())
        rows.extend(day_rows)
        day += timedelta(days=1)
        time.sleep(0.2)  # 서버에 연달아 요청하지 않도록 잠깐 쉼

    # 오류를 정상 데이터처럼 저장하지 않는다
    if not rows:
        sys.exit("받은 데이터가 없습니다. 키와 날짜를 확인하세요.")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"boxoffice_{start:%Y%m%d}_{end:%Y%m%d}.csv"
    # utf-8-sig: 엑셀에서 열어도 한글이 깨지지 않음
    pd.DataFrame(rows).to_csv(out, index=False, encoding="utf-8-sig")

    days = (end - start).days + 1
    print(f"저장 완료: {out}")
    print(f"요청한 날짜 {days}일, 받은 날짜 {days - len(failed)}일, 총 {len(rows)}건, 수집 시각 {fetched_at}")
    if failed:
        print(f"받지 못한 날짜: {', '.join(failed)}")


if __name__ == "__main__":
    main()
