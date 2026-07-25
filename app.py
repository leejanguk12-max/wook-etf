import streamlit as st
import pandas as pd
import io
import requests
import re
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import plotly.express as px

st.set_page_config(page_title="타임폴리오 ETF 실시간 대시보드", layout="wide")

st.title("🎯 타임폴리오 액티브 ETF 실시간 iNAV 대시보드")
st.markdown("타임폴리오 공식 홈페이지의 **전체 구성종목(PDF)** 및 **전일 대비 비중 변화**, **실시간 기준가**를 연동합니다.")

# 1. 타임폴리오 사이트 바로가기 버튼 (상단배치)
st.link_button(
    "🔗 타임폴리오 공식 구성종목 페이지 바로가기", 
    "https://timeetf.co.kr/m11_view.php?idx=2#constituentItems",
    use_container_width=True
)

st.markdown("---")

# 기준 날짜(dt) 대비 직전 영업일 구하기 함수
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

# 특정 날짜(date_str)의 타임폴리오 구성종목 및 공시 기준일자 정밀 수집 (현금 평가금액 포함)
def get_timefolio_constituents_by_date(idx=2, date_str=None):
    if date_str:
        url = f"https://timeetf.co.kr/m11_view.php?idx={idx}&pdfDate={date_str}#constituentItems"
    else:
        url = f"https://timeetf.co.kr/m11_view.php?idx={idx}#constituentItems"
        
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    data = []
    fetched_date = None
    
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        
        date_input = soup.select_one("input[name='pdfDate']") or soup.select_one("input#pdfDate") or soup.select_one(".datepicker")
        if date_input:
            val = date_input.get("value", "") or date_input.get_text()
            m = re.search(r'(\d{4})[.-/]\s*(\d{2})[.-/]\s*(\d{2})', val)
            if m:
                fetched_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

        if not fetched_date:
            constituent_section = soup.select_one("#constituentItems") or soup
            m_sec = re.search(r'(\d{4})[.-/]\s*(\d{2})[.-/]\s*(\d{2})', constituent_section.get_text())
            if m_sec:
                fetched_date = f"{m_sec.group(1)}-{m_sec.group(2)}-{m_sec.group(3)}"

        rows = soup.select("table tr")
        
        exclude_keywords = [
            "기준가", "비교지수", "초과성과", "종목", "총액", "합계", "TOTAL", "CODE", "DATE", "날짜", "일자"
        ]
        
        for row in rows:
            tds = row.select("td")
            if len(tds) >= 4:
                cols = [td.get_text().strip() for td in tds]
                
                raw_code = cols[0] if len(cols) > 0 else ""
                raw_name = cols[1] if len(cols) > 1 else ""
                raw_weight = cols[-1] if len(cols) > 0 else ""
                
                amt_val = 0.0
                for col_text in cols[1:-1]:
                    clean_txt = col_text.replace(',', '').strip()
                    if clean_txt.replace('.', '', 1).isdigit() and float(clean_txt) > 1000:
                        try:
                            amt_val = float(clean_txt)
                            break
                        except ValueError:
                            pass
                
                clean_ticker = ""
                
                if "현금" in raw_name or "예금" in raw_name or "현금" in raw_code or "CASH" in raw_name.upper() or "KRW" in raw_name.upper():
                    clean_ticker = "KRW"
                else:
                    code_parts = raw_code.split()
                    if code_parts:
                        clean_ticker = code_parts[0].strip().upper()
                
                if not clean_ticker or clean_ticker == "NAN" or clean_ticker == "NONE":
                    if "현금" in raw_name or "예금" in raw_name:
                        clean_ticker = "KRW"
                    else:
                        continue

                if any(kw == clean_ticker or kw in raw_code or kw in raw_name for kw in exclude_keywords):
                    continue
                    
                if re.match(r'^\d{4}[.-/]\d{2}[.-/]\d{2}$', clean_ticker) or re.match(r'^\d{4}[.-/]\d{2}[.-/]\d{2}$', raw_code):
                    continue
                    
                weight_clean = raw_weight.replace('%', '').replace(',', '').strip()
                
                try:
                    weight_val = float(weight_clean)
                    if 0 < weight_val <= 100:
                        data.append({
                            "종목코드": clean_ticker,
                            "비중": weight_val,
                            "평가금액": amt_val
                        })
                except ValueError:
                    pass

        if data:
            df = pd.DataFrame(data)
            df = df.drop_duplicates(subset=["종목코드"]).reset_index(drop=True)
            return df, fetched_date

    except Exception:
        pass
        
    return None, fetched_date

