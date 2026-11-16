"""수집: 영화진흥위원회 일별 박스오피스를 날짜별로 받아 CSV로 저장한다.

사용법
    python collect.py                      # 그저께까지 최근 7일
    python collect.py 20261101 20261107    # 시작일 종료일 직접 지정
"""
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

URL = "http://www.kobis.or.kr/kobisopenapi/webservice/rest/boxoffice/searchDailyBoxOfficeList.json"
OUT_DIR = Path("data/sample")

# API가 주는 이름 -> 우리가 쓸 컬럼 이름 (회의에서 확정하면 오른쪽만 고치면 됨)
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


def fetch_day(key: str, day: date) -> list[dict]:
    """하루치 박스오피스(상위 10편)를 받아 행 목록으로 돌려준다. 실패하면 빈 목록."""
    params = {"key": key, "targetDt": day.strftime("%Y%m%d")}
    try:
        res = requests.get(URL, params=params, timeout=10)
        res.raise_for_status()
        data = res.json()
    except (requests.RequestException, ValueError) as e:
        print(f"  {day} 요청 실패: {e}")
        return []

    # 키가 틀리거나 한도를 넘으면 정상 응답 대신 faultInfo가 온다
    if "faultInfo" in data:
        print(f"  {day} API 오류: {data['faultInfo'].get('message')}")
        return []

    movies = data.get("boxOfficeResult", {}).get("dailyBoxOfficeList", [])
    rows = []
    for m in movies:
        row = {"date": day.isoformat()}
        for api_name, our_name in COLUMNS.items():
            row[our_name] = m.get(api_name)
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

    rows = []
    day = start
    while day <= end:
        day_rows = fetch_day(key, day)
        print(f"{day}: {len(day_rows)}건")
        rows.extend(day_rows)
        day += timedelta(days=1)
        time.sleep(0.2)  # 서버에 연달아 요청하지 않도록 잠깐 쉼

    if not rows:
        sys.exit("받은 데이터가 없습니다. 키와 날짜를 확인하세요.")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"boxoffice_{start:%Y%m%d}_{end:%Y%m%d}.csv"
    # utf-8-sig: 엑셀에서 열어도 한글이 깨지지 않음
    pd.DataFrame(rows).to_csv(out, index=False, encoding="utf-8-sig")
    print(f"저장 완료: {out} (총 {len(rows)}건)")


if __name__ == "__main__":
    main()
