import streamlit as st
import mysql.connector
import pickle
import os
import base64
from datetime import datetime
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import io
import plotly.express as px
import pdfplumber
import pandas as pd
import re
from decimal import Decimal

import subprocess
import base64
import os
from dotenv import load_dotenv

def get_image_as_base64(path):
    """Reads an image file and returns its base64 encoded string."""
    with open(path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode()
st.set_page_config(layout="wide")
st.markdown(
    """
    <style>
    /* Center whole page */
    .block-container {
        display: flex;
        flex-direction: column;
        align-items: center;   /* horizontal center */
        justify-content: center;
        text-align: center;
    }

    /* Center buttons */
    div.stButton > button {
        display: block;
        margin: 0 auto;
    }
    </style>
    """,
    unsafe_allow_html=True
)
def extract_bill_details(pdf_path):
    bill_data = {}

    def find_money(text, patterns):
        for p in patterns:
            m = re.search(p, text, re.IGNORECASE | re.MULTILINE | re.DOTALL)
            if m:
                return Decimal(m.group(1).replace(",", "").strip())
        return None

    with pdfplumber.open(pdf_path) as pdf:
        text = ""
        for page in pdf.pages:
            t = page.extract_text() or ""
            text += t + "\n"

    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)

    patterns = {
        "Account Number": r"\b(\d{10})\b",
        "Customer Name": r"(Mr\.?\s+[A-Za-z ]+)",
        "Bill Number": r"\b(\d{5,})\b",
        "Bill Cycle": r"\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)-\d{4}\b",
        "CA/Meter Number": r"\b(\d{7})\b",
        "Meter Serial Number": r"\b(\d{12})\b",
        "Connection Type": r"(LMV-1 \(DOMESTIC\))",
        "Connection Status": r"\b(LIVE)\b",
        "Voltage": r"\b(230 V)\b",
        "Sanctioned Load": r"\b(\d+\s*KW)\b",
    }
    for k, p in patterns.items():
        m = re.search(p, text)
        if m:
            bill_data[k] = m.group(1).strip()

    raw_dates = re.findall(r"\b(?:\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})\b", text)
    normalized_dates = []
    for d in raw_dates:
        try:
            if "-" in d:
                dt = datetime.strptime(d, "%Y-%m-%d").date()
            else:
                dt = datetime.strptime(d, "%d.%m.%Y").date()
            normalized_dates.append(dt)
        except ValueError:
            print(f"⚠️ Invalid date skipped: {d}")

    if len(normalized_dates) >= 2:
        bill_data["Bill Date"] = normalized_dates[0]
        bill_data["Due Date"] = normalized_dates[1]

    cd = re.findall(r"\b\d+\.\d+\s*KVA\b", text)
    if cd:
        bill_data["Contract Demand"] = cd  # keep as string list

    m = re.search(r"(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+\d+\.\d+\s+(\d+\.\d+)\s*KWH", text)
    if m:
        bill_data["Consumption Details"] = {
            "Current Reading": Decimal(m.group(1)),
            "Previous Reading": Decimal(m.group(2)),
            "Units Billed": Decimal(m.group(3))
        }

    slabs = re.findall(r"(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+Slab", text)
    if slabs:
        bill_data["Slab Details"] = [
            {"Units": Decimal(u), "Rate": Decimal(r), "Amount": Decimal(a)}
            for u, r, a in slabs
        ]

    charges = {}
    simple_patterns = {
        "Fixed Charges": r"Fixed Charges\s*Rs\.?\s*([\d,]+\.\d+)",
        "Energy Charges": r"Energy Charges\s*Rs\.?\s*([\d,]+\.\d+)",
        "Fuel Power Purch Adj Surcharge": r"Fuel Power Purch Adj Surcharge\s*Rs\.?\s*([\d,]+\.\d+)",
        "Electricity Duty": r"Electricity Duty\s*Rs\.?\s*([\d,]+\.\d+)",
        "Rebate": r"REBATE.*?Rs\.?\s*-?\s*([\d,]+\.\d+)",
        "Int. on SD": r"Int\. on SD\s*Rs\.?\s*-?\s*([\d,]+\.\d+)",
        "Rounding Amount": r"Rounding Amount\s*Rs\.?\s*-?\s*([\d,]+\.\d+)",
        "Regulatory Discount": r"Regulatory Discount\s*@\s*10%\s*Rs\.?\s*-?\s*([\d,]+\.\d+)",
        "Total Amount": r"Total Amount\s*Rs\.?\s*([\d,]+\.\d+)",
    }
    for k, p in simple_patterns.items():
        m = re.search(p, text, re.IGNORECASE)
        if m:
            charges[k] = Decimal(m.group(1).replace(",", ""))

    grand_total = find_money(
        text,
        [
            r"Payable\s+on\s+or\s+Before\s+Due\s+Date\s*\(Rs\.?\)\s*:?\s*([\d,]+(?:\.\d{1,2})?)",
            r"Grand\s*Total(?:\s*\(.*?\))?\s*:?\s*([\d,]+(?:\.\d{1,2})?)",
        ],
    )
    late_amount = find_money(
        text,
        [r"Payable\s+after\s+Due\s+Date\s*\(Rs\.?\)\s*:?\s*([\d,]+(?:\.\d{1,2})?)"]
    )

    if grand_total is not None:
        charges["Grand Total"] = grand_total
    if late_amount is not None:
        charges["Late Payment Amount"] = late_amount

    if charges:
        bill_data["Charges Breakdown"] = charges
        bill_data["Bill Amount"] = charges.get("Grand Total") or charges.get("Total Amount")

    return bill_data


