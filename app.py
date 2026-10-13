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
