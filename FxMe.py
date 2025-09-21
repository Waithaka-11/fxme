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
            
    except Exception as e:
        return None

def get_multiple_prices(instruments):
    """Get prices for multiple instruments efficiently"""
    prices = {}
    for instrument in instruments:
        price = get_live_price(instrument)
        if price:
            prices[instrument] = price
        time.sleep(0.1)
    return prices

def check_trade_outcome(trade, live_price):
    """Check if trade should hit SL or TP based on live price"""
    if not live_price or trade.get('outcome') in ['Target Hit', 'SL Hit']:
        return trade
    
    entry = trade.get('entry', 0)
    sl = trade.get('sl', 0)
    target = trade.get('target', 0)
    
    if not all([entry, sl, target]):
        return trade
    
    is_long = target > entry
    
    if is_long:
        if live_price <= sl:
            trade['outcome'] = 'SL Hit'
            trade['result'] = 'Loss'
        elif live_price >= target:
            trade['outcome'] = 'Target Hit'
            trade['result'] = 'Win'
    else:
        if live_price >= sl:
            trade['outcome'] = 'SL Hit' 
            trade['result'] = 'Loss'
        elif live_price <= target:
            trade['outcome'] = 'Target Hit'
            trade['result'] = 'Win'
    
    return trade

def update_trade_outcomes(trades):
    """Update outcomes for all open trades based on live prices"""
    if not trades:
        return trades, False
    
    open_trades = [t for t in trades if t.get('outcome') not in ['Target Hit', 'SL Hit']]
    if not open_trades:
        return trades, False
    
    unique_instruments = list(set(t.get('instrument', '') for t in open_trades))
    live_prices = get_multiple_prices(unique_instruments)
    
    updated_trades = []
    changes_made = False
    
    for trade in trades:
        instrument = trade.get('instrument', '')
        live_price = live_prices.get(instrument)
        
        original_outcome = trade.get('outcome', '')
        updated_trade = check_trade_outcome(trade.copy(), live_price)
        
        if updated_trade.get('outcome') != original_outcome:
            changes_made = True
            
        updated_trades.append(updated_trade)
    
    return updated_trades, changes_made

def sync_trades_to_sheets(trades):
    """Sync all trades to Google Sheets"""
    try:
        gc = init_connection()
        if gc is None:
            return False
        
        spreadsheet = gc.open(SHEET_NAME)
        sheet = spreadsheet.worksheet(WORKSHEET_NAME)
        
        sheet.clear()
        
        headers = ['id', 'date', 'trader', 'instrument', 'entry', 'sl', 'target', 'risk', 'reward', 'rrRatio', 'outcome', 'result']
        sheet.append_row(headers)
        
        for trade in trades:
            row_data = [
                str(trade.get('id', '')),
                str(trade.get('date', '')),
                str(trade.get('trader', '')),
                str(trade.get('instrument', '')),
                float(trade.get('entry', 0)),
                float(trade.get('sl', 0)),
                float(trade.get('target', 0)),
                float(trade.get('risk', 0)),
                float(trade.get('reward', 0)),
                float(trade.get('rrRatio', 0)),
                str(trade.get('outcome', '')),
                str(trade.get('result', ''))
            ]
            sheet.append_row(row_data)
            
        return True
        
    except Exception as e:
        return False

@st.cache_resource
def init_connection():
    """Initialize connection to Google Sheets"""
    try:
        credentials = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]
        )
        return gspread.authorize(credentials)
    except Exception as e:
        st.error(f"Failed to connect to Google Sheets: {e}")
        return None

