from datetime import datetime, timedelta
import io
import re
import time
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
import pandas as pd
import plotly.express as px
import requests
import streamlit as st

st.set_page_config(page_title="타임폴리오 ETF 실시간 대시보드", layout="wide")

st.title("🎯 타임폴리오 액티브")
st.markdown(
    "타임폴리오 공식 홈페이지에서 나스닥100 액티브 **전체 구성종목** 및 **전일 대비 비중 변화**,"
    " **실시간 기준가**를 연동합니다."
)

st.link_button(
    "🔗 타임폴리오 공식 구성종목 페이지 바로가기",
    "https://timeetf.co.kr/m11_view.php?idx=2#constituentItems",
    use_container_width=True,
)

st.markdown("---")


def get_market_session_status():
    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    weekday = now_kst.weekday()  # 월:0, 화:1, 수:2, 목:3, 금:4, 토:5, 일:6
    current_time_val = now_kst.hour * 60 + now_kst.minute

    # 한국장 영업일/장중 여부 (토/일 제외)
    is_korean_weekend = weekday >= 5
    korean_market_open = 9 * 60
    korean_market_close = 15 * 60 + 30
    is_korean_market_hours = (not is_korean_weekend) and (
        korean_market_open <= current_time_val <= korean_market_close
    )

    year = now_kst.year
    march_1 = datetime(year, 3, 1, tzinfo=ZoneInfo("Asia/Seoul"))
    second_sunday_march = 14 - (march_1.weekday() + 1) % 7
    dst_start = datetime(
        year, 3, second_sunday_march, 2, 0, tzinfo=ZoneInfo("Asia/Seoul")
    )

    nov_1 = datetime(year, 11, 1, tzinfo=ZoneInfo("Asia/Seoul"))
    first_sunday_nov = 7 - nov_1.weekday() if nov_1.weekday() != 6 else 7
    dst_end = datetime(
        year, 11, first_sunday_nov, 2, 0, tzinfo=ZoneInfo("Asia/Seoul")
    )

    is_dst = dst_start <= now_kst < dst_end
    premarket_start_val = (17 if is_dst else 18) * 60  # 17:00 / 18:00
    reg_start_val = (22 * 60 + 30) if is_dst else (23 * 60 + 30)  # 22:30 / 23:30

    # 주말 판별
    is_weekend_closed = (
        (weekday == 5 and now_kst.hour >= 9)
        or (weekday == 6)
        or (weekday == 0 and current_time_val < premarket_start_val)
    )

    # 평일 중 프리마켓 시작 전 대기시간 판별 (15:30 ~ 17:00/18:00)
    is_weekday_waiting = (
        (weekday < 5)
        and (not is_korean_market_hours)
        and (korean_market_close <= current_time_val < premarket_start_val)
    )

    # 프리장 시작 후 15분 지연 버퍼 구간 판별
    is_pre_delay_buffer = (not is_weekend_closed) and (
        premarket_start_val <= current_time_val < (premarket_start_val + 15)
    )
    is_reg_delay_buffer = (not is_weekend_closed) and (
        reg_start_val <= current_time_val < (reg_start_val + 15)
    )

    return (
        is_korean_market_hours,
        is_weekday_waiting,
        is_pre_delay_buffer,
        is_reg_delay_buffer,
        now_kst,
        is_dst,
    )


def get_prev_business_day(ref_date):
    if isinstance(ref_date, str):
        ref_date = datetime.strptime(ref_date, "%Y-%m-%d")

    if ref_date.weekday() == 0:
        prev_day = ref_date - timedelta(days=3)
    elif ref_date.weekday() == 6:
        prev_day = ref_date - timedelta(days=2)
    elif ref_date.weekday() == 5:
        prev_day = ref_date - timedelta(days=1)
    else:
        prev_day = ref_date - timedelta(days=1)
    return prev_day.strftime("%Y-%m-%d")


def get_timefolio_constituents_by_date(idx=2, date_str=None):
    """POST / GET 방식으로 타임폴리오 특정 날짜의 실제 PDF 구성종목을 크롤링합니다."""
    url = f"https://timeetf.co.kr/m11_view.php?idx={idx}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Referer": url,
    }
    data = []
    fetched_date = date_str

    try:
        if date_str:
            formatted_date_dot = date_str.replace("-", ".")
            post_data = {"pdfDate": formatted_date_dot, "idx": str(idx)}
            resp = requests.post(url, headers=headers, data=post_data, timeout=10)
        else:
            resp = requests.get(url, headers=headers, timeout=10)

        soup = BeautifulSoup(resp.text, "html.parser")

        rows = soup.select("table tr")
        exclude_keywords = [
            "기준가",
            "비교지수",
            "초과성과",
            "종목",
            "총액",
            "합계",
            "TOTAL",
            "CODE",
            "DATE",
            "날짜",
            "일자",
        ]

        for row in rows:
            tds = row.select("td")
            if len(tds) >= 4:
                cols = [td.get_text().strip() for td in tds]
                raw_code, raw_name, raw_weight = cols[0], cols[1], cols[-1]

                amt_val = 0.0
                for col_text in cols[1:-1]:
                    clean_txt = col_text.replace(",", "").strip()
                    if clean_txt.replace(".", "", 1).isdigit() and float(clean_txt) > 1000:
                        try:
                            amt_val = float(clean_txt)
                            break
                        except ValueError:
                            pass

                clean_ticker = (
                    "현금"
                    if "현금" in raw_name
                    or "예금" in raw_name
                    or "CASH" in raw_name.upper()
                    or "KRW" in raw_name.upper()
                    else (
                        raw_code.split()[0].strip().upper() if raw_code.split() else ""
                    )
                )
                if (
                    not clean_ticker
                    or clean_ticker in ["NAN", "NONE"]
                    or any(
                        kw == clean_ticker or kw in raw_code or kw in raw_name
                        for kw in exclude_keywords
                    )
                    or re.match(r"^\d{4}[.-/]\d{2}[.-/]\d{2}$", clean_ticker)
                ):
                    continue

                try:
                    weight_val = float(
                        raw_weight.replace("%", "").replace(",", "").strip()
                    )
                    if 0 < weight_val <= 100:
                        data.append({
                            "종목코드": clean_ticker,
                            "비중": weight_val,
                            "평가금액": amt_val,
                        })
                except ValueError:
                    pass

        if data:
            return (
                pd.DataFrame(data)
                .drop_duplicates(subset=["종목코드"])
                .reset_index(drop=True),
                fetched_date,
            )
    except Exception:
        pass
    return None, fetched_date


