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


def is_nasdaq_future(sym):
    """나스닥100 선물(NQU6, NQZ6, NQH7, MNQZ6, NQ1!, NQ=F 등) 여부를 만기월/연도와 상관없이 판별"""
    raw = str(sym).strip()

    if not raw:
        return False

    if "나스닥" in raw:
        return True

    s = raw.split()[0].upper()

    # 월물 코드: F G H J K M N Q U V X Z + 연도 숫자 (예: NQZ6, NQH27, MNQZ6)
    if re.match(r"^M?NQ[FGHJKMNQUVXZ]\d{1,4}$", s):
        return True

    # 연속월물 표기: NQ1!, NQ2!
    if re.match(r"^M?NQ\d*!$", s):
        return True

    # 야후식 표기: NQ=F
    if s.startswith("NQ="):
        return True

    if s == "NQ":
        return True

    return False


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

    # 기존 반환 구조 유지를 위해 남겨둠
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
                        data.append(
                            {
                                "종목코드": clean_ticker,
                                "비중": weight_val,
                                "평가금액": amt_val,
                            }
                        )

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


def _to_float(value):
    return float(
        str(value)
        .replace(",", "")
        .replace("원", "")
        .replace("%", "")
        .replace("+", "")
        .strip()
    )


# =========================================================
# 현재가격: 네이버 모바일 API 우선
# NAV: 네이버 ETF API 우선
# 괴리율: 현재가격과 NAV로 계산
# 기존 PC HTML은 fallback
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
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    # 1. 네이버 모바일 증권 API (현재가격 / 등락률)
    try:
        api_resp = requests.get(
            f"https://m.stock.naver.com/api/stock/{ticker_code}/basic",
            headers={
                **common_headers,
                "Accept": "application/json,text/plain,*/*",
                "Referer": f"https://m.stock.naver.com/domestic/stock/{ticker_code}/total",
            },
            timeout=5,
        )

        if api_resp.status_code == 200:
            api_data = api_resp.json()

            close_price = api_data.get("closePrice")
            if close_price is not None:
                try:
                    result["current_price"] = _to_float(close_price)
                except (ValueError, TypeError):
                    pass

            fluctuations_ratio = api_data.get("fluctuationsRatio")
            if fluctuations_ratio is not None:
                try:
                    result["price_change_pct"] = _to_float(fluctuations_ratio)
                except (ValueError, TypeError):
                    pass

    except Exception:
        pass

    # 2. 네이버 ETF API (현재가격 fallback / 등락률 fallback / NAV)
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
            items = etf_json.get("result", {}).get("etfItemList", [])

            for item in items:
                item_code = str(item.get("itemcode", ""))

                if item_code != str(ticker_code):
                    continue

                if result["current_price"] <= 0:
                    now_val = item.get("nowVal")
                    if now_val is not None:
                        try:
                            result["current_price"] = _to_float(now_val)
                        except (ValueError, TypeError):
                            pass

                if result["price_change_pct"] == 0.0:
                    change_rate = item.get("changeRate")
                    if change_rate is not None:
                        try:
                            result["price_change_pct"] = _to_float(change_rate)
                        except (ValueError, TypeError):
                            pass

                nav_val = item.get("nav")
                if nav_val is not None:
                    try:
                        result["naver_nav"] = _to_float(nav_val)
                    except (ValueError, TypeError):
                        pass

                break

    except Exception:
        pass

    # 3. 기존 네이버 PC 페이지 fallback
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

                m_pct = re.search(r"([\+\-]?\d+\.\d+)\s*%", exday_text)

                if m_pct:
                    val = float(m_pct.group(1))
                    result["price_change_pct"] = (
                        -val
                        if (is_minus and val > 0 and "-" not in m_pct.group(1))
                        else val
                    )

        # 전일종가
        prev_tag = soup.select_one("td.first em span.blind")
        if prev_tag:
            try:
                result["prev_close"] = float(prev_tag.text.strip().replace(",", ""))
            except (ValueError, TypeError):
                pass

        # NAV fallback
        if result["naver_nav"] <= 0:
            page_html = resp.text

            nav_pattern = re.search(
                r"NAV[^<]*</t[dh]>\s*<t[dh][^>]*>\s*([\d,]+(?:\.\d+)?)\s*</t[dh]>",
                page_html,
                re.IGNORECASE,
            )

            if nav_pattern:
                try:
                    result["naver_nav"] = float(nav_pattern.group(1).replace(",", ""))
                except (ValueError, TypeError):
                    pass

        if result["naver_nav"] <= 0:
            for tr in soup.find_all("tr"):
                tr_text = tr.get_text(" ", strip=True)

                if "NAV" not in tr_text:
                    continue

                nums = re.findall(r"([\d,]+(?:\.\d+)?)", tr_text)

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

    # 4. 괴리율 계산
    if result["current_price"] > 0 and result["naver_nav"] > 0:
        result["naver_disparity"] = (
            (result["current_price"] - result["naver_nav"]) / result["naver_nav"]
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
                m_val = re.search(r"([\d,]+\.\d+)", box_text)
                if m_val:
                    result["live_nav"] = float(m_val.group(1).replace(",", ""))

                m_time = re.search(
                    r"(\d{4}[-.\/]\d{2}[-.\/]\d{2}\s+\d{2}:\d{2}:\d{2})",
                    box_text,
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
    """현재 세션 상태에 따라 프리장은 트레이딩뷰, 본장/주말은 Finnhub API를 각각 명확히 호출"""

    clean_symbols = []

    for s in symbols:
        sym_str = str(s).split()[0].upper().replace("/", "-")

        # 나스닥 선물(만기월/연도 무관) → QQQ로 대체
        if is_nasdaq_future(s):
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
    reg_start_val = (22 * 60 + 30) if is_dst else (23 * 60 + 30)

    # 평일 프리마켓 시간대
    is_premarket_session = (
        weekday < 5 and pre_start_val <= current_time_val < reg_start_val
    )

    if is_premarket_session:
        # 1. 프리장 시간대 → TradingView
        tv_symbols = [
            f"NASDAQ:{s}" if s != "QQQ" else "NASDAQ:QQQ" for s in clean_symbols
        ]

        try:
            tv_payload = {
                "symbols": {"tickers": tv_symbols},
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
        # 2. 본장 / 주말 / 장마감 후 → Finnhub
        finnhub_key = st.secrets.get(
            "FINNHUB_API_KEY",
            "d9op4bpr01qnvunojplgd9op4bpr01qnvunojpm0",
        )

        for s in clean_symbols:
            try:
                url = f"https://finnhub.io/api/v1/quote?symbol={s}&token={finnhub_key}"

                resp_fh = requests.get(url, timeout=4)

                if resp_fh.status_code == 200:
                    fh_data = resp_fh.json()

                    current_p = float(fh_data.get("c", 0.0))
                    prev_close_p = float(fh_data.get("pc", 0.0))

                    if current_p > 0 and prev_close_p > 0:
                        change_p = ((current_p - prev_close_p) / prev_close_p) * 100
                        result_map[s] = (current_p, change_p)

                time.sleep(0.05)

            except Exception:
                pass

    # 환율 USDKRW
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
        session.get("https://finance.yahoo.com/", timeout=4)

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
            "https://query1.finance.yahoo.com/v7/finance/quote?symbols=USDKRW=X"
            + (f"&crumb={crumb}" if crumb else "")
        )

        resp_fx = session.get(fx_url, timeout=5)

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
                json={
                    "symbols": {"tickers": ["FX_IDC:USDKRW", "FX:USDKRW"]},
                    "columns": ["close"],
                },
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=4,
            )

            if resp_fx.status_code == 200:
                data_fx = resp_fx.json().get("data", [])

                if data_fx and data_fx[0].get("d"):
                    live_fx = (
                        float(data_fx[0]["d"][0])
                        if data_fx[0]["d"][0] is not None
                        else 0.0
                    )

        except Exception:
            pass

    if "QQQ" in result_map:
        for s in symbols:
            sym_upper = str(s).upper()

            # 나스닥 선물(만기월/연도 무관)에 QQQ 시세 매핑
            if is_nasdaq_future(s):
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


# =========================================================
# 📅 TOP 10 기간별 비중 비교 (1개월/3개월/6개월/1년 전)
# =========================================================
PERIOD_OPTIONS = {
    "1개월 전": 1,
    "3개월 전": 3,
    "6개월 전": 6,
    "1년 전": 12,
}


def normalize_compare_ticker(code):
    """기간 비교용 티커 정규화 (나스닥 선물은 월물 무관하게 '나스닥'으로 통일)"""
    s = str(code).strip()

    if is_nasdaq_future(s):
        return "나스닥"

    up = s.split()[0].upper() if s.split() else ""

    if up in ("KRW", "CASH") or "현금" in s:
        return "현금"

    return up


@st.cache_data(ttl=3600, show_spinner=False)
def get_period_snapshot(target_date_str, ref_weights, idx=2, max_back=5):
    """목표 날짜의 구성종목 스냅샷. 휴일/주말이면 직전 영업일로 보정.
    실패 시 예외를 발생시켜 실패 결과가 캐시되지 않도록 함."""
    d = datetime.strptime(target_date_str, "%Y-%m-%d")

    while d.weekday() >= 5:
        d -= timedelta(days=1)

    cand = d.strftime("%Y-%m-%d")

    for _ in range(max_back):
        df, _d = get_timefolio_constituents_by_date(idx=idx, date_str=cand)

        if df is not None and not df.empty:
            # 사이트가 해당 날짜 데이터가 없을 때 최신 데이터를 돌려주는 경우 방지
            if tuple(df["비중"].tolist()) != tuple(ref_weights):
                return df, cand

        cand = get_prev_business_day(cand)

    raise ValueError("snapshot not found")


def style_map_compat(df, func, subset):
    sty = df.style

    if hasattr(sty, "map"):
        return sty.map(func, subset=subset)

    return sty.applymap(func, subset=subset)


_fragment = getattr(st, "fragment", None) or getattr(
    st, "experimental_fragment", None
)

if _fragment is None:

    def _fragment(f):
        return f


# =========================================================
# 📖 종목·티커 설명 팝업 (종목명은 타임폴리오 공식 구성종목 표 기준)
# =========================================================
_dialog = getattr(st, "dialog", None) or getattr(st, "experimental_dialog", None)


@st.cache_data(ttl=3600, show_spinner=False)
def get_timefolio_name_map(date_str=None, idx=2):
    """타임폴리오 구성종목 표에서 {티커: 종목명} 매핑만 추출 (기존 크롤링 함수와 별도).
    실패/빈 결과는 예외로 처리해 캐시되지 않도록 함."""
    url = f"https://timeetf.co.kr/m11_view.php?idx={idx}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Referer": url,
    }

    if date_str:
        post_data = {
            "pdfDate": date_str.replace("-", "."),
            "idx": str(idx),
        }
        resp = requests.post(url, headers=headers, data=post_data, timeout=10)
    else:
        resp = requests.get(url, headers=headers, timeout=10)

    soup = BeautifulSoup(resp.text, "html.parser")

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

    name_map = {}

    for row in soup.select("table tr"):
        tds = row.select("td")

        if len(tds) < 4:
            continue

        cols = [td.get_text().strip() for td in tds]
        raw_code, raw_name, raw_weight = cols[0], cols[1], cols[-1]

        try:
            w_val = float(raw_weight.replace("%", "").replace(",", "").strip())
        except ValueError:
            continue

        if not (0 < w_val <= 100):
            continue

        if any(kw in raw_code or kw in raw_name for kw in exclude_keywords):
            continue

        upper_name = raw_name.upper()

        if (
            "현금" in raw_name
            or "예금" in raw_name
            or "CASH" in upper_name
            or "KRW" in upper_name
        ):
            name_map.setdefault("현금", raw_name or "현금")
            continue

        if not raw_code.split():
            continue

        key = normalize_compare_ticker(raw_code)

        if key and raw_name:
            name_map.setdefault(key, raw_name)

    if not name_map:
        raise ValueError("name map not found")

    return name_map


