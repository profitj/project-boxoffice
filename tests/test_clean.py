"""정리·결합: 중복·결측 처리, 날짜·영화코드로 결합, 상영 1회당 관객 수 계산.

서비스 질문: 멀티플렉스 편성 담당자가 개봉 2주차에 어느 영화의 상영 횟수를 줄이고, 어느 영화를 유지할까?

collect.py가 받은 박스오피스(+ 상세정보, 공휴일)를 정리하고 합쳐서 저장한다.

사용법 (프로젝트 맨 위 폴더에서)
    python clean.py                 # 정리·결합 + 건수 검증표 만들기
    python clean.py --make-sample   # 위 작업 + data/sample 에 3일치 샘플 만들기

읽는 파일 (data/raw 를 먼저 찾고, 없으면 data/sample)
    boxoffice_*.csv    박스오피스  (필수. 여러 개면 전부 합쳐서 씀)
    movie_info*.csv    영화 상세정보 (있으면 영화코드로 결합)
    holidays*.csv      공휴일       (있으면 날짜로 결합)

만드는 파일
    data/clean/boxoffice_clean.csv   서버(api.py)와 화면(app.py)이 쓰는 정리된 표
    reports/count_check.md               건수 검증표
    reports/unmatched_movies.csv         상세정보와 매칭에 실패한 행

행 하나 = 영화 1편 × 날짜 1일  (키: date + movie_cd)
"""
import sys
from pathlib import Path

import pandas as pd

# ─────────────────────────────────────────────
# 0. 설정 (파일 위치나 규칙이 바뀌면 여기만 고친다)
# ─────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent             # 프로젝트 맨 위 폴더 (clean.py가 있는 곳)
RAW_DIR = ROOT / "data" / "raw"
SAMPLE_DIR = ROOT / "data" / "sample"
OUT_DIR = ROOT / "data" / "clean"
REPORT_DIR = ROOT / "reports"

BOX_PATTERN = "boxoffice_*.csv"
INFO_PATTERN = "movie_info*.csv"
HOLIDAY_PATTERN = "holidays*.csv"

KEY = ["date", "movie_cd"]                          # 행 하나를 구분하는 값
REQUIRED = ["date", "movie_cd", "audi_cnt"]         # 비어 있으면 행을 지우는 컬럼
NUMBERS = ["rank", "sales_amt", "audi_cnt", "scrn_cnt", "show_cnt"]
TOP_N = 10                                          # 일별 박스오피스는 하루 10편
WINDOW = 7                                          # 편성 판단에 쓰는 기간 (최근 7일)
RERELEASE_DAYS = 366                                # 개봉 후 이 날수를 넘으면 재개봉작으로 보고 제거

# API 원래 이름(camelCase)으로 저장된 파일이 와도 우리 이름(snake_case)으로 맞춰 준다
RENAME = {
    "movieCd": "movie_cd", "movieNm": "movie_nm", "openDt": "open_dt",
    "salesAmt": "sales_amt", "audiCnt": "audi_cnt", "scrnCnt": "scrn_cnt", "showCnt": "show_cnt",
    "genreNm": "genre_nm", "nationNm": "nation_nm", "watchGradeNm": "watch_grade_nm",
    "targetDt": "date",
    # 공휴일 (한국천문연구원 특일정보 API 이름)
    "locdate": "date", "dateName": "holiday_name", "date_name": "holiday_name",
    "isHoliday": "is_holiday",
}
INFO_COLS = ["genre_nm", "nation_nm", "watch_grade_nm"]


# ─────────────────────────────────────────────
# 1. 파일 찾기와 읽기
# ─────────────────────────────────────────────
def pick_folder():
    """data/raw 에 박스오피스가 있으면 raw, 없으면 sample"""
    for folder, name in [(RAW_DIR, "실제 수집 데이터 (data/raw)"), (SAMPLE_DIR, "샘플 데이터 (data/sample)")]:
        if list(folder.glob(BOX_PATTERN)):
            return folder, name
    sys.exit("❌ data/raw 와 data/sample 어디에도 boxoffice_*.csv 가 없습니다. collect.py를 먼저 실행하세요.")


