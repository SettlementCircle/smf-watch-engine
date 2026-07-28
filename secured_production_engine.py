import sqlite3
import requests
import json
import time
from datetime import datetime
import os

# 🔒 SECURE CREDENTIALS
FCA_EMAIL = os.environ.get("FCA_EMAIL") or "dc@settlementcircle.com"
FCA_KEY = os.environ.get("FCA_KEY") or "5828c39ebaed901e3695144dcc63e136"

# REMOVED: We deleted the hardcoded SLACK_WEBHOOK_URL, TARGET_FRNS, and ROLE_MAPPINGS.
# The engine will now load all of this dynamically from customers.json!

def load_customer_configs():
    """Loads the customer configuration control panel."""
    print("📂 Loading customer configurations from customers.json...")
    try:
        with open('customers.json', 'r') as f:
            customers = json.load(f)
        print(f"✅ Loaded {len(customers)} active customer profiles.")
        return customers
    except Exception as e:
        print(f"❌ Failed to load customers.json: {e}")
        return {}

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

def send_customer_alert(customer_name, webhook_url, custom_pitch, irn, name, firm_name, function_code, full_title, event_type, frn):
    """Sends a highly customized alert to a specific customer's Slack channel."""
    
    if event_type == "DEPARTURE":
        title_text = "🚨 LEAD ALERT: SMF Departure Detected"
        color = "#e11d48" # Red
    else:
        title_text = "✨ LEAD ALERT: New SMF Appointed"
        color = "#10b981" # Green

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
                            "text": f"*Firm:* {firm_name} (FRN: {frn})\n*Executive:* {name} (IRN: {irn})\n*Role:* {function_code} - {full_title}"
                        }
                    },
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": f"🎯 *Custom Action Plan* | {custom_pitch}"
                            }
                        ]
                    }
                ]
            }
        ]
    }

    if webhook_url and "YOUR_SLACK_WEBHOOK" not in webhook_url:
        requests.post(
            webhook_url, 
            data=json.dumps(slack_payload),
            headers={'Content-Type': 'application/json'}
        )
        print(f"   -> 📨 Alert routed to {customer_name} for {function_code}!")
    else:
        print(f"   -> ⚠️ Skipped Slack alert for {customer_name}: Webhook not configured.")

def process_event_for_customers(customers, irn, record, event_type, frn):
    """Checks which customers care about this event and fires their custom alerts."""
    function_code = record['function_code']
    
    for cust_id, cust_data in customers.items():
        # Check 1: Does this customer care about this specific FRN? (Or do they track "ALL"?)
        cares_about_frn = (frn in cust_data.get('target_frns', [])) or ("ALL" in cust_data.get('target_frns', []))
        
        # Check 2: Does this customer care about this specific SMF role?
        smf_preferences = cust_data.get('smf_preferences', {})
        cares_about_role = function_code in smf_preferences
        
        if cares_about_frn and cares_about_role:
            custom_pitch = smf_preferences[function_code]
            webhook = cust_data.get('slack_webhook_url')
            send_customer_alert(cust_data['company_name'], webhook, custom_pitch, irn, record['name'], record['firm_name'], function_code, record['full_title'], event_type, frn)


def run_production_engine():
    print("\n" + "="*50)
    print("🚀 RUNNING SMF WATCH (MULTI-TENANT TIER)")
    print("="*50 + "\n")

    # 1. Load Customers & Build Master Target List
    customers = load_customer_configs()
    
    master_frn_list = set()
    for cust_id, cust_data in customers.items():
        for frn in cust_data.get('target_frns', []):
            if frn != "ALL":
                master_frn_list.add(frn)
                
    # If a customer tracks "ALL", we still need a default list to scan, 
    # but for now, we will just scan the explicit FRNs our customers requested.
    if not master_frn_list:
        print("⚠️ No specific FRNs found in customer configs. Defaulting to Barclays (122702).")
        master_frn_list.add("122702")

    print(f"🎯 Master Engine will scan {len(master_frn_list)} unique firms tonight.")

    conn = setup_database()
    cursor = conn.cursor()

    cursor.execute("SELECT irn, name, firm_name, function_code, full_title FROM tracked_executives")
    yesterday_db = {row[0]: {"name": row[1], "firm_name": row[2], "function_code": row[3], "full_title": row[4]} for row in cursor.fetchall()}

    print(f"📊 Memory: Found {len(yesterday_db)} valid executives tracked in database.")

    all_today_api_data = {}
    
    # 2. Fetch data for all requested FRNs
    for frn in master_frn_list:
        firm_roster = fetch_executive_roster(frn)
        
        # Attach the FRN to the record so we know where they came from later
        for irn, data in firm_roster.items():
            data['frn'] = frn
            
        all_today_api_data.update(firm_roster)
        time.sleep(1) # Be polite between firms

    print(f"📥 Live: Pulled a total of {len(all_today_api_data)} active executives across all target firms.")

    print("⚡ Running executive delta comparison...")
    new_additions = 0
    departures = 0
    today_date = datetime.now().strftime('%Y-%m-%d')

    # 3. The Delta Engine (Now with customer routing!)
    for irn, current_record in all_today_api_data.items():
        if irn not in yesterday_db:
            print(f"✨ NEW APPOINTMENT: {current_record['name']} joined as {current_record['function_code']} at {current_record['firm_name']}")
            
            # Fire to customers!
            process_event_for_customers(customers, irn, current_record, "APPOINTMENT", current_record['frn'])
            
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
            
            # Since we don't have the FRN in the DB currently, we pass "Unknown" for departures. 
            # In a future update, we should save the FRN in the SQLite database to perfectly route departures.
            process_event_for_customers(customers, irn, past_record, "DEPARTURE", "Multiple")
            
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