def resolve_name_map(date_candidates, needed_tickers):
    """필요한 티커의 종목명을 모두 찾을 때까지 여러 날짜의 구성종목 표를 합쳐서 조회"""
    found = {}

    for d in date_candidates:
        if all(t in found for t in needed_tickers):
            break

        try:
            m = get_timefolio_name_map(d)

            for k, v in m.items():
                found.setdefault(k, v)

        except Exception:
            pass

    return found


# =========================================================
# 🏢 기업 설명 / 섹터 (종목 행 클릭 시 팝업 안에 표시)
# =========================================================
SECTOR_KO = {
    "Technology": "기술 (IT)",
    "Communication Services": "커뮤니케이션 서비스",
    "Consumer Cyclical": "경기소비재",
    "Consumer Defensive": "필수소비재",
    "Healthcare": "헬스케어",
    "Financial Services": "금융",
    "Industrials": "산업재",
    "Energy": "에너지",
    "Utilities": "유틸리티",
    "Real Estate": "부동산",
    "Basic Materials": "소재",
}


def _translate_to_ko(text):
    if not text:
        return ""
    try:
        r = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={
                "client": "gtx",
                "sl": "en",
                "tl": "ko",
                "dt": "t",
                "q": text[:1500],
            },
            timeout=6,
        )
        if r.status_code == 200:
            return "".join(seg[0] for seg in r.json()[0] if seg and seg[0])
    except Exception:
        pass
    return ""


