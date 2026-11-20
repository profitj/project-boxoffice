"""수집 2: 공휴일과 영화 상세정보를 받아 CSV로 저장한다. (collect.py 를 먼저 실행한 뒤 실행)

사용법
    python collect_extra.py              # 공휴일과 상세정보 둘 다
    python collect_extra.py holidays     # 공휴일만
    python collect_extra.py movies       # 상세정보만

수집한 박스오피스 CSV(data/raw 또는 data/sample 의 boxoffice_*.csv)를 읽어,
그 안의 날짜 범위와 영화코드에 맞춰 받는다. 파일 이름과 컬럼은 clean.py 에 맞췄다.
    holidays_시작_끝.csv   : date, holiday_name, is_holiday
    movie_info.csv         : movie_cd, genre_nm, nation_nm, watch_grade_nm
"""
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote

import pandas as pd
import requests
from dotenv import load_dotenv

HOLIDAY_URL = "https://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeInfo"
MOVIE_URL = "http://www.kobis.or.kr/kobisopenapi/webservice/rest/movie/searchMovieInfo.json"
OUT_DIR = Path("data/raw")
RAW_DIR = Path("data/raw/json")   # 원본 응답 (.gitignore 에 있어 올라가지 않음)


def get(url: str, params: dict, label: str):
    """요청을 보내고 응답을 돌려준다. 실패하면 None. 오류에는 주소(키 포함)를 출력하지 않는다."""
    try:
        res = requests.get(url, params=params, timeout=10)
    except requests.RequestException as e:
        print(f"  {label} 요청 실패: {type(e).__name__}")
        return None
    if res.status_code != 200:
        print(f"  {label} HTTP 상태코드 {res.status_code}")
        return None
    return res


def load_boxoffice() -> pd.DataFrame:
    """수집해 둔 박스오피스 CSV를 읽는다. data/raw 에 있으면 그것을, 없으면 data/sample 을 쓴다."""
    for folder in (Path("data/raw"), Path("data/sample")):
        files = sorted(folder.glob("boxoffice_*.csv"))
        if files:
            print(f"박스오피스 파일 {len(files)}개 읽음 ({folder})")
            frames = [pd.read_csv(f, dtype=str, encoding="utf-8-sig") for f in files]
            return pd.concat(frames, ignore_index=True)
    sys.exit("boxoffice_*.csv 가 없습니다. collect.py 를 먼저 실행하세요.")


# ---------------------------------------------------------------- 공휴일
def parse_holiday_items(res) -> list[dict] | None:
    """특일 정보 응답에서 item 목록을 꺼낸다. JSON이 아니면 XML로 읽는다. 오류면 None."""
    try:
        body = res.json()["response"]
        code = str(body.get("header", {}).get("resultCode", "00"))
        if code != "00":
            print(f"  API resultCode {code}: {body.get('header', {}).get('resultMsg')}")
            return None
        items = body.get("body", {}).get("items") or {}
        item = items.get("item", []) if isinstance(items, dict) else []
        return [item] if isinstance(item, dict) else list(item)   # 한 건이면 목록이 아니라 한 개로 옴
    except (ValueError, KeyError, AttributeError):
        pass
    try:
        root = ET.fromstring(res.text)
    except ET.ParseError:
        print("  응답이 JSON도 XML도 아님")
        return None
    code = root.findtext(".//resultCode") or root.findtext(".//returnReasonCode") or "00"
    if code != "00":
        print(f"  API resultCode {code}: {root.findtext('.//resultMsg') or root.findtext('.//returnAuthMsg')}")
        return None
    return [{child.tag: child.text for child in item} for item in root.iter("item")]


