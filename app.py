
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


st.set_page_config(
    page_title="타임폴리오 ETF 실시간 대시보드",
    layout="wide",
)

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


# =========================================================
# 나스닥100 선물 코드 자동 인식
# NQZ6, NQH7, NQM7, NQU7 등 지원
# 기존 NQU, NQ1!, NQ=도 지원
# =========================================================

def is_nasdaq_futures(symbol):
    s = str(symbol).strip().upper().split()[0]

    if any(
        keyword in s
        for keyword in [
            "NQ1!",
            "NQ=",
            "나스닥",
        ]
    ):
        return True

    # NQ + 만기월 코드 + 연도(1~4자리)
    if re.match(r"^NQ[HMUZ]\d{1,4}$", s):
        return True

    # 기존 NQU 같은 축약 코드
    if s in ["NQH", "NQM", "NQU", "NQZ"]:
        return True

    return False


def normalize_ticker(symbol):
    s = str(symbol).strip().upper()

    if not s:
        return ""

    s = s.split()[0]

    if is_nasdaq_futures(s):
        return "나스닥"

    if any(
        kw in s
        for kw in [
            "현금",
            "CASH",
            "KRW",
            "원화",
            "달러",
            "예금",
        ]
    ):
        return "현금"

    return s.replace("/", ".")


def get_market_session_status():
    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    weekday = now_kst.weekday()
    current_time_val = now_kst.hour * 60 + now_kst.minute

    is_korean_weekend = weekday >= 5
    korean_market_open = 9 * 60
    korean_market_close = 15 * 60 + 30

    is_korean_market_hours = (
        not is_korean_weekend
        and korean_market_open <= current_time_val <= korean_market_close
    )

    # 미국 현지 시간 기준 DST 계산
    now_us = now_kst.astimezone(ZoneInfo("America/New_York"))
    is_dst = bool(now_us.dst())

    premarket_start_val = (17 if is_dst else 18) * 60
    reg_start_val = (
        22 * 60 + 30
        if is_dst
        else 23 * 60 + 30
    )

    is_weekend_closed = (
        (weekday == 5 and now_kst.hour >= 9)
        or weekday == 6
        or (
            weekday == 0
            and current_time_val < premarket_start_val
        )
    )

    is_weekday_waiting = (
        weekday < 5
        and not is_korean_market_hours
        and korean_market_close <= current_time_val < premarket_start_val
    )

    is_pre_delay_buffer = (
        not is_weekend_closed
        and premarket_start_val
        <= current_time_val
        < premarket_start_val + 15
    )

    is_reg_delay_buffer = (
        not is_weekend_closed
        and reg_start_val
        <= current_time_val
        < reg_start_val + 15
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
    """타임폴리오 특정 날짜의 구성종목을 수집합니다."""

    url = f"https://timeetf.co.kr/m11_view.php?idx={idx}"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Referer": url,
    }

    data = []
    fetched_date = date_str

    try:
        if date_str:
            formatted_date_dot = date_str.replace("-", ".")

            post_data = {
                "pdfDate": formatted_date_dot,
                "idx": str(idx),
            }

            resp = requests.post(
                url,
                headers=headers,
                data=post_data,
                timeout=10,
            )

        else:
            resp = requests.get(
                url,
                headers=headers,
                timeout=10,
            )

        resp.raise_for_status()

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

            if len(tds) < 4:
                continue

            cols = [
                td.get_text().strip()
                for td in tds
            ]

            raw_code = cols[0]
            raw_name = cols[1]
            raw_weight = cols[-1]

            amt_val = 0.0

            for col_text in cols[1:-1]:
                clean_txt = col_text.replace(",", "").strip()

                if (
                    clean_txt.replace(".", "", 1).isdigit()
                    and float(clean_txt) > 1000
                ):
                    amt_val = float(clean_txt)
                    break

            if any(
                kw in raw_name.upper()
                for kw in ["현금", "예금", "CASH", "KRW"]
            ):
                clean_ticker = "현금"
            else:
                clean_ticker = (
                    raw_code.split()[0].strip().upper()
                    if raw_code.split()
                    else ""
                )

            if (
                not clean_ticker
                or clean_ticker in ["NAN", "NONE"]
                or any(
                    kw == clean_ticker
                    or kw in raw_code
                    or kw in raw_name
                    for kw in exclude_keywords
                )
                or re.match(
                    r"^\d{4}[.-/]\d{2}[.-/]\d{2}$",
                    clean_ticker,
                )
            ):
                continue

            try:
                weight_val = float(
                    raw_weight
                    .replace("%", "")
                    .replace(",", "")
                    .strip()
                )

                if 0 < weight_val <= 100:
                    data.append(
                        {
                            "종목코드": normalize_ticker(clean_ticker),
                            "비중": weight_val,
                            "평가금액": amt_val,
                        }
                    )

            except ValueError:
                pass

        if data:
            df = pd.DataFrame(data)

            # 동일 선물의 만기 코드가 바뀌어도 하나로 집계
            df = (
                df.groupby(
                    "종목코드",
                    as_index=False,
                )
                .agg(
                    {
                        "비중": "sum",
                        "평가금액": "sum",
                    }
                )
            )

            return df, fetched_date

    except Exception:
        pass

    return None, fetched_date


