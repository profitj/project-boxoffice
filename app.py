"""Streamlit 화면: 표와 그래프를 보여 준다.

실행 (먼저 다른 터미널에서 uvicorn api:app --reload)
    streamlit run app.py
"""
import os
from datetime import date

import pandas as pd
import requests
import streamlit as st

API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")
NO_DATA_MSG = "조건에 맞는 데이터가 없습니다"
TARGET_WEEK = 2  # 경영 질문: 개봉 2주차 편성 판단

# 숫자 표기: 관객 수·상영 횟수·매출액은 천 단위 콤마, 회당 관객 수는 소수점 첫째 자리
NUM_FORMAT = {
    "audi_cnt": "%,d",
    "show_cnt": "%,d",
    "scrn_cnt": "%,d",
    "sales_amt": "%,d",
    "audi_per_show": "%.1f",
    "day_avg_audi_per_show": "%.1f",
}
LABELS = {
    "date": "날짜",
    "rank": "순위",
    "movie_nm": "영화명",
    "open_dt": "개봉일",
    "open_week": "개봉 주차",
    "audi_cnt": "관객 수",
    "show_cnt": "상영 횟수",
    "audi_per_show": "회당 관객 수",
    "day_avg_audi_per_show": "당일 평균 회당 관객 수",
    "scrn_cnt": "스크린 수",
    "sales_amt": "매출액(원)",
}
CARD = {"expand": st.success, "keep": st.info, "reduce": st.error}

st.set_page_config(page_title="영화 상영 횟수 조정 도우미", page_icon="🎬", layout="wide")
st.title("🎬 영화 상영 횟수 조정 도우미")
st.caption("개봉 2주차 영화의 상영 1회당 관객 수를 당일 평균과 비교해 상영 횟수 확대·유지·축소를 권고합니다.")


# ── API 호출 ─────────────────────────────
class ApiDown(Exception):
    """FastAPI 서버에 연결할 수 없음."""


def api_get(path: str, **params):
    """API를 호출한다. 데이터가 없으면(404) None을 돌려준다."""
    try:
        res = requests.get(f"{API_URL}{path}", params=params, timeout=5)
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        raise ApiDown from e
    if res.status_code == 404:
        return None
    res.raise_for_status()
    return res.json()


def show_api_down():
    st.error(f"API 서버({API_URL})에 연결할 수 없습니다. 먼저 `uvicorn api:app --reload`를 실행해 주세요.")


@st.cache_data(ttl=60)
def fetch_dates():
    return api_get("/api/dates")


@st.cache_data(ttl=60)
def fetch_movie_list():
    return api_get("/api/movie-list")


def show_table(df: pd.DataFrame, cols: list[str]):
    config = {
        c: st.column_config.NumberColumn(LABELS[c], format=NUM_FORMAT[c]) if c in NUM_FORMAT
        else LABELS.get(c, c)
        for c in cols
    }
    st.dataframe(df[cols], hide_index=True, width="stretch", column_config=config)


# ── 1. 날짜별 조회: 편성 권고 + 전체 현황 ─────────────────────────────
def render_recommendations(target: str):
    st.subheader(f"🎯 개봉 {TARGET_WEEK}주차 편성 의사결정 권고")
    rec = api_get("/api/recommendations", target_date=target, week=TARGET_WEEK)
    if not rec:
        st.warning(NO_DATA_MSG)
        return

    avg = rec["day_avg_audi_per_show"]
    st.caption(
        f"기준: 당일 평균 회당 관객 수 **{avg:.1f}명** · "
        f"확대 > {rec['upper']:.1f}명 · 유지 {rec['lower']:.1f}~{rec['upper']:.1f}명 · 축소 < {rec['lower']:.1f}명 (±10%)"
    )
    if not rec["recommendations"]:
        st.info(f"당일 개봉 {TARGET_WEEK}주차 영화가 없습니다.")
        return

    for r in rec["recommendations"]:
        CARD[r["decision"]](
            f"**{r['message']}**  \n"
            f"회당 관객 수 {r['audi_per_show']:.1f}명 / 평균 {avg:.1f}명 (평균 대비 {r['ratio_to_avg'] * 100:.0f}%)"
        )


