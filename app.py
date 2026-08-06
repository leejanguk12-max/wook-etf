def get_yahoo_realtime_prices_robust(symbols):
    """Finnhub API (무료 토큰) 및 Yahoo Chart API를 활용하여 프리장 실시간 가격/변동률 수집"""
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
    all_query_symbols = clean_symbols + ["USDKRW=X"]

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })

    result_map, live_fx = {}, 0.0

    # 1. Finnhub 무료 공개 API 조회 (프리장/본장 실시간 수집)
    # 필요 시 https://finnhub.io 에서 무료 API Key를 발급받아 token= 뒤에 대입하시면 더 안정적입니다.
    FINNHUB_TOKEN = "sandbox_c812345"  # 기본 테스트 토큰 또는 발급받은 무료 API Key

    for sym in all_query_symbols:
        try:
            if sym == "USDKRW=X":
                # 환율은 야후 파이낸스 차트 API로 수집
                url_fx = "https://query1.finance.yahoo.com/v8/finance/chart/USDKRW=X?interval=1m&range=1d"
                r_fx = session.get(url_fx, timeout=3)
                if r_fx.status_code == 200:
                    meta = r_fx.json().get("chart", {}).get("result", [{}])[0].get("meta", {})
                    live_fx = float(meta.get("regularMarketPrice", 0.0))
                continue

            # 주식 종목 Finnhub 시세 조회
            url = f"https://finnhub.io/api/v1/quote?symbol={sym}&token={FINNHUB_TOKEN}"
            resp = session.get(url, timeout=3)

            if resp.status_code == 200:
                data = resp.json()
                current_price = float(data.get("c", 0.0))      # 현재 실시간 체결가 (프리장 포함)
                prev_close = float(data.get("pc", 0.0))         # 전일 본장 종가

                if current_price > 0 and prev_close > 0:
                    change_pct = ((current_price - prev_close) / prev_close) * 100.0
                    result_map[sym] = (current_price, change_pct)
                    continue

            # Finnhub 실패 시 야후 파이낸스 v8 chart 백업 연동
            url_yf = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1m&range=1d&includePrePost=true"
            resp_yf = session.get(url_yf, timeout=3)
            if resp_yf.status_code == 200:
                meta = resp_yf.json().get("chart", {}).get("result", [{}])[0].get("meta", {})
                
                # preMarketPrice 우선 확인
                p_price = meta.get("preMarketPrice")
                r_price = meta.get("regularMarketPrice", 0.0)
                p_close = meta.get("chartPreviousClose") or meta.get("previousClose", 0.0)

                c_price = float(p_price) if p_price is not None and float(p_price) > 0 else float(r_price)

                if c_price > 0 and p_close > 0:
                    chg = ((c_price - float(p_close)) / float(p_close)) * 100.0
                    result_map[sym] = (c_price, chg)

        except Exception:
            pass

    if "QQQ" in result_map:
        for s in symbols:
            sym_upper = str(s).upper()
            if any(kw in sym_upper for kw in ["NQU", "NQ1!", "NQ="]):
                result_map[sym_upper.split()[0]] = result_map["QQQ"]

    return result_map, live_fx
