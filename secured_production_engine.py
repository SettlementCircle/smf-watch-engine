
import psycopg2
import requests
import json
import time
import os
from datetime import datetime

# 🔒 SECURE CREDENTIALS (Pulled from Render Environment Variables)
FCA_EMAIL = os.environ.get("FCA_EMAIL") or "dc@settlementcircle.com"
FCA_KEY = os.environ.get("FCA_KEY") or "5828c39ebaed901e3695144dcc63e136"
DATABASE_URL = os.environ.get("DATABASE_URL")

# STREAMING_CHUNK:Defining customer config helper functions
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

def save_customer_configs(customers):
    """Saves updated trial countdowns back to the JSON file."""
    try:
        with open('customers.json', 'w') as f:
            json.dump(customers, f, indent=4)
    except Exception as e:
        print(f"❌ Failed to save customers.json: {e}")

# STREAMING_CHUNK:Defining alert functions
def send_upsell_alert(customer_name, webhook_url):
    """Sends an automatic upgrade prompt when a trial expires."""
    slack_payload = {
        "attachments": [{"color": "#f59e0b", "blocks": [{"type": "header", "text": {"type": "plain_text", "text": "⏳ Your SMF Watch Trial Has Expired!", "emoji": True}}, {"type": "section", "text": {"type": "mrkdwn", "text": f"Hi {customer_name}! Your 2-Day free trial is now complete. To keep receiving live FCA alerts tomorrow morning, please activate your subscription."}}, {"type": "actions", "elements": [{"type": "button", "text": {"type": "plain_text", "text": "Upgrade to Pro (£199/mo)"}, "style": "primary", "url": "https://buy.stripe.com/4gM8wRaV41toeTY2NMfw400"}]}]}]
    }
    if webhook_url and webhook_url.startswith("http"):
        try:
            requests.post(webhook_url, data=json.dumps(slack_payload), headers={'Content-Type': 'application/json'})
            print(f"   -> 💸 Upsell alert successfully sent to {customer_name}!")
        except: pass

def send_customer_alert(customer_name, webhook_url, custom_pitch, irn, name, firm_name, function_code, full_title, event_type, frn):
    """Sends a highly customized alert to a specific customer's Slack channel."""
    color = "#e11d48" if event_type == "DEPARTURE" else "#10b981"
    title_text = "🚨 LEAD ALERT: SMF Departure Detected" if event_type == "DEPARTURE" else "✨ LEAD ALERT: New SMF Appointed"

    slack_payload = {
        "attachments": [{"color": color, "blocks": [{"type": "header", "text": {"type": "plain_text", "text": title_text, "emoji": True}}, {"type": "section", "text": {"type": "mrkdwn", "text": f"*Firm:* {firm_name} (FRN: {frn})\n*Executive:* {name} (IRN: {irn})\n*Role:* {function_code} - {full_title}"}}, {"type": "context", "elements": [{"type": "mrkdwn", "text": f"🎯 *Custom Action Plan* | {custom_pitch}"}]}]}]
    }

    if webhook_url and webhook_url.startswith("http"):
        try:
            requests.post(webhook_url, data=json.dumps(slack_payload), headers={'Content-Type': 'application/json'})
            print(f"   -> 📨 Alert routed to {customer_name} for {function_code}!")
        except Exception as e:
            print(f"   -> ❌ Failed to send alert to {customer_name}: {e}")
    else:
        print(f"   -> ⚠️ Skipped Slack alert for {customer_name}: Webhook not configured properly.")

# STREAMING_CHUNK:Configuring database connection
def setup_database():
    """Connects to Neon.tech PostgreSQL and initializes the table."""
    print("⚙️ Connecting to permanent cloud database (Neon.tech)...")
    conn = psycopg2.connect(DATABASE_URL)
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

# STREAMING_CHUNK:Defining executive fetch logic
def fetch_executive_roster(frn):
    """Fetches the executive roster for a specific FRN."""
    print(f"🌐 Fetching live Directory Persons roster for FRN: {frn}...")
    headers = {'X-Auth-Email': FCA_EMAIL, 'X-Auth-Key': FCA_KEY, 'Content-Type': 'application/json'}
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
        if not irn or irn == 'N/A': continue
            
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
                        raw_code = raw_role_name.split(' ')[0]
                        function_code = raw_code.replace('(1)', '').replace('(2)', '') 
                        
                        if function_code.startswith('SMF') or function_code.startswith('CF'):
                            today_roster[irn] = {"name": name, "firm_name": firm_name, "function_code": function_code, "full_title": raw_role_name}
            time.sleep(0.3)
        except Exception as e:
            print(f"⚠️ Failed to fetch CF for {irn}: {e}")
    return today_roster