def render_daily(info: dict):
    min_d = date.fromisoformat(info["min_date"])
    max_d = date.fromisoformat(info["max_date"])

    c_date, c_btn = st.columns([4, 1], vertical_alignment="bottom")
    with c_date:
        target = st.date_input("조회 날짜", value=max_d, min_value=min_d, max_value=max_d)
    with c_btn:
        if st.button("조회", type="primary", width="stretch"):
            st.session_state["target"] = target

    if "target" not in st.session_state:
        st.info(f"날짜를 고르고 **조회** 버튼을 눌러 주세요. (조회 가능: {min_d} ~ {max_d})")
        return

    key = st.session_state["target"].isoformat()
    data = api_get("/api/movies", target_date=key)
    if not data or not data["movies"]:
        st.warning(NO_DATA_MSG)
        return

    # 상단: 2주차 편성 권고
    render_recommendations(key)

    # 전체 상영 현황 (2주차 영화와 다른 영화 비교용)
    df = pd.DataFrame(data["movies"])
    st.subheader(f"📊 {key} 전체 상영 현황 ({data['count']}편)")
    m1, m2, m3 = st.columns(3)
    m1.metric("총 관객 수", f"{int(df['audi_cnt'].sum()):,}명")
    m2.metric("총 상영 횟수", f"{int(df['show_cnt'].sum()):,}회")
    m3.metric("당일 평균 회당 관객 수", f"{data['day_avg_audi_per_show']:.1f}명",
              help="전체 관객 수 ÷ 전체 상영 횟수")

    df["is_target"] = df["open_week"] == TARGET_WEEK
    show_table(df.assign(movie_nm=df["movie_nm"].where(~df["is_target"], "⭐ " + df["movie_nm"])),
               ["rank", "movie_nm", "open_dt", "open_week", "audi_cnt", "show_cnt",
                "audi_per_show", "scrn_cnt", "sales_amt"])
    st.caption(f"⭐ = 개봉 {TARGET_WEEK}주차 영화")

    # 차트: 지표마다 크기가 달라 따로 그림
    charts = {
        "audi_cnt": ("관객 수", "#4c78a8"),
        "show_cnt": ("상영 횟수", "#72b7b2"),
        "audi_per_show": ("상영 1회당 관객 수", "#e4572e"),
    }
    for col, (field, (name, color)) in zip(st.columns(3), charts.items()):
        with col:
            st.markdown(f"**{name}**")
            st.bar_chart(
                df.rename(columns={field: name}), x="movie_nm", y=name,
                horizontal=True, sort=f"-{name}", color=color,
                x_label="", y_label="", height=380,
            )


# ── 2. 영화별 추이 ─────────────────────────────
def render_trend():
    st.subheader("📈 영화별 상영 1회당 관객 수 추이")
    movie_list = fetch_movie_list()
    if not movie_list or not movie_list["movies"]:
        st.warning(NO_DATA_MSG)
        return

    names = {m["movie_cd"]: m["movie_nm"] for m in movie_list["movies"]}
    movie_cd = st.selectbox("영화 선택", options=list(names), format_func=names.get,
                            help="수집 기간 누적 관객 수가 많은 순서")

    trend = api_get("/api/trend", movie_cd=movie_cd)
    if not trend or not trend["points"]:
        st.warning(NO_DATA_MSG)
        return

    pts = pd.DataFrame(trend["points"])
    chart = pts.set_index("date")[["audi_per_show", "day_avg_audi_per_show"]].rename(
        columns={"audi_per_show": "회당 관객 수", "day_avg_audi_per_show": "당일 평균"}
    )
    st.line_chart(chart, x_label="날짜", y_label="회당 관객 수(명)",
                  color=["#e4572e", "#9e9e9e"], height=320)
    st.caption(f"수집 기간 {len(fetch_dates()['dates'])}일 중 {len(pts)}일 박스오피스 순위에 포함된 날만 표시")

    show_table(pts, ["date", "rank", "open_week", "audi_cnt", "show_cnt",
                     "audi_per_show", "day_avg_audi_per_show"])


# ── 화면 구성 ─────────────────────────────
try:
    info = fetch_dates()
    if not info:
        st.warning(NO_DATA_MSG)
        st.stop()

    render_daily(info)
    st.divider()
    render_trend()
except ApiDown:
    show_api_down()
except requests.exceptions.RequestException as e:
    st.error(f"데이터를 불러오지 못했습니다: {e}")