@st.cache_data(ttl=86400, show_spinner=False)
def get_company_profile(ticker):
    """기업 소개/섹터/산업 조회. 실패(빈 결과)는 예외로 처리해 캐시되지 않게 함."""
    sym = str(ticker).strip().upper().replace(".", "-")
    info = {"name": "", "sector": "", "industry": "", "summary": "", "website": ""}

    # 1) Yahoo Finance
    try:
        s = requests.Session()
        s.headers.update(
            {"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"}
        )
        s.get("https://finance.yahoo.com/", timeout=4)
        crumb = s.get(
            "https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=4
        ).text.strip()

        r = s.get(
            f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{sym}",
            params={"modules": "assetProfile,price", "crumb": crumb},
            timeout=6,
        )
        if r.status_code == 200:
            res = r.json()["quoteSummary"]["result"][0]
            ap = res.get("assetProfile", {})
            info["sector"] = ap.get("sector", "")
            info["industry"] = ap.get("industry", "")
            info["summary"] = ap.get("longBusinessSummary", "")
            info["website"] = ap.get("website", "")
            info["name"] = res.get("price", {}).get("longName", "")
    except Exception:
        pass

    # 2) Finnhub fallback (섹터/산업만)
    if not info["sector"] and not info["industry"]:
        try:
            key = st.secrets.get(
                "FINNHUB_API_KEY",
                "d9op4bpr01qnvunojplgd9op4bpr01qnvunojpm0",
            )
            r = requests.get(
                "https://finnhub.io/api/v1/stock/profile2",
                params={"symbol": sym, "token": key},
                timeout=5,
            )
            if r.status_code == 200 and r.json():
                d = r.json()
                info["industry"] = d.get("finnhubIndustry", "")
                info["name"] = info["name"] or d.get("name", "")
                info["website"] = info["website"] or d.get("weburl", "")
        except Exception:
            pass

    if not (info["sector"] or info["industry"] or info["summary"]):
        raise ValueError("profile not found")

    info["sector_ko"] = SECTOR_KO.get(info["sector"], info["sector"])
    info["summary_ko"] = _translate_to_ko(info["summary"]) if info["summary"] else ""
    return info


