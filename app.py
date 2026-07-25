import streamlit as st
import pandas as pd
import io
import requests
import re
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import plotly.express as px

st.set_page_config(page_title="타임폴리오 ETF 실시간 대시보드(TradingView)", layout="wide")

st.title("🎯 타임폴리오 액티브 ETF 실시간 iNAV 대시보드 (TradingView)")
st.markdown("타임폴리오 공식 홈페이지의 **전체 구성종목(PDF)** 및 **전일 대비 비중 변화**, 그리고 **실시간 기준가**를 자동으로 연동하여 산출합니다.")

# 1. 타임폴리오 사이트 바로가기 버튼 (상단배치)
st.link_button(
    "🔗 타임폴리오 공식 구성종목 페이지 바로가기", 
    "https://timeetf.co.kr/m11_view.php?idx=2#constituentItems",
    use_container_width=False
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

# 특정 날짜(date_str)의 타임폴리오 구성종목 및 공시 기준일자 정밀 수집
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
                
                clean_ticker = ""
                
                if "현금" in raw_name or "현금" in raw_code or "CASH" in raw_name.upper() or "USD" in raw_name.upper():
                    clean_ticker = "USD (현금)"
                else:
                    code_parts = raw_code.split()
                    if code_parts:
                        clean_ticker = code_parts[0].strip().upper()
                
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
                            "비중": weight_val
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
        elif "현금" not in sym_str and "CASH" not in sym_str and "KRW" not in sym_str and "USD" not in sym_str:
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

# 주가 변동률 색상 스타일 (상승: 파란색, 하락: 빨간색)
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

# 비중 변화 색상 스타일 (확대: 파란색, 축소: 빨간색, 신규: 보라색)
def color_weight_change(val):
    try:
        if "NEW" in str(val):
            return 'color: #8A2BE2; font-weight: bold;'
        val_num = float(str(val).replace('%p', '').replace('+', '').strip())
        if val_num > 0:
            return 'color: #0055FF; font-weight: bold;'
        elif val_num < 0:
            return 'color: #FF0000; font-weight: bold;'
    except Exception:
        pass
    return ''

# 메인 UI
st.markdown("### 🌐 구성종목 가져오기 방식을 선택하세요")

col_btn1, col_btn2 = st.columns([2, 1])

with col_btn1:
    fetch_auto = st.button("🚀 타임폴리오 웹사이트에서 구성종목 전체 수집 (당일 & 전일 비중 추적)", use_container_width=True, type="primary")

with col_btn2:
    uploaded_file = st.file_uploader("📁 백업용 엑셀 업로드(.xlsx)", type=["xlsx", "xls"], label_visibility="collapsed")

df_input = None
df_prev = None
current_pdf_date_str = ""
prev_pdf_date_str = ""

if fetch_auto:
    with st.spinner("타임폴리오 웹사이트에서 최신 및 직전 영업일 구성종목(PDF)을 수집 중입니다..."):
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
        df_input = pd.read_excel(uploaded_file)
    except Exception as e:
        st.error(f"엑셀을 읽는 중 오류 발생: {e}")

if df_input is not None and not df_input.empty:
    date_info_msg = f" (최신 공시일: {current_pdf_date_str} vs 전일: {prev_pdf_date_str})" if current_pdf_date_str and prev_pdf_date_str else ""
    st.success(f"✅ 총 {len(df_input)}개의 종목(현금 포함) 데이터를 정확히 읽었습니다!{date_info_msg}")
    
    ticker_col = "종목코드"
    weight_col = "비중"
    
    for col in df_input.columns:
        col_str = str(col)
        if "코드" in col_str or "티커" in col_str or "Symbol" in col_str:
            ticker_col = col
        if "비중" in col_str or "평가" in col_str or "Weight" in col_str:
            weight_col = col
            
    with st.spinner("타임폴리오 공식 데이터 및 실시간 시세 연산 중..."):
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
        
        ticker_list = clean_df[ticker_col].tolist()
        
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
        
        cash_keywords = ["USD", "CASH", "KRW", "현금", "원화", "달러", "예금"]
        
        for index, row in clean_df.iterrows():
            raw_ticker = str(row[ticker_col]).strip()
            ticker = raw_ticker.split()[0].upper().replace("/", ".")
            
            try:
                weight = float(str(row[weight_col]).replace('%', '').replace(',', '').strip())
            except ValueError:
                weight = 0.0
            
            prev_w = prev_weight_map.get(ticker, None)
            if prev_w is None and any(kw in ticker for kw in cash_keywords):
                prev_w = prev_weight_map.get("USD (현금)", None)
                
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
                w_diff_str = "NEW"
                w_diff_val = weight
            
            if any(kw in ticker for kw in cash_keywords):
                live_data.append({
                    "종목코드": "USD (현금)", 
                    "TradingView실시간가($)": 1.0, 
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
                    "TradingView실시간가($)": qqq_price, 
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
                    "TradingView실시간가($)": live_price, 
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
                    "TradingView실시간가($)": 0.0, 
                    "주가변동률(%)": 0.0, 
                    "당일비중(%)": weight,
                    "전일비중(%)": prev_w_str,
                    "비중변화(%p)": w_diff_str,
                    "비중변화_수치": w_diff_val
                })
        
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
        
        # 상단 2열 레이아웃 카드 구성
        metric_col1, metric_col2 = st.columns(2)
        
        with metric_col1:
            st.metric(
                label="📈 실시간 iNAV 추정 총 변동률 [3번 = 1번 + 2번]", 
                value=f"{total_inav_change:+.2f}%", 
                delta=f"1.주가 변동({stock_inav_change:+.2f}%) + 2.환율 변동({fx_inav_change:+.2f}%)"
            )
            
        with metric_col2:
            if etf_prev_close > 0:
                estimated_inav_price = etf_prev_close * (1 + (total_inav_change / 100))
                price_diff = estimated_inav_price - etf_prev_close
                st.metric(
                    label="💵 TIMEFOLIO 미국나스닥100액티브 (426030) 예상 iNAV", 
                    value=f"{estimated_inav_price:,.0f} 원", 
                    delta=f"{price_diff:+,.0f} 원 (직전 종가: {etf_prev_close:,.0f}원 기준)"
                )
            else:
                st.metric(label="💵 TIMEFOLIO 미국나스닥100액티브 예상 iNAV", value="종가 수집 불가")
        
        # 타임폴리오 공식 홈페이지 정보 카드
        if timefolio_data["live_nav"] > 0 or timefolio_data["base_nav"] > 0:
            live_nav_str = f"**{timefolio_data['live_nav']:,.2f}원**" if timefolio_data['live_nav'] > 0 else "**수집 대기 중**"
            base_nav_str = f"**{timefolio_data['base_nav']:,.2f}원**" if timefolio_data['base_nav'] > 0 else "**수집 대기 중**"
            
            time_info = f" ({timefolio_data['live_time']} 기준)" if timefolio_data['live_time'] else ""
            date_info = f" ({timefolio_data['base_date']} 기준)" if timefolio_data['base_date'] else ""
            
            st.success(
                f"🏛️ **타임폴리오 공식 홈페이지 공시 정보**  \n"
                f"- 실시간 기준가: {live_nav_str}{time_info}  \n"
                f"- 전일 확정 기준가: {base_nav_str}{date_info}"
            )
        
        # 환율 상세 박스
        st.info(
            f"💵 **서울외환시장 공식 마감가 기준 환율 보정 완료**  \n"
            f"- 트레이딩뷰 실시간 환율: **{live_fx:,.2f}원**  \n"
            f"- 서울외환시장 마감 기준 환율(네이버금융): **{official_base_fx:,.2f}원**  \n"
            f"- 한국 장 마감 대비 환율 변동률: **{usdkrw_change_pct:+.2f}%**"
        )
        
        if failed_tickers:
            st.warning(f"⚠️ 시세를 불러오지 못한 티커: {', '.join(set(failed_tickers))}")

        # =========================================================
        # 🔥 Finviz 스타일 (초록/회색/빨강) 히트맵 & 🔄 비중변화 TOP10
        # =========================================================
        st.markdown("---")
        
        col_viz1, col_viz2 = st.columns([3, 2])
        
        # 1. 🔥 비중 TOP 20 히트맵 (Finviz 색상 적용)
        with col_viz1:
            st.markdown("### 🔥 비중 TOP 20 주가 상승/하락 히트맵")
            
            top20_df = result_df.sort_values(by="당일비중(%)", ascending=False).head(20).copy()
            top20_df['표시명'] = top20_df['종목코드'] + "<br>" + top20_df['당일비중(%)'].map('{:.2f}%'.format) + "<br>" + top20_df['주가변동률(%)'].map('{:+.2f}%'.format)
            
            # Finviz 스타일: 초록(상승) - 회색(보합) - 빨강(하락) 스케일
            fig_treemap = px.treemap(
                top20_df,
                path=['표시명'],
                values='당일비중(%)',
                color='주가변동률(%)',
                color_continuous_scale=[
                    [0.0, '#8B0000'],   # 큰 하락 (-3% 이하): 진한 빨강
                    [0.25, '#FF5555'],  # 일반 하락 (-1%~-2%): 빨강
                    [0.5, '#CCCCCC'],   # 보합 (0% 근처): 연회색
                    [0.75, '#55BF73'],  # 일반 상승 (+1%~+2%): 밝은 초록
                    [1.0, '#107C41']    # 큰 상승 (+3% 이상): 진한 초록
                ],
                color_continuous_midpoint=0
            )
            fig_treemap.update_layout(
                margin=dict(t=10, l=10, r=10, b=10),
                height=380
            )
            st.plotly_chart(fig_treemap, use_container_width=True)

        # 2. 🔄 비중 변화 TOP 10
        with col_viz2:
            st.markdown("### 🔄 전일 대비 비중 변화 TOP 10")
            
            top10_change_df = result_df.copy()
            top10_change_df['절대변화량'] = top10_change_df['비중변화_수치'].abs()
            top10_change_df = top10_change_df.sort_values(by="절대변화량", ascending=False).head(10)
            
            display_top10 = top10_change_df[['종목코드', '당일비중(%)', '전일비중(%)', '비중변화(%p)']].reset_index(drop=True)
            
            stiler_top10 = display_top10.style
            if hasattr(stiler_top10, "map"):
                styled_top10 = stiler_top10.map(color_weight_change, subset=['비중변화(%p)'])
            else:
                styled_top10 = stiler_top10.applymap(color_weight_change, subset=['비중변화(%p)'])
                
            styled_top10 = styled_top10.format({
                '당일비중(%)': '{:.2f}'
            })
            
            st.dataframe(styled_top10, use_container_width=True, height=380)

        # =========================================================
        # 📊 3. TradingView 종목별 실시간 전체 현황 표
        # =========================================================
        st.markdown("### 📊 TradingView 종목별 실시간 현황 (전일 대비 비중 추적)")
        
        display_full_df = result_df.drop(columns=['비중변화_수치'], errors='ignore')
        stiler_full = display_full_df.style
        
        if hasattr(stiler_full, "map"):
            styled_full = stiler_full.map(color_change_pct, subset=['주가변동률(%)']).map(color_weight_change, subset=['비중변화(%p)'])
        else:
            styled_full = stiler_full.applymap(color_change_pct, subset=['주가변동률(%)']).applymap(color_weight_change, subset=['비중변화(%p)'])
            
        styled_full = styled_full.format({
            'TradingView실시간가($)': '{:,.2f}',
            '주가변동률(%)': '{:+.2f}',
            '당일비중(%)': '{:.2f}'
        })
        
        st.dataframe(styled_full, use_container_width=True)
