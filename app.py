import streamlit as st
import pandas as pd
import io
import requests
import re
from bs4 import BeautifulSoup

st.set_page_config(page_title="타임폴리오 ETF 실시간 대시보드(TradingView)", layout="wide")

st.title("🎯 타임폴리오 액티브 ETF 실시간 iNAV 대시보드 (TradingView)")
st.markdown("네이버 금융의 **서울외환시장 마감 환율(오후 3:30)**과 **426030 종가**, 그리고 **타임폴리오 공식 사이트의 실시간 기준가**를 연동하여 산출합니다.")

# 1. 타임폴리오 사이트 바로가기 버튼 (상단배치)
st.link_button(
    "🔗 타임폴리오 공식 구성종목 페이지 바로가기", 
    "https://timeetf.co.kr/m11_view.php?idx=2&cate=&pdfDate=2026-07-24#constituentItems",
    use_container_width=False
)

st.markdown("---")

# 2. 엑셀 파일 업로드 창
uploaded_file = st.file_uploader("📁 타임폴리오 구성종목 엑셀 파일(.xlsx) 업로드", type=["xlsx", "xls"])

df_input = None
if uploaded_file is not None:
    try:
        df_input = pd.read_excel(uploaded_file)
    except Exception as e:
        st.error(f"엑셀을 읽는 중 오류 발생: {e}")

# 네이버 금융에서 서울외환시장 정규장 마감 환율(오후 3:30 기준가) 수집
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

# 타임폴리오 공식 홈페이지(timeetf.co.kr) 정밀 파싱
def get_timefolio_official_data(idx=2):
    url = f"https://timeetf.co.kr/m11_view.php?idx={idx}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    result = {
        "live_nav": 0.0,       # 실시간 기준가
        "live_time": "",       # 실시간 기준시간
        "base_nav": 0.0,       # 전일 기준가
        "base_date": ""        # 전일 기준일자
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

# 트레이딩뷰 직접 API 수집 함수 (주식 및 실시간 환율)
def get_tradingview_direct_prices(symbols):
    url = "https://scanner.tradingview.com/america/scan"
    
    clean_symbols = []
    for s in symbols:
        sym_str = str(s).split()[0].upper().replace("/", ".")
        if "NQU" in sym_str or "NQ1!" in sym_str or "NQ=" in sym_str:
            clean_symbols.append("QQQ")
        else:
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
        # 1. 주식 시세 조회
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
                    
        # 2. 트레이딩뷰 실시간 환율 조회
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

# 변동률 수치 색상 스타일 적용 함수 (상승: 파란색, 하락: 빨간색)
def color_change_pct(val):
    try:
        val_num = float(val)
        if val_num > 0:
            return 'color: #0055FF; font-weight: bold;'  # 상승: 파란색
        elif val_num < 0:
            return 'color: #FF0000; font-weight: bold;'  # 하락: 빨간색
    except Exception:
        pass
    return ''

if df_input is not None and not df_input.empty:
    st.success(f"✅ 총 {len(df_input)}개의 종목 데이터를 읽었습니다!")
    
    # 열 자동 탐색
    ticker_col = df_input.columns[0]
    weight_col = df_input.columns[-1]
    
    for col in df_input.columns:
        col_str = str(col)
        if "코드" in col_str or "티커" in col_str or "Symbol" in col_str:
            ticker_col = col
        if "비중" in col_str or "평가" in col_str or "Weight" in col_str:
            weight_col = col
    
    if st.button("🚀 TradingView 주가 & 정밀 환율 실시간 가져오기 및 iNAV 계산", use_container_width=True):
        with st.spinner("타임폴리오 공식 데이터 및 실시간 시세 수집 중..."):
            clean_df = df_input.dropna(subset=[ticker_col, weight_col]).copy()
            ticker_list = clean_df[ticker_col].tolist()
            
            # 1. 서울외환시장 마감 환율 수집
            official_base_fx = get_naver_official_base_fx()
            
            # 2. ETF (426030) 직전 정규장 마감 종가 수집
            etf_prev_close = get_naver_etf_prev_close("426030")
            
            # 3. 타임폴리오 공식 홈페이지 수치 정밀 파싱 (idx=2)
            timefolio_data = get_timefolio_official_data(idx=2)
            
            # 4. 트레이딩뷰 주가 및 실시간 환율 수집
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
                    weight = float(str(row[weight_col]).replace('%', '').strip())
                except ValueError:
                    weight = 0.0
                
                # 1) 현금 자산 처리
                if any(kw in ticker for kw in cash_keywords):
                    live_data.append({
                        "종목코드": ticker + " (현금)", 
                        "TradingView실시간가($)": 1.0, 
                        "주가변동률(%)": 0.0, 
                        "비중(%)": weight
                    })
                # 2) NQU6 등 선물 -> QQQ 대체 처리
                elif "NQU" in ticker or "NQ1!" in ticker or "NQ=" in ticker:
                    qqq_price, qqq_change = batch_results.get("QQQ", (0.0, 0.0))
                    live_data.append({
                        "종목코드": f"{ticker} (QQQ대체)", 
                        "TradingView실시간가($)": qqq_price, 
                        "주가변동률(%)": qqq_change, 
                        "비중(%)": weight
                    })
                # 3) 일반 주식 종목 매칭
                elif ticker in batch_results and batch_results[ticker][0] > 0:
                    live_price, stock_change_pct = batch_results[ticker]
                    live_data.append({
                        "종목코드": ticker, 
                        "TradingView실시간가($)": live_price, 
                        "주가변동률(%)": stock_change_pct, 
                        "비중(%)": weight
                    })
                # 4) 시세 수집 실패 시
                else:
                    failed_tickers.append(ticker)
                    live_data.append({
                        "종목코드": ticker, 
                        "TradingView실시간가($)": 0.0, 
                        "주가변동률(%)": 0.0, 
                        "비중(%)": weight
                    })
            
            result_df = pd.DataFrame(live_data)
            
            total_weight = result_df['비중(%)'].sum()
            if total_weight > 0:
                stock_inav_change = ((result_df['비중(%)'] / total_weight) * result_df['주가변동률(%)']).sum()
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
                
            st.markdown("### 📊 TradingView 종목별 실시간 현황")
            
            # Pandas 버전 호환성 보정 (applymap -> map 변환)
            stiler = result_df.style
            if hasattr(stiler, "map"):
                styled_df = stiler.map(color_change_pct, subset=['주가변동률(%)'])
            else:
                styled_df = stiler.applymap(color_change_pct, subset=['주가변동률(%)'])
                
            styled_df = styled_df.format({
                'TradingView실시간가($)': '{:,.2f}',
                '주가변동률(%)': '{:+.2f}',
                '비중(%)': '{:.2f}'
            })
            
            st.dataframe(styled_df, use_container_width=True)

elif df_input is not None and df_input.empty:
    st.warning("⚠️ 선택한 데이터/파일이 비어있습니다.")