def get_naver_official_base_fx():
    headers = {"User-Agent": "Mozilla/5.0"}

    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    weekday = now_kst.weekday()

    if weekday >= 5 or now_kst.hour < 9:
        target_dt_str = get_prev_business_day(now_kst)
    else:
        target_dt_str = now_kst.strftime("%Y-%m-%d")

    try:
        url = (
            "https://query1.finance.yahoo.com/"
            "v8/finance/chart/USDKRW=X?interval=5m&range=5d"
        )

        resp = requests.get(
            url,
            headers=headers,
            timeout=5,
        )

        if resp.status_code == 200:
            json_data = resp.json()

            result = (
                json_data
                .get("chart", {})
                .get("result", [])
            )

            if result:
                timestamps = result[0].get("timestamp", [])

                indicators = (
                    result[0]
                    .get("indicators", {})
                    .get("quote", [{}])[0]
                )

                close_prices = indicators.get("close", [])

                target_time_val = 15 * 60 + 30
                best_rate = 0.0
                min_diff = float("inf")

                for ts, price in zip(timestamps, close_prices):
                    if price is None:
                        continue

                    dt_kst = datetime.fromtimestamp(
                        ts,
                        tz=ZoneInfo("Asia/Seoul"),
                    )

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


# =========================================================
# 네이버 ETF 현재가격 / NAV / 괴리율
# =========================================================

def get_naver_etf_market_data(ticker_code="426030"):
    result = {
        "current_price": 0.0,
        "prev_close": 0.0,
        "price_change_pct": 0.0,
        "naver_nav": 0.0,
        "naver_disparity": 0.0,
    }

    common_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
    }

    # 1. 네이버 모바일 API
    try:
        api_resp = requests.get(
            f"https://m.stock.naver.com/api/stock/{ticker_code}/basic",
            headers={
                **common_headers,
                "Accept": "application/json,text/plain,*/*",
                "Referer": (
                    f"https://m.stock.naver.com/domestic/"
                    f"stock/{ticker_code}/total"
                ),
            },
            timeout=5,
        )

        if api_resp.status_code == 200:
            api_data = api_resp.json()

            close_price = api_data.get("closePrice")

            if close_price is not None:
                try:
                    result["current_price"] = float(
                        str(close_price)
                        .replace(",", "")
                        .replace("원", "")
                        .strip()
                    )
                except (ValueError, TypeError):
                    pass

            fluctuations_ratio = api_data.get("fluctuationsRatio")

            if fluctuations_ratio is not None:
                try:
                    result["price_change_pct"] = float(
                        str(fluctuations_ratio)
                        .replace("%", "")
                        .replace(",", "")
                        .replace("+", "")
                        .strip()
                    )
                except (ValueError, TypeError):
                    pass

    except Exception:
        pass

    # 2. 네이버 ETF API
    try:
        etf_resp = requests.get(
            "https://finance.naver.com/api/sise/etfItemList.nhn",
            headers={
                **common_headers,
                "Accept": "application/json,text/plain,*/*",
                "Referer": "https://finance.naver.com/sise/etf.naver",
            },
            timeout=5,
        )

        if etf_resp.status_code == 200:
            etf_json = etf_resp.json()

            items = (
                etf_json
                .get("result", {})
                .get("etfItemList", [])
            )

            for item in items:
                if str(item.get("itemcode", "")) != str(ticker_code):
                    continue

                if result["current_price"] <= 0:
                    now_val = item.get("nowVal")

                    if now_val is not None:
                        try:
                            result["current_price"] = float(
                                str(now_val).replace(",", "").strip()
                            )
                        except (ValueError, TypeError):
                            pass

                if result["price_change_pct"] == 0.0:
                    change_rate = item.get("changeRate")

                    if change_rate is not None:
                        try:
                            result["price_change_pct"] = float(
                                str(change_rate)
                                .replace("%", "")
                                .replace(",", "")
                                .replace("+", "")
                                .strip()
                            )
                        except (ValueError, TypeError):
                            pass

                nav_val = item.get("nav")

                if nav_val is not None:
                    try:
                        result["naver_nav"] = float(
                            str(nav_val)
                            .replace(",", "")
                            .replace("원", "")
                            .strip()
                        )
                    except (ValueError, TypeError):
                        pass

                break

    except Exception:
        pass

    # 3. 네이버 PC fallback
    try:
        resp = requests.get(
            f"https://finance.naver.com/item/main.naver?code={ticker_code}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=5,
        )

        soup = BeautifulSoup(resp.text, "html.parser")

        if result["current_price"] <= 0:
            today_tag = soup.select_one("p.no_today em span.blind")

            if today_tag:
                try:
                    result["current_price"] = float(
                        today_tag.text.strip().replace(",", "")
                    )
                except (ValueError, TypeError):
                    pass

        if result["price_change_pct"] == 0.0:
            rate_info = soup.select_one("div.rate_info")

            if rate_info:
                exday_text = rate_info.get_text()

                is_minus = bool(
                    rate_info.select_one("p.no_exday em span.ico.down")
                    or rate_info.select_one("em.no_down")
                    or "하락" in exday_text
                )

                m_pct = re.search(
                    r"([\+\-]?\d+\.\d+)\s*%",
                    exday_text,
                )

                if m_pct:
                    val = float(m_pct.group(1))

                    result["price_change_pct"] = (
                        -val
                        if is_minus and val > 0
                        and "-" not in m_pct.group(1)
                        else val
                    )

        prev_tag = soup.select_one("td.first em span.blind")

        if prev_tag:
            try:
                result["prev_close"] = float(
                    prev_tag.text.strip().replace(",", "")
                )
            except (ValueError, TypeError):
                pass

        if result["naver_nav"] <= 0:
            page_html = resp.text

            nav_pattern = re.search(
                r"NAV[^<]*</t[dh]>\s*"
                r"<t[dh][^>]*>\s*"
                r"([\d,]+(?:\.\d+)?)\s*"
                r"</t[dh]>",
                page_html,
                re.IGNORECASE,
            )

            if nav_pattern:
                try:
                    result["naver_nav"] = float(
                        nav_pattern.group(1).replace(",", "")
                    )
                except (ValueError, TypeError):
                    pass

        if result["naver_nav"] <= 0:
            for tr in soup.find_all("tr"):
                tr_text = tr.get_text(" ", strip=True)

                if "NAV" not in tr_text:
                    continue

                nums = re.findall(
                    r"([\d,]+(?:\.\d+)?)",
                    tr_text,
                )

                for n in nums:
                    try:
                        val = float(n.replace(",", ""))

                        if 10000 <= val <= 200000:
                            result["naver_nav"] = val
                            break

                    except ValueError:
                        pass

                if result["naver_nav"] > 0:
                    break

    except Exception:
        pass

    # 4. 괴리율
    if (
        result["current_price"] > 0
        and result["naver_nav"] > 0
    ):
        result["naver_disparity"] = (
            (
                result["current_price"]
                - result["naver_nav"]
            )
            / result["naver_nav"]
        ) * 100

    return result


