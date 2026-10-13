from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import re
import time

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(page_title="타임폴리오 ETF 실시간 대시보드", layout="wide")
st.title("🎯 타임폴리오 액티브")
st.markdown("타임폴리오 공식 홈페이지에서 나스닥100 액티브 **전체 구성종목**, **전일 대비 비중 변화**, **실시간 기준가**를 연동합니다.")
st.link_button("🔗 타임폴리오 공식 구성종목 페이지 바로가기", "https://timeetf.co.kr/m11_view.php?idx=2#constituentItems", use_container_width=True)
st.markdown("---")

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36"}
# 기존에 제공한 키를 복구. 공개 저장소에서는 반드시 Secrets로 옮기고 키를 재발급하세요.
DEFAULT_FINNHUB_KEY = "d9op4bpr01qnvunojplgd9op4bpr01qnvunojpm0"


def is_nq_future(symbol):
    s = str(symbol).strip().upper().split()[0]
    return bool(re.fullmatch(r"NQ[HMUZ]\d{1,4}", s) or s in ("NQH", "NQM", "NQU", "NQZ", "NQ1!", "NQ=") or "나스닥" in s or s.startswith("NQ1!"))


def normalize(symbol):
    s = str(symbol).strip().upper().split()[0] if str(symbol).strip() else ""
    if is_nq_future(s):
        return "나스닥"
    if any(k in s for k in ("현금", "CASH", "KRW", "원화", "예금")):
        return "현금"
    return s.replace("/", "-")


def previous_weekday(date):
    if isinstance(date, str):
        date = datetime.strptime(date, "%Y-%m-%d")
    date -= timedelta(days=1)
    while date.weekday() >= 5:
        date -= timedelta(days=1)
    return date.strftime("%Y-%m-%d")


def session_status():
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    dst = bool(now.astimezone(ZoneInfo("America/New_York")).dst())
    minute = now.hour * 60 + now.minute
    pre = (17 if dst else 18) * 60
    reg = (22 * 60 + 30) if dst else (23 * 60 + 30)
    kr_open = now.weekday() < 5 and 540 <= minute <= 930
    waiting = now.weekday() < 5 and 930 < minute < pre
    premarket = now.weekday() < 5 and pre <= minute < reg
    buffer = premarket and minute < pre + 15
    return now, dst, kr_open, waiting, premarket, buffer


def clean_holdings(df):
    if df is None or df.empty:
        return None
    df = df.copy()
    df["종목코드"] = df["종목코드"].map(normalize)
    df["비중"] = pd.to_numeric(df["비중"], errors="coerce")
    df["평가금액"] = pd.to_numeric(df.get("평가금액", 0), errors="coerce").fillna(0)
    df = df.dropna(subset=["비중"])
    df = df[(df["비중"] > 0) & (df["비중"] <= 100) & (df["종목코드"] != "")]
    if df.empty:
        return None
    return df.groupby("종목코드", as_index=False).agg({"비중": "sum", "평가금액": "sum"})


def fetch_holdings(date_str=None, idx=2):
    url = f"https://timeetf.co.kr/m11_view.php?idx={idx}"
    try:
        if date_str:
            response = requests.post(url, headers={**HEADERS, "Referer": url}, data={"pdfDate": date_str.replace("-", "."), "idx": str(idx)}, timeout=10)
        else:
            response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        excluded = ("기준가", "비교지수", "초과성과", "총액", "합계", "TOTAL", "CODE", "DATE", "날짜", "일자")
        items = []
        for tr in soup.select("table tr"):
            cols = [td.get_text(" ", strip=True) for td in tr.select("td")]
            if len(cols) < 4:
                continue
            code, name, raw_weight = cols[0], cols[1], cols[-1]
            if any(k in code.upper() or k in name.upper() for k in excluded):
                continue
            ticker = "현금" if any(k in name.upper() for k in ("현금", "예금", "CASH", "KRW")) else normalize(code)
            if ticker in ("", "NAN", "NONE") or re.fullmatch(r"\d{4}[./-]\d{2}[./-]\d{2}", ticker):
                continue
            try:
                weight = float(raw_weight.replace(",", "").replace("%", "").strip())
            except ValueError:
                continue
            amount = 0.0
            for col in cols[1:-1]:
                try:
                    n = float(col.replace(",", "").strip())
                    if n > 1000:
                        amount = n
                        break
                except ValueError:
                    continue
            items.append({"종목코드": ticker, "비중": weight, "평가금액": amount})
        return clean_holdings(pd.DataFrame(items)) if items else None
    except (requests.RequestException, ValueError):
        return None


