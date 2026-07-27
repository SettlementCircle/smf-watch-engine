import sqlite3
import requests
import json
import time
from datetime import datetime
import os

# Python looks for these in Render's secure vault (or uses fallbacks for local testing)
FCA_EMAIL = os.environ.get("FCA_EMAIL") or "dc@settlementcircle.com"
FCA_KEY = os.environ.get("FCA_KEY") or "5828c39ebaed901e3695144dcc63e136"
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")

TARGET_FRN = "122702" # Barclays Bank PLC

def setup_database():
    """Upgraded database schema to track individual executives instead of firms."""
    print("⚙️ Connecting to local database (live_smf_watch.db)...")
    conn = sqlite3.connect('live_smf_watch.db')
    cursor = conn.cursor()
    
    # We now track by IRN (Individual Reference Number)
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

def fetch_executive_roster():
    """Extracts all regulated individuals and makes secondary calls to get their specific SMF roles."""
    print(f"🌐 Fetching live Directory Persons roster for FRN: {TARGET_FRN}...")
    
    headers = {
        'X-Auth-Email': FCA_EMAIL,
        'X-Auth-Key': FCA_KEY,
        'Content-Type': 'application/json'
    }
    
    # Step 1: Get the list of all individuals at the firm
    roster_url = f"https://register.fca.org.uk/services/V0.1/Firm/{TARGET_FRN}/Individuals"
    response = requests.get(roster_url, headers=headers)
    
    if response.status_code != 200:
        print(f"❌ API Error fetching roster: {response.status_code}")
        return {}

    raw_individuals = response.json().get('Data', [])
    print(f"✅ Found {len(raw_individuals)} individuals. Extracting SMF roles (this takes a moment)...")
    
    today_roster = {}
    
    # Step 2: Loop through them to get their specific roles
    for person in raw_individuals:
        irn = person.get('IRN')
        name = person.get('Name')
        
        if not irn or irn == 'N/A':
            continue
            
        # We skip the main profile and jump straight to the /CF endpoint to save time!
        cf_url = f"https://register.fca.org.uk/services/V0.1/Individuals/{irn}/CF"
        
        try:
            cf_response = requests.get(cf_url, headers=headers)
            if cf_response.status_code == 200:
                cf_data = cf_response.json().get('Data')
                
                # If they have active roles, extract the first one
                if cf_data and len(cf_data) > 0:
                    current_roles = cf_data[0].get('Current', {})
                    
                    if current_roles:
                        # Grab the first role available in the dictionary
                        first_role_key = list(current_roles.keys())[0] 
                        role_details = current_roles[first_role_key]
                        
                        raw_role_name = role_details.get('Name', '')
                        firm_name = role_details.get('Firm Name', 'Unknown Firm')
                        function_code = raw_role_name.split(' ')[0] # turns "SMF2 Chief..." into "SMF2"
                        
                        # Save them to our daily roster dictionary
                        today_roster[irn] = {
                            "name": name,
                            "firm_name": firm_name,
                            "function_code": function_code,
                            "full_title": raw_role_name
                        }
            
            # 🚦 Crucial: Be polite to government servers so we don't get blocked
            time.sleep(0.3)
            
        except Exception as e:
            print(f"⚠️ Failed to fetch CF for {irn}: {e}")
            
    return today_roster

def send_slack_alert(irn, name, firm_name, function_code, full_title, event_type):
    """Fires a specialized Slack message for executive departures."""
    
    if event_type == "DEPARTURE":
        title_text = "🚨 LEAD ALERT: SMF Departure Detected"
        color = "#e11d48" # Red
        action_text = f"The {function_code} seat is now vacant or transitioning. Prime time to pitch!"
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
                            "text": f"*Firm:* {firm_name} (FRN: {TARGET_FRN})\n*Executive:* {name} (IRN: {irn})\n*Function Code:* {function_code}\n*Full Title:* {full_title}"
                        }
                    },
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": f"⚡ *SMF Watch Signal* | {action_text}"
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
        print(f"   -> 📨 Slack alert routed to sales team for {name} ({function_code})!")
    else:
        print("   -> ⚠️ Skipping Slack alert: Webhook URL not set in environment.")

def run_production_engine():
    print("\n" + "="*50)
    print("🚀 RUNNING SMF WATCH (EXECUTIVE TIER)")
    print("="*50 + "\n")

    conn = setup_database()
    cursor = conn.cursor()

    # 1. Fetch Yesterday's Roster from the Database
    cursor.execute("SELECT irn, name, firm_name, function_code, full_title FROM tracked_executives")
    db_records = cursor.fetchall()
    
    yesterday_db = {}
    for row in db_records:
        irn, name, firm_name, function_code, full_title = row
        yesterday_db[irn] = {
            "name": name,
            "firm_name": firm_name,
            "function_code": function_code,
            "full_title": full_title
        }

    print(f"📊 Memory: Found {len(yesterday_db)} executives tracked in database.")

    # 2. Fetch Today's Roster from the API
    today_api_data = fetch_executive_roster()
    print(f"📥 Live: Pulled {len(today_api_data)} active executives with SMF roles.")

    # 3. THE DELTA ENGINE
    print("⚡ Running executive delta comparison...")
    
    new_additions = 0
    departures = 0
    today_date = datetime.now().strftime('%Y-%m-%d')

    # Check for NEW hires (In today's API, but not in yesterday's DB)
    for irn, current_record in today_api_data.items():
        if irn not in yesterday_db:
            print(f"✨ NEW APPOINTMENT: {current_record['name']} joined as {current_record['function_code']}")
            send_slack_alert(irn, current_record['name'], current_record['firm_name'], current_record['function_code'], current_record['full_title'], "APPOINTMENT")
            
            cursor.execute('''
                INSERT INTO tracked_executives (irn, name, firm_name, function_code, full_title, last_checked)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (irn, current_record['name'], current_record['firm_name'], current_record['function_code'], current_record['full_title'], today_date))
            new_additions += 1
        else:
            # They are still there, just update the 'last_checked' date
            cursor.execute('''
                UPDATE tracked_executives 
                SET last_checked = ? WHERE irn = ?
            ''', (today_date, irn))

    # Check for DEPARTURES (In yesterday's DB, but missing from today's API!)
    for irn, past_record in yesterday_db.items():
        if irn not in today_api_data:
            print(f"🚨 DEPARTURE TRIGGER: {past_record['name']} ({past_record['function_code']}) left the firm!")
            send_slack_alert(irn, past_record['name'], past_record['firm_name'], past_record['function_code'], past_record['full_title'], "DEPARTURE")
            
            # Remove them from our active tracking database so we don't alert again tomorrow
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