def get_naver_official_base_fx():
    headers = {"User-Agent": "Mozilla/5.0"}
    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    weekday = now_kst.weekday()

    if weekday >= 5 or (weekday == 0 and now_kst.hour < 9):
        target_dt_str = get_prev_business_day(now_kst)
    elif now_kst.hour < 9:
        target_dt_str = get_prev_business_day(now_kst)
    else:
        target_dt_str = now_kst.strftime("%Y-%m-%d")

    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/USDKRW=X?interval=5m&range=5d"
        resp = requests.get(url, headers=headers, timeout=5)

        if resp.status_code == 200:
            json_data = resp.json()
            result = json_data.get("chart", {}).get("result", [])

            if result and len(result) > 0:
                timestamps = result[0].get("timestamp", [])
                indicators = result[0].get("indicators", {}).get("quote", [{}])[0]
                close_prices = indicators.get("close", [])

                target_time_val = 15 * 60 + 30
                best_rate = 0.0
                min_diff = float("inf")

                for ts, price in zip(timestamps, close_prices):
                    if price is not None:
                        dt_kst = datetime.fromtimestamp(ts, tz=ZoneInfo("Asia/Seoul"))
                        date_str = dt_kst.strftime("%Y-%m-%d")

                        if date_str <= target_dt_str:
                            curr_min = dt_kst.hour * 60 + dt_kst.minute
                            diff = abs(curr_min - target_time_val)

                            if date_str == target_dt_str:
                                if diff < min_diff:
                                    min_diff = diff
                                    best_rate = float(price)
                            elif best_rate == 0.0 and diff < 30:
                                best_rate = float(price)

                if best_rate > 0:
                    return best_rate
    except Exception:
        pass

    return 0.0


def get_naver_etf_market_data(ticker_code="426030"):
    result = {
        "current_price": 0.0,
        "prev_close": 0.0,
        "price_change_pct": 0.0,
        "naver_nav": 0.0,
        "naver_disparity": 0.0,
    }
    try:
        resp = requests.get(
            f"https://finance.naver.com/item/main.naver?code={ticker_code}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=5,
        )
        soup = BeautifulSoup(resp.text, "html.parser")

        today_tag = soup.select_one("p.no_today em span.blind")
        if today_tag:
            result["current_price"] = float(today_tag.text.strip().replace(",", ""))

        rate_info = soup.select_one("div.rate_info")
        if rate_info:
            exday_text = rate_info.get_text()

            is_minus = bool(
                rate_info.select_one("p.no_exday em span.ico.down")
                or rate_info.select_one("em.no_down")
                or "하락" in exday_text
            )

            m_pct = re.search(r"([\+\-]?\d+\.\d+)\s*%", exday_text)
            if m_pct:
                val = float(m_pct.group(1))
                result["price_change_pct"] = (
                    -val
                    if (is_minus and val > 0 and "-" not in m_pct.group(1))
                    else val
                )

        prev_tag = soup.select_one("td.first em span.blind")
        if prev_tag:
            result["prev_close"] = float(prev_tag.text.strip().replace(",", ""))

        page_html = resp.text
        nav_pattern = re.search(
            r"NAV[^<]*</t[dh]>\s*<t[dh][^>]*>\s*([\d,]+)\s*</t[dh]>",
            page_html,
            re.IGNORECASE,
        )
        if nav_pattern:
            nav_str = nav_pattern.group(1).replace(",", "")
            if nav_str.isdigit():
                result["naver_nav"] = float(nav_str)

        if result["naver_nav"] == 0.0:
            for tr in soup.find_all("tr"):
                tr_text = tr.get_text()
                if "NAV" in tr_text:
                    nums = re.findall(r"([\d,]+)", tr_text)
                    for n in nums:
                        clean_n = n.replace(",", "")
                        if clean_n.isdigit():
                            val = float(clean_n)
                            if 10000 <= val <= 200000:
                                result["naver_nav"] = val
                                break
                    if result["naver_nav"] > 0:
                        break

        if result["current_price"] > 0 and result["naver_nav"] > 0:
            result["naver_disparity"] = (
                (result["current_price"] - result["naver_nav"])
                / result["naver_nav"]
            ) * 100
    except Exception:
        pass
    return result


