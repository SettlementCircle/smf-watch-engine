import sqlite3
import requests
import json
import time
from datetime import datetime
import os

# 🔒 SECURE CREDENTIALS
FCA_EMAIL = os.environ.get("FCA_EMAIL") or "dc@settlementcircle.com"
FCA_KEY = os.environ.get("FCA_KEY") or "5828c39ebaed901e3695144dcc63e136"
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")

# NEW: We now use an array of Target FRNs instead of just one!
# Here we have Barclays, ClearBank, and Monzo as examples.
TARGET_FRNS = ["122702", "754568", "730427"]

# ==========================================
# 🧠 THE INTELLIGENCE DICTIONARY
# ==========================================
ROLE_MAPPINGS = {
    "SMF16": {"role": "Compliance Oversight", "buyer": "RegTech & AML", "pitch": "The Compliance seat is transitioning. Prime time to pitch AML/KYC automation or regulatory audits."},
    "SMF17": {"role": "MLRO", "buyer": "RegTech & AML", "pitch": "The MLRO seat is transitioning. Prime time to pitch AML/KYC automation or transaction monitoring."},
    "SMF24": {"role": "Chief Operations", "buyer": "Cyber & Cloud", "pitch": "Operations mandate detected. Pitch your cloud/cyber solutions before they lock in their 12-month IT roadmap."},
    "SMF4":  {"role": "Chief Risk", "buyer": "FinTech & Data", "pitch": "Risk leadership is changing. Massive trigger for new risk modeling software and reporting dashboards."},
    "SMF3":  {"role": "Executive Director", "buyer": "WealthTech & CRM", "pitch": "Director departure detected. High probability they are launching a new boutique. Pitch Wealth CRM."},
    "SMF1":  {"role": "Chief Executive", "buyer": "Enterprise ERP", "pitch": "CEO transition. Expect a massive budget unlock as they audit the firm's strategic direction."},
    "SMF2":  {"role": "Chief Finance", "buyer": "Enterprise ERP", "pitch": "CFO transition. Prime opportunity to pitch financial planning tools or cost-saving infrastructure."},
    "CF30":  {"role": "Customer Function", "buyer": "Recruiters & Tech", "pitch": "Top producer movement. Track where they land for immediate software seat expansion."}
}