def read_holdings_excel(file):
    df = pd.read_excel(file)
    columns = list(df.columns)
    def find_col(words, default=None):
        return next((c for c in columns if any(w.lower() in str(c).lower() for w in words)), default)
    code_col = find_col(("코드", "티커", "symbol"), columns[0])
    weight_col = find_col(("비중", "weight"), columns[-1])
    name_col = find_col(("종목명", "name", "명칭"))
    amount_col = find_col(("평가", "금액", "amount"))
    rows = []
    for _, row in df.iterrows():
        code = str(row[code_col]).strip() if pd.notna(row[code_col]) else ""
        name = str(row[name_col]) if name_col is not None and pd.notna(row[name_col]) else ""
        if any(k in name.upper() for k in ("현금", "예금", "CASH", "KRW")):
            code = "현금"
        try:
            weight = float(str(row[weight_col]).replace("%", "").replace(",", ""))
        except (ValueError, TypeError):
            continue
        try:
            amount = float(str(row[amount_col]).replace(",", "")) if amount_col is not None else 0.0
        except (ValueError, TypeError):
            amount = 0.0
        rows.append({"종목코드": code, "비중": weight, "평가금액": amount})
    return clean_holdings(pd.DataFrame(rows)) if rows else None


def fetch_etf_market(code="426030"):
    data = {"current_price": 0.0, "prev_close": 0.0, "price_change_pct": 0.0, "naver_nav": 0.0, "naver_disparity": 0.0}
    def number(v):
        try:
            return float(str(v).replace(",", "").replace("%", "").replace("원", "").strip())
        except (ValueError, TypeError):
            return 0.0
    try:
        r = requests.get(f"https://m.stock.naver.com/api/stock/{code}/basic", headers={**HEADERS, "Referer": f"https://m.stock.naver.com/domestic/stock/{code}/total"}, timeout=5)
        if r.ok:
            d = r.json()
            data["current_price"] = number(d.get("closePrice"))
            data["price_change_pct"] = number(d.get("fluctuationsRatio"))
    except (requests.RequestException, ValueError):
        pass
    try:
        r = requests.get("https://finance.naver.com/api/sise/etfItemList.nhn", headers=HEADERS, timeout=5)
        if r.ok:
            for d in r.json().get("result", {}).get("etfItemList", []):
                if str(d.get("itemcode")) == code:
                    data["current_price"] = data["current_price"] or number(d.get("nowVal"))
                    if not data["price_change_pct"]:
                        data["price_change_pct"] = number(d.get("changeRate"))
                    data["naver_nav"] = number(d.get("nav"))
                    break
    except (requests.RequestException, ValueError):
        pass
    try:
        r = requests.get(f"https://finance.naver.com/item/main.naver?code={code}", headers=HEADERS, timeout=5)
        soup = BeautifulSoup(r.text, "html.parser")
        today = soup.select_one("p.no_today em span.blind")
        prev = soup.select_one("td.first em span.blind")
        if today and not data["current_price"]:
            data["current_price"] = number(today.get_text())
        if prev:
            data["prev_close"] = number(prev.get_text())
        if not data["naver_nav"]:
            for tr in soup.select("tr"):
                txt = tr.get_text(" ", strip=True)
                if "NAV" in txt.upper():
                    nums = re.findall(r"[\d,]+(?:\.\d+)?", txt)
                    for v in nums:
                        n = number(v)
                        if 10000 <= n <= 200000:
                            data["naver_nav"] = n
                            break
                    if data["naver_nav"]:
                        break
    except (requests.RequestException, ValueError):
        pass
    if data["current_price"] and data["naver_nav"]:
        data["naver_disparity"] = (data["current_price"] / data["naver_nav"] - 1) * 100
    return data


