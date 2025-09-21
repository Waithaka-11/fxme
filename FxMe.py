with sync_col4:
    # Auto-refresh toggle moved here
    auto_refresh_enabled = st.checkbox("⚡ Auto", value=True, help="Auto-refresh and price checks", key="auto_refresh_toggle")
    if not auto_refresh_enabled:
        st.session_state.last_auto_refresh = 0
        st.session_state.last_price_check = 0import streamlit as st
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
REAL_TIME_UPDATE_INTERVAL = 3  # Update every 3 seconds
CACHE_TTL = 5  # Cache data for only 5 seconds
PRICE_UPDATE_INTERVAL = 30  # Update prices every 30 seconds

# Live Price Data Functions
@st.cache_data(ttl=30)  # Cache prices for 30 seconds
def get_live_price(pair="EURUSD"):
    """Get live forex price from Alpha Vantage"""
    try:
        if "alpha_vantage" not in st.secrets:
            return None
            
        api_key = st.secrets["alpha_vantage"]["api_key"]
        
        # Handle different pair formats
        if len(pair) == 6:
            from_currency = pair[:3]
            to_currency = pair[3:]
        else:
            # Handle pairs like EUR/USD
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
        
        # Handle different response formats
        if "Realtime Currency Exchange Rate" in data:
            price = float(data["Realtime Currency Exchange Rate"]["5. Exchange Rate"])
            return price
        elif "Error Message" in data:
            st.warning(f"API Error for {pair}: {data['Error Message']}")
            return None
        else:
            return None
            
    except Exception as e:
        # Silent fail for live prices to avoid disrupting user experience
        return None

def get_multiple_prices(instruments):
    """Get prices for multiple instruments efficiently"""
    prices = {}
    for instrument in instruments:
        price = get_live_price(instrument)
        if price:
            prices[instrument] = price
        time.sleep(0.1)  # Small delay to respect rate limits
    return prices

def check_trade_outcome(trade, live_price):
    """Check if trade should hit SL or TP based on live price"""
    if not live_price or trade.get('outcome') in ['Target Hit', 'SL Hit']:
        return trade  # Already closed or no price data
    
    entry = trade.get('entry', 0)
    sl = trade.get('sl', 0)
    target = trade.get('target', 0)
    
    if not all([entry, sl, target]):
        return trade
    
    # Determine trade direction (long or short)
    is_long = target > entry
    
    if is_long:
        # Long trade: SL below entry, target above
        if live_price <= sl:
            trade['outcome'] = 'SL Hit'
            trade['result'] = 'Loss'
        elif live_price >= target:
            trade['outcome'] = 'Target Hit'
            trade['result'] = 'Win'
    else:
        # Short trade: SL above entry, target below
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
        return trades
    
    # Get unique instruments from open trades
    open_trades = [t for t in trades if t.get('outcome') not in ['Target Hit', 'SL Hit']]
    if not open_trades:
        return trades
    
    unique_instruments = list(set(t.get('instrument', '') for t in open_trades))
    
    # Get live prices for all instruments at once
    live_prices = get_multiple_prices(unique_instruments)
    
    # Check each trade
    updated_trades = []
    changes_made = False
    
    for trade in trades:
        instrument = trade.get('instrument', '')
        live_price = live_prices.get(instrument)
        
        original_outcome = trade.get('outcome', '')
        updated_trade = check_trade_outcome(trade.copy(), live_price)
        
        # Track if outcome changed
        if updated_trade.get('outcome') != original_outcome:
            changes_made = True
            
        updated_trades.append(updated_trade)
    
    return updated_trades, changes_made

# Initialize Google Sheets connection
@st.cache_resource
def init_connection():
    """Initialize connection to Google Sheets"""
    try:
        # Try to get credentials from Streamlit secrets
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