# 네이버 금융에서 서울외환시장 정규장 마감 환율 수집
def get_naver_official_base_fx():
    try:
        url = "https://finance.naver.com/marketindex/exchangeDetail.naver?marketindexCd=FX_USDKRW"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(resp.text, "html.parser")
        
        price_tag = soup.select_one("p.no_today em")
        if price_tag:
            price_str = price_tag.text.strip().replace(",", "")
            return float(price_str)
    except Exception:
        pass
    return 0.0

# 네이버 금융에서 타임나스닥100액티브(426030) 직전 정규장 마감 종가 수집
def get_naver_etf_prev_close(ticker_code="426030"):
    try:
        url = f"https://finance.naver.com/item/main.naver?code={ticker_code}"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(resp.text, "html.parser")
        
        today_tag = soup.select_one("p.no_today em span.blind")
        if today_tag:
            price_str = today_tag.text.strip().replace(",", "")
            return float(price_str)
            
        prev_tag = soup.select_one("td.first em span.blind")
        if prev_tag:
            price_str = prev_tag.text.strip().replace(",", "")
            return float(price_str)
    except Exception:
        pass
    return 0.0

# 타임폴리오 공식 홈페이지 정밀 파싱
def get_timefolio_official_data(idx=2):
    url = f"https://timeetf.co.kr/m11_view.php?idx={idx}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    result = {
        "live_nav": 0.0,       
        "live_time": "",       
        "base_nav": 0.0,       
        "base_date": ""        
    }
    
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(resp.text, "html.parser")
        
        boxes = soup.select("div.standard_price_box") or soup.select("ul.price_info li") or soup.find_all("div")
        
        for box in boxes:
            box_text = box.get_text()
            
            if "실시간" in box_text and "기준가" in box_text:
                m_val = re.search(r'([\d,]+\.\d+)', box_text)
                if m_val:
                    result["live_nav"] = float(m_val.group(1).replace(",", ""))
                m_time = re.search(r'(\d{4}[-.\/]\d{2}[-.\/]\d{2}\s+\d{2}:\d{2}:\d{2})', box_text)
                if m_time:
                    result["live_time"] = m_time.group(1)
            
            elif "기준가" in box_text and "실시간" not in box_text:
                m_val = re.search(r'([\d,]+\.\d+)', box_text)
                if m_val:
                    result["base_nav"] = float(m_val.group(1).replace(",", ""))
                m_date = re.search(r'(\d{4}[-.\/]\d{2}[-.\/]\d{2})', box_text)
                if m_date:
                    result["base_date"] = m_date.group(1)

        if result["base_nav"] == 0.0:
            all_text = soup.get_text()
            matches = re.findall(r'기준가\s*\(원\)\s*([\d,]+\.\d+)', all_text)
            if len(matches) > 1:
                result["base_nav"] = float(matches[1].replace(",", ""))
            elif len(matches) == 1 and result["live_nav"] != float(matches[0].replace(",", "")):
                result["base_nav"] = float(matches[0].replace(",", ""))

            dates = re.findall(r'(\d{4}-\d{2}-\d{2})', all_text)
            for d in dates:
                if "03:" not in d and d not in result["live_time"]:
                    result["base_date"] = d
                    break
    except Exception:
        pass
        
    return result