def fetch_official_nav(idx=2):
    result = {"live_nav": 0.0, "live_time": "", "base_nav": 0.0, "base_date": ""}
    try:
        r = requests.get(f"https://timeetf.co.kr/m11_view.php?idx={idx}", headers=HEADERS, timeout=5)
        soup = BeautifulSoup(r.text, "html.parser")
        boxes = soup.select("div.standard_price_box") or soup.select("ul.price_info li") or soup.find_all("div")
        for box in boxes:
            txt = box.get_text(" ", strip=True)
            match = re.search(r"([\d,]+\.\d+)", txt)
            if "실시간" in txt and "기준가" in txt:
                if match:
                    result["live_nav"] = float(match.group(1).replace(",", ""))
                dt = re.search(r"\d{4}[-./]\d{2}[-./]\d{2}\s+\d{2}:\d{2}:\d{2}", txt)
                if dt:
                    result["live_time"] = dt.group(0)
            elif "기준가" in txt and "실시간" not in txt:
                if match:
                    result["base_nav"] = float(match.group(1).replace(",", ""))
                dt = re.search(r"\d{4}[-./]\d{2}[-./]\d{2}", txt)
                if dt:
                    result["base_date"] = dt.group(0)
    except (requests.RequestException, ValueError):
        pass
    return result


def fetch_base_fx():
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    target = previous_weekday(now) if now.weekday() >= 5 or now.hour < 9 else now.strftime("%Y-%m-%d")
    try:
        r = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/USDKRW=X?interval=5m&range=5d", headers=HEADERS, timeout=5)
        d = r.json()["chart"]["result"][0]
        candidates = []
        for ts, price in zip(d.get("timestamp", []), d["indicators"]["quote"][0].get("close", [])):
            if price is None:
                continue
            dt = datetime.fromtimestamp(ts, tz=ZoneInfo("Asia/Seoul"))
            if dt.strftime("%Y-%m-%d") == target:
                diff = abs(dt.hour * 60 + dt.minute - 930)
                candidates.append((diff, float(price)))
        if candidates:
            return min(candidates)[1]
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
        pass
    return 0.0


