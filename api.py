"""FastAPI 서버: 조건을 받아 결과를 돌려준다.

실행 (프로젝트 맨 위 폴더에서)
    uvicorn api:app --reload

확인
    http://127.0.0.1:8000/api/movies?target_date=2026-11-08
    http://127.0.0.1:8000/api/recommendations?target_date=2026-11-14&week=2
    http://127.0.0.1:8000/api/movie-list
    http://127.0.0.1:8000/api/trend?movie_cd=20261807
    http://127.0.0.1:8000/docs   (자동 문서)

데이터 갱신
    clean.py를 다시 실행해 CSV가 바뀌면, 다음 요청 때 자동으로 다시 읽는다 (서버 재시작 불필요).
"""
import logging
import threading
from datetime import date
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, Query

ROOT = Path(__file__).resolve().parent
CLEAN_DIR = ROOT / "data" / "clean"
SAMPLE_DIR = ROOT / "data" / "sample"

NO_DATA_MSG = "조건에 맞는 데이터가 없습니다"

COLUMNS = [
    "date", "rank", "movie_cd", "movie_nm", "open_dt",
    "sales_amt", "audi_cnt", "scrn_cnt", "show_cnt", "audi_per_show",
]

log = logging.getLogger("uvicorn.error")


# ── 데이터 읽기 ─────────────────────────────
def data_files() -> list[Path]:
    """정리된 CSV 목록. data/clean/ 이 비어 있으면 data/sample/ 을 쓴다."""
    return sorted(CLEAN_DIR.glob("*.csv")) or sorted(SAMPLE_DIR.glob("*.csv"))