def render_company_card(ticker, display_name):
    if ticker == "현금":
        st.info("💵 **현금**: 운용 중인 원화 예금·현금성 자산입니다.")
        return

    if ticker == "나스닥":
        st.info(
            "📈 **나스닥100 선물**: 현물 주식을 사지 않고도 나스닥100 지수에 "
            "투자 효과를 내기 위한 파생상품입니다. 월물이 바뀌어도 QQQ 시세로 대체해 반영합니다."
        )
        return

    try:
        with st.spinner(f"{ticker} 기업 정보 불러오는 중..."):
            p = get_company_profile(ticker)
    except Exception:
        st.warning(f"⚠️ {ticker} 기업 정보를 가져오지 못했습니다. 잠시 후 다시 시도해주세요.")
        return

    title = (
        display_name
        if display_name and display_name != "-"
        else (p["name"] or ticker)
    )
    st.markdown(f"#### 🏢 {title} ({ticker})")
    st.markdown(
        f"**섹터**: {p.get('sector_ko') or '-'}  \n"
        f"**산업**: {p.get('industry') or '-'}"
    )

    body = p.get("summary_ko") or p.get("summary")
    if body:
        st.write(body)
        if not p.get("summary_ko"):
            st.caption("※ 번역에 실패해 원문(영어)을 표시합니다.")

    if p.get("website"):
        st.caption(f"🔗 {p['website']}")


def build_ticker_name_table(tickers, name_map, tags=None):
    rows = []

    for t in tickers:
        row = {
            "티커": t,
            "종목명": name_map.get(t, "-"),
        }

        if tags is not None:
            row["구분"] = tags.get(t, "")

        rows.append(row)

    df = pd.DataFrame(rows)
    df.index = range(1, len(df) + 1)

    return df


def show_ticker_popup(title, df):
    """팝업(dialog)으로 표시. 지원되지 않는 환경이면 펼침 영역(expander)으로 대체"""

    def _body():
        event = st.dataframe(
            df.style.set_properties(**{"text-align": "center"}),
            use_container_width=True,
            height=min(35 * (len(df) + 1) + 3, 360),
            on_select="rerun",
            selection_mode="single-row",
            key=f"sel_{title}",
        )

        rows = event.selection.rows if event and event.selection else []

        if rows:
            r = df.iloc[rows[0]]
            st.markdown("---")
            render_company_card(r["티커"], r["종목명"])
        else:
            st.caption("👆 표에서 종목 행을 클릭하면 기업 설명과 섹터가 나옵니다.")

        st.caption(
            "※ 종목명은 타임폴리오 공식 구성종목 표 기준입니다. "
            "'나스닥'은 나스닥100 선물이며, 월물이 바뀌어도 "
            "QQQ 시세로 대체해 반영합니다."
        )

    try:
        if _dialog is None:
            raise RuntimeError("dialog not supported")

        try:
            _dialog(title, width="large")(_body)()
        except TypeError:
            _dialog(title)(_body)()

    except Exception:
        with st.expander(title, expanded=True):
            _body()


@_fragment
def render_change_popup_button(tickers, base_date_str):
    """전일 대비 비중 변화 TOP10 표의 종목·티커 설명 팝업 버튼"""
    try:
        if st.button(
            "📖 표에 나온 종목·티커 설명 보기",
            key="popup_change_btn",
        ):
            prev1 = get_prev_business_day(base_date_str)
            prev2 = get_prev_business_day(prev1)

            names = resolve_name_map([None, base_date_str, prev1, prev2], tickers)

            show_ticker_popup(
                "전일 대비 비중 변화 TOP 10 – 종목·티커",
                build_ticker_name_table(tickers, names),
            )

    except Exception as e:
        st.warning(f"종목 설명을 표시하는 중 문제가 발생했습니다: {e}")