def fetch_live_fx():
    try:
        r = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/USDKRW=X?interval=1m&range=1d", headers=HEADERS, timeout=5)
        closes = r.json()["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        valid = [float(v) for v in closes if v is not None]
        if valid:
            return valid[-1]
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
        pass
    try:
        r = requests.post("https://scanner.tradingview.com/forex/scan", json={"symbols": {"tickers": ["FX_IDC:USDKRW", "FX:USDKRW"]}, "columns": ["close"]}, headers=HEADERS, timeout=5)
        for item in r.json().get("data", []):
            if item.get("d") and item["d"][0] is not None:
                return float(item["d"][0])
    except (requests.RequestException, ValueError, KeyError, TypeError):
        pass
    return 0.0


def fetch_prices(symbols):
    _, _, _, _, premarket, _ = session_status()
    symbols = sorted({"QQQ" if normalize(s) == "나스닥" else normalize(s) for s in symbols if normalize(s) not in ("", "현금")})
    results = {}
    if premarket:
        # 거래소 접두사를 고정하지 않아 NYSE/AMEX 상장 종목도 조회 가능
        tv_symbols = [f"NASDAQ:{s}" for s in symbols]
        try:
            r = requests.post("https://scanner.tradingview.com/america/scan", json={"symbols": {"tickers": tv_symbols}, "columns": ["close", "change", "premarket_close", "premarket_change", "premarket_change_abs", "market"]}, headers=HEADERS, timeout=6)
            for item in r.json().get("data", []):
                name = item.get("s", "").split(":")[-1].upper()
                vals = item.get("d", [])
                if len(vals) >= 4:
                    price = vals[2] if vals[2] is not None and vals[2] > 0 else vals[0]
                    change = vals[3] if vals[2] is not None and vals[2] > 0 else vals[1]
                    if price is not None and price > 0:
                        results[name] = (float(price), float(change or 0))
        except (requests.RequestException, ValueError, TypeError):
            pass
    else:
        try:
            key = st.secrets.get("FINNHUB_API_KEY", DEFAULT_FINNHUB_KEY)
        except Exception:
            key = DEFAULT_FINNHUB_KEY
        for symbol in symbols:
            try:
                r = requests.get("https://finnhub.io/api/v1/quote", params={"symbol": symbol, "token": key}, timeout=4)
                if r.ok:
                    d = r.json()
                    price = float(d.get("c") or 0)
                    prev = float(d.get("pc") or 0)
                    if price > 0 and prev > 0:
                        results[symbol] = (price, (price / prev - 1) * 100)
                time.sleep(0.05)
            except (requests.RequestException, ValueError, TypeError):
                pass
    if "QQQ" in results:
        results["나스닥"] = results["QQQ"]
    return results, fetch_live_fx()


def color_pct(v):
    try:
        x = float(str(v).replace("%", ""))
        return "color: #0055FF; font-weight: bold" if x > 0 else "color: #FF0000; font-weight: bold" if x < 0 else ""
    except ValueError:
        return ""


def color_weight(v):
    if "NEW" in str(v):
        return "color: #8A2BE2; font-weight: bold"
    if "OUT" in str(v):
        return "color: #FF8C00; font-weight: bold"
    return color_pct(v)


def style_table(df, percent_columns=()):
    styler = df.style
    styler = styler.map(color_weight, subset=["비중변화(%)"]) if hasattr(styler, "map") else styler.applymap(color_weight, subset=["비중변화(%)"])
    styler = styler.map(color_pct, subset=["주가변동률(%)"]) if hasattr(styler, "map") else styler.applymap(color_pct, subset=["주가변동률(%)"])
    formats = {col: "{:.2f}%" for col in percent_columns}
    return styler.format(formats).set_properties(**{"text-align": "center"})


st.markdown("### 🌐 구성종목 가져오기 방식을 선택하세요")
col1, col2 = st.columns(2)
with col1:
    st.button("🚀 자동 불러오기", use_container_width=True)
with col2:
    if st.button("📁 엑셀 파일 수동 업로드하기", use_container_width=True):
        st.session_state.show_uploader = not st.session_state.get("show_uploader", False)

uploaded = previous_upload = None
if st.session_state.get("show_uploader", False):
    uploaded = st.file_uploader("1️⃣ **[오늘]** 구성종목 엑셀 파일", type=["xlsx", "xls"], key="today")
    previous_upload = st.file_uploader("2️⃣ **[직전 영업일]** 구성종목 엑셀 파일", type=["xlsx", "xls"], key="yesterday")

current_date = ""
df_today = df_previous = None
if uploaded is None:
    with st.spinner("타임폴리오 구성종목 수집 중..."):
        candidate = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
        for _ in range(5):
            df_today = fetch_holdings(candidate)
            if df_today is not None:
                current_date = candidate
                break
            candidate = previous_weekday(candidate)
        if df_today is None:
            df_today = fetch_holdings()
            current_date = candidate
        previous_date = previous_weekday(current_date)
        df_previous = fetch_holdings(previous_date)
        if df_today is not None and df_previous is not None:
            a = df_today.set_index("종목코드")["비중"].sort_index()
            b = df_previous.set_index("종목코드")["비중"].sort_index()
            if a.equals(b):
                older = fetch_holdings(previous_weekday(previous_date))
                if older is not None:
                    df_previous = older
else:
    try:
        df_today = read_holdings_excel(uploaded)
        if previous_upload is not None:
            df_previous = read_holdings_excel(previous_upload)
    except Exception as e:
        st.error(f"엑셀 파일 읽기 오류: {e}")

if df_today is None or df_today.empty:
    st.warning("구성종목을 불러오지 못했습니다. 타임폴리오 페이지 또는 엑셀 파일을 확인하세요.")
    st.stop()

st.success(f"✅ 총 {len(df_today)}개 종목 로드 완료!" + (f" ({current_date} vs 전일)" if current_date else ""))
with st.spinner("실시간 시세 연산 중..."):
    previous_weights = dict(zip(df_previous["종목코드"], df_previous["비중"])) if df_previous is not None else {}
    current_weights = dict(zip(df_today["종목코드"], df_today["비중"]))
    symbols = list(dict.fromkeys(list(current_weights) + list(previous_weights)))
    base_fx = fetch_base_fx()
    etf = fetch_etf_market()
    official = fetch_official_nav()
    prices, live_fx = fetch_prices(symbols)
    if not live_fx:
        live_fx = base_fx
    fx_change = (live_fx / base_fx - 1) * 100 if base_fx and live_fx else 0.0
    records = []
    new_stocks, removed_stocks = [], []
    for symbol in symbols:
        weight = float(current_weights.get(symbol, 0.0))
        prev = previous_weights.get(symbol)
        difference = weight - prev if prev is not None else weight
        if prev is None and weight:
            diff_label = "✨ NEW"
            new_stocks.append(f"**{symbol}** ({weight:.2f}%)")
        elif prev is not None and weight == 0:
            diff_label = "🚪 OUT"
            removed_stocks.append(f"**{symbol}** (전일 {prev:.2f}%)")
        else:
            diff_label = f"{difference:+.2f}%"
        price, change = prices.get(symbol, (0.0, 0.0))
        if symbol == "현금":
            match = df_today.loc[df_today["종목코드"] == "현금", "평가금액"]
            price = float(match.iloc[0]) if not match.empty and match.iloc[0] > 0 else 1.0
            change = 0.0
        records.append({"종목코드": symbol, "실시간 가격($)": price, "주가변동률(%)": change, "당일비중(%)": weight, "전일비중(%)": f"{prev:.2f}%" if prev is not None else "-", "비중변화(%)": diff_label, "비중변화_수치": difference})
    result = pd.DataFrame(records)
    total_weight = result["당일비중(%)"].sum()
    stock_change = float((result["당일비중(%)"] * result["주가변동률(%)"]).sum() / total_weight) if total_weight else 0.0
    total_change = stock_change + fx_change

price = etf["current_price"]
if price:
    st.caption("🏷️ 현재가격")
    st.markdown(f"<div style='font-size:42px;color:inherit'>{price:,.0f} 원 <span style='font-size:20px;color:{'#c62828' if etf['price_change_pct'] >= 0 else '#0277bd'}'>({etf['price_change_pct']:+.2f}%)</span></div>", unsafe_allow_html=True)
    if etf["naver_nav"]:
        st.markdown(f"실시간 괴리율: **{etf['naver_disparity']:+.2f}%** (NAV: {etf['naver_nav']:,.0f}원 기준)")

now, dst, kr_open, waiting, premarket, buffer = session_status()
if kr_open:
    status = "한국시장 거래중"
    explanation = "장중에는 실시간 가격과 괴리율을 참고하세요."
elif waiting:
    status = "미국 프리마켓 대기 중 ⏳"
    explanation = "미국 프리마켓 시작 시 추정치가 제공됩니다."
elif buffer:
    status = f"프리장 15분 지연데이터 대기 중 ⏳ ({'17:15' if dst else '18:15'}부터 제공)"
    explanation = "프리마켓 시작 후 15분간은 지연 시세 반영 대기 시간입니다."
else:
    status = None
    explanation = ""

st.markdown("---")
st.caption("📈 실시간 iNAV 추정 총 변동률" + (" (15분 지연)" if premarket else ""))
if status:
    st.subheader(status)
    st.info(explanation)
else:
    st.markdown(f"## {total_change:+.2f}%")
    st.markdown(f"주가: **{stock_change:+.2f}%** + 환율: **{fx_change:+.2f}%**")
    st.caption(f"현재 환율: {live_fx:,.2f}원 / 기준 환율: {base_fx:,.2f}원")

st.caption("💵 나스닥100액티브(426030) 예상 iNAV" + (" (15분 지연)" if premarket else ""))
if status:
    st.subheader(status)
    st.info(explanation)
else:
    base_nav = etf["naver_nav"] or official["live_nav"] or etf["prev_close"]
    if base_nav:
        estimate = base_nav * (1 + total_change / 100)
        st.markdown(f"## {estimate:,.0f} 원")
        st.markdown(f"{estimate - base_nav:+,.0f} 원 (기준 iNAV: {base_nav:,.0f}원)")
        if price:
            st.caption(f"예상 iNAV와 현재가격 차이: {(estimate / price - 1) * 100:+.2f}%")
    else:
        st.warning("기준 NAV를 가져오지 못했습니다.")

if official["live_nav"] or official["base_nav"]:
    st.success(f"🏛️ **타임폴리오 공식 기준가**\n\n- 실시간: **{official['live_nav']:,.2f}원** ({official['live_time'] or '시각 미확인'})\n- 전일 확정: **{official['base_nav']:,.2f}원** ({official['base_date'] or '날짜 미확인'})")
if new_stocks or removed_stocks:
    st.warning("📋 **포트폴리오 변동 내역**\n\n- ✨ **신규 편입**: " + (", ".join(new_stocks) if new_stocks else "없음") + "\n- 🚪 **편출 (전량 매도)**: " + (", ".join(removed_stocks) if removed_stocks else "없음"))

st.markdown("---")
active = result[(result["당일비중(%)"] > 0) & (result["종목코드"] != "현금")].copy()
st.markdown(f"### 🔥 전체 구성종목 ({len(active)}개) 히트맵")
if not active.empty:
    active["표시명"] = "<span style='font-size:20px;font-weight:bold'>" + active["종목코드"] + "</span><br><span style='font-size:15px'>" + active["주가변동률(%)"].map("{:+.2f}%".format) + "</span>"
    fig = px.treemap(active, path=["표시명"], values="당일비중(%)", color="주가변동률(%)", color_continuous_scale=[[0, "#4285F4"], [0.166, "#3B72E2"], [0.333, "#345FCF"], [0.5, "#404552"], [0.666, "#8B3A48"], [0.833, "#C83742"], [1, "#FF1744"]], range_color=[-3, 3])
    fig.update_traces(textposition="middle center", selector=dict(type="treemap"))
    fig.update_layout(margin=dict(t=5, l=5, r=5, b=5), height=600, uniformtext=dict(minsize=8, mode=False), coloraxis_showscale=False)
    st.plotly_chart(fig, use_container_width=True)

st.markdown("---")
st.markdown("### 🔄 전일 대비 비중 변화 TOP 10")
top10 = result.assign(절대변화량=result["비중변화_수치"].abs()).sort_values("절대변화량", ascending=False).head(10)[["종목코드", "당일비중(%)", "전일비중(%)", "비중변화(%)", "주가변동률(%)"]].copy()
top10.index = range(1, len(top10) + 1)
st.dataframe(style_table(top10, ["당일비중(%)", "주가변동률(%)"]), use_container_width=True, height=385)

st.markdown("---")
st.markdown("### 📊 종목별 실시간 전체 현황")
full = result[result["당일비중(%)"] > 0].sort_values("당일비중(%)", ascending=False).copy()
full["실시간 가격($)"] = full["실시간 가격($)"].map(lambda v: f"{v:,.2f}" if v > 0 else "-")
full["주가변동률(%)"] = full["주가변동률(%)"].map("{:+.2f}%".format)
full = full[["종목코드", "실시간 가격($)", "주가변동률(%)", "당일비중(%)", "전일비중(%)", "비중변화(%)"]]
full.index = range(1, len(full) + 1)
st.dataframe(style_table(full, ["당일비중(%)"]), use_container_width=True, height=500)