def read_all(folder, pattern):
    """패턴에 맞는 파일을 모두 읽어 하나로 합친다. 파일이 없으면 None"""
    files = sorted(folder.glob(pattern))
    if not files:
        return None, []
    # 모든 값을 일단 문자로 읽는다 (영화코드·날짜가 숫자로 바뀌는 것을 막기 위해)
    frames = [pd.read_csv(f, dtype=str).rename(columns=RENAME) for f in files]
    return pd.concat(frames, ignore_index=True), [f.name for f in files]


def to_date(series):
    """'2026-11-01' 과 '20261101' 둘 다 날짜로 바꾼다. 날짜가 아니면 빈칸(NaT)"""
    s = series.astype(str).str.strip().str.replace("-", "", regex=False)
    return pd.to_datetime(s, format="%Y%m%d", errors="coerce")


def to_number(series):
    """'1,234' 같은 쉼표를 지우고 숫자로. 숫자가 아니면 빈칸(NaN)"""
    return pd.to_numeric(series.astype(str).str.replace(",", "", regex=False).str.strip(), errors="coerce")


# ─────────────────────────────────────────────
# 2. 박스오피스 정리 : 형식 맞추기 → 중복 → 빈칸
# ─────────────────────────────────────────────
def clean_boxoffice(box, log):
    box = box.copy()
    log["box_raw"] = len(box)

    # 형식 맞추기
    box["date"] = to_date(box["date"])
    box["movie_cd"] = box["movie_cd"].str.strip()
    box["movie_nm"] = box["movie_nm"].str.strip()
    for col in NUMBERS:
        if col in box.columns:
            box[col] = to_number(box[col])

    # 중복 ① 모든 값이 같은 행 (같은 날짜를 두 번 수집했을 때 생김)
    before = len(box)
    box = box.drop_duplicates()
    log["dup_exact"] = before - len(box)

    # 중복 ② 키(날짜+영화코드)는 같은데 값이 다른 행 (나중에 보정된 값) → 가장 나중에 수집한 행만 남김
    #   fetched_at(수집 시각)이 있으면 그 순서로 정렬. 시각이 없는 옛 파일은 가장 오래된 것으로 봄
    if "fetched_at" in box.columns:
        box = box.sort_values("fetched_at", na_position="first", kind="stable")
    log["dup_key_rows"] = box[box.duplicated(subset=KEY, keep=False)]
    before = len(box)
    box = box.drop_duplicates(subset=KEY, keep="last")
    log["dup_key"] = before - len(box)

    # 빈칸 : 필수 컬럼 중 하나라도 비면 제거 (0은 지우지 않는다. 0과 빈칸은 다르다)
    log["na_rows"] = box[box[REQUIRED].isna().any(axis=1)]
    before = len(box)
    box = box.dropna(subset=REQUIRED)
    log["na"] = before - len(box)

    # 재개봉작 제거 : KOBIS는 재개봉작의 개봉일을 원래 개봉일로 주므로, 개봉 후 1년이 넘은 영화를 재개봉작으로 본다
    #   (편성 판단 대상이 아니고, 특별관 소량 상영이라 평균·점유율을 왜곡하므로 정리 단계에서 뺀다. 개봉일이 빈 영화는 남김)
    days = (box["date"] - to_date(box["open_dt"])).dt.days + 1
    is_rerelease = days > RERELEASE_DAYS
    log["rerelease_rows"] = box[is_rerelease]
    log["rerelease"] = int(is_rerelease.sum())
    box = box[~is_rerelease]

    for col in NUMBERS:
        if col in box.columns and box[col].notna().all():
            box[col] = box[col].astype("int64")

    log["box_clean"] = len(box)
    return box