@st.cache_data(ttl=CACHE_TTL)  # Much shorter cache - 5 seconds only
def load_trades_from_sheets():
    """Load trades from Google Sheets with minimal caching for real-time updates"""
    try:
        gc = init_connection()
        if gc is None:
            return load_fallback_data()
        
        # Open the spreadsheet
        spreadsheet = gc.open(SHEET_NAME)
        sheet = spreadsheet.worksheet(WORKSHEET_NAME)
        
        # Get all values as a list of lists first
        all_values = sheet.get_all_values()
        
        if not all_values or len(all_values) < 2:  # No data or just headers
            return load_fallback_data()
        
        # First row should be headers
        headers = all_values[0]
        data_rows = all_values[1:]
        
        # Expected headers
        expected_headers = ['id', 'date', 'trader', 'instrument', 'entry', 'sl', 'target', 'risk', 'reward', 'rrRatio', 'outcome', 'result']
        
        # If headers don't match expected, try to fix or skip
        if headers != expected_headers:
            st.warning(f"Headers mismatch. Found: {headers[:6]}... Expected: {expected_headers[:6]}...")
            # Try to use the data anyway if it has the right number of columns
            if len(headers) < 12:
                st.error("Not enough columns in spreadsheet. Please run setup again.")
                return load_fallback_data()
        
        # Process each data row
        processed_records = []
        for i, row in enumerate(data_rows):
            # Skip empty rows
            if not any(str(cell).strip() for cell in row):
                continue
                
            try:
                # Ensure row has enough columns
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
                
                # Only add records that have meaningful data
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
        
        # Open the spreadsheet and worksheet
        spreadsheet = gc.open(SHEET_NAME)
        sheet = spreadsheet.worksheet(WORKSHEET_NAME)
        
        # Prepare row data in the same order as headers
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
        
        # Add the row and verify it was added
        response = sheet.append_row(row_data, value_input_option='RAW')
        
        # Check if the response indicates success
        if response and 'updates' in response:
            return True
        else:
            # Even if response format is different, try to verify the row was added
            time.sleep(1)  # Brief delay to allow for propagation
            all_records = sheet.get_all_records()
            # Check if our trade was added (look for matching ID)
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
        
        # Find the row with the trade ID
        try:
            cell = sheet.find(str(trade_id))
            if cell and cell.col == 1:  # Make sure it's in the ID column
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
    """Set up the Google Sheet with proper headers if it doesn't exist"""
    try:
        gc = init_connection()
        if gc is None:
            st.error("Cannot setup Google Sheets - connection failed.")
            return False
        
        # Step 1: Handle the spreadsheet
        st.info("🔍 Step 1: Checking for spreadsheet...")
        try:
            spreadsheet = gc.open(SHEET_NAME)
            st.success(f"✅ Found existing spreadsheet: '{SHEET_NAME}'")
        except gspread.SpreadsheetNotFound:
            # Create new spreadsheet
            st.info(f"📝 Creating new spreadsheet: '{SHEET_NAME}'")
            spreadsheet = gc.create(SHEET_NAME)
            st.success(f"✅ Created new spreadsheet: '{SHEET_NAME}'")
        
        # Step 2: Handle the worksheet
        st.info("🔍 Step 2: Checking for 'Trades' worksheet...")
        try:
            worksheet = spreadsheet.worksheet(WORKSHEET_NAME)
            st.success(f"✅ Found existing worksheet: '{WORKSHEET_NAME}'")
        except gspread.WorksheetNotFound:
            # Create new worksheet named "Trades"
            st.info(f"📝 Creating new worksheet: '{WORKSHEET_NAME}'")
            worksheet = spreadsheet.add_worksheet(title=WORKSHEET_NAME, rows=1000, cols=12)
            st.success(f"✅ Created new worksheet: '{WORKSHEET_NAME}'")
        
        # Step 3: Setup headers
        st.info("🔍 Step 3: Checking worksheet headers...")
        try:
            headers = worksheet.row_values(1)
            expected_headers = ['id', 'date', 'trader', 'instrument', 'entry', 'sl', 'target', 'risk', 'reward', 'rrRatio', 'outcome', 'result']
            
            if not headers or len(headers) == 0 or headers != expected_headers:
                # Clear first row and set proper headers
                st.info("📝 Setting up column headers...")
                worksheet.clear()  # Clear the worksheet first
                worksheet.append_row(expected_headers)
                st.success("✅ Added column headers to worksheet")
            else:
                st.success("✅ Headers already exist and are correct")
        except Exception as e:
            st.warning(f"⚠️ Could not check/set headers: {e}")
            # Try to add headers anyway
            try:
                headers = ['id', 'date', 'trader', 'instrument', 'entry', 'sl', 'target', 'risk', 'reward', 'rrRatio', 'outcome', 'result']
                worksheet.append_row(headers)
                st.success("✅ Added column headers")
            except:
                st.error("❌ Failed to add headers")
        
        # Step 4: Provide user information
        spreadsheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet.id}"
        st.success("🎉 Setup Complete!")
        
        # Show final summary
        st.markdown("""
        ### ✅ Setup Summary:
        - **Spreadsheet**: Ready ✅
        - **Worksheet 'Trades'**: Ready ✅  
        - **Column Headers**: Ready ✅
        - **Ready for Trading Data**: Yes ✅
        """)
        
        st.info(f"🔗 **Your Google Sheet**: [Click here to open]({spreadsheet_url})")
        st.info("💡 **Next Steps**: You can now add trades and they will automatically sync to Google Sheets!")
        
        # Test the setup by trying to add a sample row (then remove it)
        try:
            # Add a test row to verify everything works
            test_row = ['TEST', 'TEST', 'TEST', 'TEST', 0, 0, 0, 0, 0, 0, 'TEST', 'TEST']
            worksheet.append_row(test_row)
            # Immediately delete the test row
            worksheet.delete_rows(2, 2)  # Delete row 2 (the test row)
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

def sync_trades_to_sheets(trades):
    """Sync all trades to Google Sheets (for bulk updates like price checks)"""
    try:
        gc = init_connection()
        if gc is None:
            return False
        
        spreadsheet = gc.open(SHEET_NAME)
        sheet = spreadsheet.worksheet(WORKSHEET_NAME)
        
        # Clear existing data (except headers)
        sheet.clear()
        
        # Add headers
        headers = ['id', 'date', 'trader', 'instrument', 'entry', 'sl', 'target', 'risk', 'reward', 'rrRatio', 'outcome', 'result']
        sheet.append_row(headers)
        
        # Add all trades
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
    """Auto-refresh trades data and check live prices for real-time updates"""
    if 'last_auto_refresh' not in st.session_state:
        st.session_state.last_auto_refresh = time.time()
        
    if 'last_price_check' not in st.session_state:
        st.session_state.last_price_check = time.time()
        
    # Auto-refresh data every few seconds if Google Sheets is connected and toggle is enabled
    if (st.session_state.sheets_connected and 
        st.session_state.get('auto_refresh_toggle', True) and 
        time.time() - st.session_state.last_auto_refresh > REAL_TIME_UPDATE_INTERVAL):
        
        st.session_state.last_auto_refresh = time.time()
        
        # Get fresh data from Google Sheets
        try:
            st.cache_data.clear()
            fresh_data = load_trades_from_sheets()
            
            # Only update and rerun if data actually changed
            if 'trades' not in st.session_state or st.session_state.trades != fresh_data:
                st.session_state.trades = fresh_data
                st.rerun()
        except Exception as e:
            # Silently handle refresh errors to avoid disrupting user experience
            pass
    
    # Check live prices less frequently to respect API limits
    if (time.time() - st.session_state.last_price_check > PRICE_UPDATE_INTERVAL and
        st.session_state.get('auto_refresh_toggle', True)):
        
        st.session_state.last_price_check = time.time()
        
        # Update trade outcomes based on live prices
        if 'trades' in st.session_state and st.session_state.trades:
            try:
                updated_trades, changes_made = update_trade_outcomes(st.session_state.trades)
                
                if changes_made:
                    st.session_state.trades = updated_trades
                    
                    # Sync changes to Google Sheets
                    if st.session_state.sheets_connected:
                        sync_trades_to_sheets(updated_trades)
                    
                    st.rerun()
                    
            except Exception as e:
                # Silent fail for price checks
                pass