@_fragment
def render_period_compare(today_rows, base_date_str, ref_weights):
    """기간 선택 버튼 → 해당 기간 TOP10과 오늘 TOP10 비교 (이 영역만 다시 실행됨)"""
    try:
        choice = st.radio(
            "비교할 기간을 선택하세요",
            list(PERIOD_OPTIONS.keys()),
            horizontal=True,
            key="period_compare_choice",
        )

        months = PERIOD_OPTIONS[choice]

        target_date = (
            pd.Timestamp(base_date_str) - pd.DateOffset(months=months)
        ).strftime("%Y-%m-%d")

        try:
            with st.spinner(f"{choice} 구성종목 불러오는 중..."):
                snap_df, snap_date = get_period_snapshot(
                    target_date,
                    tuple(ref_weights),
                )
        except Exception:
            st.warning(
                f"⚠️ {choice}({target_date} 부근) 구성종목 데이터를 "
                f"가져오지 못했습니다. 잠시 후 다시 시도해주세요."
            )
            return

        past = snap_df.copy()
        past["티커"] = past["종목코드"].map(normalize_compare_ticker)
        past = past[past["티커"] != "현금"]

        past = (
            past.groupby("티커", as_index=False)["비중"]
            .sum()
            .sort_values(by="비중", ascending=False)
            .reset_index(drop=True)
        )

        past_map = dict(zip(past["티커"], past["비중"]))

        # 왼쪽: 오늘 TOP10 (기준 시점 대비 증감)
        left_rows = []

        for t_name, t_weight in today_rows:
            if t_name in past_map:
                diff = t_weight - past_map[t_name]
                diff_str = f"{diff:+.2f}%"
            else:
                diff_str = "✨ NEW"

            left_rows.append(
                {
                    "종목코드": t_name,
                    "비중": f"{t_weight:.2f}%",
                    "증감": diff_str,
                }
            )

        left_df = pd.DataFrame(left_rows)
        left_df.index = range(1, len(left_df) + 1)

        # 오른쪽: 기준 시점 TOP10
        right_df = past.head(10).copy()
        right_df = pd.DataFrame(
            {
                "종목코드": right_df["티커"],
                "비중": right_df["비중"].map("{:.2f}%".format),
            }
        )
        right_df.index = range(1, len(right_df) + 1)

        note = ""

        if snap_date != target_date:
            note = " (해당일 데이터가 없어 직전 영업일 기준)"

        st.caption(f"오늘({base_date_str}) vs {choice} 기준일({snap_date}){note}")

        col_l, col_r = st.columns(2)

        with col_l:
            st.markdown("**📌 오늘 TOP 10** (기준 시점 대비 증감)")

            st.dataframe(
                style_map_compat(
                    left_df,
                    color_weight_change,
                    ["증감"],
                ).set_properties(**{"text-align": "center"}),
                use_container_width=True,
                height=385,
            )

        with col_r:
            st.markdown(f"**🕰️ {choice} TOP 10** ({snap_date})")

            st.dataframe(
                right_df.style.set_properties(**{"text-align": "center"}),
                use_container_width=True,
                height=385,
            )

        # 표에 포함된 종목·티커 설명 팝업
        today_tickers = [t for t, _ in today_rows]
        past_tickers = right_df["종목코드"].tolist()

        popup_tickers = []
        popup_tags = {}

        for t in today_tickers + past_tickers:
            if t not in popup_tickers:
                popup_tickers.append(t)

            in_today = t in today_tickers
            in_past = t in past_tickers

            popup_tags[t] = (
                "오늘 · 기준시점"
                if (in_today and in_past)
                else ("오늘만" if in_today else "기준시점만")
            )

        if st.button(
            "📖 표에 나온 종목·티커 설명 보기",
            key="popup_period_btn",
        ):
            names = resolve_name_map(
                [None, base_date_str, snap_date],
                popup_tickers,
            )

            show_ticker_popup(
                f"{choice} 비교 TOP 10 – 종목·티커",
                build_ticker_name_table(popup_tickers, names, popup_tags),
            )

    except Exception as e:
        st.warning(f"기간별 비교를 표시하는 중 문제가 발생했습니다: {e}")