def get_timefolio_official_data(idx=2):
    result = {
        "live_nav": 0.0,
        "live_time": "",
        "base_nav": 0.0,
        "base_date": "",
    }

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
                m_val = re.search(
                    r"([\d,]+\.\d+)",
                    box_text,
                )

                if m_val:
                    result["live_nav"] = float(
                        m_val.group(1).replace(",", "")
                    )

                m_time = re.search(
                    r"(\d{4}[-.\/]\d{2}[-.\/]\d{2}\s+\d{2}:\d{2}:\d{2})",
                    box_text,
                )

                if m_time:
                    result["live_time"] = m_time.group(1)

            elif "기준가" in box_text and "실시간" not in box_text:
                m_val = re.search(
                    r"([\d,]+\.\d+)",
                    box_text,
                )

                if m_val:
                    result["base_nav"] = float(
                        m_val.group(1).replace(",", "")
                    )

                m_date = re.search(
                    r"(\d{4}[-.\/]\d{2}[-.\/]\d{2})",
                    box_text,
                )

                if m_date:
                    result["base_date"] = m_date.group(1)

    except Exception:
        pass

    return result


# =========================================================
# 실시간 종목 가격 조회
# 프리마켓: TradingView
# 본장 및 그 외: Finnhub
# 나스닥 선물: QQQ로 대체
# =========================================================

def get_realtime_prices_by_session(symbols):
    clean_symbols = []

    for s in symbols:
        normalized = normalize_ticker(s)

        if normalized == "나스닥":
            clean_symbols.append("QQQ")

        elif normalized != "현금" and normalized:
            clean_symbols.append(
                normalized.replace(".", "-")
            )

    clean_symbols = list(set(clean_symbols))

    result_map = {}
    live_fx = 0.0

    (
        _,
        _,
        is_pre_delay_buffer,
        is_reg_delay_buffer,
        now_kst,
        is_dst,
    ) = get_market_session_status()

    weekday = now_kst.weekday()
    current_time_val = now_kst.hour * 60 + now_kst.minute

    pre_start_val = (17 if is_dst else 18) * 60

    reg_start_val = (
        22 * 60 + 30
        if is_dst
        else 23 * 60 + 30
    )

    is_premarket_session = (
        weekday < 5
        and pre_start_val <= current_time_val < reg_start_val
    )

    if is_premarket_session:
        # TradingView 프리마켓
        tv_symbols = [
            f"NASDAQ:{s}"
            for s in clean_symbols
        ]

        try:
            tv_payload = {
                "symbols": {
                    "tickers": tv_symbols
                },
                "columns": [
                    "close",
                    "change",
                    "premarket_close",
                    "premarket_change",
                    "premarket_change_abs",
                    "market",
                ],
            }

            resp_tv = requests.post(
                "https://scanner.tradingview.com/america/scan",
                json=tv_payload,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=5,
            )

            if resp_tv.status_code == 200:
                tv_data = resp_tv.json().get("data", [])

                for item in tv_data:
                    s_name = (
                        item.get("s", "")
                        .split(":")[-1]
                        .upper()
                    )

                    d_vals = item.get("d", [])

                    if len(d_vals) >= 4:
                        close_val = d_vals[0] or 0.0
                        close_chg = d_vals[1] or 0.0
                        pm_close = d_vals[2]
                        pm_chg = d_vals[3]

                        if pm_close is not None and pm_close > 0:
                            p_val = float(pm_close)

                            c_val = (
                                float(pm_chg)
                                if pm_chg is not None
                                else 0.0
                            )

                        else:
                            p_val = float(close_val)
                            c_val = float(close_chg)

                        result_map[s_name] = (
                            p_val,
                            c_val,
                        )

        except Exception:
            pass

    else:
        # Finnhub
        finnhub_key = st.secrets.get(
            "FINNHUB_API_KEY",
            "",
        )

        if not finnhub_key:
            st.warning(
                "Finnhub API 키가 설정되지 않았습니다. "
                "Streamlit secrets에 FINNHUB_API_KEY를 등록하세요."
            )

        for s in clean_symbols:
            if not finnhub_key:
                break

            try:
                url = (
                    "https://finnhub.io/api/v1/quote"
                    f"?symbol={s}"
                    f"&token={finnhub_key}"
                )

                resp_fh = requests.get(
                    url,
                    timeout=4,
                )

                if resp_fh.status_code == 200:
                    fh_data = resp_fh.json()

                    current_p = float(
                        fh_data.get("c", 0.0)
                    )

                    prev_close_p = float(
                        fh_data.get("pc", 0.0)
                    )

                    if current_p > 0 and prev_close_p > 0:
                        change_p = (
                            (current_p - prev_close_p)
                            / prev_close_p
                        ) * 100

                        result_map[s] = (
                            current_p,
                            change_p,
                        )

                time.sleep(0.05)

            except Exception:
                pass

    # USD/KRW 환율
    session = requests.Session()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
        "Referer": "https://finance.yahoo.com/",
    }

    session.headers.update(headers)

    crumb = None

    try:
        session.get(
            "https://finance.yahoo.com/",
            timeout=4,
        )

        resp_crumb = session.get(
            "https://query1.finance.yahoo.com/v1/test/getcrumb",
            timeout=4,
        )

        if resp_crumb.status_code == 200 and resp_crumb.text:
            crumb = resp_crumb.text.strip()

    except Exception:
        pass

    try:
        fx_url = (
            "https://query1.finance.yahoo.com/"
            "v7/finance/quote?symbols=USDKRW=X"
            + (
                f"&crumb={crumb}"
                if crumb
                else ""
            )
        )

        resp_fx = session.get(
            fx_url,
            timeout=5,
        )

        if resp_fx.status_code == 200:
            quotes = (
                resp_fx.json()
                .get("quoteResponse", {})
                .get("result", [])
            )

            for q in quotes:
                if q.get("symbol", "").upper() == "USDKRW=X":
                    live_fx = float(
                        q.get("regularMarketPrice", 0.0)
                    )
                    break

    except Exception:
        pass

    if live_fx == 0.0:
        try:
            resp_fx = requests.post(
                "https://scanner.tradingview.com/forex/scan",
                json={
                    "symbols": {
                        "tickers": [
                            "FX_IDC:USDKRW",
                            "FX:USDKRW",
                        ]
                    },
                    "columns": ["close"],
                },
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=4,
            )

            if resp_fx.status_code == 200:
                data_fx = resp_fx.json().get("data", [])

                for fx_item in data_fx:
                    values = fx_item.get("d", [])

                    if values and values[0] is not None:
                        live_fx = float(values[0])
                        break

        except Exception:
            pass

    # 나스닥 선물 항목에 QQQ 가격 연결
    if "QQQ" in result_map:
        result_map["나스닥"] = result_map["QQQ"]

        for s in symbols:
            if is_nasdaq_futures(s):
                result_map[str(s).strip().upper()] = result_map["QQQ"]

    return result_map, live_fx


