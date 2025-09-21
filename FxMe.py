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

# Google Sheets Configuration
SHEET_NAME = "Forex Trading Analytics"
WORKSHEET_NAME = "Trades"

# Real-time update configuration
REAL_TIME_UPDATE_INTERVAL = 10  # Update every 10 seconds (reduced frequency)
CACHE_TTL = 30  # Cache data for 30 seconds (longer cache)

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
        return None

@st.cache_data(ttl=CACHE_TTL, show_spinner=False)  # Longer cache with no spinner
def load_trades_from_sheets():
    """Load trades from Google Sheets with optimized caching"""
    try:
        gc = init_connection()
        if gc is None:
            return load_fallback_data()
        
        # Single API call to get all data
        spreadsheet = gc.open(SHEET_NAME)
        sheet = spreadsheet.worksheet(WORKSHEET_NAME)
        all_values = sheet.get_all_values()
        
        if not all_values or len(all_values) < 2:
            return load_fallback_data()
        
        # Process data efficiently
        processed_records = []
        for i, row in enumerate(all_values[1:], 1):  # Skip headers
            if not any(str(cell).strip() for cell in row):
                continue
                
            try:
                while len(row) < 12:
                    row.append('')
                
                # Only process rows with complete valid data
                if (row[2] and row[3] and row[4] and row[5] and row[6] and 
                    str(row[2]).strip() not in ['', 'trader'] and 
                    str(row[3]).strip() not in ['', 'instrument'] and
                    str(row[4]).strip() not in ['', '0.0', '0'] and
                    str(row[10]).strip() not in ['', 'outcome'] and
                    str(row[11]).strip() not in ['', 'result']):
                    
                    try:
                        entry_val = float(row[4])
                        sl_val = float(row[5]) 
                        target_val = float(row[6])
                        
                        # Skip if all prices are zero
                        if entry_val == 0.0 and sl_val == 0.0 and target_val == 0.0:
                            continue
                            
                        processed_record = {
                            'id': int(row[0]) if row[0] and str(row[0]).strip().isdigit() else i,
                            'date': str(row[1]).strip() if row[1] else '',
                            'trader': str(row[2]).strip(),
                            'instrument': str(row[3]).strip(),
                            'entry': entry_val,
                            'sl': sl_val,
                            'target': target_val,
                            'risk': float(row[7]) if row[7] and str(row[7]).replace('.', '').replace('-', '').isdigit() else abs(entry_val - sl_val),
                            'reward': float(row[8]) if row[8] and str(row[8]).replace('.', '').replace('-', '').isdigit() else abs(target_val - entry_val),
                            'rrRatio': float(row[9]) if row[9] and str(row[9]).replace('.', '').replace('-', '').isdigit() else 0.0,
                            'outcome': str(row[10]).strip(),
                            'result': str(row[11]).strip()
                        }
                        processed_records.append(processed_record)
                    except (ValueError, TypeError):
                        continue
                    
            except (ValueError, TypeError, IndexError):
                continue
        
        return processed_records if processed_records else load_fallback_data()
        
    except:
        return load_fallback_data()

def save_trade_to_sheets(trade_data):
    """Save a single trade to Google Sheets - optimized for speed"""
    try:
        gc = init_connection()
        if gc is None:
            return False
        
        spreadsheet = gc.open(SHEET_NAME)
        sheet = spreadsheet.worksheet(WORKSHEET_NAME)
        
        # Prepare row data efficiently
        row_data = [
            str(trade_data['id']), str(trade_data['date']), str(trade_data['trader']),
            str(trade_data['instrument']), float(trade_data['entry']), float(trade_data['sl']),
            float(trade_data['target']), float(trade_data['risk']), float(trade_data['reward']),
            float(trade_data['rrRatio']), str(trade_data['outcome']), str(trade_data['result'])
        ]
        
        # Single API call
        sheet.append_row(row_data, value_input_option='RAW')
        return True
        
    except:
        return False

def delete_trade_from_sheets(trade_id):
    """Delete a trade from Google Sheets - optimized for speed"""
    try:
        gc = init_connection()
        if gc is None:
            return False
        
        spreadsheet = gc.open(SHEET_NAME)
        sheet = spreadsheet.worksheet(WORKSHEET_NAME)
        
        # Find and delete in single operation
        cell = sheet.find(str(trade_id))
        if cell and cell.col == 1:
            sheet.delete_rows(cell.row)
            return True
        return False
        
    except:
        return False