def collect_holidays(box: pd.DataFrame) -> None:
    key = os.getenv("DATA_GO_KR_API_KEY")
    if not key:
        print("공휴일 건너뜀: .env 에 DATA_GO_KR_API_KEY 가 없습니다.")
        return
    # Encoding 키(%2B, %3D 같은 기호가 섞인 형태)를 넣어도 Decoding 키로 바꿔서 쓴다
    key = unquote(key.strip())
    start, end = box["date"].min(), box["date"].max()
    months = pd.period_range(start, end, freq="M")
    rows, failed = [], []
    for m in months:
        label = f"{m.year}-{m.month:02d}"
        params = {"ServiceKey": key, "solYear": m.year, "solMonth": f"{m.month:02d}",
                  "numOfRows": 100, "_type": "json"}
        res = get(HOLIDAY_URL, params, label)
        items = parse_holiday_items(res) if res is not None else None
        if items is None:
            failed.append(label)
            continue
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        (RAW_DIR / f"holidays_{m.year}{m.month:02d}.txt").write_text(res.text, encoding="utf-8")
        for it in items:
            d = str(it.get("locdate", ""))
            rows.append({"date": f"{d[:4]}-{d[4:6]}-{d[6:8]}",
                         "holiday_name": it.get("dateName"),
                         "is_holiday": it.get("isHoliday")})
        print(f"{label}: {len(items)}건")
        time.sleep(0.2)

    df = pd.DataFrame(rows, columns=["date", "holiday_name", "is_holiday"])
    df = df[(df["date"] >= start) & (df["date"] <= end)].sort_values("date")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"holidays_{start.replace('-', '')}_{end.replace('-', '')}.csv"
    if failed and df.empty:
        print(f"공휴일을 받지 못했습니다. 실패한 달: {', '.join(failed)}")
        return
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"저장 완료: {out} / 요청한 달 {len(months)}개, 받은 달 {len(months) - len(failed)}개, 공휴일 {len(df)}건")
    if failed:
        print(f"받지 못한 달: {', '.join(failed)}")


# ---------------------------------------------------------------- 영화 상세정보
def names(info: dict, list_key: str, name_key: str) -> str:
    """장르처럼 여러 개가 오는 값을 '/'로 이어 붙인다."""
    values = [str(x.get(name_key, "")).strip() for x in info.get(list_key, [])]
    values = [v for v in values if v]
    return "/".join(dict.fromkeys(values))   # 순서를 지키며 중복 제거


def last_name(info: dict, list_key: str, name_key: str) -> str:
    """관람등급처럼 심의를 다시 받아 여러 개가 오는 값은 가장 마지막(최근) 것만 쓴다."""
    values = [str(x.get(name_key, "")).strip() for x in info.get(list_key, [])]
    values = [v for v in values if v]
    return values[-1] if values else ""


def collect_movies(box: pd.DataFrame) -> None:
    key = os.getenv("KOBIS_API_KEY")
    if not key:
        print("상세정보 건너뜀: .env 에 KOBIS_API_KEY 가 없습니다.")
        return
    codes = sorted(box["movie_cd"].dropna().unique())
    print(f"영화 {len(codes)}편의 상세정보를 받습니다. (호출 {len(codes)}번, 하루 한도 3,000번)")
    rows, failed = [], []
    for i, cd in enumerate(codes, 1):
        res = get(MOVIE_URL, {"key": key, "movieCd": cd}, cd)
        info = None
        if res is not None:
            try:
                data = res.json()
            except ValueError:
                data = {}
                print(f"  {cd} 응답이 JSON이 아님")
            if "faultInfo" in data:
                print(f"  {cd} API 오류: {data['faultInfo'].get('message')}")
            else:
                info = data.get("movieInfoResult", {}).get("movieInfo") or None
        if not info:
            failed.append(cd)
            continue
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        (RAW_DIR / f"movie_{cd}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        rows.append({"movie_cd": cd,
                     "genre_nm": names(info, "genres", "genreNm"),
                     "nation_nm": names(info, "nations", "nationNm"),
                     "watch_grade_nm": last_name(info, "audits", "watchGradeNm")})
        if i % 20 == 0:
            print(f"  {i}/{len(codes)}편 진행")
        time.sleep(0.2)

    if not rows:
        print("상세정보를 받지 못했습니다. 키를 확인하세요.")
        return
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "movie_info.csv"
    pd.DataFrame(rows).to_csv(out, index=False, encoding="utf-8-sig")
    print(f"저장 완료: {out} / 요청 {len(codes)}편, 받은 영화 {len(rows)}편, 실패 {len(failed)}편")
    if failed:
        print(f"받지 못한 영화코드: {', '.join(failed)}")


def main() -> None:
    load_dotenv()
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what not in ("all", "holidays", "movies"):
        sys.exit("사용법: python collect_extra.py [holidays|movies]")
    box = load_boxoffice()
    if what in ("all", "holidays"):
        collect_holidays(box)
    if what in ("all", "movies"):
        collect_movies(box)


if __name__ == "__main__":
    main()