def read_data(files: list[Path]) -> pd.DataFrame:
    if not files:
        raise FileNotFoundError("data/clean/ 과 data/sample/ 에 CSV 파일이 없습니다.")

    df = pd.concat(
        (pd.read_csv(f, encoding="utf-8-sig", dtype={"movie_cd": str}) for f in files),
        ignore_index=True,
    )
    # 샘플 데이터에는 회당 관객 수가 없으므로 직접 계산
    if "audi_per_show" not in df.columns:
        df["audi_per_show"] = (df["audi_cnt"] / df["show_cnt"].where(df["show_cnt"] > 0)).round(1)

    df = df[COLUMNS].copy()
    day = pd.to_datetime(df["date"])
    opened = pd.to_datetime(df["open_dt"], errors="coerce")
    df["date"] = day.dt.strftime("%Y-%m-%d")
    df["open_dt"] = opened.dt.strftime("%Y-%m-%d")

    # 개봉 주차 = (조회 날짜 - 개봉일) // 7 + 1   (개봉일이 없으면 비워 둠)
    df["open_week"] = ((day - opened).dt.days // 7 + 1).astype("Int64")

    return df.drop_duplicates(subset=["date", "movie_cd"]).reset_index(drop=True)


class DataStore:
    """CSV가 바뀌었을 때만 다시 읽는 캐시.

    - 파일 목록과 수정 시각을 비교해 바뀐 경우에만 다시 읽는다.
    - 여러 요청이 동시에 와도 한 번만 읽도록 잠금(lock)을 건다.
    - 다시 읽다 실패하면 (예: clean.py가 파일을 쓰는 중) 직전 데이터를 계속 쓴다.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._df: pd.DataFrame | None = None
        self._signature = None

    @staticmethod
    def _signature_of(files: list[Path]):
        return tuple((str(f), f.stat().st_mtime_ns, f.stat().st_size) for f in files)

    def get(self) -> pd.DataFrame:
        with self._lock:
            try:
                files = data_files()
                sig = self._signature_of(files)
                if sig != self._signature:
                    self._df = read_data(files)
                    self._signature = sig
                    log.info("데이터를 다시 읽었습니다: %d행", len(self._df))
            except Exception:
                if self._df is None:
                    raise
                log.exception("데이터를 다시 읽지 못해 직전 데이터를 사용합니다.")
            return self._df


store = DataStore()


def to_records(rows: pd.DataFrame) -> list[dict]:
    # NaN/NA는 JSON으로 보낼 수 없으므로 None으로 바꿈
    return rows.astype(object).where(rows.notna(), None).to_dict(orient="records")


def day_average(rows: pd.DataFrame) -> float | None:
    """당일 전체 평균 회당 관객 수 = 전체 관객 수 ÷ 전체 상영 횟수."""
    shows = rows["show_cnt"].sum()
    return round(float(rows["audi_cnt"].sum() / shows), 1) if shows else None


# ── API ─────────────────────────────
app = FastAPI(title="영화 상영 횟수 조정 도우미 API")


@app.get("/api/dates")
def get_dates():
    """조회 가능한 날짜 범위 (화면에서 날짜 선택 범위로 사용)."""
    dates = sorted(store.get()["date"].unique())
    if not dates:
        raise HTTPException(status_code=404, detail=NO_DATA_MSG)
    return {"min_date": dates[0], "max_date": dates[-1], "dates": dates}


@app.get("/api/movies")
def get_movies(target_date: date = Query(..., description="조회할 날짜 (YYYY-MM-DD)")):
    """해당 날짜의 영화 목록(개봉 주차 포함)과 당일 평균 회당 관객 수."""
    df = store.get()
    key = target_date.isoformat()
    rows = df[df["date"] == key].sort_values("rank")
    if rows.empty:
        raise HTTPException(status_code=404, detail=NO_DATA_MSG)

    return {
        "date": key,
        "count": len(rows),
        "day_avg_audi_per_show": day_average(rows),
        "movies": to_records(rows),
    }


BUFFER = 0.1  # 평균 대비 ±10% 구간은 '유지'

DECISIONS = {
    "expand": ("확대 권고", "상영 1회당 관객 수 우수, 상영 횟수 확대 추천"),
    "keep": ("유지 권고", "당일 평균 수준의 상영 효율 유지"),
    "reduce": ("축소 검토", "상영 1회당 관객 수 저조, 상영 횟수 축소 검토"),
}


def decide(audi_per_show: float, avg: float) -> str:
    """회당 관객 수를 당일 평균과 비교해 3단계로 판정한다.

    확대: > avg × 1.1 / 유지: avg × 0.9 이상 avg × 1.1 이하 / 축소: < avg × 0.9
    """
    if audi_per_show > avg * (1 + BUFFER):
        return "expand"
    if audi_per_show < avg * (1 - BUFFER):
        return "reduce"
    return "keep"


@app.get("/api/recommendations")
def get_recommendations(
    target_date: date = Query(..., description="조회할 날짜 (YYYY-MM-DD)"),
    week: int = Query(2, ge=1, description="판단할 개봉 주차 (기본 2주차)"),
):
    """해당 날짜, 해당 개봉 주차 영화의 상영 횟수 편성 권고."""
    df = store.get()
    key = target_date.isoformat()
    day = df[df["date"] == key]
    avg = day_average(day) if not day.empty else None
    if avg is None:
        raise HTTPException(status_code=404, detail=NO_DATA_MSG)

    picked = day[(day["open_week"] == week) & day["audi_per_show"].notna()]
    picked = picked.sort_values("audi_per_show", ascending=False)

    items = []
    for _, m in picked.iterrows():
        code = decide(m["audi_per_show"], avg)
        label, reason = DECISIONS[code]
        items.append({
            "movie_cd": m["movie_cd"],
            "movie_nm": m["movie_nm"],
            "open_dt": m["open_dt"],
            "audi_per_show": float(m["audi_per_show"]),
            "ratio_to_avg": round(float(m["audi_per_show"]) / avg, 2),
            "decision": code,
            "label": label,
            "message": f"[{label}] {m['movie_nm']}: {reason}",
        })

    return {
        "date": key,
        "week": week,
        "day_avg_audi_per_show": avg,
        "upper": round(avg * (1 + BUFFER), 1),
        "lower": round(avg * (1 - BUFFER), 1),
        "count": len(items),
        "recommendations": items,
    }


@app.get("/api/movie-list")
def get_movie_list():
    """수집 기간에 한 번이라도 나온 영화 목록 (누적 관객 수 많은 순)."""
    df = store.get()
    movies = (
        df.groupby("movie_cd", as_index=False)
        .agg(movie_nm=("movie_nm", "last"), total_audi=("audi_cnt", "sum"), days=("date", "nunique"))
        .sort_values("total_audi", ascending=False)
    )
    return {"count": len(movies), "movies": to_records(movies)}


@app.get("/api/trend")
def get_trend(movie_cd: str = Query(..., description="영화코드")):
    """선택한 영화의 날짜별 추이 (당일 평균 회당 관객 수도 함께)."""
    df = store.get()
    rows = df[df["movie_cd"] == movie_cd].sort_values("date")
    if rows.empty:
        raise HTTPException(status_code=404, detail=NO_DATA_MSG)

    day_avg = df.groupby("date").apply(day_average, include_groups=False).rename("day_avg_audi_per_show")
    rows = rows.join(day_avg, on="date")
    cols = ["date", "rank", "open_week", "audi_cnt", "show_cnt", "audi_per_show", "day_avg_audi_per_show"]
    return {
        "movie_cd": movie_cd,
        "movie_nm": rows["movie_nm"].iloc[-1],
        "points": to_records(rows[cols]),
    }