def setup_google_sheet_silently():
    """Set up the Google Sheet with proper headers if it doesn't exist - runs silently in background"""
    try:
        gc = init_connection()
        if gc is None:
            return False
        
        # Step 1: Handle the spreadsheet
        try:
            spreadsheet = gc.open(SHEET_NAME)
        except gspread.SpreadsheetNotFound:
            # Create new spreadsheet
            spreadsheet = gc.create(SHEET_NAME)
        
        # Step 2: Handle the worksheet
        try:
            worksheet = spreadsheet.worksheet(WORKSHEET_NAME)
        except gspread.WorksheetNotFound:
            # Create new worksheet named "Trades"
            worksheet = spreadsheet.add_worksheet(title=WORKSHEET_NAME, rows=1000, cols=12)
        
        # Step 3: Setup headers
        try:
            headers = worksheet.row_values(1)
            expected_headers = ['id', 'date', 'trader', 'instrument', 'entry', 'sl', 'target', 'risk', 'reward', 'rrRatio', 'outcome', 'result']
            
            if not headers or len(headers) == 0 or headers != expected_headers:
                # Clear first row and set proper headers
                worksheet.clear()  # Clear the worksheet first
                worksheet.append_row(expected_headers)
            
        except Exception as e:
            # Try to add headers anyway
            try:
                headers = ['id', 'date', 'trader', 'instrument', 'entry', 'sl', 'target', 'risk', 'reward', 'rrRatio', 'outcome', 'result']
                worksheet.append_row(headers)
            except:
                pass
        
        return True
        
    except:
        return False

def load_fallback_data():
    """Load fallback data when Google Sheets is not available"""
    return [
        { 'id': 1, 'date': '2023-10-08', 'trader': 'Waithaka', 'instrument': 'XAUUSD', 'entry': 1820.50, 'sl': 1815.00, 'target': 1830.00, 'risk': 5.50, 'reward': 9.50, 'rrRatio': 1.73, 'outcome': 'Target Hit', 'result': 'Win' },
        { 'id': 2, 'date': '2023-10-07', 'trader': 'Wallace', 'instrument': 'USOIL', 'entry': 89.30, 'sl': 88.50, 'target': 91.00, 'risk': 0.80, 'reward': 1.70, 'rrRatio': 2.13, 'outcome': 'SL Hit', 'result': 'Loss' },
        { 'id': 3, 'date': '2023-10-06', 'trader': 'Max', 'instrument': 'BTCUSD', 'entry': 27450.00, 'sl': 27200.00, 'target': 27800.00, 'risk': 250.00, 'reward': 350.00, 'rrRatio': 1.40, 'outcome': 'Target Hit', 'result': 'Win' },
        { 'id': 4, 'date': '2023-10-05', 'trader': 'Waithaka', 'instrument': 'EURUSD', 'entry': 1.06250, 'sl': 1.06000, 'target': 1.06700, 'risk': 0.00250, 'reward': 0.00450, 'rrRatio': 1.80, 'outcome': 'Target Hit', 'result': 'Win' }
    ]

# Real-time update functions
def force_refresh_data():
    """Force refresh data from Google Sheets and update session state - optimized"""
    try:
        # Clear cache and get fresh data
        st.cache_data.clear()
        fresh_data = load_trades_from_sheets()
        st.session_state.trades = fresh_data
        st.session_state.last_data_hash = hash(str(fresh_data))  # Track changes
        return True
    except:
        return False

def auto_refresh_trades():
    """Auto-refresh trades data - optimized to reduce unnecessary updates"""
    if 'last_auto_refresh' not in st.session_state:
        st.session_state.last_auto_refresh = time.time()
    if 'last_data_hash' not in st.session_state:
        st.session_state.last_data_hash = None
        
    # Check if enough time has passed and we're connected
    if (st.session_state.sheets_connected and 
        time.time() - st.session_state.last_auto_refresh > REAL_TIME_UPDATE_INTERVAL):
        
        st.session_state.last_auto_refresh = time.time()
        
        try:
            # Get fresh data without clearing cache immediately
            fresh_data = load_trades_from_sheets()
            current_hash = hash(str(fresh_data))
            
            # Only update if data actually changed
            if st.session_state.last_data_hash != current_hash:
                st.session_state.trades = fresh_data
                st.session_state.last_data_hash = current_hash
                st.rerun()
        except:
            pass