# ─────────────────────────────────────────────
# 3. 영화 상세정보 정리 (영화 1편 = 1행)
# ─────────────────────────────────────────────
def clean_info(info, log):
    log["info_file"] = info is not None
    if info is None:
        log["info_raw"] = log["info_dup"] = log["info_clean"] = 0
        return None

    info = info.copy()
    log["info_raw"] = len(info)
    info["movie_cd"] = info["movie_cd"].str.strip()
    info = info.dropna(subset=["movie_cd"])

    before = len(info)
    info = info.drop_duplicates(subset=["movie_cd"], keep="last")
    log["info_dup"] = before - len(info)

    keep = ["movie_cd"] + [c for c in INFO_COLS if c in info.columns]
    info = info[keep]
    for col in keep[1:]:
        info[col] = info[col].fillna("미상").replace("", "미상")
    log["info_clean"] = len(info)
    return info


# ─────────────────────────────────────────────
# 4. 공휴일 정리 (날짜 1일 = 1행)
# ─────────────────────────────────────────────
def clean_holidays(hol, log):
    log["hol_file"] = hol is not None
    if hol is None:
        return None

    hol = hol.copy()
    if "is_holiday" in hol.columns:                      # 특일정보 API는 Y/N 으로 표시
        hol = hol[hol["is_holiday"].astype(str).str.upper().isin(["Y", "TRUE", "1"])]
    hol["date"] = to_date(hol["date"])
    hol = hol.dropna(subset=["date"])
    if "holiday_name" not in hol.columns:
        hol["holiday_name"] = "공휴일"
    # 같은 날짜에 이름이 둘이면(예: 추석 + 대체공휴일) 한 행으로 합친다
    return (hol.groupby("date")["holiday_name"]
               .apply(lambda s: ", ".join(sorted(set(s.astype(str)))))
               .reset_index())