@st.cache_data(ttl=CACHE_TTL)
def load_trades_from_sheets():
    """Load trades from Google Sheets"""
    try:
        gc = init_connection()
        if gc is None:
            return load_fallback_data()
        
        spreadsheet = gc.open(SHEET_NAME)
        sheet = spreadsheet.worksheet(WORKSHEET_NAME)
        
        all_values = sheet.get_all_values()
        
        if not all_values or len(all_values) < 2:
            return load_fallback_data()
        
        headers = all_values[0]
        data_rows = all_values[1:]
        
        expected_headers = ['id', 'date', 'trader', 'instrument', 'entry', 'sl', 'target', 'risk', 'reward', 'rrRatio', 'outcome', 'result']
        
        if headers != expected_headers:
            st.warning(f"Headers mismatch. Found: {headers[:6]}... Expected: {expected_headers[:6]}...")
            if len(headers) < 12:
                st.error("Not enough columns in spreadsheet. Please run setup again.")
                return load_fallback_data()
        
        processed_records = []
        for i, row in enumerate(data_rows):
            if not any(str(cell).strip() for cell in row):
                continue
                
            try:
                while len(row) < 12:
                    row.append('')
                
                processed_record = {
                    'id': int(row[0]) if row[0] and str(row[0]).strip().isdigit() else i + 1,
                    'date': str(row[1]).strip() if row[1] else '',
                    'trader': str(row[2]).strip() if row[2] else '',
                    'instrument': str(row[3]).strip() if row[3] else '',
                    'entry': float(row[4]) if row[4] and str(row[4]).replace('.', '').replace('-', '').isdigit() else 0.0,
                    'sl': float(row[5]) if row[5] and str(row[5]).replace('.', '').replace('-', '').isdigit() else 0.0,
                    'target': float(row[6]) if row[6] and str(row[6]).replace('.', '').replace('-', '').isdigit() else 0.0,
                    'risk': float(row[7]) if row[7] and str(row[7]).replace('.', '').replace('-', '').isdigit() else 0.0,
                    'reward': float(row[8]) if row[8] and str(row[8]).replace('.', '').replace('-', '').isdigit() else 0.0,
                    'rrRatio': float(row[9]) if row[9] and str(row[9]).replace('.', '').replace('-', '').isdigit() else 0.0,
                    'outcome': str(row[10]).strip() if row[10] else '',
                    'result': str(row[11]).strip() if row[11] else ''
                }
                
                if processed_record['trader'] and processed_record['instrument']:
                    processed_records.append(processed_record)
                    
            except (ValueError, TypeError, IndexError) as e:
                st.warning(f"Skipped row {i+2}: {e}. Row data: {row[:6]}...")
                continue
        
        return processed_records if processed_records else load_fallback_data()
        
    except gspread.exceptions.SpreadsheetNotFound:
        st.warning(f"Spreadsheet '{SHEET_NAME}' not found. Using fallback data.")
        return load_fallback_data()
    except gspread.exceptions.WorksheetNotFound:
        st.warning(f"Worksheet '{WORKSHEET_NAME}' not found. Using fallback data.")
        return load_fallback_data()
    except gspread.exceptions.APIError as e:
        st.warning(f"Google Sheets API Error: {e}. Using fallback data.")
        return load_fallback_data()
    except Exception as e:
        st.warning(f"Error loading from Google Sheets: {e}. Using fallback data.")
        return load_fallback_data()

def save_trade_to_sheets(trade_data):
    """Save a single trade to Google Sheets"""
    try:
        gc = init_connection()
        if gc is None:
            return False
        
        spreadsheet = gc.open(SHEET_NAME)
        sheet = spreadsheet.worksheet(WORKSHEET_NAME)
        
        row_data = [
            str(trade_data['id']),
            str(trade_data['date']),
            str(trade_data['trader']),
            str(trade_data['instrument']),
            float(trade_data['entry']),
            float(trade_data['sl']),
            float(trade_data['target']),
            float(trade_data['risk']),
            float(trade_data['reward']),
            float(trade_data['rrRatio']),
            str(trade_data['outcome']),
            str(trade_data['result'])
        ]
        
        response = sheet.append_row(row_data, value_input_option='RAW')
        
        if response and 'updates' in response:
            return True
        else:
            time.sleep(1)
            all_records = sheet.get_all_records()
            for record in all_records:
                if str(record.get('id', '')) == str(trade_data['id']):
                    return True
            return False
        
    except gspread.exceptions.SpreadsheetNotFound:
        st.error("Spreadsheet not found. Please check the SHEET_NAME or create the spreadsheet.")
        return False
    except gspread.exceptions.WorksheetNotFound:
        st.error("Worksheet not found. Please check the WORKSHEET_NAME or create the worksheet.")
        return False
    except gspread.exceptions.APIError as e:
        st.error(f"Google Sheets API Error: {e}")
        return False
    except Exception as e:
        st.error(f"Unexpected error saving to Google Sheets: {e}")
        return False