# Page configuration
st.set_page_config(
    page_title="The War Zone - Forex Trading Analytics",
    page_icon="⚔️",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Custom CSS
st.markdown("""
<style>
    .stApp {
        background-color: #f3f4f6;
    }
    
    .war-zone-header {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
        color: white;
        padding: 3rem 1.5rem 2rem 1.5rem;
        border-radius: 0;
        margin: -1rem -1rem 0 -1rem;
        box-shadow: 0 4px 20px rgba(0,0,0,0.3);
        text-align: center;
        position: relative;
        overflow: hidden;
    }
    
    .war-zone-header::before {
        content: '';
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: url("data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%23ffffff' fill-opacity='0.03'%3E%3Ccircle cx='30' cy='30' r='2'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E") repeat;
        pointer-events: none;
    }
    
    .war-zone-title {
        font-size: 4rem;
        font-weight: 900;
        margin: 0;
        text-shadow: 2px 2px 4px rgba(0,0,0,0.5);
        letter-spacing: 2px;
        position: relative;
        z-index: 1;
    }
    
    .war-zone-subtitle {
        font-size: 1.2rem;
        font-style: italic;
        margin: 1rem 0 0.5rem 0;
        opacity: 0.9;
        position: relative;
        z-index: 1;
    }
    
    .war-zone-author {
        font-size: 1rem;
        font-weight: 600;
        margin: 0;
        opacity: 0.8;
        position: relative;
        z-index: 1;
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

# Initialize session state with optimized data loading
if 'trades' not in st.session_state:
    st.session_state.trades = load_trades_from_sheets()
    st.session_state.last_data_hash = hash(str(st.session_state.trades))
    
if 'sheets_connected' not in st.session_state:
    connection = init_connection()
    st.session_state.sheets_connected = connection is not None
    # Silently setup Google Sheets if connected (non-blocking)
    if st.session_state.sheets_connected:
        try:
            setup_google_sheet_silently()
        except:
            pass

# Optimized real-time updates
if st.session_state.sheets_connected:
    auto_refresh_trades()

# Header - The War Zone
st.markdown("""
<div class="war-zone-header">
    <h1 class="war-zone-title">THE WAR ZONE</h1>
    <p class="war-zone-subtitle">"Don't be afraid to give up the good to go for the great."</p>
    <p class="war-zone-author">— John D. Rockefeller</p>
</div>
""", unsafe_allow_html=True)

st.markdown('<div class="main-content">', unsafe_allow_html=True)

# Refresh Controls at the top
refresh_col1, refresh_col2, refresh_col3 = st.columns([1, 2, 1])

with refresh_col2:
    refresh_container = st.container()
    with refresh_container:
        button_col, timer_col = st.columns([1, 2])
        
        with button_col:
            if st.button("🔄 Refresh Data", type="primary", use_container_width=True):
                with st.spinner("Refreshing..."):
                    force_refresh_data()
                    st.success("Data refreshed!")
                    time.sleep(1)
                    st.rerun()
        
        with timer_col:
            if st.session_state.sheets_connected:
                # Calculate time until next auto-refresh
                time_since_last = time.time() - st.session_state.get('last_auto_refresh', 0)
                time_until_next = max(0, REAL_TIME_UPDATE_INTERVAL - time_since_last)
                
                if time_until_next > 0:
                    minutes = int(time_until_next // 60)
                    seconds = int(time_until_next % 60)
                    if minutes > 0:
                        next_refresh_text = f"Next auto-refresh in {minutes}m {seconds}s"
                    else:
                        next_refresh_text = f"Next auto-refresh in {seconds}s"
                else:
                    next_refresh_text = "Auto-refreshing now..."
                
                st.markdown(f"""
                <div style="display: flex; align-items: center; height: 2.5rem; padding-left: 1rem;">
                    <span style="color: #10b981; font-size: 0.875rem; font-weight: 500;">
                        ⚡ {next_refresh_text}
                    </span>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.markdown("""
                <div style="display: flex; align-items: center; height: 2.5rem; padding-left: 1rem;">
                    <span style="color: #6b7280; font-size: 0.875rem;">
                        📱 Local mode - Manual refresh only
                    </span>
                </div>
                """, unsafe_allow_html=True)

st.markdown("---")

# Add New Trade Section
st.markdown("""
<div class="trade-card">
    <div class="card-header">
        <div style="display: flex; align-items: center;">
            <div style="background-color: #0d9488; border-radius: 50%; padding: 0.25rem; margin-right: 0.75rem;">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <line x1="12" y1="5" x2="12" y2="19"></line>
                    <line x1="5" y1="12" x2="19" y2="12"></line>
                </svg>
            </div>
            <span style="font-weight: 600;">Add New Trade</span>
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
    instrument_pairs = ['Select Instrument', 'XAUUSD', 'USDOIL', 'BTCUSD', 'USTECH', 'EURUSD', 'GBPUSD', 'AUDUSD', 'USDJPY', 'USDCAD', 'NZDUSD']
    instrument = st.selectbox("", instrument_pairs, key="instrument_select", label_visibility="collapsed")

with col3:
    st.markdown('<div class="form-group"><label>Date</label></div>', unsafe_allow_html=True)
    trade_date = st.date_input("", value=date.today(), key="date_input", label_visibility="collapsed")

with col4:
    st.markdown('<div class="form-group"><label>Outcome</label></div>', unsafe_allow_html=True)
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
        if trader != "Select Trader" and instrument != "Select Instrument" and outcome != "Select Outcome" and entry and sl and target:
            risk = abs(entry - sl)
            reward = abs(target - entry)
            rr_ratio = reward / risk if risk != 0 else 0
            result = "Win" if outcome == "Target Hit" else "Loss"
            
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
                'outcome': outcome,
                'result': result
            }
            
            # Add to session state and sync
            st.session_state.trades.append(new_trade)
            
            # Sync to Google Sheets if connected (silently)
            if st.session_state.sheets_connected:
                try:
                    save_trade_to_sheets(new_trade)
                    force_refresh_data()
                except Exception as e:
                    pass
            
            st.success("✅ Trade added successfully!")
            
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
            if outcome == "Select Outcome":
                missing_fields.append("Outcome")
            if not entry:
                missing_fields.append("Entry Price")
            if not sl:
                missing_fields.append("Stop Loss")
            if not target:
                missing_fields.append("Target Price")
            
            if missing_fields:
                st.error(f"Missing: {', '.join(missing_fields)}")

st.markdown('</div></div>', unsafe_allow_html=True)

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
        
        for idx, trade in enumerate(sorted_trades):
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
                st.markdown(f'<div style="color: #000000; padding: 0.25rem 0; font-size: 0.875rem;">{trade.get("outcome", "N/A")}</div>', unsafe_allow_html=True)
            with cols[10]:
                result = trade.get('result', 'Unknown')
                if result == 'Win':
                    st.markdown('<span style="background-color: #dcfce7; color: #166534; padding: 4px 8px; border-radius: 12px; font-weight: 500; font-size: 0.75rem;">Win</span>', unsafe_allow_html=True)
                elif result == 'Loss':
                    st.markdown('<span style="background-color: #fee2e2; color: #dc2626; padding: 4px 8px; border-radius: 12px; font-weight: 500; font-size: 0.75rem;">Loss</span>', unsafe_allow_html=True)
                else:
                    st.markdown('<span style="background-color: #f3f4f6; color: #6b7280; padding: 4px 8px; border-radius: 12px; font-weight: 500; font-size: 0.75rem;">Unknown</span>', unsafe_allow_html=True)
            with cols[11]:
                trade_id = trade.get('id')
                if trade_id:
                    # Use a simple, unique key based on position and ID
                    unique_key = f"delete_btn_{idx}_{trade_id}"
                    
                    if st.button("🗑️", key=unique_key, help="Delete this trade", type="secondary"):
                        # Immediate removal from session state
                        st.session_state.trades = [t for t in st.session_state.trades if t.get('id') != trade_id]
                        
                        # Background Google Sheets sync
                        if st.session_state.sheets_connected:
                            try:
                                delete_trade_from_sheets(trade_id)
                                st.session_state.last_data_hash = hash(str(st.session_state.trades))
                            except:
                                pass
                        
                        st.success("✅ Trade deleted!")
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

# Footer
current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
st.markdown(f"""
<div style="text-align: center; padding: 2rem 0; color: #6b7280; font-size: 0.875rem; border-top: 1px solid #e5e7eb; margin-top: 2rem;">
    <p>⚔️ The War Zone - Where Traders Rise or Fall</p>
    <p>Last updated: {current_time}</p>
</div>
""", unsafe_allow_html=True)