def color_change_pct(val):
    try:
        val_num = float(
            str(val).replace("%", "").strip()
        )

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

        val_num = float(
            val_str
            .replace("%", "")
            .replace("+", "")
            .strip()
        )

        if val_num > 0:
            return "color: #0055FF; font-weight: bold;"

        elif val_num < 0:
            return "color: #FF0000; font-weight: bold;"

    except Exception:
        pass

    return ""


# =========================================================
# 구성종목 불러오기
# =========================================================

st.markdown("### 🌐 구성종목 가져오기 방식을 선택하세요")

col1, col2 = st.columns(2)

with col1:
    fetch_auto = st.button(
        "🚀 자동 불러오기",
        use_container_width=True,
    )

with col2:
    toggle_uploader = st.button(
        "📁 엑셀 파일 수동 업로드하기",
        use_container_width=True,
    )

if "show_uploader" not in st.session_state:
    st.session_state.show_uploader = False

if toggle_uploader:
    st.session_state.show_uploader = (
        not st.session_state.show_uploader
    )

uploaded_file = None
uploaded_file_prev = None

if st.session_state.show_uploader:
    st.markdown("---")

    uploaded_file = st.file_uploader(
        "1️⃣ **[오늘]** 구성종목 엑셀 파일 (.xlsx)",
        type=["xlsx", "xls"],
        key="today",
    )

    uploaded_file_prev = st.file_uploader(
        "2️⃣ **[어제 / 직전 영업일]** 구성종목 엑셀 파일 (.xlsx)",
        type=["xlsx", "xls"],
        key="yesterday",
    )

    st.markdown("---")


df_input = None
df_prev = None
current_pdf_date_str = ""


if uploaded_file is None:

    with st.spinner("타임폴리오 웹사이트에서 구성종목 수집 중..."):

        now_kst_dt = datetime.now(
            ZoneInfo("Asia/Seoul")
        )

        target_date_candidate = now_kst_dt.strftime("%Y-%m-%d")

        for _ in range(5):
            df_input, _ = get_timefolio_constituents_by_date(
                idx=2,
                date_str=target_date_candidate,
            )

            if df_input is not None and not df_input.empty:
                current_pdf_date_str = target_date_candidate
                break

            target_date_candidate = get_prev_business_day(
                target_date_candidate
            )

        if df_input is None or df_input.empty:
            df_input, _ = get_timefolio_constituents_by_date(
                idx=2
            )

            current_pdf_date_str = target_date_candidate

        prev_pdf_date_str = get_prev_business_day(
            current_pdf_date_str
        )

        df_prev, _ = get_timefolio_constituents_by_date(
            idx=2,
            date_str=prev_pdf_date_str,
        )

        if (
            df_prev is not None
            and not df_prev.empty
            and df_input is not None
        ):
            curr_compare = (
                df_input.set_index("종목코드")["비중"]
                .sort_index()
            )

            prev_compare = (
                df_prev.set_index("종목코드")["비중"]
                .sort_index()
            )

            if curr_compare.equals(prev_compare):
                older_prev_date = get_prev_business_day(
                    prev_pdf_date_str
                )

                df_prev, _ = get_timefolio_constituents_by_date(
                    idx=2,
                    date_str=older_prev_date,
                )