def get_timefolio_official_data(idx=2):
    result = {"live_nav": 0.0, "live_time": "", "base_nav": 0.0, "base_date": ""}
    try:
        resp = requests.get(
            f"https://timeetf.co.kr/m11_view.php?idx={idx}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=5,
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        for box in (
            soup.select("div.standard_price_box")
            or soup.select("ul.price_info li")
            or soup.find_all("div")
        ):
            box_text = box.get_text()
            if "실시간" in box_text and "기준가" in box_text:
                m_val = re.search(r"([\d,]+\.\d+)", box_text)
                if m_val:
                    result["live_nav"] = float(m_val.group(1).replace(",", ""))
                m_time = re.search(
                    r"(\d{4}[-.\/]\d{2}[-.\/]\d{2}\s+\d{2}:\d{2}:\d{2})", box_text
                )
                if m_time:
                    result["live_time"] = m_time.group(1)
            elif "기준가" in box_text and "실시간" not in box_text:
                m_val = re.search(r"([\d,]+\.\d+)", box_text)
                if m_val:
                    result["base_nav"] = float(m_val.group(1).replace(",", ""))
                m_date = re.search(r"(\d{4}[-.\/]\d{2}[-.\/]\d{2})", box_text)
                if m_date:
                    result["base_date"] = m_date.group(1)
    except Exception:
        pass
    return result


def get_realtime_prices_by_session(symbols):
    """현재 세션 상태에 따라 프리장은 트레이딩뷰, 본장은 Finnhub API를 각각 명확히 호출 (속도 최적화 버전)"""
    clean_symbols = []
    for s in symbols:
        sym_str = str(s).split()[0].upper().replace("/", "-")
        if any(kw in sym_str for kw in ["NQU", "NQ1!", "NQ=", "나스닥"]):
            clean_symbols.append("QQQ")
        elif (
            sym_str != "현금"
            and "현금" not in sym_str
            and "CASH" not in sym_str
            and "KRW" not in sym_str
        ):
            clean_symbols.append(sym_str)

    clean_symbols = list(set(clean_symbols))
    result_map = {}
    live_fx = 0.0

    _, _, _, _, now_kst, is_dst = get_market_session_status()
    current_time_val = now_kst.hour * 60 + now_kst.minute
    reg_start_val = (22 * 60 + 30) if is_dst else (23 * 60 + 30)

    is_regular_session = (current_time_val >= reg_start_val) or (current_time_val < 6 * 60)

    if not is_regular_session:
        # 프리장 시간대: 트레이딩뷰 스캐너 API
        tv_symbols = [f"NASDAQ:{s}" if s != "QQQ" else "NASDAQ:QQQ" for s in clean_symbols]
        try:
            tv_payload = {
                "symbols": {"tickers": tv_symbols},
                "columns": ["close", "change", "premarket_close", "premarket_change", "premarket_change_abs", "market"]
            }
            resp_tv = requests.post(
                "https://scanner.tradingview.com/america/scan",
                json=tv_payload,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=5
            )
            if resp_tv.status_code == 200:
                tv_data = resp_tv.json().get("data", [])
                for item in tv_data:
                    s_name = item.get("s", "").split(":")[-1].upper()
                    d_vals = item.get("d", [])
                    if len(d_vals) >= 4:
                        close_val = d_vals[0] or 0.0
                        close_chg = d_vals[1] or 0.0
                        pm_close = d_vals[2]
                        pm_chg = d_vals[3]

                        if pm_close is not None and pm_close > 0:
                            p_val = float(pm_close)
                            c_val = float(pm_chg) if pm_chg is not None else 0.0
                        else:
                            p_val = float(close_val)
                            c_val = float(close_chg)
                        result_map[s_name] = (p_val, c_val)
        except Exception:
            pass
    else:
        # 본장 시간대: Finnhub API (지연 시간 0.01초로 대폭 단축 및 세션 재사용으로 속도 최적화)
        finnhub_key = st.secrets.get("FINNHUB_API_KEY", "d9op4bpr01qnvunojplgd9op4bpr01qnvunojpm0")
        session = requests.Session()
        for s in clean_symbols:
            try:
                url = f"https://finnhub.io/api/v1/quote?symbol={s}&token={finnhub_key}"
                resp_fh = session.get(url, timeout=3)
                if resp_fh.status_code == 200:
                    fh_data = resp_fh.json()
                    current_p = float(fh_data.get("c", 0.0))
                    prev_close_p = float(fh_data.get("pc", 0.0))
                    if current_p > 0 and prev_close_p > 0:
                        change_p = ((current_p - prev_close_p) / prev_close_p) * 100
                        result_map[s] = (current_p, change_p)
                time.sleep(0.01)  # 로딩 속도를 빠르게 하기 위해 딜레이를 0.01초로 최소화
            except Exception:
                pass

    # 환율(USDKRW=X) 수집 로직
    session = requests.Session()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
        "Referer": "https://finance.yahoo.com/",
    }
    session.headers.update(headers)

    crumb = None
    try:
        session.get("https://finance.yahoo.com/", timeout=3)
        resp_crumb = session.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=3)
        if resp_crumb.status_code == 200 and resp_crumb.text:
            crumb = resp_crumb.text.strip()
    except Exception:
        pass

    try:
        fx_url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols=USDKRW=X" + (f"&crumb={crumb}" if crumb else "")
        resp_fx = session.get(fx_url, timeout=4)
        if resp_fx.status_code == 200:
            quotes = resp_fx.json().get("quoteResponse", {}).get("result", [])
            for q in quotes:
                if q.get("symbol", "").upper() == "USDKRW=X":
                    live_fx = float(q.get("regularMarketPrice", 0.0))
                    break
    except Exception:
        pass

    if live_fx == 0.0:
        try:
            resp_fx = requests.post(
                "https://scanner.tradingview.com/forex/scan",
                json={"symbols": {"tickers": ["FX_IDC:USDKRW", "FX:USDKRW"]}, "columns": ["close"]},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=3,
            )
            if resp_fx.status_code == 200:
                data_fx = resp_fx.json().get("data", [])
                if data_fx and data_fx[0].get("d"):
                    live_fx = float(data_fx[0]["d"][0]) if data_fx[0]["d"][0] is not None else 0.0
        except Exception:
            pass

    if "QQQ" in result_map:
        for s in symbols:
            sym_upper = str(s).upper()
            if any(kw in sym_upper for kw in ["NQU", "NQ1!", "NQ="]):
                result_map[sym_upper.split()[0]] = result_map["QQQ"]

    return result_map, live_fx


def color_change_pct(val):
    try:
        val_num = float(str(val).replace("%", "").strip())
        if val_num > 0:
            return "color: #0055FF; font-weight: bold;"
        elif val_num < 0:
            return "color: #FF0000; font-weight: bold;"
    except Exception:
        pass
    return ""