# ─────────────────────────────────────────────
# 5. 결합 + 파생 컬럼
# ─────────────────────────────────────────────
def merge_all(box, info, hol, log):
    merged = box.copy()

    # ① 상세정보 : 영화코드 기준. validate 는 상세정보에 같은 영화코드가 2개면 멈춘다
    if info is not None:
        merged = merged.merge(info, on="movie_cd", how="left", indicator=True, validate="many_to_one")
        log["unmatched"] = merged[merged["_merge"] == "left_only"].drop(columns="_merge")
        merged = merged.drop(columns="_merge")
    else:
        log["unmatched"] = merged.iloc[0:0]
    for col in INFO_COLS:
        merged[col] = merged[col].fillna("정보없음") if col in merged.columns else "정보없음"
    log["info_fail"] = len(log["unmatched"]) if info is not None else 0
    log["info_ok"] = log["box_clean"] - log["info_fail"] if info is not None else 0

    # ② 공휴일 : 날짜 기준. 매칭 안 된 날은 실패가 아니라 "공휴일이 아닌 날"
    if hol is not None:
        merged = merged.merge(hol, on="date", how="left", validate="many_to_one")
    else:
        merged["holiday_name"] = pd.NA
    merged["is_holiday"] = merged["holiday_name"].notna()
    merged["holiday_name"] = merged["holiday_name"].fillna("")

    # ③ 파생 컬럼
    dow = merged["date"].dt.dayofweek
    merged["weekday"] = dow.map(dict(enumerate("월화수목금토일")))
    merged["day_type"] = "평일"
    merged.loc[dow >= 5, "day_type"] = "주말"
    merged.loc[merged["is_holiday"], "day_type"] = "공휴일"
    # 상영 1회당 관객 수 = 관객 수 ÷ 상영 횟수 (상영 횟수가 0이거나 비어 있으면 계산하지 않음)
    if "show_cnt" in merged.columns:
        merged["audi_per_show"] = (merged["audi_cnt"] / merged["show_cnt"].where(merged["show_cnt"] > 0)).round(1)
    else:
        merged["audi_per_show"] = pd.NA
    # (검증용) 평균 관람료 = 매출액 ÷ 관객수. 특별관처럼 튀는 값을 찾는 데 씀
    merged["avg_price"] = (merged["sales_amt"] / merged["audi_cnt"].where(merged["audi_cnt"] > 0)).round(0)
    # 개봉 경과일과 주차 : 개봉일 = 1일째, 1~7일째 = 1주차, 8~14일째 = 2주차
    # (개봉일 전 유료 시사회 상영은 0 이하가 되어 0주차로 표시. 개봉일이 비어 있으면 빈칸)
    days = (merged["date"] - to_date(merged["open_dt"])).dt.days + 1
    merged["days_since_open"] = days.astype("Int64")
    merged["week_since_open"] = ((days.clip(lower=0) + 6) // 7).astype("Int64")

    # 같은 날 Top10 안에서의 비중 (%) : 상영을 얼마나 받았나 vs 관객을 얼마나 모았나
    if "show_cnt" in merged.columns:
        day_show = merged.groupby("date")["show_cnt"].transform("sum")
        day_audi = merged.groupby("date")["audi_cnt"].transform("sum")
        merged["show_share"] = (merged["show_cnt"] / day_show.where(day_show > 0) * 100).round(1)
        merged["audi_share"] = (merged["audi_cnt"] / day_audi.where(day_audi > 0) * 100).round(1)
        # 상영 효율 지수 = 이 영화의 상영 1회당 관객 수 ÷ 그날 Top10 전체의 상영 1회당 관객 수
        # 1보다 크면 평균보다 상영 1회에 관객이 많이 든 영화, 1보다 작으면 적게 든 영화
        day_per_show = day_audi / day_show.where(day_show > 0)
        # 반올림하기 전 값으로 나눠야 오차가 생기지 않는다
        raw_per_show = merged["audi_cnt"] / merged["show_cnt"].where(merged["show_cnt"] > 0)
        merged["efficiency"] = (raw_per_show / day_per_show).round(2)

    merged = add_window_columns(merged)
    log["merged"] = len(merged)
    return merged


def add_window_columns(merged, window=WINDOW):
    """각 행(영화 × 날짜)에 '이번 주(최근 7일)'와 '전주(그 직전 7일)' 값을 붙인다.

    이번 주 = 기준일 포함 최근 7일 (기준일 11/14 → 11/8~11/14)
    전주     = 그 직전 7일            (기준일 11/14 → 11/1~11/7)
    두 기간 모두 연속 7일이라 주말이 한 번씩 들어가 요일 영향이 같다.

    - audi_avg_7d       : 이번 주 하루 평균 관객 수 = 이번 주 관객 합 ÷ 이번 주 Top10에 든 날 수
    - audi_avg_prev7d   : 전주 하루 평균 관객 수
    - audi_change_7d    : 전주 대비 증감률(%) = (이번 주 ÷ 전주 − 1) × 100. 전주 데이터가 없으면 빈칸
    - days_in_7d / days_in_prev7d : 각 주에 Top10에 든 날 수 (적으면 평균을 믿기 어려움)
    - efficiency_7d     : 이번 주 상영 효율 지수 (같은 7일 Top10 평균 대비, 참고용)
    """
    cols = ["days_in_7d", "window_days_7d", "audi_7d", "show_7d", "audi_avg_7d", "audi_per_show_7d", "efficiency_7d",
            "days_in_prev7d", "window_days_prev7d", "audi_prev7d", "audi_avg_prev7d", "audi_change_7d"]
    if "show_cnt" not in merged.columns:
        for c in cols:
            merged[c] = pd.NA
        return merged

    merged = merged.copy()
    all_dates = set(merged["date"].dropna().unique())

    def window_stats(end):
        start = end - pd.Timedelta(days=window - 1)
        win = merged[(merged["date"] >= start) & (merged["date"] <= end)]
        per_movie = win.groupby("movie_cd").agg(days=("date", "count"), audi=("audi_cnt", "sum"), show=("show_cnt", "sum"))
        total_show = win["show_cnt"].sum()
        base = win["audi_cnt"].sum() / total_show if total_show > 0 else float("nan")
        n_days = sum(1 for d in pd.date_range(start, end) if d in all_dates)
        return per_movie, base, n_days

    rows = []
    for d in sorted(all_dates):
        cur, base, n_cur = window_stats(d)
        prev, _, n_prev = window_stats(d - pd.Timedelta(days=window))
        today = merged.loc[merged["date"] == d, ["movie_cd"]]
        today = today.join(cur.add_suffix("_c"), on="movie_cd").join(prev.add_suffix("_p"), on="movie_cd")
        today["base"], today["window_days_7d"], today["window_days_prev7d"] = base, n_cur, n_prev
        rows.append(today)

    w = pd.concat(rows)
    w["days_in_7d"], w["audi_7d"], w["show_7d"] = w["days_c"], w["audi_c"], w["show_c"]
    w["audi_avg_7d"] = (w["audi_c"] / w["days_c"]).round(0)
    w["audi_per_show_7d"] = (w["audi_c"] / w["show_c"].where(w["show_c"] > 0)).round(1)
    w["efficiency_7d"] = ((w["audi_c"] / w["show_c"].where(w["show_c"] > 0)) / w["base"]).round(2)
    w["days_in_prev7d"] = w["days_p"].fillna(0)
    w["audi_prev7d"] = w["audi_p"]
    w["audi_avg_prev7d"] = (w["audi_p"] / w["days_p"]).round(0)
    w["audi_change_7d"] = ((w["audi_c"] / w["days_c"]) / (w["audi_p"] / w["days_p"]) * 100 - 100).round(1)
    for c in cols:
        merged[c] = w[c]
    for c in ["days_in_7d", "window_days_7d", "audi_7d", "show_7d", "days_in_prev7d", "window_days_prev7d", "audi_prev7d"]:
        merged[c] = merged[c].astype("Int64")
    for c in ["audi_avg_7d", "audi_avg_prev7d"]:
        merged[c] = merged[c].astype("Int64")
    return merged

    merged = merged.copy()
    rows = []
    for d in sorted(merged["date"].dropna().unique()):
        start = d - pd.Timedelta(days=window - 1)
        win = merged[(merged["date"] >= start) & (merged["date"] <= d)]
        total_audi, total_show = win["audi_cnt"].sum(), win["show_cnt"].sum()
        base = total_audi / total_show if total_show > 0 else float("nan")
        per_movie = win.groupby("movie_cd").agg(
            days_in_7d=("date", "count"), audi_7d=("audi_cnt", "sum"), show_7d=("show_cnt", "sum"))
        today = merged.loc[merged["date"] == d, ["movie_cd"]].join(per_movie, on="movie_cd")
        today["window_days_7d"] = win["date"].nunique()
        today["base"] = base
        rows.append(today)

    w = pd.concat(rows)
    w["audi_per_show_7d"] = (w["audi_7d"] / w["show_7d"].where(w["show_7d"] > 0)).round(1)
    w["efficiency_7d"] = ((w["audi_7d"] / w["show_7d"].where(w["show_7d"] > 0)) / w["base"]).round(2)
    for c in cols:
        merged[c] = w[c]
    for c in ["days_in_7d", "window_days_7d", "audi_7d", "show_7d"]:
        merged[c] = merged[c].astype("Int64")
    return merged


# ─────────────────────────────────────────────
# 6. 건수 확인 + 검증표 저장
# ─────────────────────────────────────────────
def check_counts(log):
    """규칙이 깨지면 여기서 멈춘다 (틀린 결과를 조용히 저장하지 않도록)"""
    assert log["box_raw"] - log["dup_exact"] - log["dup_key"] - log["na"] - log["rerelease"] == log["box_clean"], "정리 단계 건수가 맞지 않음"
    assert log["merged"] == log["box_clean"], "결합 후 행 수가 바뀜 (상세정보나 공휴일에 중복이 있는지 확인)"
    if log["info_file"]:
        assert log["box_clean"] == log["info_ok"] + log["info_fail"], "매칭 성공+실패가 전체와 다름"


def write_report(merged, log, source_name, files):
    days = merged["date"].dt.date.nunique()
    start, end = merged["date"].min().date(), merged["date"].max().date()
    all_days = pd.date_range(start, end).date
    missing_days = [d for d in all_days if d not in set(merged["date"].dt.date)]
    with_rr = pd.concat([merged[["date"]], log["rerelease_rows"][["date"]]])
    per_day = with_rr.groupby(with_rr["date"].dt.date).size()
    rr = log["rerelease_rows"]
    rr_names = ", ".join(sorted(rr["movie_nm"].dropna().unique())) if len(rr) else "없음"
    odd_days = per_day[per_day != TOP_N]

    def zero(n, how):
        return f"0건 — 확인 방법: {how}" if n == 0 else f"{n}건"

    lines = [
        "# 건수 검증표",
        "",
        f"- 데이터: {source_name}",
        f"- 파일: {', '.join(files['box'])}",
        f"- 기간: {start} ~ {end} ({days}일)",
        "- 행 하나 = 영화 1편 × 날짜 1일 (키: date + movie_cd)",
        "- 이 파일은 `python clean.py`를 실행할 때마다 자동으로 다시 만들어집니다.",
        "",
        "## ① 박스오피스 정리 : 원본 − 제거 = 정리 후",
        "",
        "| 단계 | 건수 | 설명 |",
        "| --- | ---: | --- |",
        f"| 원본 | {log['box_raw']} | 수집 파일 전체 행 수 |",
        f"| 제거: 완전히 같은 중복 | -{log['dup_exact']} | 같은 날짜를 두 번 수집한 경우 |",
        f"| 제거: 키 중복 (값이 다름) | -{log['dup_key']} | 같은 날짜·영화인데 값이 다른 행. 가장 나중에 수집한 값(fetched_at 기준)만 남김 |",
        f"| 제거: 필수값 빈칸 | -{log['na']} | {', '.join(REQUIRED)} 중 하나라도 빈 행 |",
        f"| 제거: 재개봉작 | -{log['rerelease']} | 개봉 후 {RERELEASE_DAYS}일 초과 ({rr_names}) |",
        f"| **정리 후** | **{log['box_clean']}** | |",
        "",
        f"- 기대 행 수: {days}일 × {TOP_N}편 − 재개봉작 {log['rerelease']}행 = {days * TOP_N - log['rerelease']}행 → 실제 {log['box_clean']}행",
        f"- 재개봉작을 빼기 전 하루 {TOP_N}편이 아닌 날: " + (", ".join(f"{d}({n}편)" for d, n in odd_days.items()) if len(odd_days) else "없음"),
        "- 기간 중 빠진 날짜: " + (", ".join(str(d) for d in missing_days) if missing_days else "없음"),
        "",
        "## ② 영화 상세정보 결합 (영화코드 기준) : 전체 = 성공 + 실패",
        "",
    ]
    if log["info_file"]:
        names = ", ".join(sorted(log["unmatched"]["movie_nm"].dropna().unique())) or "-"
        lines += [
            "| 전체 | 매칭 성공 | 매칭 실패 | 실패한 영화 |",
            "| ---: | ---: | ---: | --- |",
            f"| {log['box_clean']} | {log['info_ok']} | {log['info_fail']} | {names} |",
            "",
            f"- 상세정보 원본 {log['info_raw']}편 − 중복 {log['info_dup']}편 = {log['info_clean']}편",
            "- 매칭 실패 행은 지우지 않고 장르·국가를 \"정보없음\"으로 표시 (목록: reports/unmatched_movies.csv)",
        ]
    else:
        lines += ["- ⚠️ 상세정보 파일(movie_info*.csv)이 아직 없어 결합하지 않았습니다. 장르·국가는 \"정보없음\"."]
    lines += ["", "## ③ 공휴일 결합 (날짜 기준)", ""]
    if log["hol_file"]:
        hol_days = merged.loc[merged["is_holiday"], "date"].dt.date.nunique()
        hol_list = merged.loc[merged["is_holiday"], ["date", "holiday_name"]].drop_duplicates()
        lines += [
            "| 전체 날짜 | 공휴일 | 공휴일 아님 |",
            "| ---: | ---: | ---: |",
            f"| {days} | {hol_days} | {days - hol_days} |",
            "",
            "- 공휴일과 매칭되지 않은 날은 실패가 아니라 \"공휴일이 아닌 날\"입니다.",
            "- 이 기간 공휴일: " + (", ".join(f"{r.date.date()} {r.holiday_name}" for r in hol_list.itertuples())
                                 if hol_days else "없음 (공휴일 파일에서 이 기간 날짜를 검색해 확인)"),
        ]
    else:
        lines += ["- ⚠️ 공휴일 파일(holidays*.csv)이 아직 없어 모든 날을 공휴일이 아닌 날로 처리했습니다."]
    lines += [
        "",
        "## ④ 상영 1회당 관객 수 계산",
        "",
        f"- 계산한 행: {int(merged['audi_per_show'].notna().sum())} / 계산 못 한 행(상영 횟수 0 또는 빈칸): {int(merged['audi_per_show'].isna().sum())}",
        "- 계산식: audi_per_show = audi_cnt ÷ show_cnt (소수 첫째 자리 반올림)",
        "",
        f"## ⑤ 이번 주와 전주 비교 (편성 판단용, {WINDOW}일씩)",
        "",
        "- 이번 주 = 기준일 포함 최근 7일, 전주 = 그 직전 7일",
        f"- 전주 대비 증감률을 계산한 행: {int(merged['audi_change_7d'].notna().sum())} / 계산 못 한 행: {int(merged['audi_change_7d'].isna().sum())} (전주에 Top10 기록이 없거나, 수집 시작 직후라 전주 데이터가 없음)",
        f"- 전주 7일 데이터가 모두 있는 행: {int((merged['window_days_prev7d'] == WINDOW).sum())}",
        f"- 이번 주 Top10 일수가 5일 미만인 행: {int((merged['days_in_7d'] < 5).sum())} (평균을 믿기 어려워 판단 보류 권장)",
        "- 계산식: audi_change_7d = (이번 주 관객 합 ÷ 이번 주 일수) ÷ (전주 관객 합 ÷ 전주 일수) × 100 − 100",
        "",
        "## ⑥ 제거·실패 건수 (0건이면 확인한 방법)",
        "",
        f"- 완전 중복: {zero(log['dup_exact'], '`duplicated()` 결과가 모두 False')}",
        f"- 키 중복: {zero(log['dup_key'], '`duplicated(subset=[date, movie_cd])` 결과가 모두 False')}",
        f"- 필수값 빈칸: {zero(log['na'], '필수 컬럼의 `isna().sum()`이 모두 0')}",
    ]
    if log["info_file"]:
        lines.append(f"- 상세정보 매칭 실패: {zero(log['info_fail'], '결합 결과 `_merge`에 left_only가 없음')}")
    lines.append("")

    REPORT_DIR.mkdir(exist_ok=True)
    (REPORT_DIR / "count_check.md").write_text("\n".join(lines), encoding="utf-8")
    log["unmatched"].to_csv(REPORT_DIR / "unmatched_movies.csv", index=False, encoding="utf-8-sig")
    # 지운 행도 따로 남겨서 나중에 이유를 확인할 수 있게
    removed = pd.concat([log["dup_key_rows"].assign(reason="키 중복"), log["na_rows"].assign(reason="필수값 빈칸"),
                         log["rerelease_rows"].assign(reason="재개봉작")])
    removed.to_csv(REPORT_DIR / "removed_rows.csv", index=False, encoding="utf-8-sig")


# ─────────────────────────────────────────────
# 7. 샘플 만들기 (인증키 없는 팀이 실행할 수 있게)
# ─────────────────────────────────────────────
def make_sample(days=3):
    """data/raw 원본에서 앞쪽 며칠만 잘라 data/sample 에 같은 형식으로 저장"""
    box, _ = read_all(RAW_DIR, BOX_PATTERN)
    if box is None:
        print("⚠️ data/raw 에 원본이 없어 샘플을 만들 수 없습니다.")
        return
    first = sorted(box["date"].dropna().unique())[:days]
    box_s = box[box["date"].isin(first)]
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    box_s.to_csv(SAMPLE_DIR / "boxoffice_sample.csv", index=False, encoding="utf-8-sig")

    info, _ = read_all(RAW_DIR, INFO_PATTERN)
    if info is not None:
        info[info["movie_cd"].isin(box_s["movie_cd"])].to_csv(
            SAMPLE_DIR / "movie_info_sample.csv", index=False, encoding="utf-8-sig")
    hol, _ = read_all(RAW_DIR, HOLIDAY_PATTERN)
    if hol is not None:
        hol.to_csv(SAMPLE_DIR / "holidays_sample.csv", index=False, encoding="utf-8-sig")
    print(f"📦 샘플 저장: data/sample ({len(first)}일, {len(box_s)}행)")


# ─────────────────────────────────────────────
# 전체 실행
# ─────────────────────────────────────────────
def run():
    folder, source_name = pick_folder()
    box_raw, box_files = read_all(folder, BOX_PATTERN)
    info_raw, info_files = read_all(folder, INFO_PATTERN)
    hol_raw, hol_files = read_all(folder, HOLIDAY_PATTERN)
    files = {"box": box_files, "info": info_files, "hol": hol_files}
    print(f"① 읽기: {source_name} / 박스오피스 {len(box_files)}개, 상세정보 {len(info_files)}개, 공휴일 {len(hol_files)}개 파일")

    log = {}
    box = clean_boxoffice(box_raw, log)
    info = clean_info(info_raw, log)
    hol = clean_holidays(hol_raw, log)
    print(f"② 정리: 원본 {log['box_raw']}행 − 중복 {log['dup_exact'] + log['dup_key']} − 빈칸 {log['na']} − 재개봉 {log['rerelease']} = {log['box_clean']}행")

    merged = merge_all(box, info, hol, log)
    check_counts(log)
    if log["info_file"]:
        print(f"③ 결합: 상세정보 매칭 성공 {log['info_ok']} / 실패 {log['info_fail']}")
    else:
        print("③ 결합: 상세정보 파일 없음 → 건너뜀")

    cols = ["date", "weekday", "day_type", "is_holiday", "holiday_name", "rank", "movie_cd", "movie_nm",
            "open_dt", "days_since_open", "week_since_open",
            "genre_nm", "nation_nm", "watch_grade_nm",
            "audi_cnt", "show_cnt", "audi_per_show", "show_share", "audi_share", "efficiency",
            "days_in_7d", "audi_avg_7d", "days_in_prev7d", "audi_avg_prev7d", "audi_change_7d",
            "window_days_7d", "window_days_prev7d", "audi_7d", "show_7d", "audi_prev7d",
            "audi_per_show_7d", "efficiency_7d",
            "sales_amt", "avg_price", "scrn_cnt"]
    out = merged[[c for c in cols if c in merged.columns]].sort_values(["date", "rank"])
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_DIR / "boxoffice_clean.csv", index=False, encoding="utf-8-sig")
    write_report(merged, log, source_name, files)
    print("④ 저장: data/clean/boxoffice_clean.csv, reports/count_check.md")
    return out, log


if __name__ == "__main__":
    run()
    if "--make-sample" in sys.argv:
        make_sample()
    print("✅ 완료")