# 트레이딩뷰 직접 API 수집 함수
def get_tradingview_direct_prices(symbols):
    url = "https://scanner.tradingview.com/america/scan"
    
    clean_symbols = []
    for s in symbols:
        sym_str = str(s).split()[0].upper().replace("/", ".")
        if "NQU" in sym_str or "NQ1!" in sym_str or "NQ=" in sym_str:
            clean_symbols.append("QQQ")
        elif sym_str != "KRW" and "현금" not in sym_str and "CASH" not in sym_str and "KRW" not in sym_str:
            clean_symbols.append(sym_str)
            
    clean_symbols = list(set(clean_symbols))
    
    exchanges = ["NASDAQ", "NYSE", "AMEX"]
    tickers_to_query = []
    
    for sym in clean_symbols:
        for ex in exchanges:
            tickers_to_query.append(f"{ex}:{sym}")
            
    payload_stocks = {
        "symbols": {"tickers": tickers_to_query},
        "columns": ["close", "change"]
    }
    
    payload_fx = {
        "symbols": {"tickers": ["FX_IDC:USDKRW", "FX:USDKRW"]},
        "columns": ["close"]
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    result_map = {}
    live_fx = 0.0
    
    try:
        if tickers_to_query:
            resp_stocks = requests.post(url, json=payload_stocks, headers=headers, timeout=5)
            if resp_stocks.status_code == 200:
                data = resp_stocks.json().get("data", [])
                for item in data:
                    ticker_full = item.get("s", "")
                    ticker_clean = ticker_full.split(":")[-1]
                    values = item.get("d", [0.0, 0.0])
                    
                    close_price = values[0] if len(values) > 0 and values[0] is not None else 0.0
                    change_pct = values[1] if len(values) > 1 and values[1] is not None else 0.0
                    
                    if ticker_clean not in result_map or result_map[ticker_clean][0] == 0.0:
                        result_map[ticker_clean] = (close_price, change_pct)
                    
        url_fx = "https://scanner.tradingview.com/forex/scan"
        resp_fx = requests.post(url_fx, json=payload_fx, headers=headers, timeout=5)
        if resp_fx.status_code == 200:
            data_fx = resp_fx.json().get("data", [])
            if data_fx:
                values_fx = data_fx[0].get("d", [0.0])
                live_fx = values_fx[0] if len(values_fx) > 0 and values_fx[0] is not None else 0.0
    except Exception as e:
        st.error(f"TradingView API 연동 오류: {e}")
        
    return result_map, live_fx

# 주가 변동률 색상 스타일
def color_change_pct(val):
    try:
        val_num = float(val)
        if val_num > 0:
            return 'color: #0055FF; font-weight: bold;'
        elif val_num < 0:
            return 'color: #FF0000; font-weight: bold;'
    except Exception:
        pass
    return ''

# 비중 변화 색상 스타일
def color_weight_change(val):
    try:
        val_str = str(val)
        if "NEW" in val_str or "신규" in val_str:
            return 'color: #8A2BE2; font-weight: bold;'
        if "OUT" in val_str or "편출" in val_str:
            return 'color: #FF8C00; font-weight: bold;'
            
        val_num = float(val_str.replace('%p', '').replace('+', '').strip())
        if val_num > 0:
            return 'color: #0055FF; font-weight: bold;'
        elif val_num < 0:
            return 'color: #FF0000; font-weight: bold;'
    except Exception:
        pass
    return ''

# 메인 UI
st.markdown("### 🌐 구성종목 가져오기 방식을 선택하세요")

fetch_auto = st.button("🚀 자동 불러오기", use_container_width=True)

if "show_uploader" not in st.session_state:
    st.session_state.show_uploader = False

if st.button("📁 엑셀 파일 수동 업로드하기 (오늘 + 어제 파일)", use_container_width=True):
    st.session_state.show_uploader = not st.session_state.show_uploader

uploaded_file = None
uploaded_file_prev = None

if st.session_state.show_uploader:
    st.markdown("---")
    st.markdown("📥 **비중 비교를 위해 2개의 파일을 모두 업로드해주세요.**")
    uploaded_file = st.file_uploader("1️⃣ **[오늘]** 구성종목 엑셀 파일 (.xlsx)", type=["xlsx", "xls"], key="today")
    uploaded_file_prev = st.file_uploader("2️⃣ **[어제 / 직전 영업일]** 구성종목 엑셀 파일 (.xlsx)", type=["xlsx", "xls"], key="yesterday")
    st.markdown("---")

df_input = None
df_prev = None
current_pdf_date_str = ""
prev_pdf_date_str = ""

if fetch_auto:
    with st.spinner("타임폴리오 웹사이트에서 구성종목 수집 중..."):
        df_input, fetched_date = get_timefolio_constituents_by_date(idx=2)
        
        if fetched_date:
            current_pdf_date_str = fetched_date
        else:
            current_pdf_date_str = "2026-07-24"
            
        curr_dt = datetime.strptime(current_pdf_date_str, "%Y-%m-%d")
        prev_pdf_date_str = get_prev_business_day(curr_dt)
            
        df_prev, _ = get_timefolio_constituents_by_date(idx=2, date_str=prev_pdf_date_str)

elif uploaded_file is not None:
    try:
        # [오늘 파일 읽기]
        raw_df_in = pd.read_excel(uploaded_file)
        in_t_col, in_w_col, in_n_col, in_a_col = "종목코드", "비중", "종목명", "평가금액(원)"
        for col in raw_df_in.columns:
            c_str = str(col)
            if "코드" in c_str or "티커" in c_str or "Symbol" in c_str:
                in_t_col = col
            if "비중" in c_str or "평가" in c_str or "Weight" in c_str:
                in_w_col = col
            if "명" in c_str or "Name" in c_str:
                in_n_col = col
            if "금액" in c_str or "평가" in c_str or "Amount" in c_str:
                in_a_col = col
        
        clean_in_data = []
        for _, r in raw_df_in.iterrows():
            t_val = str(r[in_t_col]).split()[0].strip().upper() if pd.notna(r[in_t_col]) else ""
            n_val = str(r[in_n_col]) if in_n_col in raw_df_in.columns and pd.notna(r[in_n_col]) else ""
            w_val_raw = str(r[in_w_col]).replace('%', '').replace(',', '').strip() if pd.notna(r[in_w_col]) else "0"
            
            amt_val = 0.0
            if in_a_col in raw_df_in.columns and pd.notna(r[in_a_col]):
                try:
                    amt_val = float(str(r[in_a_col]).replace(',', '').strip())
                except ValueError:
                    pass
            
            if "현금" in n_val or "예금" in n_val or t_val == "NAN" or not t_val:
                t_val = "KRW"
            
            if t_val == "NAN" or t_val == "NONE" or t_val == "열1":
                continue
            
            try:
                w_val = float(w_val_raw)
                clean_in_data.append({"종목코드": t_val, "비중": w_val, "평가금액": amt_val})
            except ValueError:
                pass
        df_input = pd.DataFrame(clean_in_data).drop_duplicates(subset=["종목코드"]).reset_index(drop=True)

        # [어제 파일 읽기]
        if uploaded_file_prev is not None:
            raw_df_prev = pd.read_excel(uploaded_file_prev)
            p_t_col, p_w_col, p_n_col = "종목코드", "비중", "종목명"
            for col in raw_df_prev.columns:
                c_str = str(col)
                if "코드" in c_str or "티커" in c_str or "Symbol" in c_str:
                    p_t_col = col
                if "비중" in c_str or "평가" in c_str or "Weight" in c_str:
                    p_w_col = col
                if "명" in c_str or "Name" in c_str:
                    p_n_col = col
            
            clean_prev_data = []
            for _, r in raw_df_prev.iterrows():
                t_val = str(r[p_t_col]).split()[0].strip().upper() if pd.notna(r[p_t_col]) else ""
                n_val = str(r[p_n_col]) if p_n_col in raw_df_prev.columns and pd.notna(r[p_n_col]) else ""
                w_val_raw = str(r[p_w_col]).replace('%', '').replace(',', '').strip() if pd.notna(r[p_w_col]) else "0"
                
                if "현금" in n_val or "예금" in n_val or t_val == "NAN" or not t_val:
                    t_val = "KRW"
                
                if t_val == "NAN" or t_val == "NONE" or t_val == "열1":
                    continue
                
                try:
                    w_val = float(w_val_raw)
                    clean_prev_data.append({"종목코드": t_val, "비중": w_val})
                except ValueError:
                    pass
            df_prev = pd.DataFrame(clean_prev_data).drop_duplicates(subset=["종목코드"]).reset_index(drop=True)

    except Exception as e:
        st.error(f"엑셀을 읽는 중 오류 발생: {e}")

if df_input is not None and not df_input.empty:
    date_info_msg = f" ({current_pdf_date_str} vs 전일)" if current_pdf_date_str else ""
    st.success(f"✅ 총 {len(df_input)}개 종목 데이터 로드 완료!{date_info_msg}")
    
    ticker_col = "종목코드"
    weight_col = "비중"
            
    with st.spinner("실시간 시세 연산 중..."):
        clean_df = df_input.dropna(subset=[ticker_col, weight_col]).copy()
        
        prev_weight_map = {}
        if df_prev is not None and not df_prev.empty:
            for _, p_row in df_prev.iterrows():
                p_code = str(p_row['종목코드']).strip().upper()
                try:
                    p_w = float(str(p_row['비중']).replace('%', '').replace(',', '').strip())
                    prev_weight_map[p_code] = p_w
                except ValueError:
                    pass
        
        # [추가] 어제 파일에는 있었으나 오늘 파일에 없는 종목(편출 종목)도 비중 변화 분석에 포함시키기 위해 추출
        all_known_tickers = set(clean_df[ticker_col].tolist())
        if df_prev is not None and not df_prev.empty:
            all_known_tickers.update(df_prev['종목코드'].tolist())
            
        ticker_list = list(all_known_tickers)
        
        official_base_fx = get_naver_official_base_fx()
        etf_prev_close = get_naver_etf_prev_close("426030")
        timefolio_data = get_timefolio_official_data(idx=2)
        batch_results, live_fx = get_tradingview_direct_prices(ticker_list)
        
        if official_base_fx == 0.0:
            official_base_fx = live_fx
            
        if official_base_fx > 0:
            usdkrw_change_pct = ((live_fx - official_base_fx) / official_base_fx) * 100
        else:
            usdkrw_change_pct = 0.0
        
        live_data = []
        failed_tickers = []
        new_added_stocks = []
        
        cash_keywords = ["KRW", "CASH", "현금", "원화", "달러", "예금"]
        
        # 오늘 보유 중인 종목 데이터 처리
        curr_tickers_map = {}
        for index, row in clean_df.iterrows():
            raw_ticker = str(row[ticker_col]).strip()
            ticker = raw_ticker.split()[0].upper().replace("/", ".")
            try:
                weight = float(str(row[weight_col]).replace('%', '').replace(',', '').strip())
            except ValueError:
                weight = 0.0
            curr_tickers_map[ticker] = (weight, row)

        processed_tickers = set()

        # 1. 오늘 보유 중이거나 어제 있었던 모든 종목 순회
        for ticker in ticker_list:
            if ticker in processed_tickers:
                continue
            processed_tickers.add(ticker)

            weight = 0.0
            row_data = None
            if ticker in curr_tickers_map:
                weight, row_data = curr_tickers_map[ticker]

            prev_w = prev_weight_map.get(ticker, None)
            if prev_w is None and any(kw in ticker for kw in cash_keywords):
                prev_w = prev_weight_map.get("KRW", None)

            w_diff_val = 0.0
            if prev_w is not None:
                w_diff = weight - prev_w
                w_diff_val = w_diff
                prev_w_str = f"{prev_w:.2f}%"
                if abs(w_diff) < 0.001:
                    w_diff_str = "-"
                else:
                    w_diff_str = f"{w_diff:+.2f}%p"
            else:
                prev_w_str = "-"
                w_diff_str = "✨ NEW"
                w_diff_val = weight
                if weight > 0:
                    new_added_stocks.append(f"**{ticker}** ({weight:.2f}%)")

            # 편출(OUT)된 종목 처리
            if weight == 0.0 and prev_w is not None and prev_w > 0:
                w_diff = 0.0 - prev_w
                w_diff_val = w_diff
                prev_w_str = f"{prev_w:.2f}%"
                w_diff_str = "🚪 OUT"

            # 주가 변동률 및 가격 매핑
            stock_change_pct = 0.0
            live_price = 0.0

            if any(kw in ticker for kw in cash_keywords) or ticker == "KRW":
                krw_amount = row_data.get("평가금액", 0.0) if row_data is not None and "평가금액" in row_data and row_data["평가금액"] > 0 else 1.0
                live_data.append({
                    "종목코드": "KRW", 
                    "실시간 가격($)": krw_amount, 
                    "주가변동률(%)": 0.0, 
                    "당일비중(%)": weight,
                    "전일비중(%)": prev_w_str,
                    "비중변화(%p)": w_diff_str,
                    "비중변화_수치": w_diff_val
                })
            elif "NQU" in ticker or "NQ1!" in ticker or "NQ=" in ticker:
                qqq_price, qqq_change = batch_results.get("QQQ", (0.0, 0.0))
                live_data.append({
                    "종목코드": f"{ticker} (QQQ대체)", 
                    "실시간 가격($)": qqq_price, 
                    "주가변동률(%)": qqq_change, 
                    "당일비중(%)": weight,
                    "전일비중(%)": prev_w_str,
                    "비중변화(%p)": w_diff_str,
                    "비중변화_수치": w_diff_val
                })
            elif ticker in batch_results and batch_results[ticker][0] > 0:
                live_price, stock_change_pct = batch_results[ticker]
                live_data.append({
                    "종목코드": ticker, 
                    "실시간 가격($)": live_price, 
                    "주가변동률(%)": stock_change_pct, 
                    "당일비중(%)": weight,
                    "전일비중(%)": prev_w_str,
                    "비중변화(%p)": w_diff_str,
                    "비중변화_수치": w_diff_val
                })
            else:
                failed_tickers.append(ticker)
                live_data.append({
                    "종목코드": ticker, 
                    "실시간 가격($)": 0.0, 
                    "주가변동률(%)": 0.0, 
                    "당일비중(%)": weight,
                    "전일비중(%)": prev_w_str,
                    "비중변화(%p)": w_diff_str,
                    "비중변화_수치": w_diff_val
                })

        curr_tickers = set([str(r[ticker_col]).split()[0].upper() for _, r in clean_df.iterrows()])
        removed_stocks = []
        if df_prev is not None and not df_prev.empty:
            for p_code, p_w in prev_weight_map.items():
                if p_code not in curr_tickers and p_code != "KRW":
                    removed_stocks.append(f"**{p_code}** (전일 {p_w:.2f}%)")

        result_df = pd.DataFrame(live_data)
        
        total_weight = result_df['당일비중(%)'].sum()
        if total_weight > 0:
            stock_inav_change = ((result_df['당일비중(%)'] / total_weight) * result_df['주가변동률(%)']).sum()
            fx_inav_change = usdkrw_change_pct
            total_inav_change = stock_inav_change + fx_inav_change
        else:
            stock_inav_change = 0.0
            fx_inav_change = 0.0
            total_inav_change = 0.0
        
        # 상단 요약 카드
        st.metric(
            label="📈 실시간 iNAV 추정 총 변동률", 
            value=f"{total_inav_change:+.2f}%", 
            delta=f"주가({stock_inav_change:+.2f}%) + 환율({fx_inav_change:+.2f}%)"
        )
            
        if etf_prev_close > 0:
            estimated_inav_price = etf_prev_close * (1 + (total_inav_change / 100))
            price_diff = estimated_inav_price - etf_prev_close
            st.metric(
                label="💵 나스닥100액티브(426030) 예상 iNAV", 
                value=f"{estimated_inav_price:,.0f} 원", 
                delta=f"{price_diff:+,.0f} 원 (종가: {etf_prev_close:,.0f}원)"
            )
        
        # 타임폴리오 공식 홈페이지 정보 카드
        if timefolio_data["live_nav"] > 0 or timefolio_data["base_nav"] > 0:
            live_nav_str = f"**{timefolio_data['live_nav']:,.2f}원**" if timefolio_data['live_nav'] > 0 else "대기 중"
            base_nav_str = f"**{timefolio_data['base_nav']:,.2f}원**" if timefolio_data['base_nav'] > 0 else "대기 중"
            
            st.success(
                f"🏛️ **공식 공시 기준가**  \n"
                f"- 실시간: {live_nav_str}  \n"
                f"- 전일 확정: {base_nav_str}"
            )
        
        # 신규 편입 / 편출 종목 요약 카드 박스
        if new_added_stocks or removed_stocks:
            new_msg = f"✨ **신규 편입**: {', '.join(new_added_stocks)}" if new_added_stocks else "✨ **신규 편입**: 없음"
            out_msg = f"🚪 **편출 (전량 매도)**: {', '.join(removed_stocks)}" if removed_stocks else "🚪 **편출 (전량 매도)**: 없음"
            st.warning(f"📋 **포트폴리오 변동 내역**  \n- {new_msg}  \n- {out_msg}")

        # =========================================================
        # 🔥 히트맵 (컬러바 하단 배치 및 가로 꽉 참)
        # =========================================================
        st.markdown("---")
        
        active_df = result_df[result_df['당일비중(%)'] > 0].copy()
        st.markdown(f"### 🔥 전체 구성종목 ({len(active_df)}개) 히트맵")
        
        all_df = active_df.sort_values(by="당일비중(%)", ascending=False).copy()
        all_df['표시명'] = (
            "<span style='font-size:20px; font-weight:bold;'>" + all_df['종목코드'] + "</span><br>" +
            "<span style='font-size:15px;'>" + all_df['주가변동률(%)'].map('{:+.2f}%'.format) + "</span>"
        )
        
        fig_treemap = px.treemap(
            all_df,
            path=['표시명'],
            values='당일비중(%)',
            color='주가변동률(%)',
            color_continuous_scale=[
                [0.0, '#4285F4'],   # 파랑 (하락)
                [0.166, '#3B72E2'], 
                [0.333, '#345FCF'], 
                [0.5, '#404552'],   # 회색 (보합)
                [0.666, '#8B3A48'], 
                [0.833, '#C83742'], 
                [1.0, '#FF1744']    # 빨강 (상승)
            ],
            range_color=[-3.0, 3.0]
        )
        
        fig_treemap.update_traces(
            textposition="middle center",
            selector=dict(type='treemap')
        )
        
        fig_treemap.update_layout(
            margin=dict(t=5, l=5, r=5, b=5),
            height=600,
            uniformtext=dict(minsize=8, mode=False),
            coloraxis_colorbar=dict(
                title=dict(text="주가변동률(%)", side="top"),
                orientation="h",
                y=-0.1,
                x=0.5,
                xanchor="center",
                len=0.8
            )
        )
        
        st.plotly_chart(fig_treemap, use_container_width=True)

        # =========================================================
        # 🔄 비중 변화 TOP 10 (편출 종목도 변동폭 크면 포함되도록 수정)
        # =========================================================
        st.markdown("---")
        st.markdown("### 🔄 전일 대비 비중 변화 TOP 10")
        
        top10_change_df = result_df.copy()
        top10_change_df['절대변화량'] = top10_change_df['비중변화_수치'].abs()
        top10_change_df = top10_change_df.sort_values(by="절대변화량", ascending=False).head(10)
        
        display_top10 = top10_change_df[['종목코드', '당일비중(%)', '전일비중(%)', '비중변화(%p)', '주가변동률(%)']].reset_index(drop=True)
        display_top10.index = range(1, len(display_top10) + 1)
        
        stiler_top10 = display_top10.style
        if hasattr(stiler_top10, "map"):
            styled_top10 = stiler_top10.map(color_weight_change, subset=['비중변화(%p)']).map(color_change_pct, subset=['주가변동률(%)'])
        else:
            styled_top10 = stiler_top10.applymap(color_weight_change, subset=['비중변화(%p)']).applymap(color_change_pct, subset=['주가변동률(%)'])
            
        styled_top10 = styled_top10.format({
            '당일비중(%)': '{:.2f}%',
            '전일비중(%)': lambda x: f"{float(x.replace('%','')):.2f}%" if '%' in str(x) else x,
            '주가변동률(%)': '{:+.2f}%'
        }).set_properties(**{'text-align': 'center'})
        
        st.dataframe(styled_top10, use_container_width=True, height=385)

        # =========================================================
        # 📊 3. 종목별 실시간 전체 현황 표 (보유 중인 종목만 표시)
        # =========================================================
        st.markdown("---")
        st.markdown("### 📊 종목별 실시간 전체 현황")
        
        display_full_df = result_df[result_df['당일비중(%)'] > 0].drop(columns=['비중변화_수치'], errors='ignore').reset_index(drop=True)
        display_full_df.index = range(1, len(display_full_df) + 1)
        
        def format_price_col(row):
            val = row['실시간 가격($)']
            code = row['종목코드']
            if code == "KRW":
                return f"{val:,.0f} (원)"
            elif val > 10:
                return f"{val:,.2f}"
            else:
                return f"{val:,.2f}"

        display_full_df['실시간 가격(표시용)'] = display_full_df.apply(format_price_col, axis=1)
        display_full_df = display_full_df[['종목코드', '실시간 가격(표시용)', '주가변동률(%)', '당일비중(%)', '전일비중(%)', '비중변화(%p)']]
        display_full_df.columns = ['종목코드', '실시간 가격($)', '주가변동률(%)', '당일비중(%)', '전일비중(%)', '비중변화(%p)']

        styled_full = display_full_df.style.map(color_change_pct, subset=['주가변동률(%)']).map(color_weight_change, subset=['비중변화(%p)']) if hasattr(display_full_df.style, "map") else display_full_df.style.applymap(color_change_pct, subset=['주가변동률(%)']).applymap(color_weight_change, subset=['비중변화(%p)'])

        styled_full = styled_full.format({
            '주가변동률(%)': '{:+.2f}',
            '당일비중(%)': '{:.2f}%'
        }).set_properties(**{'text-align': 'center'})
        
        st.dataframe(styled_full, use_container_width=True, height=500)
