"""clean.py 값 확인 테스트

실행 (프로젝트 맨 위 폴더에서)
    pytest -v

A. 함수 테스트  : 일부러 문제를 넣은 작은 표로 clean.py 함수가 제대로 고치는지 확인 (데이터 파일 없이 실행됨)
B. 결과 테스트  : clean.py가 만든 data/clean/boxoffice_clean.csv 가 규칙을 지키는지 확인
                 (먼저 python clean.py 를 실행해야 함. 파일이 없으면 건너뜀)
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import clean  # noqa: E402


def make_box(rows):
    cols = ["date", "rank", "movie_cd", "movie_nm", "open_dt", "sales_amt", "audi_cnt", "scrn_cnt", "show_cnt"]
    return pd.DataFrame(rows, columns=cols).astype(str)


ROW_A = ["2026-11-01", "1", "20260001", "영화A", "2026-10-01", "1000000", "100", "50", "200"]
ROW_B = ["2026-11-01", "2", "20260002", "영화B", "2015-01-01", "500000", "50", "20", "80"]


# ── A. 함수 테스트 ─────────────────────────────

def test_완전히_같은_중복은_하나만_남긴다():
    log = {}
    out = clean.clean_boxoffice(make_box([ROW_A, ROW_A, ROW_B]), log)
    assert len(out) == 2
    assert log["dup_exact"] == 1


def test_키는_같고_값이_다르면_마지막_값을_남긴다():
    fixed = ROW_A.copy()
    fixed[6] = "120"  # 관객수만 다름 (보정된 값)
    log = {}
    out = clean.clean_boxoffice(make_box([ROW_A, fixed]), log)
    assert len(out) == 1
    assert log["dup_key"] == 1
    assert out["audi_cnt"].iloc[0] == 120


def test_필수값이_비면_지우고_0은_지우지_않는다():
    empty = ROW_B.copy()
    empty[6] = ""       # 관객수 빈칸 → 지움
    zero = ["2026-11-01", "3", "20260003", "영화C", "2026-10-01", "0", "0", "1", "1"]  # 관객수 0 → 남김
    log = {}
    out = clean.clean_boxoffice(make_box([ROW_A, empty, zero]), log)
    assert log["na"] == 1
    assert "20260003" in set(out["movie_cd"])


def test_날짜_두_형식_모두_읽는다():
    s = clean.to_date(pd.Series(["2026-11-01", "20261101", "날짜아님"]))
    assert s.iloc[0] == s.iloc[1] == pd.Timestamp("2026-11-01")
    assert pd.isna(s.iloc[2])


def test_쉼표가_있는_숫자도_읽는다():
    assert clean.to_number(pd.Series(["1,234"])).iloc[0] == 1234


def test_결합해도_행_수가_그대로이고_실패도_센다():
    log = {}
    box = clean.clean_boxoffice(make_box([ROW_A, ROW_B]), log)
    info = clean.clean_info(pd.DataFrame({"movie_cd": ["20260001", "20260001"], "genre_nm": ["드라마", "드라마"]}), log)
    merged = clean.merge_all(box, info, None, log)
    assert len(merged) == 2                       # 상세정보 중복이 있어도 행이 늘지 않음
    assert log["info_ok"] == 1 and log["info_fail"] == 1
    assert merged.loc[merged["movie_cd"] == "20260002", "genre_nm"].iloc[0] == "정보없음"


def test_공휴일과_재개봉_표시():
    log = {}
    box = clean.clean_boxoffice(make_box([ROW_A, ROW_B]), log)
    clean.clean_info(None, log)
    hol = clean.clean_holidays(pd.DataFrame({"locdate": ["20261101"], "dateName": ["테스트휴일"], "isHoliday": ["Y"]})
                               .rename(columns=clean.RENAME), log)
    merged = clean.merge_all(box, None, hol, log)
    assert merged["is_holiday"].all()
    assert (merged["day_type"] == "공휴일").all()
    assert merged.loc[merged["movie_cd"] == "20260002", "is_rerelease"].iloc[0]   # 2015년 개봉
    assert not merged.loc[merged["movie_cd"] == "20260001", "is_rerelease"].iloc[0]


def test_상영_1회당_관객수_계산():
    zero_show = ["2026-11-01", "3", "20260003", "영화C", "2026-10-01", "1000", "10", "1", "0"]  # 상영 0회
    log = {}
    box = clean.clean_boxoffice(make_box([ROW_A, zero_show]), log)
    clean.clean_info(None, log)
    merged = clean.merge_all(box, None, None, log)
    a = merged.set_index("movie_cd")["audi_per_show"]
    assert a["20260001"] == 0.5          # 관객 100명 ÷ 상영 200회
    assert pd.isna(a["20260003"])        # 상영 0회면 나누지 않고 빈칸


# ── B. 결과 테스트 ─────────────────────────────

OUT = ROOT / "data" / "clean" / "boxoffice_clean.csv"


@pytest.fixture(scope="module")
def df():
    if not OUT.exists():
        pytest.skip("data/clean/boxoffice_clean.csv 가 없습니다. 먼저 python clean.py 를 실행하세요.")
    return pd.read_csv(OUT, dtype={"movie_cd": str})


def test_키_중복_없음(df):
    assert not df.duplicated(subset=["date", "movie_cd"]).any()


def test_필수값_빈칸_없음(df):
    assert df[["date", "movie_cd", "audi_cnt"]].notna().all().all()


def test_관객수와_매출액은_0_이상(df):
    assert (df["audi_cnt"] >= 0).all()
    assert (df["sales_amt"] >= 0).all()


def test_순위는_1부터_10(df):
    assert df["rank"].between(1, 10).all()


def test_하루에_10편_이하(df):
    assert (df.groupby("date").size() <= 10).all()


def test_같은_날_순위가_겹치지_않음(df):
    assert not df.duplicated(subset=["date", "rank"]).any()


def test_평균_관람료가_상식적인_범위(df):
    price = df["avg_price"].dropna()
    # 너무 넓게 잡으면 의미가 없고, 좁게 잡으면 특별관 때문에 실패함. 실제 데이터를 보고 조정하기
    assert price.between(1000, 50000).all(), price[~price.between(1000, 50000)]


def test_상영_1회당_관객수가_맞게_계산됨(df):
    ok = df.dropna(subset=["audi_per_show"])
    assert ((ok["audi_cnt"] / ok["show_cnt"]).round(1) - ok["audi_per_show"]).abs().max() < 0.01
    assert (ok["audi_per_show"] >= 0).all()