def delete_trade_from_sheets(trade_id):
    """Delete a trade from Google Sheets"""
    try:
        gc = init_connection()
        if gc is None:
            return False
        
        spreadsheet = gc.open(SHEET_NAME)
        sheet = spreadsheet.worksheet(WORKSHEET_NAME)
        
        try:
            cell = sheet.find(str(trade_id))
            if cell and cell.col == 1:
                sheet.delete_rows(cell.row)
                return True
            else:
                st.error(f"Trade ID {trade_id} not found in spreadsheet.")
                return False
        except gspread.exceptions.CellNotFound:
            st.error(f"Trade ID {trade_id} not found in spreadsheet.")
            return False
        
    except gspread.exceptions.SpreadsheetNotFound:
        st.error("Spreadsheet not found. Please check the SHEET_NAME.")
        return False
    except gspread.exceptions.WorksheetNotFound:
        st.error("Worksheet not found. Please check the WORKSHEET_NAME.")
        return False
    except gspread.exceptions.APIError as e:
        st.error(f"Google Sheets API Error: {e}")
        return False
    except Exception as e:
        st.error(f"Unexpected error deleting from Google Sheets: {e}")
        return False

def setup_google_sheet():
    """Set up the Google Sheet with proper headers"""
    try:
        gc = init_connection()
        if gc is None:
            st.error("Cannot setup Google Sheets - connection failed.")
            return False
        
        st.info("🔍 Step 1: Checking for spreadsheet...")
        try:
            spreadsheet = gc.open(SHEET_NAME)
            st.success(f"✅ Found existing spreadsheet: '{SHEET_NAME}'")
        except gspread.SpreadsheetNotFound:
            st.info(f"📝 Creating new spreadsheet: '{SHEET_NAME}'")
            spreadsheet = gc.create(SHEET_NAME)
            st.success(f"✅ Created new spreadsheet: '{SHEET_NAME}'")
        
        st.info("🔍 Step 2: Checking for 'Trades' worksheet...")
        try:
            worksheet = spreadsheet.worksheet(WORKSHEET_NAME)
            st.success(f"✅ Found existing worksheet: '{WORKSHEET_NAME}'")
        except gspread.WorksheetNotFound:
            st.info(f"📝 Creating new worksheet: '{WORKSHEET_NAME}'")
            worksheet = spreadsheet.add_worksheet(title=WORKSHEET_NAME, rows=1000, cols=12)
            st.success(f"✅ Created new worksheet: '{WORKSHEET_NAME}'")
        
        st.info("🔍 Step 3: Checking worksheet headers...")
        try:
            headers = worksheet.row_values(1)
            expected_headers = ['id', 'date', 'trader', 'instrument', 'entry', 'sl', 'target', 'risk', 'reward', 'rrRatio', 'outcome', 'result']
            
            if not headers or len(headers) == 0 or headers != expected_headers:
                st.info("📝 Setting up column headers...")
                worksheet.clear()
                worksheet.append_row(expected_headers)
                st.success("✅ Added column headers to worksheet")
            else:
                st.success("✅ Headers already exist and are correct")
        except Exception as e:
            st.warning(f"⚠️ Could not check/set headers: {e}")
            try:
                headers = ['id', 'date', 'trader', 'instrument', 'entry', 'sl', 'target', 'risk', 'reward', 'rrRatio', 'outcome', 'result']
                worksheet.append_row(headers)
                st.success("✅ Added column headers")
            except:
                st.error("❌ Failed to add headers")
        
        spreadsheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet.id}"
        st.success("🎉 Setup Complete!")
        
        st.markdown("""
        ### ✅ Setup Summary:
        - **Spreadsheet**: Ready ✅
        - **Worksheet 'Trades'**: Ready ✅  
        - **Column Headers**: Ready ✅
        - **Ready for Trading Data**: Yes ✅
        """)
        
        st.info(f"🔗 **Your Google Sheet**: [Click here to open]({spreadsheet_url})")
        st.info("💡 **Next Steps**: You can now add trades and they will automatically sync to Google Sheets!")
        
        try:
            test_row = ['TEST', 'TEST', 'TEST', 'TEST', 0, 0, 0, 0, 0, 0, 'TEST', 'TEST']
            worksheet.append_row(test_row)
            worksheet.delete_rows(2, 2)
            st.success("✅ Connection test passed - ready to sync trades!")
        except Exception as e:
            st.warning(f"⚠️ Setup complete but sync test failed: {e}")
        
        return True
        
    except gspread.exceptions.APIError as e:
        st.error(f"❌ Google Sheets API Error during setup: {e}")
        st.info("💡 This might be due to API limits or permissions. Please wait a moment and try again.")
        return False
    except Exception as e:
        st.error(f"❌ Unexpected error during Google Sheets setup: {e}")
        return False

