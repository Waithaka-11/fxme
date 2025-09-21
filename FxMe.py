import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, date
import numpy as np
import json
import os
from google.oauth2.service_account import Credentials
import gspread
import time
import requests

# Google Sheets Configuration
SHEET_NAME = "Forex Trading Analytics"
WORKSHEET_NAME = "Trades"

# Real-time update configuration
REAL_TIME_UPDATE_INTERVAL = 3
CACHE_TTL = 5
PRICE_UPDATE_INTERVAL = 30

# Live Price Data Functions
@st.cache_data(ttl=30)
def get_live_price(pair="EURUSD"):
    """Get live forex price from Alpha Vantage"""
    try:
        if "alpha_vantage" not in st.secrets:
            return None
            
        api_key = st.secrets["alpha_vantage"]["api_key"]
        
        if len(pair) == 6:
            from_currency = pair[:3]
            to_currency = pair[3:]
        else:
            if "/" in pair:
                from_currency, to_currency = pair.split("/")
            else:
                return None
        
        url = (
            f"https://www.alphavantage.co/query"
            f"?function=CURRENCY_EXCHANGE_RATE"
            f"&from_currency={from_currency}"
            f"&to_currency={to_currency}"
            f"&apikey={api_key}"
        )
        
        response = requests.get(url, timeout=10)
        data = response.json()
        
        if "Realtime Currency Exchange Rate" in data:
            price = float(data["Realtime Currency Exchange Rate"]["5. Exchange Rate"])
            return price
        elif "Error Message" in data:
            st.warning(f"API Error for {pair}: {data['Error Message']}")
            return None
        else:
            return None