# =========================================================
# 엑셀 업로드 파서
# =========================================================
def parse_uploaded_excel(file, with_amount=False):
    raw_df = pd.read_excel(file)

    t_col = None
    w_col = None
    n_col = None
    a_col = None

    for col in raw_df.columns:
        c_str = str(col)

        if "코드" in c_str or "티커" in c_str or "Symbol" in c_str:
            t_col = col
        elif "비중" in c_str or "Weight" in c_str:
            w_col = col
        elif "명" in c_str or "Name" in c_str:
            n_col = col
        elif "금액" in c_str or "평가" in c_str or "Amount" in c_str:
            a_col = col

    if not t_col:
        t_col = raw_df.columns[0]

    if not w_col:
        w_col = raw_df.columns[-1]

    clean_data = []

    for _, r in raw_df.iterrows():
        t_val = (
            str(r[t_col]).split()[0].strip().upper() if pd.notna(r[t_col]) else ""
        )

        n_val = str(r[n_col]) if (n_col and pd.notna(r[n_col])) else ""

        w_val_raw = (
            str(r[w_col]).replace("%", "").replace(",", "").strip()
            if pd.notna(r[w_col])
            else "0"
        )

        amt_val = 0.0
        if with_amount:
            amt_val = (
                float(str(r[a_col]).replace(",", "").strip())
                if (
                    a_col
                    and pd.notna(r[a_col])
                    and str(r[a_col]).replace(".", "", 1).isdigit()
                )
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
                item = {"종목코드": t_val, "비중": w_val}
                if with_amount:
                    item["평가금액"] = amt_val
                clean_data.append(item)

        except ValueError:
            pass

    return (
        pd.DataFrame(clean_data)
        .drop_duplicates(subset=["종목코드"])
        .reset_index(drop=True)
    )


# =========================================================
# 상태 카드 렌더링 helper
# =========================================================
def render_status_pair(delay_suffix, big_text, badge_bg, badge_color, badge_text):
    labels = [
        f"📈 실시간 iNAV 추정 총 변동률{delay_suffix}",
        f"💵 나스닥100액티브(426030) 예상 iNAV{delay_suffix}",
    ]

    for i, label in enumerate(labels):
        st.markdown(
            f"""
<div style="margin-bottom: 10px;">
<div style="font-size: 14px; color: #6f727b; margin-bottom: 2px;">{label}</div>
<div style="font-size: 32px; font-weight: normal; color: #1f1f1f; line-height: 1.2; margin-bottom: 4px;">{big_text}</div>
<div style="display: inline-block; background-color: {badge_bg}; color: {badge_color}; padding: 2px 8px; border-radius: 12px; font-size: 13px; font-weight: 500;">
{badge_text}
</div>
</div>
""",
            unsafe_allow_html=True,
        )

        if i == 0:
            st.markdown(
                "<div style='margin-top: 20px;'></div>",
                unsafe_allow_html=True,
            )


def render_custom_metric(label, value, delta_text, is_plus, extra_info=""):
    bg_color, text_color, arrow = (
        ("#ffebee", "#c62828", "↑") if is_plus else ("#e3f2fd", "#0277bd", "↓")
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


# =========================================================
# 메인 UI
# =========================================================
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
        now_kst_dt = datetime.now(ZoneInfo("Asia/Seoul"))
        target_date_candidate = now_kst_dt.strftime("%Y-%m-%d")

        for _ in range(5):
            df_input, _ = get_timefolio_constituents_by_date(
                idx=2,
                date_str=target_date_candidate,
            )

            if df_input is not None and not df_input.empty:
                current_pdf_date_str = target_date_candidate
                break

            target_date_candidate = get_prev_business_day(target_date_candidate)

        if df_input is None or df_input.empty:
            df_input, _ = get_timefolio_constituents_by_date(idx=2)
            current_pdf_date_str = target_date_candidate

        prev_pdf_date_str = get_prev_business_day(current_pdf_date_str)

        df_prev, _ = get_timefolio_constituents_by_date(
            idx=2,
            date_str=prev_pdf_date_str,
        )

        if df_prev is not None and not df_prev.empty and df_input is not None:
            if df_input["비중"].tolist() == df_prev["비중"].tolist():
                older_prev_date = get_prev_business_day(prev_pdf_date_str)

                df_prev, _ = get_timefolio_constituents_by_date(
                    idx=2,
                    date_str=older_prev_date,
                )

else:
    try:
        df_input = parse_uploaded_excel(uploaded_file, with_amount=True)

        if uploaded_file_prev is not None:
            df_prev = parse_uploaded_excel(uploaded_file_prev, with_amount=False)

    except Exception as e:
        st.error(f"엑셀 파일 읽기 오류: {e}")


if df_input is not None and not df_input.empty:
    date_info_msg = (
        f" ({current_pdf_date_str} vs 전일)" if current_pdf_date_str else ""
    )

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
                    if (df_prev is not None and not df_prev.empty)
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

        live_data = []
        new_added_stocks = []

        cash_keywords = ["KRW", "CASH", "현금", "원화", "달러", "예금"]

        curr_tickers_map = {}

        for _, row in clean_df.iterrows():
            raw_ticker = str(row["종목코드"]).strip()
            ticker = raw_ticker.split()[0].upper().replace("/", ".")

            if any(kw in ticker for kw in cash_keywords) or ticker == "KRW":
                ticker = "현금"

            try:
                weight = float(
                    str(row["비중"]).replace("%", "").replace(",", "").strip()
                )
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
                ticker,
                (prev_weight_map.get("KRW", None) if ticker == "현금" else None),
            )

            w_diff_val = 0.0
            prev_w_str = "-"
            w_diff_str = "-"

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
                    if (row_data is not None and row_data.get("평가금액", 0.0) > 0)
                    else 1.0
                )

                live_data.append(
                    {
                        "종목코드": "현금",
                        "실시간 가격($)": krw_amount,
                        "주가변동률(%)": 0.0,
                        "당일비중(%)": weight,
                        "전일비중(%)": prev_w_str,
                        "비중변화(%)": w_diff_str,
                        "비중변화_수치": w_diff_val,
                    }
                )

            # 나스닥 선물(만기월/연도 무관) → QQQ 시세로 표시
            elif is_nasdaq_future(ticker):
                qqq_price, qqq_change = batch_results.get("QQQ", (0.0, 0.0))

                live_data.append(
                    {
                        "종목코드": "나스닥",
                        "실시간 가격($)": qqq_price,
                        "주가변동률(%)": qqq_change,
                        "당일비중(%)": weight,
                        "전일비중(%)": prev_w_str,
                        "비중변화(%)": w_diff_str,
                        "비중변화_수치": w_diff_val,
                    }
                )

            elif ticker in batch_results and batch_results[ticker][0] > 0:
                live_price, stock_change_pct = batch_results[ticker]

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

            else:
                live_data.append(
                    {
                        "종목코드": ticker,
                        "실시간 가격($)": 0.0,
                        "주가변동률(%)": 0.0,
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
                (result_df["당일비중(%)"] / total_weight) * result_df["주가변동률(%)"]
            ).sum()

            total_inav_change = stock_inav_change + usdkrw_change_pct

        else:
            stock_inav_change = 0.0
            total_inav_change = 0.0

        # =========================================================
        # 🏷️ 상단 현재가격 및 괴리율 카드
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
                f"color: {'#c62828' if pct_is_plus else '#0277bd'}; "
                f"padding: 2px 8px; border-radius: 12px; "
                f"font-size: 20px; font-weight: normal; "
                f"display: inline-block;'>"
                f"({price_change_pct:+.2f}%)</span>"
            )

            disp_is_plus = naver_disp >= 0

            nav_text_part = (
                f"(NAV: {naver_nav:,.0f}원 기준)" if naver_nav > 0 else ""
            )

            st.markdown(
                "<div style='font-size: 14px; color: #6f727b; margin-bottom: 2px;'>"
                "🏷️ 현재가격</div>",
                unsafe_allow_html=True,
            )

            st.markdown(
                f"<p style='font-size: 42px; font-weight: normal; "
                f"margin-bottom: 0px; line-height: 1.2; color: #1f1f1f;'>"
                f"{current_etf_price:,.0f} 원 {chg_str}</p>",
                unsafe_allow_html=True,
            )

            st.markdown(
                f"<div style='display: inline-block; "
                f"background-color: {'#ffebee' if disp_is_plus else '#e3f2fd'}; "
                f"color: {'#c62828' if disp_is_plus else '#0277bd'}; "
                f"padding: 2px 8px; border-radius: 12px; "
                f"font-size: 14px; font-weight: 500; margin-top: 6px;'>"
                f"{'↑' if disp_is_plus else '↓'} "
                f"실시간 괴리율: {naver_disp:+.2f}% {nav_text_part}</div>",
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

        # 평일 프리마켓 시간 여부
        weekday = now_kst.weekday()
        curr_min = now_kst.hour * 60 + now_kst.minute

        pre_start_min = (17 if is_dst else 18) * 60
        reg_start_min = (22 * 60 + 30) if is_dst else (23 * 60 + 30)

        is_premarket_session = (
            weekday < 5 and pre_start_min <= curr_min < reg_start_min
        )

        # 프리장일 때만 15분 지연 표시
        delay_suffix = " (15분 지연)" if is_premarket_session else ""

        if is_korean_market_hours:
            render_status_pair(
                delay_suffix,
                "한국시장 거래중",
                "#e3f2fd",
                "#0277bd",
                "ℹ️ 장중에는 실시간 가격과 괴리율을 참고하세요.",
            )

        elif is_weekday_waiting:
            render_status_pair(
                delay_suffix,
                "미국 프리마켓 대기 중 ⏳",
                "#fff3e0",
                "#e65100",
                "ℹ️ 미국 프리마켓 시작시 실시간 추정치가 제공됩니다.",
            )

        # 프리장 시작 후 15분만 대기 (본장 is_reg_delay_buffer는 사용하지 않음)
        elif is_pre_delay_buffer:
            pre_next_time = "17:15" if is_dst else "18:15"

            delay_label = (
                f"프리장 15분 지연데이터 대기 중 ⏳ ({pre_next_time}부터 제공)"
            )

            render_status_pair(
                delay_suffix,
                delay_label,
                "#fff3e0",
                "#e65100",
                "ℹ️ 미국 프리마켓 시작 후 15분간은 지연 시세 반영 대기 시간입니다.",
            )

        else:
            total_is_plus = total_inav_change >= 0

            main_theme_color = "#c62828" if total_is_plus else "#0277bd"

            delta_detail_html = (
                f"주가: <span style='color: {main_theme_color}; font-weight:500;'>"
                f"{stock_inav_change:+.2f}%</span> + 환율: "
                f"<span style='color: {main_theme_color}; font-weight:500;'>"
                f"{usdkrw_change_pct:+.2f}%</span><br>"
                f"(현재 환율: {live_fx:,.2f}원 / 기준 환율: {official_base_fx:,.2f}원)"
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
                estimated_inav_price = base_nav_reference * (
                    1 + (total_inav_change / 100)
                )

                diff_val = estimated_inav_price - base_nav_reference
                diff_is_plus = diff_val >= 0

                if current_etf_price > 0:
                    actual_vs_inav_pct = (
                        (estimated_inav_price - current_etf_price) / current_etf_price
                    ) * 100

                    actual_vs_inav_is_plus = actual_vs_inav_pct >= 0

                    actual_vs_inav_color = (
                        "#c62828" if actual_vs_inav_is_plus else "#0277bd"
                    )

                    inav_extra_info = (
                        f"<span style='background-color: "
                        f"{'#ffebee' if actual_vs_inav_is_plus else '#e3f2fd'}; "
                        f"color: {actual_vs_inav_color}; "
                        f"padding: 2px 8px; border-radius: 12px; "
                        f"font-size: 20px; font-weight: normal; "
                        f"display: inline-block;'>"
                        f"({actual_vs_inav_pct:+.2f}%)</span>"
                    )

                else:
                    inav_extra_info = ""

                render_custom_metric(
                    f"💵 나스닥100액티브(426030) 예상 iNAV{delay_suffix}",
                    f"{estimated_inav_price:,.0f} 원",
                    (
                        f"{diff_val:+,.0f} 원 "
                        f"(기준 iNAV: {base_nav_reference:,.0f}원)"
                    ),
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

            tf_base_date_str = (
                f" ({timefolio_data['base_date']})"
                if timefolio_data["base_date"]
                else ""
            )

            st.success(
                f"🏛️ **타임폴리오 공식 기준가** \n"
                f"- 실시간: {live_nav_val}{live_time_str} \n"
                f"- 전일 확정: {base_nav_val}{tf_base_date_str}"
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

            st.warning(f"📋 **포트폴리오 변동 내역** \n- {new_msg} \n- {out_msg}")

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
            textposition="middle center",
            selector=dict(type="treemap"),
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
            by="절대변화량",
            ascending=False,
        ).head(10)

        display_top10 = top10_change_df[
            [
                "종목코드",
                "당일비중(%)",
                "전일비중(%)",
                "비중변화(%)",
                "주가변동률(%)",
            ]
        ].reset_index(drop=True)

        display_top10.index = range(1, len(display_top10) + 1)

        stiler_top10 = (
            display_top10.style.map(color_weight_change, subset=["비중변화(%)"]).map(
                color_change_pct, subset=["주가변동률(%)"]
            )
            if hasattr(display_top10.style, "map")
            else (
                display_top10.style.applymap(
                    color_weight_change, subset=["비중변화(%)"]
                ).applymap(color_change_pct, subset=["주가변동률(%)"])
            )
        )

        st.dataframe(
            stiler_top10.format(
                {
                    "당일비중(%)": "{:.2f}%",
                    "주가변동률(%)": "{:+.2f}%",
                }
            ).set_properties(**{"text-align": "center"}),
            use_container_width=True,
            height=385,
        )

        render_change_popup_button(
            display_top10["종목코드"].tolist(),
            (
                current_pdf_date_str
                if current_pdf_date_str
                else datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
            ),
        )

        # =========================================================
        # 📅 TOP 10 기간별 비중 비교
        # =========================================================
        st.markdown("---")

        st.markdown("### 📅 TOP 10 기간별 비중 비교")

        period_today_df = (
            display_base_df[
                (display_base_df["당일비중(%)"] > 0)
                & (display_base_df["종목코드"] != "현금")
            ]
            .sort_values(by="당일비중(%)", ascending=False)
            .head(10)
        )

        period_today_rows = [
            (str(r["종목코드"]), float(r["당일비중(%)"]))
            for _, r in period_today_df.iterrows()
        ]

        period_base_date = (
            current_pdf_date_str
            if current_pdf_date_str
            else datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
        )

        render_period_compare(
            period_today_rows,
            period_base_date,
            tuple(df_input["비중"].tolist()),
        )

        # =========================================================
        # 📊 종목별 실시간 전체 현황
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
            lambda r: (
                f"{r['실시간 가격($)']:,.2f}" if r["실시간 가격($)"] > 0 else "-"
            ),
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
            display_full_df.style.map(color_change_pct, subset=["주가변동률(%)"]).map(
                color_weight_change, subset=["비중변화(%)"]
            )
            if hasattr(display_full_df.style, "map")
            else (
                display_full_df.style.applymap(
                    color_change_pct, subset=["주가변동률(%)"]
                ).applymap(color_weight_change, subset=["비중변화(%)"])
            )
        )

        st.dataframe(
            styled_full.format({"당일비중(%)": "{:.2f}%"}).set_properties(
                **{"text-align": "center"}
            ),
            use_container_width=True,
            height=500,
        )