def load_fallback_data():
    """Load fallback data when Google Sheets is not available"""
    return [
        { 'id': 1, 'date': '2023-10-08', 'trader': 'Waithaka', 'instrument': 'XAUUSD', 'entry': 1820.50, 'sl': 1815.00, 'target': 1830.00, 'risk': 5.50, 'reward': 9.50, 'rrRatio': 1.73, 'outcome': 'Target Hit', 'result': 'Win' },
        { 'id': 2, 'date': '2023-10-07', 'trader': 'Wallace', 'instrument': 'USOIL', 'entry': 89.30, 'sl': 88.50, 'target': 91.00, 'risk': 0.80, 'reward': 1.70, 'rrRatio': 2.13, 'outcome': 'SL Hit', 'result': 'Loss' },
        { 'id': 3, 'date': '2023-10-06', 'trader': 'Max', 'instrument': 'BTCUSD', 'entry': 27450.00, 'sl': 27200.00, 'target': 27800.00, 'risk': 250.00, 'reward': 350.00, 'rrRatio': 1.40, 'outcome': 'Target Hit', 'result': 'Win' },
        { 'id': 4, 'date': '2023-10-05', 'trader': 'Waithaka', 'instrument': 'EURUSD', 'entry': 1.06250, 'sl': 1.06000, 'target': 1.06700, 'risk': 0.00250, 'reward': 0.00450, 'rrRatio': 1.80, 'outcome': 'Target Hit', 'result': 'Win' }
    ]

def force_refresh_data():
    """Force refresh data from Google Sheets and update session state"""
    try:
        st.cache_data.clear()
        if st.session_state.sheets_connected:
            fresh_data = load_trades_from_sheets()
            st.session_state.trades = fresh_data
            return True
        return False
    except Exception as e:
        st.error(f"Error refreshing data: {e}")
        return False

def auto_refresh_trades():
    """Auto-refresh trades data and check live prices"""
    if 'last_auto_refresh' not in st.session_state:
        st.session_state.last_auto_refresh = time.time()
        
    if 'last_price_check' not in st.session_state:
        st.session_state.last_price_check = time.time()
        
    if (st.session_state.sheets_connected and 
        st.session_state.get('auto_refresh_toggle', True) and 
        time.time() - st.session_state.last_auto_refresh > REAL_TIME_UPDATE_INTERVAL):
        
        st.session_state.last_auto_refresh = time.time()
        
        try:
            st.cache_data.clear()
            fresh_data = load_trades_from_sheets()
            
            if 'trades' not in st.session_state or st.session_state.trades != fresh_data:
                st.session_state.trades = fresh_data
                st.rerun()
        except Exception as e:
            pass
    
    if (time.time() - st.session_state.last_price_check > PRICE_UPDATE_INTERVAL and
        st.session_state.get('auto_refresh_toggle', True)):
        
        st.session_state.last_price_check = time.time()
        
        if 'trades' in st.session_state and st.session_state.trades:
            try:
                updated_trades, changes_made = update_trade_outcomes(st.session_state.trades)
                
                if changes_made:
                    st.session_state.trades = updated_trades
                    
                    if st.session_state.sheets_connected:
                        sync_trades_to_sheets(updated_trades)
                    
                    st.rerun()
                    
            except Exception as e:
                pass