else:
    try:
        raw_df_in = pd.read_excel(uploaded_file)

        in_t_col = None
        in_w_col = None
        in_n_col = None
        in_a_col = None

        for col in raw_df_in.columns:
            c_str = str(col)

            if (
                "코드" in c_str
                or "티커" in c_str
                or "Symbol" in c_str
            ):
                in_t_col = col

            elif (
                "비중" in c_str
                or "Weight" in c_str
            ):
                in_w_col = col

            elif (
                "명" in c_str
                or "Name" in c_str
            ):
                in_n_col = col

            elif (
                "금액" in c_str
                or "평가" in c_str
                or "Amount" in c_str
            ):
                in_a_col = col

        if in_t_col is None:
            in_t_col = raw_df_in.columns[0]

        if in_w_col is None:
            in_w_col = raw_df_in.columns[-1]

        clean_in_data = []

        for _, r in raw_df_in.iterrows():
            t_val = (
                str(r[in_t_col]).split()[0].strip().upper()
                if pd.notna(r[in_t_col])
                else ""
            )

            n_val = (
                str(r[in_n_col])
                if in_n_col is not None and pd.notna(r[in_n_col])
                else ""
            )

            w_val_raw = (
                str(r[in_w_col])
                .replace("%", "")
                .replace(",", "")
                .strip()
                if pd.notna(r[in_w_col])
                else "0"
            )

            amt_val = 0.0

            if in_a_col is not None and pd.notna(r[in_a_col]):
                try:
                    amt_val = float(
                        str(r[in_a_col]).replace(",", "").strip()
                    )
                except (ValueError, TypeError):
                    pass

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
                        {
                            "종목코드": normalize_ticker(t_val),
                            "비중": w_val,
                            "평가금액": amt_val,
                        }
                    )

            except ValueError:
                pass

        if clean_in_data:
            df_input = (
                pd.DataFrame(clean_in_data)
                .groupby("종목코드", as_index=False)
                .agg(
                    {
                        "비중": "sum",
                        "평가금액": "sum",
                    }
                )
            )

        if uploaded_file_prev is not None:
            raw_df_prev = pd.read_excel(uploaded_file_prev)

            p_t_col = None
            p_w_col = None
            p_n_col = None

            for col in raw_df_prev.columns:
                c_str = str(col)

                if (
                    "코드" in c_str
                    or "티커" in c_str
                    or "Symbol" in c_str
                ):
                    p_t_col = col

                elif (
                    "비중" in c_str
                    or "Weight" in c_str
                ):
                    p_w_col = col

                elif (
                    "명" in c_str
                    or "Name" in c_str
                ):
                    p_n_col = col

            if p_t_col is None:
                p_t_col = raw_df_prev.columns[0]

            if p_w_col is None:
                p_w_col = raw_df_prev.columns[-1]

            clean_prev_data = []

            for _, r in raw_df_prev.iterrows():
                t_val = (
                    str(r[p_t_col]).split()[0].strip().upper()
                    if pd.notna(r[p_t_col])
                    else ""
                )

                n_val = (
                    str(r[p_n_col])
                    if p_n_col is not None and pd.notna(r[p_n_col])
                    else ""
                )

                w_val_raw = (
                    str(r[p_w_col])
                    .replace("%", "")
                    .replace(",", "")
                    .strip()
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
                        clean_prev_data.append(
                            {
                                "종목코드": normalize_ticker(t_val),
                                "비중": w_val,
                            }
                        )

                except ValueError:
                    pass

            if clean_prev_data:
                df_prev = (
                    pd.DataFrame(clean_prev_data)
                    .groupby("종목코드", as_index=False)
                    .agg({"비중": "sum"})
                )

    except Exception as e:
        st.error(f"엑셀 파일 읽기 오류: {e}")


# =========================================================
# 메인 대시보드
# =========================================================

if df_input is not None and not df_input.empty:

    date_info_msg = (
        f" ({current_pdf_date_str} vs 전일)"
        if current_pdf_date_str
        else ""
    )

    st.success(
        f"✅ 총 {len(df_input)}개 종목 로드 완료!"
        f"{date_info_msg}"
    )

    with st.spinner("실시간 시세 연산 중..."):

        clean_df = (
            df_input
            .dropna(subset=["종목코드", "비중"])
            .copy()
        )

        clean_df["종목코드"] = (
            clean_df["종목코드"].map(normalize_ticker)
        )

        clean_df = (
            clean_df.groupby("종목코드", as_index=False)
            .agg(
                {
                    "비중": "sum",
                    "평가금액": "sum",
                }
            )
        )

        prev_weight_map = {}
        removed_stocks = []

        if df_prev is not None and not df_prev.empty:
            for _, p_row in df_prev.iterrows():
                p_code = normalize_ticker(
                    p_row["종목코드"]
                )

                try:
                    prev_weight_map[p_code] = (
                        prev_weight_map.get(p_code, 0.0)
                        + float(
                            str(p_row["비중"])
                            .replace("%", "")
                            .replace(",", "")
                            .strip()
                        )
                    )

                except ValueError:
                    pass

        ticker_list = list(
            dict.fromkeys(
                clean_df["종목코드"].tolist()
                + (
                    [
                        normalize_ticker(x)
                        for x in df_prev["종목코드"].tolist()
                    ]
                    if df_prev is not None and not df_prev.empty
                    else []
                )
            )
        )

        official_base_fx = get_naver_official_base_fx()

        naver_market = get_naver_etf_market_data("426030")

        timefolio_data = get_timefolio_official_data(idx=2)

        batch_results, live_fx = get_realtime_prices_by_session(
            ticker_list
        )

        if live_fx == 0.0 and official_base_fx > 0:
            live_fx = official_base_fx

        usdkrw_change_pct = (
            (
                (live_fx - official_base_fx)
                / official_base_fx
            ) * 100
            if official_base_fx > 0
            else 0.0
        )

        live_data = []
        new_added_stocks = []

        curr_tickers_map = {}

        for _, row in clean_df.iterrows():
            ticker = normalize_ticker(
                row["종목코드"]
            )

            try:
                weight = float(
                    str(row["비중"])
                    .replace("%", "")
                    .replace(",", "")
                    .strip()
                )
            except ValueError:
                weight = 0.0

            curr_tickers_map[ticker] = (
                weight,
                row,
            )

        processed_tickers = set()

        for ticker in ticker_list:
            ticker = normalize_ticker(ticker)

            if ticker in processed_tickers:
                continue

            processed_tickers.add(ticker)

            weight, row_data = curr_tickers_map.get(
                ticker,
                (0.0, None),
            )

            prev_w = prev_weight_map.get(ticker, None)

            w_diff_val = 0.0
            prev_w_str = "-"
            w_diff_str = "-"

            if prev_w is None:
                if weight > 0:
                    w_diff_str = "✨ NEW"
                    w_diff_val = weight

                    new_added_stocks.append(
                        f"**{ticker}** ({weight:.2f}%)"
                    )

            else:
                w_diff = weight - prev_w
                w_diff_val = w_diff
                prev_w_str = f"{prev_w:.2f}%"

                if weight == 0.0 and prev_w > 0:
                    w_diff_str = "🚪 OUT"

                    removed_stocks.append(
                        f"**{ticker}** (전일 {prev_w:.2f}%)"
                    )

                elif abs(w_diff) >= 0.001:
                    w_diff_str = f"{w_diff:+.2f}%"

                else:
                    w_diff_str = "+0.00%"

            if ticker == "현금":
                krw_amount = (
                    row_data.get("평가금액", 0.0)
                    if (
                        row_data is not None
                        and row_data.get("평가금액", 0.0) > 0
                    )
                    else 1.0
                )

                live_price = krw_amount
                stock_change_pct = 0.0

            elif ticker == "나스닥":
                live_price, stock_change_pct = (
                    batch_results.get(
                        "QQQ",
                        (0.0, 0.0),
                    )
                )

            else:
                lookup_ticker = ticker.replace(".", "-")

                live_price, stock_change_pct = (
                    batch_results.get(
                        lookup_ticker,
                        (0.0, 0.0),
                    )
                )

            live_data.append(
                {
                    "종목코드": ticker,
                    "실시간 가격($)": live_price,
                    "주가변동률(%)": stock_change_pct,
                    "당일비중(%)": weight,
                    "전일비중(%)": prev_w_str,
                    "비중변화(%)": w_diff_str,
                    "비중변화_수치": w_diff_val,
                }
            )

        result_df = pd.DataFrame(live_data)

        total_weight = result_df["당일비중(%)"].sum()

        if total_weight > 0:
            stock_inav_change = (
                (
                    result_df["당일비중(%)"]
                    / total_weight
                )
                * result_df["주가변동률(%)"]
            ).sum()

            total_inav_change = (
                stock_inav_change
                + usdkrw_change_pct
            )

        else:
            stock_inav_change = 0.0
            total_inav_change = 0.0

        # =========================================================
        # 현재가격 / 괴리율
        # =========================================================

        current_etf_price = naver_market["current_price"]
        price_change_pct = naver_market["price_change_pct"]
        naver_disp = naver_market["naver_disparity"]
        naver_nav = naver_market["naver_nav"]

        if current_etf_price > 0:
            pct_is_plus = price_change_pct >= 0

            chg_str = (
                f"<span style='background-color: "
                f"{'#ffebee' if pct_is_plus else '#e3f2fd'}; "
                f"color: "
                f"{'#c62828' if pct_is_plus else '#0277bd'}; "
                f"padding: 2px 8px; "
                f"border-radius: 12px; "
                f"font-size: 20px; "
                f"font-weight: normal; "
                f"display: inline-block;'>"
                f"({price_change_pct:+.2f}%)"
                f"</span>"
            )

            disp_is_plus = naver_disp >= 0

            nav_text_part = (
                f"(NAV: {naver_nav:,.0f}원 기준)"
                if naver_nav > 0
                else ""
            )

            st.markdown(
                "<div style='font-size: 14px; "
                "color: #6f727b; "
                "margin-bottom: 2px;'>"
                "🏷️ 현재가격"
                "</div>",
                unsafe_allow_html=True,
            )

            st.markdown(
                f"<p style='font-size: 42px; "
                f"font-weight: normal; "
                f"margin-bottom: 0px; "
                f"line-height: 1.2; "
                f"color: #1f1f1f;'>"
                f"{current_etf_price:,.0f} 원 "
                f"{chg_str}"
                f"</p>",
                unsafe_allow_html=True,
            )

            if naver_nav > 0:
                st.markdown(
                    f"<div style='display: inline-block; "
                    f"background-color: "
                    f"{'#ffebee' if disp_is_plus else '#e3f2fd'}; "
                    f"color: "
                    f"{'#c62828' if disp_is_plus else '#0277bd'}; "
                    f"padding: 2px 8px; "
                    f"border-radius: 12px; "
                    f"font-size: 14px; "
                    f"font-weight: 500; "
                    f"margin-top: 6px;'>"
                    f"{'↑' if disp_is_plus else '↓'} "
                    f"실시간 괴리율: "
                    f"{naver_disp:+.2f}% "
                    f"{nav_text_part}"
                    f"</div>",
                    unsafe_allow_html=True,
                )

        st.markdown(
            "<div style='margin-top: 20px;'></div>",
            unsafe_allow_html=True,
        )

        (
            is_korean_market_hours,
            is_weekday_waiting,
            is_pre_delay_buffer,
            is_reg_delay_buffer,
            now_kst,
            is_dst,
        ) = get_market_session_status()

        weekday = now_kst.weekday()
        curr_min = now_kst.hour * 60 + now_kst.minute

        pre_start_min = (17 if is_dst else 18) * 60

        reg_start_min = (
            22 * 60 + 30
            if is_dst
            else 23 * 60 + 30
        )

        is_premarket_session = (
            weekday < 5
            and pre_start_min <= curr_min < reg_start_min
        )

        delay_suffix = (
            " (15분 지연)"
            if is_premarket_session
            else ""
        )

        # =========================================================
        # 시장 상태별 iNAV 표시
        # =========================================================

        if is_korean_market_hours:
            status_label = "한국시장 거래중"
            status_note = (
                "ℹ️ 장중에는 실시간 가격과 괴리율을 참고하세요."
            )
            status_bg = "#e3f2fd"
            status_color = "#0277bd"

        elif is_weekday_waiting:
            status_label = "미국 프리마켓 대기 중 ⏳"
            status_note = (
                "ℹ️ 미국 프리마켓 시작시 "
                "실시간 추정치가 제공됩니다."
            )
            status_bg = "#fff3e0"
            status_color = "#e65100"

        elif is_pre_delay_buffer:
            pre_next_time = (
                "17:15"
                if is_dst
                else "18:15"
            )

            status_label = (
                "프리장 15분 지연데이터 대기 중 ⏳ "
                f"({pre_next_time}부터 제공)"
            )

            status_note = (
                "ℹ️ 미국 프리마켓 시작 후 15분간은 "
                "지연 시세 반영 대기 시간입니다."
            )
            status_bg = "#fff3e0"
            status_color = "#e65100"

        else:
            status_label = None

        if status_label is not None:
            for label in [
                f"📈 실시간 iNAV 추정 총 변동률{delay_suffix}",
                f"💵 나스닥100액티브(426030) 예상 iNAV{delay_suffix}",
            ]:
                st.markdown(
                    f"""
<div style="margin-bottom: 30px;">
<div style="font-size: 14px; color: #6f727b; margin-bottom: 2px;">{label}</div>
<div style="font-size: 32px; font-weight: normal; color: #1f1f1f; line-height: 1.2; margin-bottom: 4px;">{status_label}</div>
<div style="display: inline-block; background-color: {status_bg}; color: {status_color}; padding: 2px 8px; border-radius: 12px; font-size: 13px; font-weight: 500;">
{status_note}
</div>
</div>
""",
                    unsafe_allow_html=True,
                )

        else:
            def render_custom_metric(
                label,
                value,
                delta_text,
                is_plus,
                extra_info="",
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

            main_theme_color = (
                "#c62828"
                if total_is_plus
                else "#0277bd"
            )

            delta_detail_html = (
                f"주가: "
                f"<span style='color: {main_theme_color}; "
                f"font-weight:500;'>"
                f"{stock_inav_change:+.2f}%"
                f"</span> + 환율: "
                f"<span style='color: {main_theme_color}; "
                f"font-weight:500;'>"
                f"{usdkrw_change_pct:+.2f}%"
                f"</span><br>"
                f"(현재 환율: {live_fx:,.2f}원 / "
                f"기준 환율: {official_base_fx:,.2f}원)"
            )

            render_custom_metric(
                f"📈 실시간 iNAV 추정 총 변동률{delay_suffix}",
                f"{total_inav_change:+.2f}%",
                delta_detail_html,
                total_is_plus,
            )

            st.markdown(
                "<div style='margin-top: 20px;'></div>",
                unsafe_allow_html=True,
            )

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
                estimated_inav_price = (
                    base_nav_reference
                    * (1 + total_inav_change / 100)
                )

                diff_val = (
                    estimated_inav_price
                    - base_nav_reference
                )

                diff_is_plus = diff_val >= 0

                if current_etf_price > 0:
                    actual_vs_inav_pct = (
                        (
                            estimated_inav_price
                            - current_etf_price
                        )
                        / current_etf_price
                    ) * 100

                    actual_vs_inav_is_plus = (
                        actual_vs_inav_pct >= 0
                    )

                    actual_vs_inav_color = (
                        "#c62828"
                        if actual_vs_inav_is_plus
                        else "#0277bd"
                    )

                    inav_extra_info = (
                        f"<span style='"
                        f"background-color: "
                        f"{'#ffebee' if actual_vs_inav_is_plus else '#e3f2fd'}; "
                        f"color: {actual_vs_inav_color}; "
                        f"padding: 2px 8px; "
                        f"border-radius: 12px; "
                        f"font-size: 20px; "
                        f"font-weight: normal; "
                        f"display: inline-block;'>"
                        f"({actual_vs_inav_pct:+.2f}%)"
                        f"</span>"
                    )

                else:
                    inav_extra_info = ""

                render_custom_metric(
                    f"💵 나스닥100액티브(426030) 예상 iNAV{delay_suffix}",
                    f"{estimated_inav_price:,.0f} 원",
                    (
                        f"{diff_val:+,.0f} 원 "
                        f"(기준 iNAV: "
                        f"{base_nav_reference:,.0f}원)"
                    ),
                    diff_is_plus,
                    extra_info=inav_extra_info,
                )

        # =========================================================
        # 타임폴리오 공식 기준가
        # =========================================================

        if (
            timefolio_data["live_nav"] > 0
            or timefolio_data["base_nav"] > 0
        ):
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
                f"🏛️ **타임폴리오 공식 기준가** \n"
                f"- 실시간: {live_nav_val}{live_time_str} \n"
                f"- 전일 확정: {base_nav_val}{base_date_str}"
            )

        # =========================================================
        # 신규 편입 / 편출
        # =========================================================

        if new_added_stocks or removed_stocks:
            new_msg = (
                f"✨ **신규 편입**: "
                f"{', '.join(new_added_stocks)}"
                if new_added_stocks
                else "✨ **신규 편입**: 없음"
            )

            out_msg = (
                f"🚪 **편출 (전량 매도)**: "
                f"{', '.join(removed_stocks)}"
                if removed_stocks
                else "🚪 **편출 (전량 매도)**: 없음"
            )

            st.warning(
                f"📋 **포트폴리오 변동 내역** \n"
                f"- {new_msg} \n"
                f"- {out_msg}"
            )

        display_base_df = result_df.copy()

        # =========================================================
        # 히트맵
        # =========================================================

        st.markdown("---")

        active_df = (
            display_base_df[
                (display_base_df["당일비중(%)"] > 0)
                & (display_base_df["종목코드"] != "현금")
            ]
            .copy()
        )

        st.markdown(
            f"### 🔥 전체 구성종목 ({len(active_df)}개) 히트맵"
        )

        active_df["표시명"] = (
            "<span style='font-size:20px; font-weight:bold;'>"
            + active_df["종목코드"]
            + "</span><br><span style='font-size:15px;'>"
            + active_df["주가변동률(%)"].map("{:+.2f}%".format)
            + "</span>"
        )

        if not active_df.empty:
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
                textposition="middle center",
                selector=dict(type="treemap"),
            )

            fig_treemap.update_layout(
                margin=dict(t=5, l=5, r=5, b=5),
                height=600,
                uniformtext=dict(
                    minsize=8,
                    mode=False,
                ),
                coloraxis_showscale=False,
            )

            st.plotly_chart(
                fig_treemap,
                use_container_width=True,
            )

        # =========================================================
        # 전일 대비 비중 변화 TOP 10
        # =========================================================

        st.markdown("---")
        st.markdown("### 🔄 전일 대비 비중 변화 TOP 10")

        top10_change_df = display_base_df.copy()

        top10_change_df["절대변화량"] = (
            top10_change_df["비중변화_수치"].abs()
        )

        top10_change_df = (
            top10_change_df
            .sort_values(
                by="절대변화량",
                ascending=False,
            )
            .head(10)
        )

        display_top10 = (
            top10_change_df[
                [
                    "종목코드",
                    "당일비중(%)",
                    "전일비중(%)",
                    "비중변화(%)",
                    "주가변동률(%)",
                ]
            ]
            .reset_index(drop=True)
        )

        display_top10.index = range(
            1,
            len(display_top10) + 1,
        )

        if hasattr(display_top10.style, "map"):
            stiler_top10 = (
                display_top10.style
                .map(
                    color_weight_change,
                    subset=["비중변화(%)"],
                )
                .map(
                    color_change_pct,
                    subset=["주가변동률(%)"],
                )
            )
        else:
            stiler_top10 = (
                display_top10.style
                .applymap(
                    color_weight_change,
                    subset=["비중변화(%)"],
                )
                .applymap(
                    color_change_pct,
                    subset=["주가변동률(%)"],
                )
            )

        st.dataframe(
            stiler_top10
            .format(
                {
                    "당일비중(%)": "{:.2f}%",
                    "주가변동률(%)": "{:+.2f}%",
                }
            )
            .set_properties(
                **{"text-align": "center"}
            ),
            use_container_width=True,
            height=385,
        )

        # =========================================================
        # 종목별 실시간 전체 현황
        # =========================================================

        st.markdown("---")
        st.markdown("### 📊 종목별 실시간 전체 현황")

        display_full_df = (
            display_base_df[
                display_base_df["당일비중(%)"] > 0
            ]
            .copy()
            .sort_values(
                by="당일비중(%)",
                ascending=False,
            )
            .reset_index(drop=True)
        )

        display_full_df.index = range(
            1,
            len(display_full_df) + 1,
        )

        display_full_df["실시간 가격($)"] = (
            display_full_df.apply(
                lambda r: (
                    f"{r['실시간 가격($)']:,.2f}"
                    if r["실시간 가격($)"] > 0
                    else "-"
                ),
                axis=1,
            )
        )

        display_full_df["주가변동률(%)"] = (
            display_full_df["주가변동률(%)"]
            .apply(lambda x: f"{x:+.2f}%")
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

        if hasattr(display_full_df.style, "map"):
            styled_full = (
                display_full_df.style
                .map(
                    color_change_pct,
                    subset=["주가변동률(%)"],
                )
                .map(
                    color_weight_change,
                    subset=["비중변화(%)"],
                )
            )
        else:
            styled_full = (
                display_full_df.style
                .applymap(
                    color_change_pct,
                    subset=["주가변동률(%)"],
                )
                .applymap(
                    color_weight_change,
                    subset=["비중변화(%)"],
                )
            )

        st.dataframe(
            styled_full
            .format(
                {
                    "당일비중(%)": "{:.2f}%"
                }
            )
            .set_properties(
                **{"text-align": "center"}
            ),
            use_container_width=True,
            height=500,
        )

else:
    st.warning(
        "구성종목을 불러오지 못했습니다. "
        "타임폴리오 페이지 또는 엑셀 파일을 확인하세요."
    )
