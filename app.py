import streamlit as st
import pandas as pd
import io
import requests

st.set_page_config(page_title="타임폴리오 ETF 실시간 대시보드(TradingView)", layout="wide")

st.title("🎯 타임폴리오 액티브 ETF 실시간 iNAV 대시보드 (TradingView)")
st.markdown("트레이딩뷰 라이브 API를 통해 **모든 종목의 실시간 변동률(%)을 일괄 수집**하여 iNAV를 산출합니다.")

tab1, tab2 = st.tabs(["📁 엑셀 파일 업로드", "📋 웹페이지 텍스트 직접 붙여넣기"])

df_input = None

# [방식 1] 엑셀 파일 업로드
with tab1:
    uploaded_file = st.file_uploader("타임폴리오 구성종목 엑셀 파일(.xlsx) 업로드", type=["xlsx", "xls"])
    if uploaded_file is not None:
        try:
            df_input = pd.read_excel(uploaded_file)
        except Exception as e:
            st.error(f"엑셀을 읽는 중 오류 발생: {e}")

# [방식 2] 웹페이지 텍스트 붙여넣기
with tab2:
    st.markdown("타임폴리오 웹페이지의 구성종목 표를 마우스로 드래그하여 복사(`Ctrl+C`)한 후 아래에 붙여넣으세요.")
    pasted_text = st.text_area("복사한 내용 붙여넣기", height=150)
    if pasted_text:
        try:
            df_input = pd.read_csv(io.StringIO(pasted_text), sep='\t')
        except Exception:
            try:
                df_input = pd.read_csv(io.StringIO(pasted_text), sep=r'\s+')
            except Exception as e:
                st.error("붙여넣은 형식을 분석할 수 없습니다.")