# Page configuration
st.set_page_config(
    page_title="WarZone Forex Analytics",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Custom CSS
st.markdown("""
<style>
    .stApp {
        background-color: #f3f4f6;
    }
    
    .main-header {
        background-color: #334155;
        color: white;
        padding: 1rem 1.5rem;
        border-radius: 0;
        margin: -1rem -1rem 0 -1rem;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
    
    .header-nav {
        display: flex;
        justify-content: space-between;
        align-items: center;
    }
    
    .nav-buttons {
        display: flex;
        gap: 1rem;
    }
    
    .nav-btn {
        background: transparent;
        border: none;
        color: white;
        padding: 0.5rem 0.75rem;
        border-radius: 0.375rem;
        cursor: pointer;
        transition: background-color 0.2s;
        display: flex;
        align-items: center;
        gap: 0.5rem;
        font-size: 0.875rem;
    }
    
    .nav-btn:hover {
        background-color: #475569;
    }
    
    .trade-card {
        background: white;
        border-radius: 0.5rem;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        margin-bottom: 1.5rem;
    }
    
    .card-header {
        background-color: #334155;
        color: white;
        padding: 0.75rem 1rem;
        border-radius: 0.5rem 0.5rem 0 0;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }
    
    .card-body {
        padding: 1.5rem;
    }
    
    .connection-status {
        position: fixed;
        top: 80px;
        right: 20px;
        z-index: 1000;
        padding: 0.5rem 1rem;
        border-radius: 0.25rem;
        font-size: 0.875rem;
        font-weight: 500;
    }
    
    .status-connected {
        background-color: #dcfce7;
        color: #166534;
        border: 1px solid #bbf7d0;
    }
    
    .status-disconnected {
        background-color: #fee2e2;
        color: #dc2626;
        border: 1px solid #fecaca;
    }
    
    .rank-item {
        display: flex;
        align-items: center;
        margin-bottom: 1.5rem;
        padding: 1rem;
        background-color: #f8fafc;
        border-radius: 0.5rem;
        border-left: 4px solid #3b82f6;
    }
    
    .rank-number {
        width: 2rem;
        height: 2rem;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: bold;
        margin-right: 1rem;
        color: black;
    }
    
    .rank-1 { background-color: #fbbf24; }
    .rank-2 { background-color: #9ca3af; }
    .rank-3 { background-color: #fb923c; }
    
    .progress-bar {
        background-color: #e5e7eb;
        height: 8px;
        border-radius: 4px;
        overflow: hidden;
        margin: 0.5rem 0;
    }
    
    .progress-fill {
        background-color: #10b981;
        height: 100%;
        transition: width 0.3s ease;
    }
    
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    
    .stDeployButton {display: none;}
    
    .main-content {
        padding: 1.5rem;
        max-width: 90rem;
        margin: 0 auto;
    }
</style>
""", unsafe_allow_html=True)

# Initialize session state
if 'trades' not in st.session_state:
    st.session_state.trades = load_trades_from_sheets()
    
if 'sheets_connected' not in st.session_state:
    st.session_state.sheets_connected = init_connection() is not None

if st.session_state.sheets_connected:
    auto_refresh_trades()

if 'app_initialized' not in st.session_state:
    st.cache_data.clear()
    st.cache_resource.clear()
    st.session_state.app_initialized = True

# Connection status indicator
connection_class = "status-connected" if st.session_state.sheets_connected else "status-disconnected"
connection_text = "🟢 Google Sheets Connected" if st.session_state.sheets_connected else "🔴 Using Local Data"

st.markdown(f"""
<div class="connection-status {connection_class}">
    {connection_text}
</div>
""", unsafe_allow_html=True)

# Header
st.markdown("""
<div class="main-header">
    <div class="header-nav">
        <div style="display: flex; align-items: center;">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-right: 0.75rem;">
                <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"></path>
                <circle cx="12" cy="10" r="3"></circle>
            </svg>
            <h1 style="font-size: 1.25rem; font-weight: bold; margin: 0;">WarZone Forex Analytics</h1>
        </div>
        <div class="nav-buttons">
            <button class="nav-btn">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path>
                    <polyline points="9,22 9,12 15,12 15,22"></polyline>
                </svg>
                Dashboard
            </button>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

st.markdown('<div class="main-content">', unsafe_allow_html=True)

# Setup Google Sheets button
if not st.session_state.sheets_connected:
    st.warning("⚠️ Google Sheets not connected. Using local data only.")
    
    col_setup1, col_setup2, col_setup3 = st.columns([1, 2, 1])
    with col_setup2:
        if st.button("🔧 Setup Google Sheets Integration", use_container_width=True, type="primary"):
            with st.spinner("🔄 Setting up your Google Sheets integration..."):
                if setup_google_sheet():
                    st.session_state.sheets_connected = True
                    st.cache_data.clear()
                    st.balloons()
                    time.sleep(3)
                    st.rerun()
                else:
                    st.error("❌ Setup failed. Please check the error messages above and try again.")

# Live Price API Setup
if "alpha_vantage" not in st.secrets:
    st.session_state.live_prices_enabled = False
    st.warning("📈 Live price monitoring disabled - Add Alpha Vantage API key to enable automatic SL/TP detection")
    
    with st.expander("ℹ️ How to enable live price monitoring"):
        st.markdown("""
        To enable automatic stop loss and take profit detection:
        1. Get a free API key from [Alpha Vantage](https://www.alphavantage.co/support/#api-key)
        2. Add it to your Streamlit secrets as `alpha_vantage.api_key`
        3. Restart your app
        """)

# Main Dashboard Content
st.markdown("## 📊 Dashboard Overview")

# Create some sample content for the dashboard
if st.session_state.trades:
    df = pd.DataFrame(st.session_state.trades)
    
    # Key Metrics
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        total_trades = len(df)
        st.metric("Total Trades", total_trades)
    
    with col2:
        wins = len(df[df['result'] == 'Win']) if 'result' in df.columns else 0
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        st.metric("Win Rate", f"{win_rate:.1f}%")
    
    with col3:
        avg_rr = df['rrRatio'].mean() if 'rrRatio' in df.columns else 0
        st.metric("Avg R:R Ratio", f"{avg_rr:.2f}")
    
    with col4:
        active_trades = len(df[~df['outcome'].isin(['Target Hit', 'SL Hit'])]) if 'outcome' in df.columns else 0
        st.metric("Active Trades", active_trades)
    
    # Trader Performance
    st.markdown("### 🏆 Trader Performance")
    
    if 'trader' in df.columns and 'result' in df.columns:
        trader_stats = df.groupby('trader').agg({
            'result': ['count', lambda x: (x == 'Win').sum()],
            'rrRatio': 'mean'
        }).round(2)
        
        trader_stats.columns = ['Total Trades', 'Wins', 'Avg R:R']
        trader_stats['Win Rate %'] = (trader_stats['Wins'] / trader_stats['Total Trades'] * 100).round(1)
        trader_stats = trader_stats.sort_values('Win Rate %', ascending=False)
        
        # Create ranking display
        for i, (trader, stats) in enumerate(trader_stats.iterrows()):
            rank_class = f"rank-{i+1}" if i < 3 else ""
            
            st.markdown(f"""
            <div class="rank-item">
                <div class="rank-number {rank_class}">{i+1}</div>
                <div style="flex: 1;">
                    <div style="font-weight: bold; font-size: 1.1rem; margin-bottom: 0.25rem;">{trader}</div>
                    <div style="display: flex; gap: 2rem; font-size: 0.9rem; color: #6b7280;">
                        <span>Trades: {int(stats['Total Trades'])}</span>
                        <span>Wins: {int(stats['Wins'])}</span>
                        <span>Win Rate: {stats['Win Rate %']}%</span>
                        <span>Avg R:R: {stats['Avg R:R']}</span>
                    </div>
                    <div class="progress-bar">
                        <div class="progress-fill" style="width: {stats['Win Rate %']}%"></div>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)
    
    # Recent Trades
    st.markdown("### 📈 Recent Trades")
    
    # Display recent trades in a nice table format
    recent_trades = df.head(10) if len(df) > 0 else pd.DataFrame()
    
    if not recent_trades.empty:
        # Format the dataframe for better display
        display_df = recent_trades.copy()
        
        # Format numeric columns
        numeric_cols = ['entry', 'sl', 'target', 'risk', 'reward', 'rrRatio']
        for col in numeric_cols:
            if col in display_df.columns:
                display_df[col] = display_df[col].round(4)
        
        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "id": st.column_config.NumberColumn("ID", width="small"),
                "date": st.column_config.DateColumn("Date", width="medium"),
                "trader": st.column_config.TextColumn("Trader", width="medium"),
                "instrument": st.column_config.TextColumn("Instrument", width="medium"),
                "entry": st.column_config.NumberColumn("Entry", format="%.4f", width="small"),
                "sl": st.column_config.NumberColumn("SL", format="%.4f", width="small"),
                "target": st.column_config.NumberColumn("Target", format="%.4f", width="small"),
                "rrRatio": st.column_config.NumberColumn("R:R", format="%.2f", width="small"),
                "outcome": st.column_config.TextColumn("Outcome", width="medium"),
                "result": st.column_config.TextColumn("Result", width="small")
            }
        )
    
    # Charts
    st.markdown("### 📊 Analytics Charts")
    
    chart_col1, chart_col2 = st.columns(2)
    
    with chart_col1:
        st.markdown("#### Win/Loss Distribution")
        if 'result' in df.columns:
            result_counts = df['result'].value_counts()
            fig_pie = px.pie(
                values=result_counts.values, 
                names=result_counts.index,
                title="Trade Results",
                color_discrete_map={'Win': '#10b981', 'Loss': '#ef4444'}
            )
            fig_pie.update_layout(
                showlegend=True,
                height=300,
                margin=dict(t=50, b=0, l=0, r=0)
            )
            st.plotly_chart(fig_pie, use_container_width=True)
    
    with chart_col2:
        st.markdown("#### Instruments Traded")
        if 'instrument' in df.columns:
            instrument_counts = df['instrument'].value_counts().head(8)
            fig_bar = px.bar(
                x=instrument_counts.values,
                y=instrument_counts.index,
                orientation='h',
                title="Most Traded Instruments",
                labels={'x': 'Number of Trades', 'y': 'Instrument'}
            )
            fig_bar.update_layout(
                height=300,
                margin=dict(t=50, b=0, l=0, r=0),
                yaxis={'categoryorder': 'total ascending'}
            )
            st.plotly_chart(fig_bar, use_container_width=True)
    
    # Live Price Monitoring Section (if enabled)
    if st.session_state.get('live_prices_enabled', False):
        st.markdown("### 📡 Live Price Monitoring")
        
        active_trades_df = df[~df['outcome'].isin(['Target Hit', 'SL Hit'])] if 'outcome' in df.columns else pd.DataFrame()
        
        if not active_trades_df.empty:
            st.info(f"Monitoring {len(active_trades_df)} active trades for automatic SL/TP detection")
            
            # Show active trades with live prices
            for _, trade in active_trades_df.iterrows():
                instrument = trade['instrument']
                live_price = get_live_price(instrument)
                
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.write(f"**{instrument}**")
                with col2:
                    st.write(f"Entry: {trade['entry']}")
                with col3:
                    st.write(f"Current: {live_price if live_price else 'N/A'}")
                with col4:
                    if live_price:
                        pnl = live_price - trade['entry']
                        color = "green" if pnl >= 0 else "red"
                        st.write(f"P&L: <span style='color:{color}'>{pnl:+.4f}</span>", unsafe_allow_html=True)
        else:
            st.info("No active trades to monitor")