# STREAMING_CHUNK:Defining event routing logic
def process_event_for_customers(customers, irn, record, event_type, frn):
    function_code = record['function_code']
    for cust_id, cust_data in customers.items():
        cares_about_frn = (frn in cust_data.get('target_frns', [])) or ("ALL" in cust_data.get('target_frns', []))
        smf_preferences = cust_data.get('smf_preferences', {})
        cares_about_role = (function_code in smf_preferences) or ("ALL" in smf_preferences)
        
        if cares_about_frn and cares_about_role:
            custom_pitch = smf_preferences.get(function_code, smf_preferences.get("ALL", "🚨 Universal Tracker: Executive change detected."))
            webhook = cust_data.get('slack_webhook_url')
            send_customer_alert(cust_data['company_name'], webhook, custom_pitch, irn, record['name'], record['firm_name'], function_code, record['full_title'], event_type, frn)

# STREAMING_CHUNK:Executing the production engine
def run_production_engine():
    print("\n" + "="*50 + "\n🚀 RUNNING SMF WATCH (POSTGRES CLOUD TIER)\n" + "="*50 + "\n")

    all_customers = load_customer_configs()
    active_customers = {k: v for k, v in all_customers.items() if v.get('account_status') in ['active_paid', 'trial'] and v.get('trial_runs_remaining', 0) >= 0}
    
    master_frn_list = set()
    for cust_id, cust_data in active_customers.items():
        for frn in cust_data.get('target_frns', []):
            if frn != "ALL": master_frn_list.add(frn)
    if not master_frn_list: master_frn_list.add("122702")

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
        for irn, data in firm_roster.items():
            data['frn'] = frn
        all_today_api_data.update(firm_roster)
        time.sleep(1)

    print(f"📥 Live: Pulled {len(all_today_api_data)} active executives. Running comparison...")
    
    new_additions = 0
    departures = 0
    today_date = datetime.now().strftime('%Y-%m-%d')

    # Compare
    for irn, current_record in all_today_api_data.items():
        if irn not in yesterday_db:
            print(f"✨ NEW APPOINTMENT: {current_record['name']} at {current_record['firm_name']}")
            process_event_for_customers(active_customers, irn, current_record, "APPOINTMENT", current_record['frn'])
            cursor.execute('INSERT INTO tracked_executives (irn, name, firm_name, function_code, full_title, last_checked) VALUES (%s, %s, %s, %s, %s, %s)', 
                           (irn, current_record['name'], current_record['firm_name'], current_record['function_code'], current_record['full_title'], today_date))
            new_additions += 1
        else:
            cursor.execute('UPDATE tracked_executives SET last_checked = %s WHERE irn = %s', (today_date, irn))

    for irn, past_record in yesterday_db.items():
        if irn not in all_today_api_data:
            print(f"🚨 DEPARTURE TRIGGER: {past_record['name']} ({past_record['function_code']}) left {past_record['firm_name']}!")
            process_event_for_customers(active_customers, irn, past_record, "DEPARTURE", "Multiple")
            cursor.execute('DELETE FROM tracked_executives WHERE irn = %s', (irn,))
            departures += 1

    conn.commit()
    cursor.close()
    conn.close()

    # Trial logic
    for cust_id, cust_data in all_customers.items():
        if cust_data.get('account_status') == 'trial' and cust_data.get('trial_runs_remaining', 0) > 0:
            cust_data['trial_runs_remaining'] -= 1
            if cust_data['trial_runs_remaining'] == 0:
                send_upsell_alert(cust_data['company_name'], cust_data.get('slack_webhook_url'))
    
    save_customer_configs(all_customers)
    print(f"\n✅ ENGINE RUN COMPLETE. New: {new_additions}, Departures: {departures}")

if __name__ == "__main__":
    run_production_engine()