conn = mysql.connector.connect(
    host=st.secrets["database"]["host"],
    user=st.secrets["database"]["user"],
    password=st.secrets["database"]["password"],
    database=st.secrets["database"]["name"],
    port = st.secrets['database']['port']
)
cursor = conn.cursor(dictionary=True)

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.pkl"
if 'step' not in st.session_state:
    st.session_state.step = 0
if 'creds' not in st.session_state:
    st.session_state.creds = None
if 'email' not in st.session_state:
    st.session_state.email = None
def get_service(creds):
    return build('gmail', 'v1', credentials=creds)

def get_credentials():
    """Load credentials or run OAuth flow with refresh support."""
    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_config(
                st.secrets["google_credentials"], SCOPES
            )
            creds = flow.run_local_server(
                port=8080,
                access_type="offline",
                prompt="consent"
            )
        with open(TOKEN_FILE, "wb") as token:
            pickle.dump(creds, token)
    return creds

def save_bill_to_mysql(bill_data, email):
    cursor.execute("SELECT user_id FROM users WHERE email = %s", (email,))
    row = cursor.fetchone()
    if row:
        user_id = row['user_id']
    else:
        return  # user not found

    bill_number = bill_data.get('Bill Number')
    bill_date = bill_data.get('Bill Date')

    cursor.execute("""
        SELECT bill_id FROM bills
        WHERE user_id=%s AND bill_number=%s AND bill_date=%s
    """, (user_id, bill_number, bill_date))
    if cursor.fetchone():
        print(f"⚠️ Bill {bill_number} already exists for user {email}, skipping.")
        return
    cursor.execute("SELECT user_id FROM users WHERE email = %s", (email,))
    row = cursor.fetchone()
    if row:
        user_id = row['user_id']
    bill_amount = bill_data.get('Bill Amount') or bill_data.get('Total Amount') or 0
    cursor.execute('''insert into bills(
        user_id,
        account_number,
        ca_number,
        meter_serial_number,
        connection_type,
        connection_status,
        voltage,
        sanctioned_load,
        bill_number,
        bill_cycle,
        bill_date,
        due_date,
        bill_amount) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
        (user_id,bill_data['Account Number'],
         bill_data['CA/Meter Number'],
         bill_data['Meter Serial Number'],
         bill_data['Connection Type'],
         bill_data['Connection Status'],
         bill_data['Voltage'],
         bill_data['Sanctioned Load'],
         bill_data['Bill Number'],
         bill_data['Bill Cycle'],
         bill_data['Bill Date'],
         bill_data['Due Date'],
         bill_amount 
         ))
    conn.commit()
    bill_id = cursor.lastrowid
    contract_demands = bill_data.get('Contract Demand', [])
    for demand in contract_demands:
        cursor.execute('''
                       insert into contract_demands(bill_id,demand_value) 
                       values(%s,%s)
                       ''',(bill_id,demand))
    conn.commit()
    cursor.execute('''
                   insert into consumption_details(bill_id,current_reading,previous_reading,units_billed)
                   values(%s,%s,%s,%s)
                   ''',(bill_id,
                        bill_data['Consumption Details'].get('Current Reading',None),
                        bill_data['Consumption Details'].get('Previous Reading',[]),
                        bill_data['Consumption Details'].get('Units Billed',[]))
    )
    conn.commit()
    slabs = bill_data.get("Slab Details", [])
    for i, slab in enumerate(slabs):
        cursor.execute('''
            INSERT INTO slab_details (bill_id, slab_order, units, rate, amount)
            VALUES (%s, %s, %s, %s, %s)
        ''', (
            bill_id,
            i%4+1,
            slab['Units'],
            slab['Rate'],
            slab['Amount']
        ))
    conn.commit()
    charges = bill_data.get("Charges Breakdown", {})
    for name, value in charges.items():
        cursor.execute('''
            INSERT INTO charges_breakdown (bill_id, charge_name, charge_value)
            VALUES (%s, %s, %s)
        ''', (
            bill_id,
            name,
            value     
        ))
    conn.commit()

def download_and_process_invoices(service, email, limit=500):
    results = service.users().messages().list(
        userId="me",
        q='2000043130.pdf',
        maxResults=limit
    ).execute()

    if "messages" not in results:
        return "❌ No invoice emails found."

    processed_count = 0

    for msg in results["messages"]:
        msg_id = msg["id"]
        message = service.users().messages().get(userId="me", id=msg_id).execute()

        for part in message["payload"].get("parts", []):
            if part.get("filename") and part["filename"].endswith(".pdf"):
                att_id = part["body"]["attachmentId"]
                att = service.users().messages().attachments().get(
                    userId="me", messageId=msg_id, id=att_id
                ).execute()
                data = base64.urlsafe_b64decode(att["data"].encode("UTF-8"))

                with open("temp.pdf", "wb") as tmp:
                    tmp.write(data)
                bill_data = extract_bill_details("temp.pdf")

                if bill_data:
                    save_bill_to_mysql(bill_data, email)
                    processed_count += 1

    if processed_count == 0:
        return "⚠️ No PDF invoices processed."
    return f"✅ Processed and saved {processed_count} invoices."

if st.session_state.step == 0:
    st.markdown("""
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;700&display=swap');
            .custom-image {
                transition: transform 0.5s ease-in-out;
            }

            .custom-image:hover {
                transform: scale(1.05); /* Scales the image to 105% on hover */
            }
            /* --- KEYFRAMES for animations --- */
            @keyframes fadeInUp {
                from { opacity: 0; transform: translateY(30px); }
                to { opacity: 1; transform: translateY(0); }
            }
            @keyframes subtleGlow {
                0%, 100% { box-shadow: 0 0 20px rgba(74, 99, 255, 0.25); }
                50% { box-shadow: 0 0 35px rgba(74, 99, 255, 0.45); }
            }

            /* --- BASE & FONT STYLES --- */
            body {
                font-family: 'Inter', sans-serif;
                background-color: #0E1117;
                /* Subtle dot pattern for texture */
                background-image: radial-gradient(rgba(255, 255, 255, 0.05) 1px, transparent 0);
                background-size: 20px 20px;
            }
            .st-emotion-cache-1y4p8pa { padding: 0; }
            h1, h2, h3, h4 { font-weight: 700; color: #FFFFFF; letter-spacing: -0.8px; }

            /* --- HERO SECTION --- */
            /* --- WATT WISE HERO SECTION --- */
            /* --- WATT WISE HERO SECTION (IMPROVED) --- */
            .hero-section {
                padding: 6rem 2rem;
                text-align: center;
                background: linear-gradient(160deg, #1e1e3f 0%, #0E1117 70%);
                border-bottom: 1px solid rgba(255, 255, 255, 0.1);
                animation: fadeInUp 0.8s ease-out;
            }
            .hero-section h1 {
                font-size: 3.8rem;
                font-weight: 700;
                text-shadow: 0 0 20px rgba(255, 255, 255, 0.1);
                margin-bottom: 1.5rem;
                letter-spacing: -1.5px;
            }
            .hero-section p.subtitle {
                font-size: 1.25rem;
                color: #b0b0d0;
                max-width: 750px;
                margin: 0 auto 2.5rem auto; /* Creates space for the button */
                line-height: 1.7;
                font-weight: 400;
            }

            /* Optional: Add a subtle hover effect for the glow */
            .hero-section img {
                filter: drop-shadow(0 0 25px rgba(251, 188, 4, 0.2)); /* Initial glow */
                transition: filter 0.5s ease-in-out, transform 0.5s ease-in-out; /* Smooth transitions for glow AND scale */
                transform: scale(1); /* Ensure initial scale is 1 */
            }

            /* Hover effect for the logo: larger, stronger glow, and slight enlargement */
            .hero-section img:hover {
                filter: drop-shadow(0 0 45px rgba(251, 188, 4, 0.9)); /* Even stronger, wider glow on hover */
                transform: scale(1.08); /* Enlarge the logo by 8% */
            }
            /* --- MAIN CONTENT SECTIONS --- */
            .content-section { padding: 5rem 2rem; text-align: center; }
            .content-section h3 { animation: fadeInUp 1s ease-out; font-size: 2.2rem; }

            /* --- GLASSMORPHISM CARDS --- */
            .feature-card {
                background: rgba(44, 44, 84, 0.35); backdrop-filter: blur(10px);
                border-radius: 18px; padding: 40px; text-align: center; height: 100%;
                border: 1px solid rgba(255, 255, 255, 0.15);
                transition: transform 0.3s ease, box-shadow 0.3s ease, border-color 0.3s ease;
                animation: fadeInUp 1s ease-out;
            }
            .feature-card:hover {
                transform: translateY(-15px);
                box-shadow: 0 25px 50px rgba(0, 0, 0, 0.6);
                border-color: rgba(255, 255, 255, 0.3);
            }
            .feature-card .icon { height: 50px; margin-bottom: 1.5rem; filter: drop-shadow(0 0 10px rgba(74, 154, 255, 0.6)); }
            .feature-card p { color: #b0b0d0; line-height: 1.7; font-weight: 400; }

            /* --- KEY INSIGHTS SECTION --- */
            .insights-section { background-color: rgba(18, 22, 29, 0.8); }
            .insights-list li {
                list-style-type: none; font-size: 1.1rem; margin-bottom: 1.5rem;
                color: #e0e0e0; padding-left: 45px; position: relative; font-weight: 500;
            }
            .insights-list li::before {
                content: '✓'; position: absolute; left: 0; color: #34D399;
                background-color: rgba(52, 211, 153, 0.15); border-radius: 50%;
                width: 32px; height: 32px; display: grid; place-items: center; font-weight: 700;
            }
            
            /* --- GOOGLE LOGIN BUTTON --- */
            div.stButton > button {
                all: unset; /* Reset Streamlit's default button styles */
                display: inline-flex !important; align-items: center !important; justify-content: center !important;
                font-family: 'Inter', sans-serif !important; font-weight: 700 !important; font-size: 18px !important;
                background-color: #FFFFFF !important; color: #2f2f2f !important;
                padding: 14px 28px 14px 54px !important;
                border-radius: 10px !important; border: none !important;
                box-shadow: 0 5px 15px rgba(0, 0, 0, 0.2) !important;
                cursor: pointer !important; transition: all 0.2s ease-in-out !important;
                animation: subtleGlow 4s infinite ease-in-out;
                background-image: url('data:image/svg+xml;base64,...'); /* Your existing Google icon SVG */
                background-repeat: no-repeat; background-position: 20px center; background-size: 20px 20px;
            }
            div.stButton > button:hover {
                box-shadow: 0 10px 20px rgba(0, 0, 0, 0.3) !important;
                transform: translateY(-4px) !important;
                animation: none; /* Pause glow on hover */
            }
            div.stButton > button:active {
                transform: translateY(0px) !important; box-shadow: 0 2px 5px rgba(0, 0, 0, 0.2) !important;
            }
        </style>
    """, unsafe_allow_html=True)
    icon_connect = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9IiM0YTlhZmYiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJNNCA0aDE2YzEuMSAwIDIgLjkgMiAydjEyYzAgMS4xLS45IDItMiAySDRjLTEuMSAwLTItLjktMi0yVjZjMC0xLjEuOS0yIDItMnoiPjwvcGF0aD48cG9seWxpbmUgcG9pbnRzPSIyMiw2IDEyLDEzIDIsNiI+PC9wb2x5bGluZT48L3N2Zz4=" 
    icon_parse = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZHRoPSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9IiM0YTlhZmYiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cG9seWdvbiBwb2ludHM9IjIzIDEgMSAxIDUgNSAxOSAxOSA1IiBmaWxsPSJub25lIj48L3BvbHlnb24+PGxpbmUgeDE9IjgiIHkxPSI5IiB4Mj0iMTYiIHkyPSI5Ij48L2xpbmU+PGxpbmUgeDE9IjgiIHkxPSIxMyIgeDI9IjE0IiB5Mj0iMTMiPjwvbGluZT48L3N2Zz4="
    icon_visualize = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZHRoPSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9IiM0YTlhZmYiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJtMyAyMCA5LTkgOSAxIj48L3BhdGg+PHBvbHlsaW5lIHBvaW50cz0iMTUgMTIgMTUgMjAgMjEgMjAiPjwvcG9seWxpbmU+PC9zdmc+"
    logo_path = os.path.join("downloads", "Logo.png")
    preview_path = os.path.join('downloads','Dashboard.png')
    try:
        logo_base64 = get_image_as_base64(logo_path)
                logo_src = f"data:image/png;base64,{logo_base64}"
    except FileNotFoundError:
        st.error(f"Logo file not found. Please ensure it is located at: {logo_path}")
    st.markdown(f"""<div class="hero-section"><div style="text-align: center; margin-bottom: 2rem;"><img src="{logo_src}"height=160 alt="Watt Wise Logo"  style="filter: drop-shadow(0 0 25px rgba(251, 188, 4, 0.6));"><h1 style="font-size: 4.5rem; margin-top: 1rem;">Watt Wise</h1></div><p class="subtitle">From confusing PDFs to crystal-clear insights. Finally, understand your electricity usage and take control of your spending in Greater Noida.</p></div>""",unsafe_allow_html=True)
    with st.container():
        st.markdown("<div class='content-section'><h3>A Seamless Three-Step Process</h3></div>", unsafe_allow_html=True)
        cols = st.columns(3)
        cards_data = [
            (cols[0], icon_connect, "1. Connect & Fetch", "Securely link your Google account to automatically find and retrieve your NPCL e-bills."),
            (cols[1], icon_parse, "2. Parse & Structure", "Our intelligent engine extracts every data point and organizes it into a structured database."),
            (cols[2], icon_visualize, "3. Visualize & Analyze", "Dive into an interactive dashboard to track trends, compare costs, and understand your usage.")
        ]
        # Change the loop to include 'description'
        for col, icon, title, description in cards_data:
            # Use the {description} variable in the <p> tag
            col.markdown(f'<div class="feature-card" style="animation-delay: {cards_data.index((col, icon, title, description))*0.2}s"><img src="{icon}" class="icon"><h4>{title}</h4><p>{description}</p></div>', unsafe_allow_html=True)
    with st.container():
        st.markdown("<div class='content-section insights-section'><h3>Unlock Your Complete Financial Picture</h3></div>", unsafe_allow_html=True)
        left_col, right_col = st.columns([1.5, 2])
        with left_col:
            with open("downloads/Dashboard.PNG", "rb") as img_file:
                img_base64 = base64.b64encode(img_file.read()).decode()
            st.markdown(
                f"""
                <style>
                    .custom-image {{
                        transition: transform 0.5s ease-in-out;
                    }}
                    .custom-image:hover {{
                        transform: scale(1.05);
                    }}
                </style>

                <div style='margin-left: 50px;'>
                    <img src="data:image/png;base64,{img_base64}" class="custom-image" style="width:50%; height:auto;" />
                </div>
                """,
                unsafe_allow_html=True
            )  
        with right_col:
            st.markdown('''''')
            st.markdown('''''')
            st.markdown('''''')
            st.markdown('''''')
            st.markdown('''''')
            st.markdown('''''')
            st.markdown("""
            <div class="insights-container">
                <ul class="insights-list">
                    <li><b>Year-Over-Year Comparison:</b> Instantly compare monthly bills and consumption across different years.</li>
                    <li><b>Consumption Trend Analysis:</b> Identify your highest and lowest usage months to manage energy better.</li>
                    <li><b>Detailed Cost Breakdown:</b> Understand exactly where your money goes with a clear view of all charges and rebates.</li>
                    <li><b>Centralized Digital Archive:</b> Access a complete, searchable history of all your processed bills in one place.</li>
                </ul>
            </div>
            """, unsafe_allow_html=True)
    st.markdown('''''')
    st.markdown('''''')
    st.markdown('''''')
    st.markdown('''''')
    st.markdown('''''')
    st.markdown('''''')
    st.markdown('''''')
    st.markdown('''''')
    st.markdown('''''')
    st.markdown('''''')
    st.markdown('''''')
    st.markdown('''''')

    # Check if the user is logged in
    if st.session_state.get('creds'):
        # --- 1. Welcome Header ---
        email = st.session_state.get("email", "...")
        
        # Initialize user profile and DB entry only once per session
        if 'service' not in st.session_state:
            st.session_state.service = get_service(st.session_state.creds)
            profile = st.session_state.service.users().getProfile(userId='me').execute()
            st.session_state.email = profile.get("emailAddress", "N/A")
            email = st.session_state.email
            
            # Insert or update user in the database
            cursor.execute(
                "INSERT INTO users (email, created_at) VALUES (%s, NOW()) "
                "ON DUPLICATE KEY UPDATE created_at=NOW()",
                (email,)
            )
            conn.commit()

        # Display welcome message and logout button in columns
        col1, col2 = st.columns([3, 1])
        with col1:
            st.success(f"**Logged in as:** {email}")
        with col2:
            if st.button("Logout", use_container_width=True):
                # Clear all session data on logout
                st.session_state.clear() 
                if os.path.exists(TOKEN_FILE):
                    os.remove(TOKEN_FILE)
                st.rerun()
                
        st.markdown("---")

        # --- 2. Main Action: Process Invoices ---
        st.markdown("### 📧 Scan Your Gmail for Invoices")
        st.info(
            "Click the button below to automatically find, process, and save your NPCL electricity bills from your inbox. "
            "This may take a few moments."
        )

        # Center the button for a cleaner look
        _, btn_col, _ = st.columns([1, 2, 1])
        with btn_col:
            if st.button("⚡ Process Invoices Now", type="primary", use_container_width=True):
                with st.spinner("Analyzing emails and processing PDFs... Please wait."):
                    service = st.session_state.service
                    msg = download_and_process_invoices(service, email, limit=500)
                
                st.success(msg)
                # Set a flag that processing is complete to move to the next step
                st.session_state.processing_complete = True 
                st.session_state.step = 1
                st.rerun()

    else:
        # --- Fallback: Show the main login button (from your homepage) ---
        # This part should be integrated with your main homepage design
        col1,col2,col3 = st.columns([1,2,1])
        with col2:
            if st.button("Login with Google",use_container_width=True):
                st.session_state.creds = get_credentials()
                st.rerun()

# ------------------ STEP 1: DASHBOARD ------------------
if st.session_state.step == 1:
    st.markdown("<h1 style='text-align: center;'>Electricity Bill Analysis Dashboard</h1>", unsafe_allow_html=True)
    month_order = ["JAN","FEB","MAR","APR","MAY","JUN",
                "JUL","AUG","SEP","OCT","NOV","DEC"]
    full_to_short = {
        "January":"JAN","February":"FEB","March":"MAR","April":"APR",
        "May":"MAY","June":"JUN","July":"JUL","August":"AUG",
        "September":"SEP","October":"OCT","November":"NOV","December":"DEC"
    }
    month_map = {
        1: "JAN", 2: "FEB", 3: "MAR", 4: "APR",
        5: "MAY", 6: "JUN", 7: "JUL", 8: "AUG",
        9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC"
    }
    email = st.session_state.email
    if not email:
        st.error("User not logged in!")
        st.stop()

    user_id = ''
    cursor.execute("SELECT user_id FROM users WHERE email = %s", (email,))
    row = cursor.fetchone()
    if row:
        user_id = row['user_id']
    else:
        st.error("User not found!")
        st.stop()

    cursor.execute("SELECT bill_date as Date,bill_amount as Amount FROM bills WHERE user_id = %s", (user_id,))
    bills = cursor.fetchall()

    if bills:
        st.dataframe(bills)
    else:
        st.info("No bills found for this user.")
        if st.button("Logout"):
            st.session_state.creds = None
            st.session_state.email = None
            st.session_state.step = 0
            if os.path.exists(TOKEN_FILE):
                os.remove(TOKEN_FILE)
            st.rerun()
        
    st.title("Electricity Bill Comparison Across Years")

    cursor.execute('''
        SELECT user_id,
            bill_amount AS Amount,
            YEAR(bill_date) AS Year,
            MONTH(bill_date) AS MonthNum
        FROM bills
        WHERE user_id = %s
    ''', (user_id,))

    bill_comp = cursor.fetchall()

    df = pd.DataFrame(bill_comp, columns=["user_id", "Amount", "Year", "MonthNum"])

    month_map = {
        1: "JAN", 2: "FEB", 3: "MAR", 4: "APR",
        5: "MAY", 6: "JUN", 7: "JUL", 8: "AUG",
        9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC"
    }
    df["Month"] = df["MonthNum"].map(month_map)
    df["Month"] = pd.Categorical(df["Month"], categories=month_order, ordered=True)

    years = sorted(df["Year"].unique().astype(int), reverse=True)
    years = [str(y) for y in years]
    selected_year = st.selectbox("Select Year", years, key="year_selector_1")

    df_filtered = df[df["Year"] == int(selected_year)].sort_values("Month")

    fig = px.bar(
        df_filtered,
        x="Month", y="Amount",
        title=f"Electricity Bill for {selected_year}",
        text="Amount"
    )
    fig.update_xaxes(categoryorder="array", categoryarray=month_order)
    fig.update_traces(marker_color="skyblue", textposition="outside")
    st.plotly_chart(fig)

    
    st.title("Monthly Consumption Across Years")
    cursor.execute('''
                    SELECT 
                        b.user_id,
                        c.units_billed AS 'Units Consumed',
                        YEAR(b.bill_date) AS Year,
                        MONTHNAME(b.bill_date) AS Month,
                        b.bill_cycle AS BillCycle
                    FROM bills b
                    INNER JOIN consumption_details c ON b.bill_id = c.bill_id;
                   ''')
    consump = cursor.fetchall()
    df = pd.DataFrame(consump, columns=["user_id","Units Consumed","Year","Month","BillCycle"])
    df["Month"] = df["Month"].map(full_to_short)
    df["Month"] = pd.Categorical(df["Month"], categories=month_order, ordered=True)
    df = df.sort_values(["Year","Month"])
    
    fig = px.line(
        df,
        x="Month", y="Units Consumed",
        color="Year", markers=True,
        title="Monthly Units Consumed Across Years",
        category_orders={"Month": month_order}
    )
    st.plotly_chart(fig, use_container_width=True)
    
    st.title('Charges & Reductions Breakdown')
    cursor.execute('''
                    SELECT 
                        b.user_id,
                        c.charge_name AS 'Charges Name',
                        YEAR(b.bill_date) AS Year,
                        MONTHNAME(b.bill_date) AS Month,
                        b.bill_cycle AS BillCycle,
                        c.charge_value AS 'Charge Value'
                    FROM bills b
                    INNER JOIN charges_breakdown c ON b.bill_id = c.bill_id;
                   ''')
    charges = cursor.fetchall()
    df = pd.DataFrame(charges)
    df["Charge Value"] = pd.to_numeric(df["Charge Value"], errors="coerce")
    df.loc[df["Charges Name"].isin(["Rebate", "Regulatory Discount"]), "Charge Value"] *= -1
    month_order = ["January","February","March","April","May","June",
                "July","August","September","October","November","December"]
    df["Month"] = pd.Categorical(df["Month"], categories=month_order, ordered=True)

    years = sorted(df["Year"].unique())
    selected_year = st.selectbox("Select Year", years, index=len(years)-1)

    df_year = df[df["Year"] == selected_year]

    fig = px.bar(
        df_year,
        x="Month",
        y="Charge Value",
        color="Charges Name",
        barmode="group",   # clustered
        category_orders={"Month": month_order}
    )

    fig.update_layout(
        title=f"Monthly Charges Breakdown ({selected_year})",
        xaxis_title="Month",
        yaxis_title="Charge Value",
        legend_title="Charges Name"
    )

    st.plotly_chart(fig, use_container_width=True)
    if st.button("Logout"):
        st.session_state.creds = None
        st.session_state.email = None
        st.session_state.step = 0
        if os.path.exists(TOKEN_FILE):
            os.remove(TOKEN_FILE)
        st.rerun()