# Page configuration
st.set_page_config(
    page_title="Forex Trading Analytics",
    page_icon="📈",
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

# Initialize session state for trades with real-time data
if 'trades' not in st.session_state:
    st.session_state.trades = load_trades_from_sheets()
    
if 'sheets_connected' not in st.session_state:
    st.session_state.sheets_connected = init_connection() is not None

# Enable real-time updates when connected to Google Sheets
if st.session_state.sheets_connected:
    auto_refresh_trades()

# Force clear all caches on first load to ensure updates are visible
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
                <line x1="12" y1="20" x2="12" y2="10"></line>
                <line x1="18" y1="20" x2="18" y2="4"></line>
                <line x1="6" y1="20" x2="6" y2="16"></line>
            </svg>
            <h1 style="font-size: 1.25rem; font-weight: bold; margin: 0;">Forex Trading Analytics</h1>
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

# Setup Google Sheets button (for first-time setup)
if not st.session_state.sheets_connected:
    st.warning("⚠️ Google Sheets not connected. Using local data only.")
    
    st.markdown("""
    <div style="background-color: #fef3c7; border: 1px solid #f59e0b; border-radius: 0.5rem; padding: 1rem; margin: 1rem 0;">
        <h4 style="color: #92400e; margin: 0 0 0.5rem 0;">🚀 Ready to Enable Cloud Sync?</h4>
        <p style="color: #92400e; margin: 0; font-size: 0.875rem;">
            Click the button below to automatically create your "Trades" worksheet and enable real-time synchronization across all your devices!
        </p>
    </div>
    """, unsafe_allow_html=True)
    
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
else:
    # Show connection info when connected
    st.markdown("""
    <div style="background-color: #dcfce7; border: 1px solid #16a34a; border-radius: 0.5rem; padding: 1rem; margin: 1rem 0;">
        <div style="display: flex; align-items: center;">
            <span style="font-size: 1.5rem; margin-right: 0.75rem;">✅</span>
            <div>
                <h4 style="color: #15803d; margin: 0 0 0.25rem 0;">Google Sheets Connected!</h4>
                <p style="color: #15803d; margin: 0; font-size: 0.875rem;">
                    Your trades are now syncing automatically across all devices. Add a trade below to test it!
                </p>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    col_info1, col_info2, col_info3 = st.columns([1, 2, 1])
    with col_info2:        
        # Add a "Fix Spreadsheet" button
        if st.button("🛠️ Fix Spreadsheet Headers", use_container_width=True, help="Reset headers and clean up data"):
            try:
                gc = init_connection()
                if gc:
                    spreadsheet = gc.open(SHEET_NAME)
                    worksheet = spreadsheet.worksheet(WORKSHEET_NAME)
                    
                    # Get all current data
                    all_data = worksheet.get_all_values()
                    
                    st.info("🔄 Fixing spreadsheet headers and data...")
                    
                    # Clear the worksheet
                    worksheet.clear()
                    
                    # Add proper headers
                    headers = ['id', 'date', 'trader', 'instrument', 'entry', 'sl', 'target', 'risk', 'reward', 'rrRatio', 'outcome', 'result']
                    worksheet.append_row(headers)
                    
                    # If there was existing data, try to preserve valid trades
                    if len(all_data) > 1:
                        valid_trades = []
                        for i, row in enumerate(all_data[1:], 1):  # Skip first row (old headers)
                            if len(row) >= 12 and row[2] and row[3]:  # Has trader and instrument
                                valid_trades.append(row[:12])  # Take first 12 columns
                        
                        if valid_trades:
                            for trade_row in valid_trades:
                                worksheet.append_row(trade_row)
                            st.success(f"✅ Fixed headers and preserved {len(valid_trades)} valid trades!")
                        else:
                            st.success("✅ Fixed headers! No valid existing data to preserve.")
                    else:
                        st.success("✅ Fixed headers! Spreadsheet is now ready for new trades.")
                    
                    # Force refresh
                    st.cache_data.clear()
                    st.session_state.trades = load_trades_from_sheets()
                    st.rerun()
                    
            except Exception as e:
                st.error(f"Error fixing spreadsheet: {e}")
            try:
                gc = init_connection()
                if gc:
                    try:
                        spreadsheet = gc.open(SHEET_NAME)
                        st.success(f"✅ Successfully connected to spreadsheet: '{SHEET_NAME}'")
                        
                        worksheets = spreadsheet.worksheets()
                        worksheet_names = [ws.title for ws in worksheets]
                        st.info(f"📋 Available worksheets: {', '.join(worksheet_names)}")
                        
                        try:
                            worksheet = spreadsheet.worksheet(WORKSHEET_NAME)
                            st.success(f"✅ Successfully accessed worksheet: '{WORKSHEET_NAME}'")
                            
                            try:
                                records = worksheet.get_all_records()
                                record_count = len(records)
                                st.success(f"✅ Found {record_count} trades in the worksheet")
                                
                                if records:
                                    headers = list(records[0].keys()) if records else worksheet.row_values(1)
                                    st.info(f"📊 Column headers: {', '.join(headers[:6])}{'...' if len(headers) > 6 else ''}")
                                else:
                                    headers = worksheet.row_values(1)
                                    if headers:
                                        st.info(f"📊 Column headers: {', '.join(headers[:6])}{'...' if len(headers) > 6 else ''}")
                                    else:
                                        st.warning("⚠️ No headers found - worksheet may be empty")
                                
                            except Exception as e:
                                st.warning(f"⚠️ Could not read worksheet data: {str(e)}")
                                
                        except gspread.WorksheetNotFound:
                            st.error(f"❌ Worksheet '{WORKSHEET_NAME}' not found!")
                            st.info(f"💡 Available worksheets: {', '.join(worksheet_names)}")
                            st.info("💡 Click 'Setup Google Sheets Integration' to create the missing worksheet.")
                        except Exception as ws_error:
                            st.error(f"❌ Error accessing worksheet: {str(ws_error)}")
                        
                        spreadsheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet.id}"
                        st.markdown(f"🔗 **[Open your Google Sheet in browser]({spreadsheet_url})**")
                        
                    except gspread.SpreadsheetNotFound:
                        st.error(f"❌ Spreadsheet '{SHEET_NAME}' not found!")
                        st.info("💡 Click 'Setup Google Sheets Integration' to create the spreadsheet.")
                    except Exception as sheet_error:
                        st.error(f"❌ Error accessing spreadsheet: {str(sheet_error)}")
                else:
                    st.error("❌ Google Sheets connection failed!")
                    st.info("💡 Check your credentials in secrets.toml")
                    
            except Exception as e:
                error_msg = str(e)
                if "Response [200]" in error_msg:
                    st.success("✅ Connection successful! (Response 200 indicates success)")
                    st.info("💡 The connection is working - you can try adding trades now!")
                else:
                    st.error(f"❌ Connection test error: {error_msg}")
                st.info("💡 If you see 'Response [200]', that actually means success!")

# WarZone Trade Entry
st.markdown("""
<div class="trade-card">
    <div class="card-header">
        <div style="display: flex; align-items: center;">
            <div style="background-color: #dc2626; border-radius: 50%; padding: 0.25rem; margin-right: 0.75rem;">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"></path>
                    <circle cx="12" cy="10" r="3"></circle>
                </svg>
            </div>
            <span style="font-weight: 600; font-size: 1.1rem;">WarZone</span>
        </div>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="6,9 12,15 18,9"></polyline>
        </svg>
    </div>
    <div class="card-body">
""", unsafe_allow_html=True)

# First Row of Form
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.markdown('<div class="form-group"><label>Trader</label></div>', unsafe_allow_html=True)
    trader = st.selectbox("", ["Select Trader", "Waithaka", "Wallace", "Max"], key="trader_select", label_visibility="collapsed")

with col2:
    st.markdown('<div class="form-group"><label>Instrument</label></div>', unsafe_allow_html=True)
    instrument_pairs = ['Select Instrument', 'EURUSD', 'GBPUSD', 'AUDUSD', 'USDJPY', 'USDCAD', 'NZDUSD', 'USDCHF', 'XAUUSD', 'USOIL', 'BTCUSD', 'USTECH']
    instrument = st.selectbox("", instrument_pairs, key="instrument_select", label_visibility="collapsed")

with col3:
    st.markdown('<div class="form-group"><label>Date</label></div>', unsafe_allow_html=True)
    trade_date = st.date_input("", value=date.today(), key="date_input", label_visibility="collapsed")

with col4:
    st.markdown('<div class="form-group"><label>Outcome</label></div>', unsafe_allow_html=True)
    # Auto-detect open trades vs closed trades
    if st.session_state.get('live_prices_enabled', False):
        outcome = st.selectbox("", ["Open Trade", "Target Hit", "SL Hit"], key="outcome_select", label_visibility="collapsed")
    else:
        outcome = st.selectbox("", ["Select Outcome", "Target Hit", "SL Hit"], key="outcome_select", label_visibility="collapsed")

# Second Row of Form
col5, col6, col7, col8 = st.columns(4)

with col5:
    st.markdown('<div class="form-group"><label>Entry Price</label></div>', unsafe_allow_html=True)
    entry = st.number_input("", value=0.0, step=0.01, format="%.4f", key="entry_input", label_visibility="collapsed")

with col6:
    st.markdown('<div class="form-group"><label>Stop Loss (SL)</label></div>', unsafe_allow_html=True)
    sl = st.number_input("", value=0.0, step=0.01, format="%.4f", key="sl_input", label_visibility="collapsed")

with col7:
    st.markdown('<div class="form-group"><label>Target Price</label></div>', unsafe_allow_html=True)
    target = st.number_input("", value=0.0, step=0.01, format="%.4f", key="target_input", label_visibility="collapsed")

with col8:
    st.markdown('<div style="padding-top: 1.5rem;"></div>', unsafe_allow_html=True)
    if st.button("➕ Add Trade", type="primary", use_container_width=True):
        if trader != "Select Trader" and instrument != "Select Instrument" and entry and sl and target:
            risk = abs(entry - sl)
            reward = abs(target - entry)
            rr_ratio = reward / risk if risk != 0 else 0
            
            # Set default outcome and result for new trades
            if st.session_state.get('live_prices_enabled', False):
                # If live prices enabled, start as "Open Trade"
                if outcome == "Open Trade":
                    trade_outcome = "Open Trade"
                    result = "Pending"
                else:
                    trade_outcome = outcome
                    result = "Win" if outcome == "Target Hit" else "Loss"
            else:
                # Manual selection
                if outcome in ["Target Hit", "SL Hit"]:
                    trade_outcome = outcome
                    result = "Win" if outcome == "Target Hit" else "Loss"
                else:
                    st.error("❌ Please select an outcome for the trade.")
                    st.stop()
            
            # Generate new ID
            max_id = max([trade.get('id', 0) for trade in st.session_state.trades], default=0)
            
            new_trade = {
                'id': max_id + 1,
                'date': trade_date.strftime('%Y-%m-%d'),
                'trader': trader,
                'instrument': instrument,
                'entry': entry,
                'sl': sl,
                'target': target,
                'risk': round(risk, 4),
                'reward': round(reward, 4),
                'rrRatio': round(rr_ratio, 2),
                'outcome': trade_outcome,
                'result': result
            }
            
            # Add to session state and sync
            st.session_state.trades.append(new_trade)
            
            # Show what type of trade was added
            if trade_outcome == "Open Trade":
                st.success("✅ Open trade added - will monitor automatically!")
            else:
                st.success(f"✅ Trade added with outcome: {trade_outcome}!")
            
            # Sync to Google Sheets if connected
            if st.session_state.sheets_connected:
                try:
                    success = save_trade_to_sheets(new_trade)
                    if success:
                        force_refresh_data()
                except Exception as e:
                    pass  # Silent sync
            
            # Force immediate UI refresh
            time.sleep(0.5)
            st.rerun()
        else:
            st.error("❌ Please fill in all required fields.")
            # Show which fields are missing
            missing_fields = []
            if trader == "Select Trader":
                missing_fields.append("Trader")
            if instrument == "Select Instrument":
                missing_fields.append("Instrument")
            if not entry:
                missing_fields.append("Entry Price")
            if not sl:
                missing_fields.append("Stop Loss")
            if not target:
                missing_fields.append("Target Price")
            
            if missing_fields:
                st.error(f"Missing: {', '.join(missing_fields)}")

st.markdown('</div></div>', unsafe_allow_html=True)

# Debug section - show current state
st.markdown("---")
st.markdown("### 🔍 Debug Information")

debug_col1, debug_col2, debug_col3 = st.columns(3)

with debug_col1:
    st.markdown(f"""
    **Connection Status:**
    - Google Sheets: {'✅ Connected' if st.session_state.sheets_connected else '❌ Not Connected'}
    - Total Trades: {len(st.session_state.trades) if st.session_state.trades else 0}
    """)

with debug_col2:
    st.markdown(f"""
    **Session State:**
    - Trades exist: {'✅ Yes' if 'trades' in st.session_state else '❌ No'}
    - Auto-refresh: {'✅ Enabled' if st.session_state.get('auto_refresh_toggle', True) else '❌ Disabled'}
    """)

with debug_col3:
    if st.button("🔄 Force Refresh Session", help="Clear cache and reload data"):
        st.cache_data.clear()
        st.session_state.trades = load_trades_from_sheets()
        st.success("Session refreshed!")
        st.rerun()

# Show current trades in debug format
if st.session_state.trades:
    with st.expander("📊 Current Trades Data (Debug)", expanded=False):
        for i, trade in enumerate(st.session_state.trades):
            st.write(f"**Trade {i+1}:**", trade)
sync_col1, sync_col2, sync_col3, sync_col4 = st.columns([2, 1, 1, 6])

with sync_col1:
    if st.session_state.sheets_connected:
        current_time = datetime.now().strftime('%H:%M:%S')
        st.markdown(f"<small>🔄 <span style='color: #10b981;'>Live sync active</span> • Last check: {current_time}</small>", unsafe_allow_html=True)
    else:
        st.markdown("<small>⚠️ <span style='color: #f59e0b;'>Local mode only</span></small>", unsafe_allow_html=True)

with sync_col2:
    if st.button("🔄", help="Force refresh data from Google Sheets", key="manual_refresh"):
        with st.spinner("Refreshing..."):
            if force_refresh_data():
                st.success("Data refreshed!")
            else:
                st.warning("Refresh completed (local data)")
            time.sleep(1)
            st.rerun()

with sync_col3:
    auto_refresh_enabled = st.checkbox("⚡", value=True, help="Enable/disable auto-refresh (every 3 seconds)", key="auto_refresh_toggle")
    if not auto_refresh_enabled:
        st.session_state.last_auto_refresh = 0

# Main Content Grid
col_main, col_sidebar = st.columns([2, 1])

with col_main:
    # Calculate dynamic rankings based on current data
    if st.session_state.trades and len(st.session_state.trades) > 0:
        trader_stats = {}
        for trade in st.session_state.trades:
            trader = trade.get('trader', '')
            if trader and trader != '':
                if trader not in trader_stats:
                    trader_stats[trader] = {'wins': 0, 'total': 0}
                trader_stats[trader]['total'] += 1
                if trade.get('result', '') == 'Win':
                    trader_stats[trader]['wins'] += 1
        
        # Calculate win rates and sort
        rankings = []
        for trader, stats in trader_stats.items():
            if stats['total'] > 0:
                win_rate = (stats['wins'] / stats['total']) * 100
                rankings.append({
                    'name': trader,
                    'win_rate': round(win_rate, 1),
                    'wins': stats['wins'],
                    'losses': stats['total'] - stats['wins'],
                    'total': stats['total']
                })
        
        rankings.sort(key=lambda x: x['win_rate'], reverse=True)
        for i, ranking in enumerate(rankings):
            ranking['rank'] = i + 1
    else:
        rankings = []

    # Trader Performance Rankings
    st.markdown("""
    <div class="trade-card">
        <div class="card-header">
            <h3 style="font-weight: 600; margin: 0;">Trader Performance Rankings</h3>
        </div>
        <div class="card-body">
    """, unsafe_allow_html=True)
    
    if rankings and len(rankings) > 0:
        for ranking in rankings:
            rank_class = f"rank-{min(ranking['rank'], 3)}"
            st.markdown(f"""
            <div class="rank-item">
                <div class="rank-number {rank_class}">{ranking['rank']}</div>
                <div style="flex: 1;">
                    <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 0.25rem;">
                        <span style="font-weight: 600; color: #1f2937;">{ranking['name']}</span>
                        <span style="font-size: 0.875rem; font-weight: 500;">Win Rate: {ranking['win_rate']}%</span>
                    </div>
                    <div style="font-size: 0.75rem; color: #6b7280; margin-bottom: 0.5rem;">Total Trades: {ranking['total']}</div>
                    <div class="progress-bar">
                        <div class="progress-fill" style="width: {ranking['win_rate']}%;"></div>
                    </div>
                    <div style="font-size: 0.75rem; color: #6b7280;">Wins: {ranking['wins']} | Losses: {ranking['losses']}</div>
                </div>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.info("No trades available for rankings. Add some trades to see performance metrics!")
    
    st.markdown('</div></div>', unsafe_allow_html=True)
    
    # Trading History
    st.markdown("""
    <div style="background: white; border-radius: 0.5rem; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 1.5rem;">
        <div style="background-color: #334155; color: white; padding: 0.75rem 1rem; border-radius: 0.5rem 0.5rem 0 0; display: flex; align-items: center;">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 0.5rem;">
                <path d="M9 3H5a2 2 0 0 0-2 2v4m6-6h10a2 2 0 0 1 2 2v4M9 3v18m0 0h10a2 2 0 0 0 2-2V9M9 21H5a2 2 0 0 1-2-2V9m0 0h4"></path>
            </svg>
            <h3 style="font-weight: 600; margin: 0; font-size: 1rem;">Trading History</h3>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # Display trades with individual delete buttons
    if not st.session_state.trades or len(st.session_state.trades) == 0:
        st.info("No trades recorded yet. Add a trade using the form above.")
    else:
        # Header row
        header_cols = st.columns([1.5, 1.5, 1.5, 1.5, 1.5, 1.5, 1.2, 1.2, 1.2, 1.8, 1.5, 1])
        headers = ['Date', 'Trader', 'Instrument', 'Entry', 'SL', 'Target', 'Risk', 'Reward', 'R/R Ratio', 'Outcome', 'Result', 'Actions']
        
        for i, header in enumerate(headers):
            with header_cols[i]:
                st.markdown(f'<div style="font-weight: bold; color: #000000; padding: 0.5rem 0; border-bottom: 2px solid #e5e7eb; font-size: 0.875rem;">{header}</div>', unsafe_allow_html=True)
        
        # Create columns for each trade row (sort by ID descending to show newest first)
        sorted_trades = sorted(st.session_state.trades, key=lambda x: x.get('id', 0), reverse=True)
        for i, trade in enumerate(sorted_trades):
            cols = st.columns([1.5, 1.5, 1.5, 1.5, 1.5, 1.5, 1.2, 1.2, 1.2, 1.8, 1.5, 1])
            
            with cols[0]:
                st.markdown(f'<div style="color: #000000; padding: 0.25rem 0; font-size: 0.875rem;">{trade.get("date", "N/A")}</div>', unsafe_allow_html=True)
            with cols[1]:
                st.markdown(f'<div style="color: #000000; padding: 0.25rem 0; font-size: 0.875rem;">{trade.get("trader", "N/A")}</div>', unsafe_allow_html=True)
            with cols[2]:
                st.markdown(f'<div style="color: #000000; padding: 0.25rem 0; font-size: 0.875rem;">{trade.get("instrument", "N/A")}</div>', unsafe_allow_html=True)
            with cols[3]:
                st.markdown(f'<div style="color: #000000; padding: 0.25rem 0; font-size: 0.875rem;">{trade.get("entry", 0)}</div>', unsafe_allow_html=True)
            with cols[4]:
                st.markdown(f'<div style="color: #000000; padding: 0.25rem 0; font-size: 0.875rem;">{trade.get("sl", 0)}</div>', unsafe_allow_html=True)
            with cols[5]:
                st.markdown(f'<div style="color: #000000; padding: 0.25rem 0; font-size: 0.875rem;">{trade.get("target", 0)}</div>', unsafe_allow_html=True)
            with cols[6]:
                risk_val = trade.get("risk", 0)
                st.markdown(f'<div style="color: #000000; padding: 0.25rem 0; font-size: 0.875rem;">{risk_val:.4f}</div>', unsafe_allow_html=True)
            with cols[7]:
                reward_val = trade.get("reward", 0)
                st.markdown(f'<div style="color: #000000; padding: 0.25rem 0; font-size: 0.875rem;">{reward_val:.4f}</div>', unsafe_allow_html=True)
            with cols[8]:
                rr_val = trade.get("rrRatio", 0)
                st.markdown(f'<div style="color: #000000; padding: 0.25rem 0; font-size: 0.875rem;">{rr_val}</div>', unsafe_allow_html=True)
            with cols[9]:
                outcome_text = trade.get("outcome", "N/A")
                # Highlight open trades differently
                if outcome_text in ["Open Trade", "Pending"]:
                    st.markdown(f'<div style="color: #f59e0b; padding: 0.25rem 0; font-size: 0.875rem; font-weight: 600;">⏳ {outcome_text}</div>', unsafe_allow_html=True)
                else:
                    st.markdown(f'<div style="color: #000000; padding: 0.25rem 0; font-size: 0.875rem;">{outcome_text}</div>', unsafe_allow_html=True)
            with cols[10]:
                result = trade.get('result', 'Unknown')
                if result == 'Win':
                    st.markdown('<span style="background-color: #dcfce7; color: #166534; padding: 4px 8px; border-radius: 12px; font-weight: 500; font-size: 0.75rem;">Win</span>', unsafe_allow_html=True)
                elif result == 'Loss':
                    st.markdown('<span style="background-color: #fee2e2; color: #dc2626; padding: 4px 8px; border-radius: 12px; font-weight: 500; font-size: 0.75rem;">Loss</span>', unsafe_allow_html=True)
                elif result in ['Pending', 'Open']:
                    st.markdown('<span style="background-color: #fef3c7; color: #92400e; padding: 4px 8px; border-radius: 12px; font-weight: 500; font-size: 0.75rem;">⏳ Open</span>', unsafe_allow_html=True)
                else:
                    st.markdown('<span style="background-color: #f3f4f6; color: #6b7280; padding: 4px 8px; border-radius: 12px; font-weight: 500; font-size: 0.75rem;">Unknown</span>', unsafe_allow_html=True)
            with cols[11]:
                # Only show delete button for valid trades
                trade_id = trade.get('id')
                if trade_id and trade.get('trader') and trade.get('instrument'):
                    if st.button("🗑️", key=f"delete_{trade_id}_{i}", help="Delete this trade", type="secondary"):
                        # Delete from Google Sheets first
                        if st.session_state.sheets_connected:
                            try:
                                success = delete_trade_from_sheets(trade_id)
                                # Remove from session state
                                st.session_state.trades = [t for t in st.session_state.trades if t.get('id') != trade_id]
                                
                                if success:
                                    st.success("✅ Trade deleted and synced!")
                                    force_refresh_data()
                                else:
                                    st.warning("⚠️ Trade deleted locally, but Google Sheets sync may have failed")
                                    
                                st.rerun()
                                
                            except Exception as e:
                                st.session_state.trades = [t for t in st.session_state.trades if t.get('id') != trade_id]
                                error_msg = str(e)
                                if "Response [200]" in error_msg:
                                    st.success("✅ Trade deleted and synced successfully!")
                                    force_refresh_data()
                                else:
                                    st.warning(f"⚠️ Trade deleted locally, sync error: {error_msg}")
                                st.rerun()
                        else:
                            st.session_state.trades = [t for t in st.session_state.trades if t.get('id') != trade_id]
                            st.warning("⚠️ Trade deleted locally only (Google Sheets not connected)")
                            st.rerun()
                else:
                    # For invalid/empty trades, show a cleanup button
                    if st.button("🧹", key=f"cleanup_{i}", help="Remove empty trade", type="secondary"):
                        if trade in st.session_state.trades:
                            st.session_state.trades.remove(trade)
                            st.success("Empty trade removed!")
                            st.rerun()

with col_sidebar:
    # Performance Metrics
    st.markdown("""
    <div style="background: white; border-radius: 0.5rem; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 1.5rem;">
        <div style="background-color: #334155; color: white; padding: 0.75rem 1rem; border-radius: 0.5rem 0.5rem 0 0; display: flex; align-items: center;">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 0.5rem;">
                <path d="M21.21 15.89A10 10 0 1 1 8 2.83"></path>
                <path d="M22 12A10 10 0 0 0 12 2v10z"></path>
            </svg>
            <h3 style="font-weight: 600; margin: 0; font-size: 1rem;">Performance Metrics</h3>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    st.markdown("""
    <div style="background-color: #475569; color: white; padding: 0.75rem; font-size: 0.875rem; font-weight: 500; margin: -1.5rem -1rem 0 -1rem;">
        Overall Win Rate Distribution
    </div>
    """, unsafe_allow_html=True)
    
    # Create dynamic donut chart based on current data
    if rankings and len(rankings) > 0:
        labels = [r['name'] for r in rankings[:3]]
        values = [r['win_rate'] for r in rankings[:3]]
        colors = ['#fb923c', '#3b82f6', '#9ca3af']
        
        avg_win_rate = sum(values) / len(values) if values else 0
        
        fig_donut = go.Figure(data=[go.Pie(
            labels=labels, 
            values=values, 
            hole=0.6,
            marker=dict(colors=colors[:len(labels)], line=dict(color='#FFFFFF', width=2)),
            textinfo='none',
            hovertemplate='<b>%{label}</b><br>Win Rate: %{value}%<extra></extra>'
        )])
        
        fig_donut.update_layout(
            showlegend=False,
            height=300,
            margin=dict(t=20, b=20, l=20, r=20),
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            annotations=[
                dict(
                    text=f'<b>{avg_win_rate:.1f}%</b><br><span style="font-size:12px; color:#6b7280;">Avg Rate</span>', 
                    x=0.5, y=0.5, 
                    font_size=20, 
                    showarrow=False,
                    font_color='#374151'
                )
            ]
        )
        
        st.plotly_chart(fig_donut, use_container_width=True)
        
        # Dynamic Legend
        if len(rankings) >= 1:
            legend_cols = st.columns(min(3, len(rankings)))
            
            for i, ranking in enumerate(rankings[:3]):
                if i < len(legend_cols):
                    with legend_cols[i]:
                        color = colors[i] if i < len(colors) else '#6b7280'
                        st.markdown(f"""
                        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.5rem;">
                            <div style="display: flex; align-items: center;">
                                <div style="width: 0.75rem; height: 0.75rem; background-color: {color}; border-radius: 0.125rem; margin-right: 0.5rem;"></div>
                                <span style="font-size: 0.875rem; color: #000000;">{ranking['name']}</span>
                            </div>
                            <span style="font-weight: 600; font-size: 0.875rem; color: #000000;">{ranking['win_rate']}%</span>
                        </div>
                        """, unsafe_allow_html=True)
    else:
        st.info("No data available for performance metrics. Add some trades to see analytics!")
    
    # Trader of the Month
    if rankings and len(rankings) > 0:
        top_trader = rankings[0]
        st.markdown(f"""
        <div class="trade-card">
            <div class="card-header">
                <div style="display: flex; align-items: center;">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 0.5rem;">
                        <path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6"></path>
                        <path d="M14 9h1.5a2.5 2.5 0 0 0 0-5H14"></path>
                        <path d="M6 9v12l6-3 6 3V9"></path>
                    </svg>
                    <h3 style="font-weight: 600; margin: 0;">Trader of the Month</h3>
                </div>
            </div>
            <div style="text-align: center; padding: 1.5rem;">
                <div style="font-size: 4rem; margin-bottom: 1rem;">🏆</div>
                <h4 style="font-size: 1.25rem; font-weight: bold; color: #1f2937; margin: 0 0 0.5rem 0;">{top_trader['name']}</h4>
                <p style="color: #6b7280; font-size: 0.875rem; margin-bottom: 1rem;">Best performance with {top_trader['win_rate']}% win rate</p>
                <div style="background-color: #dcfce7; border-radius: 0.5rem; padding: 1rem; margin-top: 1rem;">
                    <div style="font-size: 0.75rem; color: #6b7280; text-transform: uppercase; letter-spacing: 0.05em;">WIN RATE THIS MONTH</div>
                    <div style="font-size: 2rem; font-weight: bold; color: #15803d;">{top_trader['win_rate']}%</div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown("""
        <div class="trade-card">
            <div class="card-header">
                <div style="display: flex; align-items: center;">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 0.5rem;">
                        <path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6"></path>
                        <path d="M14 9h1.5a2.5 2.5 0 0 0 0-5H14"></path>
                        <path d="M6 9v12l6-3 6 3V9"></path>
                    </svg>
                    <h3 style="font-weight: 600; margin: 0;">Trader of the Month</h3>
                </div>
            </div>
            <div style="text-align: center; padding: 1.5rem;">
                <div style="font-size: 4rem; margin-bottom: 1rem; opacity: 0.5;">🏆</div>
                <h4 style="font-size: 1.25rem; font-weight: bold; color: #6b7280; margin: 0 0 0.5rem 0;">No Data Yet</h4>
                <p style="color: #6b7280; font-size: 0.875rem; margin-bottom: 1rem;">Add trades to see top performer</p>
            </div>
        </div>
        """, unsafe_allow_html=True)
    
    # Instrument Performance
    st.markdown("""
    <div style="background: white; border-radius: 0.5rem; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 1.5rem;">
        <div style="background-color: #334155; color: white; padding: 0.75rem 1rem; border-radius: 0.5rem 0.5rem 0 0; display: flex; align-items: center;">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 0.5rem;">
                <line x1="12" y1="20" x2="12" y2="10"></line>
                <line x1="18" y1="20" x2="18" y2="4"></line>
                <line x1="6" y1="20" x2="6" y2="16"></line>
            </svg>
            <h3 style="font-weight: 600; margin: 0; font-size: 1rem;">Instrument Performance by Trader</h3>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # Calculate dynamic instrument performance
    if st.session_state.trades and len(st.session_state.trades) > 0:
        instruments = list(set(trade.get('instrument', '') for trade in st.session_state.trades if trade.get('instrument')))
        traders = list(set(trade.get('trader', '') for trade in st.session_state.trades if trade.get('trader')))
        
        if instruments and traders:
            performance_data = {'Instrument': instruments}
            
            for trader in traders:
                trader_performance = []
                for instrument in instruments:
                    trades = [t for t in st.session_state.trades 
                             if t.get('trader') == trader and t.get('instrument') == instrument]
                    if trades:
                        wins = sum(1 for t in trades if t.get('result') == 'Win')
                        win_rate = (wins / len(trades)) * 100
                        trader_performance.append(f"{win_rate:.0f}%")
                    else:
                        trader_performance.append("-")
                performance_data[trader] = trader_performance
            
            perf_df = pd.DataFrame(performance_data)
            
            def style_performance(val):
                if val == '-':
                    return 'background-color: #6b7280; color: white; text-align: center; font-weight: bold; padding: 8px; border-radius: 4px;'
                elif val.replace('%', '').replace('-', '').isdigit():
                    try:
                        rate = int(val.replace('%', ''))
                        if rate >= 70:
                            return 'background-color: #10b981; color: white; text-align: center; font-weight: bold; padding: 8px; border-radius: 4px;'
                        elif rate >= 50:
                            return 'background-color: #f59e0b; color: white; text-align: center; font-weight: bold; padding: 8px; border-radius: 4px;'
                        else:
                            return 'background-color: #ef4444; color: white; text-align: center; font-weight: bold; padding: 8px; border-radius: 4px;'
                    except:
                        pass
                return 'background-color: #f3f4f6; text-align: center; font-weight: 500; padding: 12px; color: #000000;'
            
            if len(traders) > 0:
                styled_df = perf_df.style.applymap(style_performance, subset=traders)
                styled_df = styled_df.applymap(lambda x: 'background-color: #f3f4f6; text-align: center; font-weight: 500; padding: 12px; color: #000000;', subset=['Instrument'])
                
                st.dataframe(styled_df, use_container_width=True, hide_index=True)
            else:
                st.info("No trader data available.")
        else:
            st.info("No complete trading data available.")
    else:
        st.info("No trades available for analysis. Add some trades to see detailed analytics!")

st.markdown('</div>', unsafe_allow_html=True)

# Footer (simplified)
current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
st.markdown(f"""
<div style="text-align: center; padding: 2rem 0; color: #6b7280; font-size: 0.875rem; border-top: 1px solid #e5e7eb; margin-top: 2rem;">
    <p>📊 WarZone Forex Analytics</p>
    <p>Updated: {current_time}</p>
</div>
""", unsafe_allow_html=True)
