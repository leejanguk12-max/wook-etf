from datetime import datetime, timedelta
import io
import re
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
import pandas as pd
import plotly.express as px
import requests
import streamlit as st

st.set_page_config(page_title="타임폴리오 ETF 실시간 대시보드", layout="wide")

st.title("🎯 타임폴리오 액티브 ETF 실시간 iNAV 대시보드")
st.markdown(
    "타임폴리오 공식 홈페이지의 **전체 구성종목(PDF)** 및 **전일 대비 비중 변화**,"
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
  is_weekend = now_kst.weekday() >= 5
  current_time_val = now_kst.hour * 60 + now_kst.minute

  korean_market_open = 9 * 60
  korean_market_close = 15 * 60 + 30
  is_korean_market_hours = (not is_weekend) and (
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
  premarket_start_val = (17 if is_dst else 18) * 60

  is_us_premarket_or_market_hours = (not is_weekend) and (
      current_time_val >= premarket_start_val or now_kst.hour < 9
  )

  return (
      is_korean_market_hours,
      is_us_premarket_or_market_hours,
      now_kst,
      is_dst,
  )


def get_prev_business_day(ref_date):
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
  url = (
      f"https://timeetf.co.kr/m11_view.php?idx={idx}&pdfDate={date_str}#constituentItems"
      if date_str
      else f"https://timeetf.co.kr/m11_view.php?idx={idx}#constituentItems"
  )
  headers = {"User-Agent": "Mozilla/5.0"}
  data = []
  fetched_date = None

  try:
    resp = requests.get(url, headers=headers, timeout=10)
    soup = BeautifulSoup(resp.text, "html.parser")

    date_input = (
        soup.select_one("input[name='pdfDate']")
        or soup.select_one("input#pdfDate")
        or soup.select_one(".datepicker")
    )
    if date_input:
      val = date_input.get("value", "") or date_input.get_text()
      m = re.search(r"(\d{4})[.-/]\s*(\d{2})[.-/]\s*(\d{2})", val)
      if m:
        fetched_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    if not fetched_date:
      m_sec = re.search(
          r"(\d{4})[.-/]\s*(\d{2})[.-/]\s*(\d{2})",
          (soup.select_one("#constituentItems") or soup).get_text(),
      )
      if m_sec:
        fetched_date = f"{m_sec.group(1)}-{m_sec.group(2)}-{m_sec.group(3)}"

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


def get_naver_fx_by_reversing_charts():
  """네이버 차트 API의 회차별 데이터를 가져와 역순으로 15:30 부근의 가격을 찾아냅니다."""
  current_fx = 0.0
  fx_1530 = 0.0
  headers = {"User-Agent": "Mozilla/5.0"}

  try:
    resp_curr = requests.get(
        "https://m.stock.naver.com/marketindex/price/FX_USDKRW",
        headers=headers,
        timeout=5,
    )
    if resp_curr.status_code == 200:
      data = resp_curr.json()
      if "closePrice" in data:
        current_fx = float(str(data["closePrice"]).replace(",", ""))

    chart_url = "https://m.stock.naver.com/front-api/marketIndex/prices?category=exchange&code=FX_USDKRW&page=1&pageSize=600"
    resp_chart = requests.get(chart_url, headers=headers, timeout=5)
    if resp_chart.status_code == 200:
      chart_data = resp_chart.json()
      items = (
          chart_data.get("result", [])
          if isinstance(chart_data, dict)
          else chart_data
      )

      if isinstance(items, list) and len(items) > 0:
        for item in reversed(items):
          time_str = str(
              item.get("localTime", "")
              or item.get("date", "")
              or item.get("time", "")
          )
          price_str = str(
              item.get("closePrice", "")
              or item.get("price", "")
              or item.get("value", "0")
          ).replace(",", "")
          val = float(price_str) if price_str.replace(".", "", 1).isdigit() else 0.0

          if ("15:30" in time_str or "1530" in time_str) and val > 0:
            fx_1530 = val
            break

        if fx_1530 == 0.0:
          for item in reversed(items):
            time_str = str(
                item.get("localTime", "")
                or item.get("date", "")
                or item.get("time", "")
            )
            price_str = str(
                item.get("closePrice", "")
                or item.get("price", "")
                or item.get("value", "0")
            ).replace(",", "")
            val = float(price_str) if price_str.replace(".", "", 1).isdigit() else 0.0

            if ("15:" in time_str or "15" in time_str) and val > 0:
              fx_1530 = val
              break

        if fx_1530 == 0.0 and items:
          fx_1530 = float(
              str(items[0].get("closePrice", current_fx)).replace(",", "")
          )
  except Exception:
    pass

  if current_fx == 0.0:
    current_fx = 1470.0
  if fx_1530 == 0.0:
    fx_1530 = current_fx - 2.0

  return fx_1530, current_fx


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

    exday_text = (soup.select_one("div.rate_info") or soup).get_text()
    is_minus = bool(
        soup.select_one("p.no_exday em span.ico.down")
        or soup.select_one("em.no_down")
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


def get_tradingview_direct_prices(symbols):
  url = "https://scanner.tradingview.com/america/scan"
  clean_symbols = []
  for s in symbols:
    sym_str = str(s).split()[0].upper().replace("/", ".")
    if "NQU" in sym_str or "NQ1!" in sym_str or "NQ=" in sym_str:
      clean_symbols.append("QQQ")
    elif (
        sym_str != "현금"
        and "현금" not in sym_str
        and "CASH" not in sym_str
        and "KRW" not in sym_str
    ):
      clean_symbols.append(sym_str)

  clean_symbols = list(set(clean_symbols))
  tickers_to_query = [
      f"{ex}:{sym}" for sym in clean_symbols for ex in ["NASDAQ", "NYSE", "AMEX"]
  ]

  payload_stocks = {
      "symbols": {"tickers": tickers_to_query},
      "columns": ["close", "change", "premarket_close"],
  }
  headers = {"User-Agent": "Mozilla/5.0"}

  result_map = {}
  try:
    if tickers_to_query:
      resp_stocks = requests.post(
          url, json=payload_stocks, headers=headers, timeout=5
      )
      if resp_stocks.status_code == 200:
        for item in resp_stocks.json().get("data", []):
          ticker_clean = item.get("s", "").split(":")[-1]
          values = item.get("d", [0.0, 0.0, 0.0])

          close_price = (
              values[0]
              if len(values) > 0 and values[0] is not None
              else 0.0
          )
          change_pct = (
              values[1]
              if len(values) > 1 and values[1] is not None
              else 0.0
          )
          pm_close = (
              values[2]
              if len(values) > 2 and values[2] is not None
              else 0.0
          )

          if pm_close > 0 and close_price > 0:
            active_price = pm_close
            active_change = ((pm_close - close_price) / close_price) * 100
          else:
            active_price = close_price if close_price > 0 else pm_close
            active_change = change_pct

          if (
              ticker_clean not in result_map
              or result_map[ticker_clean][0] == 0.0
          ):
            result_map[ticker_clean] = (active_price, active_change)
  except Exception:
    pass
  return result_map


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
    df_input, fetched_date = get_timefolio_constituents_by_date(idx=2)
    current_pdf_date_str = fetched_date if fetched_date else "2026-07-27"
    curr_dt = datetime.strptime(current_pdf_date_str, "%Y-%m-%d")
    prev_pdf_date_str = get_prev_business_day(curr_dt)
    df_prev, _ = get_timefolio_constituents_by_date(
        idx=2, date_str=prev_pdf_date_str
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
  st.success(f"✅ 총 {len(df_input)}개 종목 데이터 로드 완료!{date_info_msg}")

  with st.spinner("실시간 시세 및 15시 30분 회차 역추적 연산 중..."):
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

    fx_1530_base, live_fx = get_naver_fx_by_reversing_charts()
    usdkrw_change_pct = (
        ((live_fx - fx_1530_base) / fx_1530_base) * 100
        if fx_1530_base > 0
        else 0.0
    )

    naver_market = get_naver_etf_market_data("426030")
    timefolio_data = get_timefolio_official_data(idx=2)
    batch_results = get_tradingview_direct_prices(ticker_list)

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
      if prev_w is not None:
        w_diff = weight - prev_w
        w_diff_val = w_diff
        prev_w_str = f"{prev_w:.2f}%"
        if abs(w_diff) >= 0.001:
          w_diff_str = f"{w_diff:+.2f}%"
      else:
        w_diff_str = "✨ NEW"
        w_diff_val = weight
        if weight > 0:
          new_added_stocks.append(f"**{ticker}** ({weight:.2f}%)")

      if weight == 0.0 and prev_w is not None and prev_w > 0:
        w_diff_val = 0.0 - prev_w
        prev_w_str = f"{prev_w:.2f}%"
        w_diff_str = "🚪 OUT"
        removed_stocks.append(f"**{ticker}** (전일 {prev_w:.2f}%)")

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
            "종목코드": f"{ticker} (QQQ대체)",
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

    is_korean_market_hours, is_us_premarket_or_market_hours, now_kst, is_dst = (
        get_market_session_status()
    )

    if is_korean_market_hours:
      st.markdown(
          """
                <div style="margin-bottom: 10px;">
                    <div style="font-size: 14px; color: #6f727b; margin-bottom: 2px;">📈 실시간 iNAV 추정 총 변동률</div>
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
                    <div style="font-size: 14px; color: #6f727b; margin-bottom: 2px;">💵 나스닥100액티브(426030) 예상 iNAV</div>
                    <div style="font-size: 32px; font-weight: normal; color: #1f1f1f; line-height: 1.2; margin-bottom: 4px;">한국시장 거래중</div>
                    <div style="display: inline-block; background-color: #e3f2fd; color: #0277bd; padding: 2px 8px; border-radius: 12px; font-size: 13px; font-weight: 500;">
                        ℹ️ 장중에는 실시간 가격과 괴리율을 참고하세요.
                    </div>
                </div>
                """,
          unsafe_allow_html=True,
      )
    elif not is_us_premarket_or_market_hours:
      st.markdown(
          """
                <div style="margin-bottom: 10px;">
                    <div style="font-size: 14px; color: #6f727b; margin-bottom: 2px;">📈 실시간 iNAV 추정 총 변동률</div>
                    <div style="font-size: 32px; font-weight: normal; color: #1f1f1f; line-height: 1.2; margin-bottom: 4px;">미국 프리마켓 대기 중 ⏳</div>
                    <div style="display: inline-block; background-color: #fff3e0; color: #e65100; padding: 2px 8px; border-radius: 12px; font-size: 13px; font-weight: 500;">
                        ℹ️ 미국 프리마켓 시작 시각(서머타임 적용 시 오후 5시)부터 실시간 추정치가 제공됩니다.
                    </div>
                </div>
                """,
          unsafe_allow_html=True,
      )
      st.markdown("<div style='margin-top: 20px;'></div>", unsafe_allow_html=True)
      st.markdown(
          """
                <div style="margin-bottom: 10px;">
                    <div style="font-size: 14px; color: #6f727b; margin-bottom: 2px;">💵 나스닥100액티브(426030) 예상 iNAV</div>
                    <div style="font-size: 32px; font-weight: normal; color: #1f1f1f; line-height: 1.2; margin-bottom: 4px;">미국 프리마켓 대기 중 ⏳</div>
                    <div style="display: inline-block; background-color: #fff3e0; color: #e65100; padding: 2px 8px; border-radius: 12px; font-size: 13px; font-weight: 500;">
                        ℹ️ 미국 프리마켓 시작 시각(서머타임 적용 시 오후 5시)부터 실시간 추정치가 제공됩니다.
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
                        <div style="font-size: 42px; font-weight: normal; color: #1f1f1f; line-height: 1.2; margin-bottom: 4px;">{value}</div>
                        <div style="display: inline-block; background-color: {bg_color}; color: {text_color}; padding: 2px 8px; border-radius: 12px; font-size: 14px; font-weight: 500;">
                            {arrow} {delta_text} {extra_info}
                        </div>
                    </div>
                    """,
            unsafe_allow_html=True,
        )

      stock_bg = "#ffebee" if stock_inav_change >= 0 else "#e3f2fd"
      stock_color = "#c62828" if stock_inav_change >= 0 else "#0277bd"
      fx_bg = "#ffebee" if usdkrw_change_pct >= 0 else "#e3f2fd"
      fx_color = "#c62828" if usdkrw_change_pct >= 0 else "#0277bd"

      delta_detail_html = (
          f"주가: <span style='background-color: {stock_bg}; color:"
          f" {stock_color}; padding: 1px 4px; border-radius: 4px;'>{stock_inav_change:+.2f}%</span>"
          f" + 환율: <span style='background-color: {fx_bg}; color:"
          f" {fx_color}; padding: 1px 4px; border-radius: 4px;'>{usdkrw_change_pct:+.2f}%</span><br>"
          f"<span style='font-size: 13px; color: #555;'>(현재 환율:"
          f" {live_fx:,.2f}원 / 기준 환율: {fx_1530_base:,.2f}원)</span>"
      )

      render_custom_metric(
          "📈 실시간 iNAV 추정 총 변동률",
          f"{total_inav_change:+.2f}%",
          delta_detail_html,
          total_inav_change >= 0,
      )
      st.markdown("<div style='margin-top: 20px;'></div>", unsafe_allow_html=True)

      base_nav_reference = (
          timefolio_data["live_nav"]
          if timefolio_data["live_nav"] > 0
          else (
              timefolio_data["base_nav"]
              if timefolio_data["base_nav"] > 0
              else (naver_nav if naver_nav > 0 else naver_market["prev_close"])
          )
      )

      if base_nav_reference > 0:
        estimated_inav_price = base_nav_reference * (
            1 + (total_inav_change / 100)
        )
        est_diff_val = estimated_inav_price - base_nav_reference
        est_diff_pct = (est_diff_val / base_nav_reference) * 100
        pct_is_plus_est = est_diff_pct >= 0
        est_chg_str = f"<span style='background-color: {'#ffebee' if pct_is_plus_est else '#e3f2fd'}; color: {'#c62828' if pct_is_plus_est else '#0277bd'}; padding: 2px 8px; border-radius: 12px; font-size: 20px; font-weight: normal; display: inline-block;'>({est_diff_pct:+.2f}%)</span>"

        st.markdown(
            "<div style='font-size: 14px; color: #6f727b; margin-bottom:"
            " 2px;'>💵 나스닥100액티브(426030) 예상 iNAV</div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<p style='font-size: 42px; font-weight: normal; margin-bottom:"
            f" 0px; line-height: 1.2; color: #1f1f1f;'>{estimated_inav_price:,.0f}"
            f" 원 {est_chg_str}</p>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<div"
            f" style='display:inline-block;background-color:{'#ffebee' if pct_is_plus_est else '#e3f2fd'};color:{'#c62828' if pct_is_plus_est else '#0277bd'};padding:2px"
            f" 8px;border-radius:12px;font-size:14px;font-weight:500;margin-top:6px;'>{'↑'"
            f" if pct_is_plus_est else '↓'} {est_diff_val:+,.0f} 원 (기준 iNAV:"
            f" {base_nav_reference:,.0f}원)</div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            "<div style='margin-top: 20px;'></div>", unsafe_allow_html=True
        )

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
          f"🏛️ **타임폴리오 공시 기준가** &nbsp;|&nbsp; 실시간: {live_nav_val}{live_time_str}"
          f" &nbsp;|&nbsp; 전일 확정: {base_nav_val}{base_date_str}"
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
          f"📋 **포트폴리오 변동 내역**  \n- {new_msg}  \n- {out_msg}"
      )

    display_base_df = result_df.copy()
    if is_korean_market_hours:
      display_base_df["주가변동률(%)"] = 0.0

    st.markdown("---")
    active_df = display_base_df[display_base_df["당일비중(%)"] > 0].copy()
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
        margin=dict(t=5, l=5, r=5, b=30),
        height=600,
        uniformtext=dict(minsize=8, mode=False),
        coloraxis_colorbar=dict(
            title=dict(text="주가변동률(%)", side="top"),
            orientation="h",
            y=-0.15,
            x=0.5,
            xanchor="center",
            len=0.85,
            tickvals=[-3, -2, -1, 0, 1, 2, 3],
            ticktext=["-3%", "-2%", "-1%", "0%", "+1%", "+2%", "+3%"],
        ),
    )
    st.plotly_chart(fig_treemap, use_container_width=True)

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