else:
    st.info("No trades data available. Please check your Google Sheets connection or add some trade data.")
    
    # Show sample data structure
    with st.expander("📋 Expected Data Structure"):
        st.markdown("""
        Your Google Sheet should have these columns:
        - **id**: Unique trade identifier
        - **date**: Trade date (YYYY-MM-DD)
        - **trader**: Trader name
        - **instrument**: Trading instrument (e.g., EURUSD, XAUUSD)
        - **entry**: Entry price
        - **sl**: Stop loss price
        - **target**: Target price
        - **risk**: Risk amount
        - **reward**: Reward amount  
        - **rrRatio**: Risk-reward ratio
        - **outcome**: Trade outcome (Target Hit, SL Hit, or empty for active)
        - **result**: Win/Loss (or empty for active trades)
        """)

# Refresh controls
st.markdown("---")
refresh_col1, refresh_col2, refresh_col3 = st.columns([1, 1, 1])

with refresh_col1:
    if st.button("🔄 Refresh Data", use_container_width=True):
        if force_refresh_data():
            st.success("✅ Data refreshed successfully!")
            st.rerun()
        else:
            st.warning("⚠️ Could not refresh data from Google Sheets")

with refresh_col2:
    auto_refresh = st.checkbox(
        "🔄 Auto Refresh", 
        value=st.session_state.get('auto_refresh_toggle', True),
        key='auto_refresh_toggle'
    )

with refresh_col3:
    if st.session_state.sheets_connected:
        st.success("✅ Connected to Google Sheets")
    else:
        st.error("❌ Google Sheets Disconnected")

# Add some spacing
st.markdown('<div style="margin-bottom: 2rem;"></div>', unsafe_allow_html=True)

# Close main content div
st.markdown('</div>', unsafe_allow_html=True)

# Footer
st.markdown("""
---
<div style="text-align: center; color: #6b7280; font-size: 0.875rem; padding: 1rem;">
    🎯 WarZone Forex Analytics | Real-time Trading Dashboard
</div>
""", unsafe_allow_html=True)
