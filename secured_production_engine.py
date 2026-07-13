import sqlite3
import requests
import json
from datetime import datetime
import os # NEW: This allows Python to read secret environment variables!

# ==========================================
# 🔒 SECURE CREDENTIALS (No longer hardcoded!)
# ==========================================
# Python will now look for these in Render's secure vault
FCA_EMAIL = os.environ.get("FCA_EMAIL")
FCA_KEY = os.environ.get("FCA_KEY")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")

TARGET_FRN = "122702" # Barclays Bank PLC

def setup_database():
    print("⚙️ Connecting to local database (live_smf_watch.db)...")
    conn = sqlite3.connect('live_smf_watch.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tracked_entities (
            entity_name TEXT PRIMARY KEY,
            status TEXT,
            last_checked TEXT
        )
    ''')
    conn.commit()
    return conn

def fetch_live_fca_data():
    print(f"🌐 Fetching live data from FCA for Firm FRN: {TARGET_FRN}...")
    url = f"https://register.fca.org.uk/services/V0.1/Firm/{TARGET_FRN}"
    
    headers = {
        'X-Auth-Email': FCA_EMAIL,
        'X-Auth-Key': FCA_KEY,
        'Content-Type': 'application/json'
    }
    
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        print("✅ Live FCA data successfully retrieved!")
        data = response.json().get('Data', [])
        formatted_data = []
        for item in data:
            formatted_data.append({
                "Name": item.get("Organization Name", f"Firm {TARGET_FRN}"),
                "Status": item.get("Status", "Unknown")
            })
        return formatted_data
    else:
        print(f"❌ API Error: Received status code {response.status_code}")
        print(f"🔍 Error Details: {response.text}")
        return []

def send_slack_alert(entity_name, old_status, new_status):
    slack_payload = {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "🚨 FCA Status Change Detected",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Firm FRN:* {TARGET_FRN}\n*Entity:* {entity_name}\n*Previous Status:* {old_status}\n*New Status:* {new_status}"
                }
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"⚡ System checked on {datetime.now().strftime('%Y-%m-%d %H:%M')}"
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
        print(f"   -> 📨 Slack alert sent for {entity_name}!")
    else:
        print("   -> ⚠️ Skipping Slack alert: Webhook URL not set in environment.")

def run_production_engine():
    print("\n" + "="*50)
    print("🚀 RUNNING SMF WATCH PRODUCTION ENGINE")
    print("="*50 + "\n")

    conn = setup_database()
    cursor = conn.cursor()

    cursor.execute("SELECT entity_name, status FROM tracked_entities")
    db_records = cursor.fetchall()
    
    yesterday_db = {}
    for row in db_records:
        entity_name, status = row
        yesterday_db[entity_name] = {"status": status}

    print(f"📊 Found {len(yesterday_db)} entities currently tracked in database.")

    live_api_data = fetch_live_fca_data()

    print("⚡ Running delta comparison...")
    
    new_additions = 0
    status_changes = 0

    for item in live_api_data:
        entity_name = item.get("Name")
        current_status = item.get("Status")
        today_date = datetime.now().strftime('%Y-%m-%d')

        if not entity_name:
            continue

        if entity_name not in yesterday_db:
            cursor.execute('''
                INSERT INTO tracked_entities (entity_name, status, last_checked)
                VALUES (?, ?, ?)
            ''', (entity_name, current_status, today_date))
            new_additions += 1
            
        else:
            past_status = yesterday_db[entity_name]["status"]
            
            if past_status != current_status:
                print(f"🚨 CHANGE DETECTED: {entity_name} went from '{past_status}' to '{current_status}'")
                send_slack_alert(entity_name, past_status, current_status)
                
                cursor.execute('''
                    UPDATE tracked_entities 
                    SET status = ?, last_checked = ?
                    WHERE entity_name = ?
                ''', (current_status, today_date, entity_name))
                status_changes += 1
            else:
                cursor.execute('''
                    UPDATE tracked_entities 
                    SET last_checked = ?
                    WHERE entity_name = ?
                ''', (today_date, entity_name))

    conn.commit()
    conn.close()

    print("\n" + "="*50)
    print("✅ ENGINE RUN COMPLETE")
    print(f"   -> Added {new_additions} new entities to tracking.")
    print(f"   -> Detected {status_changes} status changes.")
    print("="*50 + "\n")

if __name__ == "__main__":
    run_production_engine()