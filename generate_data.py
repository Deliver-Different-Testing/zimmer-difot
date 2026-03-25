#!/usr/bin/env python3
"""
Generate Zimmer DIFOT dashboard data.

Rules:
- Count = parent jobs (rel 19) — one row per parent
- DIFOT measured using EARLIEST child with DEL suffix ucjbComplTime
- If no DEL child exists, fall back to parent ucjbComplTime
- Only Morning Delivery (speed 164, deadline 10am) and Morning Express (speed 165, deadline 8am)
- Site name from: ucjbTo → tucSuburb.ucsuID → tucSuburb.SiteID → tblSite.Name
"""
import pymssql
import json
from datetime import datetime
from collections import defaultdict

DB_CONFIG = {
    'server': 'urgent-couriers-sql-server-urgent-prod.c9wsc8ywswov.ap-southeast-2.rds.amazonaws.com',
    'port': 1433,
    'user': 'admin',
    'password': 'Y3sF0Z9*3Z~WA2yvp$0roJzLGt?f',
    'database': 'Despatch-Urgent-Prod'
}

def main():
    conn = pymssql.connect(**DB_CONFIG)
    cur = conn.cursor(as_dict=True)

    # Step 1: Build site lookup
    print("Building site lookup...")
    cur.execute("""
        SELECT s.ucsuID, si.Name as siteName
        FROM tucSuburb s
        JOIN tblSite si ON s.SiteID = si.SiteID
    """)
    site_map = {r['ucsuID']: r['siteName'] for r in cur.fetchall()}
    print(f"  {len(site_map)} suburbs mapped to sites")

    # Step 2: Get parent jobs — only speed 164 and 165
    print("Fetching Morning Delivery/Express parent jobs...")
    cur.execute("""
        SELECT 
            ucjbID, ucjbNumber, ucjbDate, ucjbTime, ucjbComplTime,
            ucjbSpeed, ucjbStatus, ucjbQty,
            ucjbTo, ucjbToAddr,
            ucjbClientID, ucjbClientCode
        FROM tucJobArchive
        WHERE ucjbClientCode = 'ZIMME'
          AND ParentID = ucjbID
          AND JobRelationshipTypeID = 19
          AND ucjbSpeed IN (164, 165)
          AND ucjbJobDone = 1
          AND ucjbVoid = 0
          AND ucjbDate >= DATEADD(MONTH, -12, GETDATE())
        ORDER BY ucjbDate DESC
    """)
    rows = cur.fetchall()
    print(f"  Found {len(rows)} parent jobs")

    # Step 3: Get earliest DEL-suffix child completion time per parent
    print("Fetching earliest DEL child completion times...")
    parent_ids = [r['ucjbID'] for r in rows]
    del_map = {}  # parent_id -> earliest DEL compltime

    # Batch in chunks to avoid huge IN clause
    chunk_size = 500
    for i in range(0, len(parent_ids), chunk_size):
        chunk = parent_ids[i:i+chunk_size]
        placeholders = ','.join(['%s'] * len(chunk))
        cur.execute(f"""
            SELECT ParentID, MIN(ucjbComplTime) as delComplTime
            FROM tucJobArchive
            WHERE ParentID IN ({placeholders})
              AND ucjbID != ParentID
              AND ucjbNumber LIKE '%%DEL%%'
              AND ucjbComplTime IS NOT NULL
            GROUP BY ParentID
        """, tuple(chunk))
        for r in cur.fetchall():
            del_map[r['ParentID']] = r['delComplTime']
    
    print(f"  {len(del_map)} parents have DEL children")

    # Step 4: Speed definitions
    cur.execute("SELECT ucjtID, ucjtName FROM tucJobType WHERE ucjtID IN (164, 165)")
    speeds = {r['ucjtID']: r['ucjtName'] for r in cur.fetchall()}

    # Step 5: Client name
    cur.execute("SELECT ucclName FROM tucClient WHERE ucclID = 14927")
    client = cur.fetchone()
    client_name = client['ucclName'] if client else 'Zimmer Biomet'

    # Step 6: Build jobs
    jobs = []
    del_used = 0
    parent_used = 0

    for r in rows:
        del_time = del_map.get(r['ucjbID'])
        if del_time:
            actual_compl = del_time
            del_used += 1
        else:
            actual_compl = r['ucjbComplTime']
            parent_used += 1

        if not actual_compl:
            continue

        speed_name = speeds.get(r['ucjbSpeed'], f"Speed {r['ucjbSpeed']}")
        suburb_id = r['ucjbTo']
        site_name = site_map.get(suburb_id, f"Unknown ({suburb_id})")
        to_addr = r['ucjbToAddr'] or ''

        def fmt(dt):
            if isinstance(dt, datetime):
                return dt.strftime('%Y-%m-%dT%H:%M:%S')
            return str(dt)[:19] if dt else None

        jobs.append({
            'jobNumber': r['ucjbNumber'],
            'bookDate': fmt(r['ucjbDate'])[:10],
            'date': fmt(r['ucjbDate'])[:10],
            'speedId': r['ucjbSpeed'],
            'speed': speed_name,
            'completedAt': fmt(actual_compl),
            'parentComplTime': fmt(r['ucjbComplTime']),
            'city': site_name,
            'address': to_addr,
            'status': 'Completed',
            'done': True,
            'clientId': r['ucjbClientID'],
            'clientCode': r['ucjbClientCode'],
            'clientName': client_name,
            'boxes': r['ucjbQty'] or 0,
        })

    output = {'14927': jobs}
    with open('/data/.openclaw/workspace/zimmer-difot/data.json', 'w') as f:
        json.dump(output, f)

    # Update clients.json
    with open('/data/.openclaw/workspace/zimmer-difot/clients.json') as f:
        clients = json.load(f)
    for c in clients:
        if str(c['id']) == '14927':
            c['jobs'] = len(jobs)
    with open('/data/.openclaw/workspace/zimmer-difot/clients.json', 'w') as f:
        json.dump(clients, f)

    total = del_used + parent_used
    print(f"\nUsing earliest DEL child: {del_used} ({del_used*100//total}%)")
    print(f"Using parent (no DEL child): {parent_used}")
    print(f"Saved {len(jobs)} jobs to data.json")

    # DIFOT check
    on_time = 0
    for j in jobs:
        ct = datetime.fromisoformat(j['completedAt'])
        deadline = 8 if j['speedId'] == 165 else 10
        if ct.hour < deadline:
            on_time += 1
    print(f"DIFOT: {on_time}/{len(jobs)} = {on_time*100//len(jobs)}%")

    conn.close()

if __name__ == '__main__':
    main()