def color_weight_change(val):
    try:
        val_str = str(val)
        if "NEW" in val_str:
            return "color: #8A2BE2; font-weight: bold;"
        if "OUT" in val_str:
            return "color: #FF8C00; font-weight: bold;"
        val_num = float(val_str.replace("%", "").replace("+", "").strip())
        if val_num > 0:
            return "color: #0055FF; font-weight: bold;"
        elif val_num < 0:
            return "color: #FF0000; font-weight: bold;"
    except Exception:
        pass
    return ""


st.markdown("### 🌐 구성종목 가져오기 방식을 선택하세요")
col1, col2 = st.columns(2)
with col1:
    fetch_auto = st.button("🚀 자동 불러오기", use_container_width=True)
with col2:
    toggle_uploader = st.button(
        "📁 엑셀 파일 수동 업로드하기", use_container_width=True
    )

if "show_uploader" not in st.session_state:
    st.session_state.show_uploader = False
if toggle_uploader:
    st.session_state.show_uploader = not st.session_state.show_uploader

uploaded_file, uploaded_file_prev = None, None
if st.session_state.show_uploader:
    st.markdown("---")
    uploaded_file = st.file_uploader(
        "1️⃣ **[오늘]** 구성종목 엑셀 파일 (.xlsx)", type=["xlsx", "xls"], key="today"
    )
    uploaded_file_prev = st.file_uploader(
        "2️⃣ **[어제 / 직전 영업일]** 구성종목 엑셀 파일 (.xlsx)",
        type=["xlsx", "xls"],
        key="yesterday",
    )
    st.markdown("---")

df_input, df_prev, current_pdf_date_str = None, None, ""

if uploaded_file is None:
    with st.spinner("타임폴리오 웹사이트에서 구성종목 수집 중..."):
        now_kst_dt = datetime.now(ZoneInfo("Asia/Seoul"))
        target_date_candidate = now_kst_dt.strftime("%Y-%m-%d")

        for _ in range(5):
            df_input, _ = get_timefolio_constituents_by_date(idx=2, date_str=target_date_candidate)
            if df_input is not None and not df_input.empty:
                current_pdf_date_str = target_date_candidate
                break
            target_date_candidate = get_prev_business_day(target_date_candidate)

        if df_input is None or df_input.empty:
            df_input, _ = get_timefolio_constituents_by_date(idx=2)
            current_pdf_date_str = target_date_candidate

        prev_pdf_date_str = get_prev_business_day(current_pdf_date_str)
        df_prev, _ = get_timefolio_constituents_by_date(
            idx=2, date_str=prev_pdf_date_str
        )

        if df_prev is not None and not df_prev.empty and df_input is not None:
            if df_input["비중"].tolist() == df_prev["비중"].tolist():
                older_prev_date = get_prev_business_day(prev_pdf_date_str)
                df_prev, _ = get_timefolio_constituents_by_date(
                    idx=2, date_str=older_prev_date
                )
else:
    try:
        raw_df_in = pd.read_excel(uploaded_file)
        in_t_col, in_w_col, in_n_col, in_a_col = None, None, None, None
        for col in raw_df_in.columns:
            c_str = str(col)
            if "코드" in c_str or "티커" in c_str or "Symbol" in c_str:
                in_t_col = col
            elif "비중" in c_str or "Weight" in c_str:
                in_w_col = col
            elif "명" in c_str or "Name" in c_str:
                in_n_col = col
            elif "금액" in c_str or "평가" in c_str or "Amount" in c_str:
                in_a_col = col
        if not in_t_col:
            in_t_col = raw_df_in.columns[0]
        if not in_w_col:
            in_w_col = raw_df_in.columns[-1]

        clean_in_data = []
        for _, r in raw_df_in.iterrows():
            t_val = (
                str(r[in_t_col]).split()[0].strip().upper()
                if pd.notna(r[in_t_col])
                else ""
            )
            n_val = str(r[in_n_col]) if in_n_col and pd.notna(r[in_n_col]) else ""
            w_val_raw = (
                str(r[in_w_col]).replace("%", "").replace(",", "").strip()
                if pd.notna(r[in_w_col])
                else "0"
            )
            amt_val = (
                float(str(r[in_a_col]).replace(",", "").strip())
                if in_a_col
                and pd.notna(r[in_a_col])
                and str(r[in_a_col]).replace(".", "", 1).isdigit()
                else 0.0
            )

            if (
                "현금" in n_val
                or "예금" in n_val
                or t_val in ["NAN", "NONE", "KRW", ""]
            ):
                t_val = "현금"
            if t_val in ["NAN", "NONE", "열1"]:
                continue
            try:
                w_val = float(w_val_raw)
                if 0 < w_val <= 100:
                    clean_in_data.append(
                        {"종목코드": t_val, "비중": w_val, "평가금액": amt_val}
                    )
            except ValueError:
                pass
        df_input = (
            pd.DataFrame(clean_in_data)
            .drop_duplicates(subset=["종목코드"])
            .reset_index(drop=True)
        )

        if uploaded_file_prev is not None:
            raw_df_prev = pd.read_excel(uploaded_file_prev)
            p_t_col, p_w_col, p_n_col = None, None, None
            for col in raw_df_prev.columns:
                c_str = str(col)
                if "코드" in c_str or "티커" in c_str or "Symbol" in c_str:
                    p_t_col = col
                elif "비중" in c_str or "Weight" in c_str:
                    p_w_col = col
                elif "명" in c_str or "Name" in c_str:
                    p_n_col = col
            if not p_t_col:
                p_t_col = raw_df_prev.columns[0]
            if not p_w_col:
                p_w_col = raw_df_prev.columns[-1]

            clean_prev_data = []
            for _, r in raw_df_prev.iterrows():
                t_val = (
                    str(r[p_t_col]).split()[0].strip().upper()
                    if pd.notna(r[p_t_col])
                    else ""
                )
                n_val = str(r[p_n_col]) if p_n_col and pd.notna(r[p_n_col]) else ""
                w_val_raw = (
                    str(r[p_w_col]).replace("%", "").replace(",", "").strip()
                    if pd.notna(r[p_w_col])
                    else "0"
                )
                if (
                    "현금" in n_val
                    or "예금" in n_val
                    or t_val in ["NAN", "NONE", "KRW", ""]
                ):
                    t_val = "현금"
                if t_val in ["NAN", "NONE", "열1"]:
                    continue
                try:
                    w_val = float(w_val_raw)
                    if 0 < w_val <= 100:
                        clean_prev_data.append({"종목코드": t_val, "비중": w_val})
                except ValueError:
                    pass
            df_prev = (
                pd.DataFrame(clean_prev_data)
                .drop_duplicates(subset=["종목코드"])
                .reset_index(drop=True)
            )
    except Exception as e:
        st.error(f"엑셀 파일 읽기 오류: {e}")