def setup_database():
    print("⚙️ Connecting to local database (live_smf_watch.db)...")
    conn = sqlite3.connect('live_smf_watch.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tracked_executives (
            irn TEXT PRIMARY KEY,
            name TEXT,
            firm_name TEXT,
            function_code TEXT,
            full_title TEXT,
            last_checked TEXT
        )
    ''')
    conn.commit()
    return conn

def fetch_executive_roster(frn):
    """Fetches the executive roster for a specific FRN."""
    print(f"🌐 Fetching live Directory Persons roster for FRN: {frn}...")
    headers = {
        'X-Auth-Email': FCA_EMAIL,
        'X-Auth-Key': FCA_KEY,
        'Content-Type': 'application/json'
    }
    
    roster_url = f"https://register.fca.org.uk/services/V0.1/Firm/{frn}/Individuals"
    response = requests.get(roster_url, headers=headers)
    
    if response.status_code != 200:
        print(f"❌ API Error fetching roster for {frn}: {response.status_code}")
        return {}

    raw_individuals = response.json().get('Data', [])
    print(f"✅ Found {len(raw_individuals)} individuals at {frn}. Extracting SMF roles...")
    
    today_roster = {}
    
    for person in raw_individuals:
        irn = person.get('IRN')
        name = person.get('Name')
        
        if not irn or irn == 'N/A':
            continue
            
        cf_url = f"https://register.fca.org.uk/services/V0.1/Individuals/{irn}/CF"
        
        try:
            cf_response = requests.get(cf_url, headers=headers)
            if cf_response.status_code == 200:
                cf_data = cf_response.json().get('Data')
                
                if cf_data and len(cf_data) > 0:
                    current_roles = cf_data[0].get('Current', {})
                    if current_roles:
                        first_role_key = list(current_roles.keys())[0] 
                        role_details = current_roles[first_role_key]
                        
                        raw_role_name = role_details.get('Name', '')
                        firm_name = role_details.get('Firm Name', 'Unknown Firm')
                        
                        # Clean the code (e.g. extracts "SMF2" from "(1)SMF2 Chief Finance")
                        raw_code = raw_role_name.split(' ')[0]
                        function_code = raw_code.replace('(1)', '').replace('(2)', '') 
                        
                        # NOISE FILTER: Only track actual regulatory codes
                        if function_code.startswith('SMF') or function_code.startswith('CF'):
                            today_roster[irn] = {
                                "name": name,
                                "firm_name": firm_name,
                                "function_code": function_code,
                                "full_title": raw_role_name
                            }
            
            # Be polite to government servers
            time.sleep(0.3)
            
        except Exception as e:
            print(f"⚠️ Failed to fetch CF for {irn}: {e}")
            
    return today_roster

def send_slack_alert(irn, name, firm_name, function_code, full_title, event_type, frn):
    # Lookup the intelligence for this specific code
    intel = ROLE_MAPPINGS.get(function_code, {
        "buyer": "General Sales", 
        "pitch": "Executive transition detected. Research this role for potential budget unlock."
    })
    
    if event_type == "DEPARTURE":
        title_text = "🚨 LEAD ALERT: SMF Departure Detected"
        color = "#e11d48" # Red
        action_text = intel["pitch"]
    else:
        title_text = "✨ LEAD ALERT: New SMF Appointed"
        color = "#10b981" # Green
        action_text = f"A new {function_code} just took over. They have 90 days to establish their new tech stack."

    slack_payload = {
        "attachments": [
            {
                "color": color,
                "blocks": [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": title_text,
                            "emoji": True
                        }
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Firm:* {firm_name} (FRN: {frn})\n*Executive:* {name} (IRN: {irn})\n*Role:* {function_code} - {intel.get('role', full_title)}\n*Target Buyer:* {intel['buyer']}"
                        }
                    },
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": f"⚡ *SMF Watch Insight* | {action_text}"
                            }
                        ]
                    }
                ]
            }
        ]
    }

    if SLACK_WEBHOOK_URL:
        requests.post(
            SLACK_WEBHOOK_URL, 
            data=json.dumps(slack_payload),
            headers={'Content-Type': 'application/json'}
        )
        print(f"   -> 📨 Slack alert routed to {intel['buyer']} team for {name} ({function_code})!")
    else:
        print("   -> ⚠️ Skipping Slack alert: Webhook URL not set.")

def run_production_engine():
    print("\n" + "="*50)
    print("🚀 RUNNING SMF WATCH (EXECUTIVE TIER)")
    print("="*50 + "\n")

    conn = setup_database()
    cursor = conn.cursor()

    cursor.execute("SELECT irn, name, firm_name, function_code, full_title FROM tracked_executives")
    yesterday_db = {row[0]: {"name": row[1], "firm_name": row[2], "function_code": row[3], "full_title": row[4]} for row in cursor.fetchall()}

    print(f"📊 Memory: Found {len(yesterday_db)} valid executives tracked in database.")

    # We now need to collect all live data across all our target firms
    all_today_api_data = {}
    
    for frn in TARGET_FRNS:
        firm_roster = fetch_executive_roster(frn)
        all_today_api_data.update(firm_roster)
        time.sleep(1) # Be polite between firms

    print(f"📥 Live: Pulled a total of {len(all_today_api_data)} active executives with verified SMF/CF roles across all target firms.")

    print("⚡ Running executive delta comparison...")
    new_additions = 0
    departures = 0
    today_date = datetime.now().strftime('%Y-%m-%d')

    for irn, current_record in all_today_api_data.items():
        if irn not in yesterday_db:
            print(f"✨ NEW APPOINTMENT: {current_record['name']} joined as {current_record['function_code']} at {current_record['firm_name']}")
            # We must figure out which FRN this person belongs to for the slack alert. 
            # We can reverse look this up, or just pass the firm_name.
            send_slack_alert(irn, current_record['name'], current_record['firm_name'], current_record['function_code'], current_record['full_title'], "APPOINTMENT", "Multiple")
            
            cursor.execute('''
                INSERT INTO tracked_executives (irn, name, firm_name, function_code, full_title, last_checked)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (irn, current_record['name'], current_record['firm_name'], current_record['function_code'], current_record['full_title'], today_date))
            new_additions += 1
        else:
            cursor.execute('UPDATE tracked_executives SET last_checked = ? WHERE irn = ?', (today_date, irn))

    for irn, past_record in yesterday_db.items():
        if irn not in all_today_api_data:
            print(f"🚨 DEPARTURE TRIGGER: {past_record['name']} ({past_record['function_code']}) left {past_record['firm_name']}!")
            send_slack_alert(irn, past_record['name'], past_record['firm_name'], past_record['function_code'], past_record['full_title'], "DEPARTURE", "Multiple")
            cursor.execute('DELETE FROM tracked_executives WHERE irn = ?', (irn,))
            departures += 1

    conn.commit()
    conn.close()

    print("\n" + "="*50)
    print("✅ ENGINE RUN COMPLETE")
    print(f"   -> Processed {new_additions} new appointments.")
    print(f"   -> Processed {departures} executive departures.")
    print("="*50 + "\n")

if __name__ == "__main__":
    run_production_engine()