# 트레이딩뷰 고성능 직접 API 수집 함수
def get_tradingview_direct_prices(symbols):
    url = "https://scanner.tradingview.com/america/scan"
    
    clean_symbols = []
    for s in symbols:
        sym_str = str(s).split()[0].upper().replace("/", ".")
        # NQU6 등 나스닥 선물 티커는 QQQ로 대체 조회
        if "NQU" in sym_str or "NQ1!" in sym_str or "NQ=" in sym_str:
            clean_symbols.append("QQQ")
        else:
            clean_symbols.append(sym_str)
            
    # 중복 제거 및 리스트화
    clean_symbols = list(set(clean_symbols))
    
    exchanges = ["NASDAQ", "NYSE", "AMEX"]
    tickers_to_query = []
    
    for sym in clean_symbols:
        for ex in exchanges:
            tickers_to_query.append(f"{ex}:{sym}")
            
    payload = {
        "symbols": {"tickers": tickers_to_query},
        "columns": ["close", "change"]
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    result_map = {}
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json().get("data", [])
            for item in data:
                ticker_full = item.get("s", "")
                ticker_clean = ticker_full.split(":")[-1]
                values = item.get("d", [0.0, 0.0])
                
                close_price = values[0] if len(values) > 0 and values[0] is not None else 0.0
                change_pct = values[1] if len(values) > 1 and values[1] is not None else 0.0
                
                if ticker_clean not in result_map or result_map[ticker_clean][0] == 0.0:
                    result_map[ticker_clean] = (close_price, change_pct)
    except Exception as e:
        st.error(f"TradingView API 연동 오류: {e}")
        
    return result_map

if df_input is not None and not df_input.empty:
    st.success(f"✅ 총 {len(df_input)}개의 종목 데이터를 읽었습니다!")
    
    st.markdown("### 🔍 1. 데이터 미리보기")
    st.dataframe(df_input.head())
    
    st.markdown("### ⚙️ 2. 컬럼 매칭 및 설정")
    col1, col2 = st.columns(2)
    
    default_ticker_idx = 0
    default_weight_idx = len(df_input.columns) - 1
    
    for i, col in enumerate(df_input.columns):
        col_str = str(col)
        if "코드" in col_str or "티커" in col_str or "Symbol" in col_str:
            default_ticker_idx = i
        if "비중" in col_str or "평가" in col_str or "Weight" in col_str:
            default_weight_idx = i

    with col1:
        ticker_col = st.selectbox("📌 '종목코드(티커)' 열 선택 (※ NVDA, AAPL 등 티커 열)", df_input.columns, index=default_ticker_idx)
    with col2:
        weight_col = st.selectbox("📌 '비중(%)' 열 선택", df_input.columns, index=default_weight_idx)
    
    if st.button("🚀 TradingView 초고속 시세 가져오기 및 iNAV 계산"):
        with st.spinner("TradingView 공식 API로 실시간 시세 및 변동률(%) 일괄 수집 중..."):
            clean_df = df_input.dropna(subset=[ticker_col, weight_col]).copy()
            ticker_list = clean_df[ticker_col].tolist()
            
            # 단 1번의 HTTP POST 요청으로 전체 시세 수집
            batch_results = get_tradingview_direct_prices(ticker_list)
            
            live_data = []
            failed_tickers = []
            
            # 현금 자산 티커 키워드 리스트
            cash_keywords = ["USD", "CASH", "KRW", "현금", "원화", "달러", "예금"]
            
            for index, row in clean_df.iterrows():
                raw_ticker = str(row[ticker_col]).strip()
                ticker = raw_ticker.split()[0].upper().replace("/", ".")
                
                try:
                    weight = float(str(row[weight_col]).replace('%', '').strip())
                except ValueError:
                    weight = 0.0
                
                # 1) 현금 자산 예외 처리 (변동률 0%)
                if any(kw in ticker for kw in cash_keywords):
                    live_data.append({
                        "종목코드": ticker + " (현금)", 
                        "TradingView실시간가($)": 1.0, 
                        "실시간변동률(%)": 0.0, 
                        "비중(%)": weight
                    })
                # 2) NQU6 등 나스닥 선물 -> QQQ 변동률 대체 처리
                elif "NQU" in ticker or "NQ1!" in ticker or "NQ=" in ticker:
                    qqq_price, qqq_change = batch_results.get("QQQ", (0.0, 0.0))
                    live_data.append({
                        "종목코드": f"{ticker} (QQQ대체)", 
                        "TradingView실시간가($)": qqq_price, 
                        "실시간변동률(%)": qqq_change, 
                        "비중(%)": weight
                    })
                # 3) 일반 주식 종목 매칭
                elif ticker in batch_results and batch_results[ticker][0] > 0:
                    live_price, tv_change_pct = batch_results[ticker]
                    live_data.append({
                        "종목코드": ticker, 
                        "TradingView실시간가($)": live_price, 
                        "실시간변동률(%)": tv_change_pct, 
                        "비중(%)": weight
                    })
                # 4) 시세 수집 실패 시
                else:
                    failed_tickers.append(ticker)
                    live_data.append({
                        "종목코드": ticker, 
                        "TradingView실시간가($)": 0.0, 
                        "실시간변동률(%)": 0.0, 
                        "비중(%)": weight
                    })
            
            result_df = pd.DataFrame(live_data)
            
            total_weight = result_df['비중(%)'].sum()
            if total_weight > 0:
                result_df['가중치반영(%)'] = (result_df['비중(%)'] / total_weight) * result_df['실시간변동률(%)']
                total_inav_change = result_df['가중치반영(%)'].sum()
            else:
                total_inav_change = 0.0
            
            st.metric(label="📈 TradingView 실시간 가중 평균 변동률 (iNAV 추정)", value=f"{total_inav_change:+.2f}%")
            
            if failed_tickers:
                st.warning(f"⚠️ 시세를 불러오지 못한 티커: {', '.join(set(failed_tickers))}")
                
            st.markdown("### 📊 3. TradingView 종목별 실시간 현황")
            st.dataframe(result_df)

elif df_input is not None and df_input.empty:
    st.warning("⚠️ 선택한 데이터/파일이 비어있습니다.")