if df_input is not None and not df_input.empty:
    date_info_msg = f" ({current_pdf_date_str} vs 전일)" if current_pdf_date_str else ""
    st.success(f"✅ 총 {len(df_input)}개 종목 로드 완료!{date_info_msg}")

    with st.spinner("실시간 시세 연산 중..."):
        clean_df = df_input.dropna(subset=["종목코드", "비중"]).copy()

        prev_weight_map = {}
        removed_stocks = []
        if df_prev is not None and not df_prev.empty:
            for _, p_row in df_prev.iterrows():
                p_code = str(p_row["종목코드"]).strip().upper()
                if p_code == "KRW":
                    p_code = "현금"
                try:
                    prev_weight_map[p_code] = float(
                        str(p_row["비중"]).replace("%", "").replace(",", "").strip()
                    )
                except ValueError:
                    pass

        ticker_list = list(
            set(
                clean_df["종목코드"].tolist()
                + (
                    df_prev["종목코드"].tolist()
                    if df_prev is not None and not df_prev.empty
                    else []
                )
            )
        )

        official_base_fx = get_naver_official_base_fx()
        naver_market = get_naver_etf_market_data("426030")
        timefolio_data = get_timefolio_official_data(idx=2)

        batch_results, live_fx = get_realtime_prices_by_session(ticker_list)

        if live_fx == 0.0 and official_base_fx > 0:
            live_fx = official_base_fx

        usdkrw_change_pct = (
            ((live_fx - official_base_fx) / official_base_fx) * 100
            if official_base_fx > 0
            else 0.0
        )

        live_data, new_added_stocks = [], []
        cash_keywords = ["KRW", "CASH", "현금", "원화", "달러", "예금"]

        curr_tickers_map = {}
        for _, row in clean_df.iterrows():
            raw_ticker = str(row["종목코드"]).strip()
            ticker = raw_ticker.split()[0].upper().replace("/", ".")
            if any(kw in ticker for kw in cash_keywords) or ticker == "KRW":
                ticker = "현금"
            try:
                weight = float(str(row["비중"]).replace("%", "").replace(",", "").strip())
            except ValueError:
                weight = 0.0
            curr_tickers_map[ticker] = (weight, row)

        processed_tickers = set()
        for ticker in ticker_list:
            if ticker in processed_tickers:
                continue
            processed_tickers.add(ticker)

            weight, row_data = curr_tickers_map.get(ticker, (0.0, None))
            prev_w = prev_weight_map.get(
                ticker, prev_weight_map.get("KRW", None) if ticker == "현금" else None
            )

            w_diff_val, prev_w_str, w_diff_str = 0.0, "-", "-"

            if prev_w is None:
                if weight > 0:
                    w_diff_str = "✨ NEW"
                    w_diff_val = weight
                    new_added_stocks.append(f"**{ticker}** ({weight:.2f}%)")
            else:
                w_diff = weight - prev_w
                w_diff_val = w_diff
                prev_w_str = f"{prev_w:.2f}%"

                if weight == 0.0 and prev_w > 0:
                    w_diff_str = "🚪 OUT"
                    removed_stocks.append(f"**{ticker}** (전일 {prev_w:.2f}%)")
                elif abs(w_diff) >= 0.001:
                    w_diff_str = f"{w_diff:+.2f}%"
                else:
                    w_diff_str = "+0.00%"

            if ticker == "현금":
                krw_amount = (
                    row_data.get("평가금액", 0.0)
                    if row_data is not None and row_data.get("평가금액", 0.0) > 0
                    else 1.0
                )
                live_data.append({
                    "종목코드": "현금",
                    "실시간 가격($)": krw_amount,
                    "주가변동률(%)": 0.0,
                    "당일비중(%)": weight,
                    "전일비중(%)": prev_w_str,
                    "비중변화(%)": w_diff_str,
                    "비중변화_수치": w_diff_val,
                })
            elif "NQU" in ticker or "NQ1!" in ticker or "NQ=" in ticker:
                qqq_price, qqq_change = batch_results.get("QQQ", (0.0, 0.0))
                live_data.append({
                    "종목코드": "나스닥",
                    "실시간 가격($)": qqq_price,
                    "주가변동률(%)": qqq_change,
                    "당일비중(%)": weight,
                    "전일비중(%)": prev_w_str,
                    "비중변화(%)": w_diff_str,
                    "비중변화_수치": w_diff_val,
                })
            elif ticker in batch_results and batch_results[ticker][0] > 0:
                live_price, stock_change_pct = batch_results[ticker]
                live_data.append({
                    "종목코드": ticker,
                    "실시간 가격($)": live_price,
                    "주가변동률(%)": stock_change_pct,
                    "당일비중(%)": weight,
                    "전일비중(%)": prev_w_str,
                    "비중변화(%)": w_diff_str,
                    "비중변화_수치": w_diff_val,
                })
            else:
                live_data.append({
                    "종목코드": ticker,
                    "실시간 가격($)": 0.0,
                    "주가변동률(%)": 0.0,
                    "당일비중(%)": weight,
                    "전일비중(%)": prev_w_str,
                    "비중변화(%)": w_diff_str,
                    "비중변화_수치": w_diff_val,
                })

        result_df = pd.DataFrame(live_data)
        total_weight = result_df["당일비중(%)"].sum()
        if total_weight > 0:
            stock_inav_change = (
                (result_df["당일비중(%)"] / total_weight)
                * result_df["주가변동률(%)"]
            ).sum()
            total_inav_change = stock_inav_change + usdkrw_change_pct
        else:
            stock_inav_change, total_inav_change = 0.0, 0.0

        # =========================================================
        # 🏷️ 상단 현재가격 및 괴리율 카드
        # =========================================================
        current_etf_price = naver_market["current_price"]
        price_change_pct = naver_market["price_change_pct"]
        naver_disp = naver_market["naver_disparity"]
        naver_nav = naver_market["naver_nav"]

        if current_etf_price > 0:
            pct_is_plus = price_change_pct >= 0
            chg_str = f"<span style='background-color: {'#ffebee' if pct_is_plus else '#e3f2fd'}; color: {'#c62828' if pct_is_plus else '#0277bd'}; padding: 2px 8px; border-radius: 12px; font-size: 20px; font-weight: normal; display: inline-block;'>({price_change_pct:+.2f}%)</span>"
            disp_is_plus = naver_disp >= 0
            nav_text_part = (
                f"(NAV: {naver_nav:,.0f}원 기준)" if naver_nav > 0 else ""
            )

            st.markdown(
                "<div style='font-size: 14px; color: #6f727b; margin-bottom: 2px;'>🏷️"
                " 현재가격</div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"<p style='font-size: 42px; font-weight: normal; margin-bottom:"
                f" 0px; line-height: 1.2; color: #1f1f1f;'>{current_etf_price:,.0f}"
                f" 원 {chg_str}</p>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"<div style='display: inline-block; background-color:"
                f" {'#ffebee' if disp_is_plus else '#e3f2fd'}; color:"
                f" {'#c62828' if disp_is_plus else '#0277bd'}; padding: 2px 8px;"
                f" border-radius: 12px; font-size: 14px; font-weight: 500;"
                f" margin-top: 6px;'>{'↑' if disp_is_plus else '↓'} 실시간 괴리율:"
                f" {naver_disp:+.2f}% {nav_text_part}</div>",
                unsafe_allow_html=True,
            )

        st.markdown("<div style='margin-top: 20px;'></div>", unsafe_allow_html=True)

        (
            is_korean_market_hours,
            is_weekday_waiting,
            is_pre_delay_buffer,
            is_reg_delay_buffer,
            now_kst,
            is_dst,
        ) = get_market_session_status()

        if is_korean_market_hours:
            st.markdown(
                """
<div style="margin-bottom: 10px;">
<div style="font-size: 14px; color: #6f727b; margin-bottom: 2px;">📈 실시간 iNAV 추정 총 변동률 (15분 지연)</div>
<div style="font-size: 32px; font-weight: normal; color: #1f1f1f; line-height: 1.2; margin-bottom: 4px;">한국시장 거래중</div>
<div style="display: inline-block; background-color: #e3f2fd; color: #0277bd; padding: 2px 8px; border-radius: 12px; font-size: 13px; font-weight: 500;">
ℹ️ 장중에는 실시간 가격과 괴리율을 참고하세요.
</div>
</div>
""",
                unsafe_allow_html=True,
            )
            st.markdown("<div style='margin-top: 20px;'></div>", unsafe_allow_html=True)
            st.markdown(
                """
<div style="margin-bottom: 10px;">
<div style="font-size: 14px; color: #6f727b; margin-bottom: 2px;">💵 나스닥100액티브(426030) 예상 iNAV (15분 지연)</div>
<div style="font-size: 32px; font-weight: normal; color: #1f1f1f; line-height: 1.2; margin-bottom: 4px;">한국시장 거래중</div>
<div style="display: inline-block; background-color: #e3f2fd; color: #0277bd; padding: 2px 8px; border-radius: 12px; font-size: 13px; font-weight: 500;">
ℹ️ 장중에는 실시간 가격과 괴리율을 참고하세요.
</div>
</div>
""",
                unsafe_allow_html=True,
            )
        elif is_weekday_waiting:
            st.markdown(
                """
<div style="margin-bottom: 10px;">
<div style="font-size: 14px; color: #6f727b; margin-bottom: 2px;">📈 실시간 iNAV 추정 총 변동률 (15분 지연)</div>
<div style="font-size: 32px; font-weight: normal; color: #1f1f1f; line-height: 1.2; margin-bottom: 4px;">미국 프리마켓 대기 중 ⏳</div>
<div style="display: inline-block; background-color: #fff3e0; color: #e65100; padding: 2px 8px; border-radius: 12px; font-size: 13px; font-weight: 500;">
ℹ️ 미국 프리마켓 시작시 실시간 추정치가 제공됩니다.
</div>
</div>
""",
                unsafe_allow_html=True,
            )
            st.markdown("<div style='margin-top: 20px;'></div>", unsafe_allow_html=True)
            st.markdown(
                """
<div style="margin-bottom: 10px;">
<div style="font-size: 14px; color: #6f727b; margin-bottom: 2px;">💵 나스닥100액티브(426030) 예상 iNAV (15분 지연)</div>
<div style="font-size: 32px; font-weight: normal; color: #1f1f1f; line-height: 1.2; margin-bottom: 4px;">미국 프리마켓 대기 중 ⏳</div>
<div style="display: inline-block; background-color: #fff3e0; color: #e65100; padding: 2px 8px; border-radius: 12px; font-size: 13px; font-weight: 500;">
ℹ️ 미국 프리마켓 시작시 실시간 추정치가 제공됩니다.
</div>
</div>
""",
                unsafe_allow_html=True,
            )
        elif is_pre_delay_buffer or is_reg_delay_buffer:
            pre_next_time = "17:15" if is_dst else "18:15"
            reg_next_time = "22:45" if is_dst else "23:45"
            delay_label = (
                f"프리장 15분 지연데이터 대기 중 ⏳ ({pre_next_time}부터 제공)"
                if is_pre_delay_buffer
                else f"본장 15분 지연데이터 대기 중 ⏳ ({reg_next_time}부터 제공)"
            )

            st.markdown(
                f"""
<div style="margin-bottom: 10px;">
<div style="font-size: 14px; color: #6f727b; margin-bottom: 2px;">📈 실시간 iNAV 추정 총 변동률 (15분 지연)</div>
<div style="font-size: 32px; font-weight: normal; color: #1f1f1f; line-height: 1.2; margin-bottom: 4px;">{delay_label}</div>
<div style="display: inline-block; background-color: #fff3e0; color: #e65100; padding: 2px 8px; border-radius: 12px; font-size: 13px; font-weight: 500;">
ℹ️ 미국 시장 개장 후 15분간은 지연 시세 반영 대기 시간입니다.
</div>
</div>
""",
                unsafe_allow_html=True,
            )
            st.markdown("<div style='margin-top: 20px;'></div>", unsafe_allow_html=True)
            st.markdown(
                f"""
<div style="margin-bottom: 10px;">
<div style="font-size: 14px; color: #6f727b; margin-bottom: 2px;">💵 나스닥100액티브(426030) 예상 iNAV (15분 지연)</div>
<div style="font-size: 32px; font-weight: normal; color: #1f1f1f; line-height: 1.2; margin-bottom: 4px;">{delay_label}</div>
<div style="display: inline-block; background-color: #fff3e0; color: #e65100; padding: 2px 8px; border-radius: 12px; font-size: 13px; font-weight: 500;">
ℹ️ 미국 시장 개장 후 15분간은 지연 시세 반영 대기 시간입니다.
</div>
</div>
""",
                unsafe_allow_html=True,
            )
        else:
            def render_custom_metric(
                label, value, delta_text, is_plus, extra_info=""
            ):
                bg_color, text_color, arrow = (
                    ("#ffebee", "#c62828", "↑")
                    if is_plus
                    else ("#e3f2fd", "#0277bd", "↓")
                )
                st.markdown(
                    f"""
<div style="margin-bottom: 10px;">
<div style="font-size: 14px; color: #6f727b; margin-bottom: 2px;">{label}</div>
<div style="font-size: 42px; font-weight: normal; color: #1f1f1f; line-height: 1.2; margin-bottom: 4px;">{value} {extra_info}</div>
<div style="display: inline-block; background-color: {bg_color}; color: {text_color}; padding: 2px 8px; border-radius: 12px; font-size: 14px; font-weight: 500;">
{arrow} {delta_text}
</div>
</div>
""",
                    unsafe_allow_html=True,
                )

            total_is_plus = total_inav_change >= 0
            main_theme_color = "#c62828" if total_is_plus else "#0277bd"

            delta_detail_html = (
                f"주가: <span style='color: {main_theme_color};"
                f" font-weight:500;'>{stock_inav_change:+.2f}%</span> + 환율:"
                f" <span style='color: {main_theme_color};"
                f" font-weight:500;'>{usdkrw_change_pct:+.2f}%</span><br>(현재 환율:"
                f" {live_fx:,.2f}원 / 기준 환율: {official_base_fx:,.2f}원)"
            )

            render_custom_metric(
                "📈 실시간 iNAV 추정 총 변동률 (15분 지연)",
                f"{total_inav_change:+.2f}%",
                delta_detail_html,
                total_is_plus,
            )
            st.markdown("<div style='margin-top: 20px;'></div>", unsafe_allow_html=True)

            base_nav_reference = (
                naver_nav
                if naver_nav > 0
                else (
                    timefolio_data["live_nav"]
                    if timefolio_data["live_nav"] > 0
                    else naver_market["prev_close"]
                )
            )

            if base_nav_reference > 0:
                estimated_inav_price = base_nav_reference * (
                    1 + (total_inav_change / 100)
                )
                diff_val = estimated_inav_price - base_nav_reference
                diff_is_plus = diff_val >= 0

                if current_etf_price > 0:
                    actual_vs_inav_pct = (
                        ((estimated_inav_price - current_etf_price) / current_etf_price)
                        * 100
                    )
                    actual_vs_inav_is_plus = actual_vs_inav_pct >= 0
                    actual_vs_inav_color = (
                        "#c62828" if actual_vs_inav_is_plus else "#0277bd"
                    )
                    inav_extra_info = f"<span style='background-color: {'#ffebee' if actual_vs_inav_is_plus else '#e3f2fd'}; color: {actual_vs_inav_color}; padding: 2px 8px; border-radius: 12px; font-size: 20px; font-weight: normal; display: inline-block;'>({actual_vs_inav_pct:+.2f}%)</span>"
                else:
                    inav_extra_info = ""

                render_custom_metric(
                    "💵 나스닥100액티브(426030) 예상 iNAV (15분 지연)",
                    f"{estimated_inav_price:,.0f} 원",
                    f"{diff_val:+,.0f} 원 (기준 iNAV: {base_nav_reference:,.0f}원)",
                    diff_is_plus,
                    extra_info=inav_extra_info,
                )

        # 🏛️ 타임폴리오 공식 기준가 박스
        if timefolio_data["live_nav"] > 0 or timefolio_data["base_nav"] > 0:
            live_nav_val = (
                f"**{timefolio_data['live_nav']:,.2f}원**"
                if timefolio_data["live_nav"] > 0
                else "대기 중"
            )
            live_time_str = (
                f" ({timefolio_data['live_time']})"
                if timefolio_data["live_time"]
                else ""
            )
            base_nav_val = (
                f"**{timefolio_data['base_nav']:,.2f}원**"
                if timefolio_data["base_nav"] > 0
                else "대기 중"
            )
            base_date_str = (
                f" ({timefolio_data['base_date']})"
                if timefolio_data["base_date"]
                else ""
            )

            st.success(
                f"🏛️ **타임폴리오 공식 기준가** \n- 실시간:"
                f" {live_nav_val}{live_time_str} \n- 전일 확정:"
                f" {base_nav_val}{base_date_str}"
            )

        if new_added_stocks or removed_stocks:
            new_msg = (
                f"✨ **신규 편입**: {', '.join(new_added_stocks)}"
                if new_added_stocks
                else "✨ **신규 편입**: 없음"
            )
            out_msg = (
                f"🚪 **편출 (전량 매도)**: {', '.join(removed_stocks)}"
                if removed_stocks
                else "🚪 **편출 (전량 매도)**: 없음"
            )
            st.warning(
                f"📋 **포트폴리오 변동 내역** \n- {new_msg} \n- {out_msg}"
            )

        display_base_df = result_df.copy()

        # =========================================================
        # 🔥 히트맵
        # =========================================================
        st.markdown("---")
        active_df = display_base_df[
            (display_base_df["당일비중(%)"] > 0)
            & (display_base_df["종목코드"] != "현금")
        ].copy()
        st.markdown(f"### 🔥 전체 구성종목 ({len(active_df)}개) 히트맵")

        active_df["표시명"] = (
            "<span style='font-size:20px; font-weight:bold;'>"
            + active_df["종목코드"]
            + "</span><br><span style='font-size:15px;'>"
            + active_df["주가변동률(%)"].map("{:+.2f}%".format)
            + "</span>"
        )

        fig_treemap = px.treemap(
            active_df,
            path=["표시명"],
            values="당일비중(%)",
            color="주가변동률(%)",
            color_continuous_scale=[
                [0.0, "#4285F4"],
                [0.166, "#3B72E2"],
                [0.333, "#345FCF"],
                [0.5, "#404552"],
                [0.666, "#8B3A48"],
                [0.833, "#C83742"],
                [1.0, "#FF1744"],
            ],
            range_color=[-3.0, 3.0],
        )

        fig_treemap.update_traces(
            textposition="middle center", selector=dict(type="treemap")
        )
        fig_treemap.update_layout(
            margin=dict(t=5, l=5, r=5, b=5),
            height=600,
            uniformtext=dict(minsize=8, mode=False),
            coloraxis_showscale=False,
        )
        st.plotly_chart(fig_treemap, use_container_width=True)

        # =========================================================
        # 🔄 전일 대비 비중 변화 TOP 10
        # =========================================================
        st.markdown("---")
        st.markdown("### 🔄 전일 대비 비중 변화 TOP 10")

        top10_change_df = display_base_df.copy()
        top10_change_df["절대변화량"] = top10_change_df["비중변화_수치"].abs()
        top10_change_df = top10_change_df.sort_values(
            by="절대변화량", ascending=False
        ).head(10)

        display_top10 = top10_change_df[[
            "종목코드",
            "당일비중(%)",
            "전일비중(%)",
            "비중변화(%)",
            "주가변동률(%)",
        ]].reset_index(drop=True)
        display_top10.index = range(1, len(display_top10) + 1)

        stiler_top10 = (
            display_top10.style.map(
                color_weight_change, subset=["비중변화(%)"]
            ).map(color_change_pct, subset=["주가변동률(%)"])
            if hasattr(display_top10.style, "map")
            else display_top10.style.applymap(
                color_weight_change, subset=["비중변화(%)"]
            ).applymap(color_change_pct, subset=["주가변동률(%)"])
        )
        st.dataframe(
            stiler_top10.format(
                {"당일비중(%)": "{:.2f}%", "주가변동률(%)": "{:+.2f}%"}
            ).set_properties(**{"text-align": "center"}),
            use_container_width=True,
            height=385,
        )

        # =========================================================
        # 📊 종목별 실시간 전체 현황 표 (비중 높은 순 정렬)
        # =========================================================
        st.markdown("---")
        st.markdown("### 📊 종목별 실시간 전체 현황")

        display_full_df = (
            display_base_df[display_base_df["당일비중(%)"] > 0]
            .copy()
            .sort_values(by="당일비중(%)", ascending=False)
            .reset_index(drop=True)
        )
        display_full_df.index = range(1, len(display_full_df) + 1)

        display_full_df["실시간 가격($)"] = display_full_df.apply(
            lambda r: f"{r['실시간 가격($)']:,.2f}"
            if r["실시간 가격($)"] > 0
            else "-",
            axis=1,
        )
        display_full_df["주가변동률(%)"] = display_full_df["주가변동률(%)"].apply(
            lambda x: f"{x:+.2f}%"
        )

        cols_to_show = [
            c
            for c in [
                "종목코드",
                "실시간 가격($)",
                "주가변동률(%)",
                "당일비중(%)",
                "전일비중(%)",
                "비중변화(%)",
            ]
            if c in display_full_df.columns
        ]
        display_full_df = display_full_df[cols_to_show]

        styled_full = (
            display_full_df.style.map(
                color_change_pct, subset=["주가변동률(%)"]
            ).map(color_weight_change, subset=["비중변화(%)"])
            if hasattr(display_full_df.style, "map")
            else display_full_df.style.applymap(
                color_change_pct, subset=["주가변동률(%)"]
            ).applymap(color_weight_change, subset=["비중변화(%)"])
        )
        st.dataframe(
            styled_full.format({"당일비중(%)": "{:.2f}%"}).set_properties(
                **{"text-align": "center"}
            ),
            use_container_width=True,
            height=500,
        )
