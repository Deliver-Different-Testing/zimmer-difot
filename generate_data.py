#!/usr/bin/env python3
"""
Generate Zimmer DIFOT dashboard data using CHILD DEL completion times.
"""
import pymssql
import json
from datetime import datetime

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

    # Step 1: Get child DEL completion times grouped by parent
    print("Fetching child DEL completion times...")
    cur.execute("""
        SELECT ParentID, MIN(ucjbComplTime) as childComplTime, COUNT(*) as childCount
        FROM tucJobArchive
        WHERE ucjbClientCode = 'ZIMME' AND ParentID != ucjbID AND ucjbComplTime IS NOT NULL
          AND ucjbDate >= DATEADD(MONTH, -12, GETDATE())
        GROUP BY ParentID
    """)
    child_map = {}
    for r in cur.fetchall():
        child_map[r['ParentID']] = (r['childComplTime'], r['childCount'])
    print(f"  Found {len(child_map)} parents with children")

    # Step 2: Get parent jobs
    print("Fetching parent jobs...")
    cur.execute("""
        SELECT 
            ucjbID, ucjbNumber, ucjbDate, ucjbTime, ucjbComplTime,
            ucjbSpeed, ucjbStatus, ucjbQty,
            ucjbTo, ucjbToAddr,
            ucjbClientID, ucjbClientCode,
            ucjbPODName, ucjbOpID
        FROM tucJobArchive
        WHERE ucjbClientCode = 'ZIMME'
          AND ParentID = ucjbID
          AND JobRelationshipTypeID = 19
          AND ucjbJobDone = 1
          AND ucjbVoid = 0
          AND ucjbDate >= DATEADD(MONTH, -12, GETDATE())
        ORDER BY ucjbDate DESC
    """)
    rows = cur.fetchall()
    print(f"  Found {len(rows)} parent jobs")

    # Step 3: Speed definitions
    cur.execute("SELECT ucjtID, ucjtName, Minutes, DeliveryMargin FROM tucJobType")
    speeds = {r['ucjtID']: r for r in cur.fetchall()}

    # Step 4: Client name
    cur.execute("SELECT ucclName FROM tucClient WHERE ucclID = 14927")
    client = cur.fetchone()
    client_name = client['ucclName'] if client else 'Zimmer Biomet'

    # Step 5: Build jobs
    jobs = []
    child_used = 0
    parent_used = 0
    
    for r in rows:
        child_data = child_map.get(r['ucjbID'])
        if child_data:
            actual_compl = child_data[0]
            child_count = child_data[1]
            child_used += 1
        else:
            actual_compl = r['ucjbComplTime']
            child_count = 0
            parent_used += 1
        
        if not actual_compl:
            continue

        speed_info = speeds.get(r['ucjbSpeed'], {})
        speed_name = speed_info.get('ucjtName', f"Speed {r['ucjbSpeed']}")

        to_addr = r['ucjbToAddr'] or ''
        suburb = to_addr.split(',')[-1].strip() if ',' in to_addr else ''

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
            'completedAtSource': 'child_del' if child_data else 'parent',
            'parentComplTime': fmt(r['ucjbComplTime']),
            'city': r['ucjbTo'] or '',
            'address': r['ucjbTo'] or '',
            'suburb': suburb,
            'status': 'Completed',
            'done': True,
            'clientId': r['ucjbClientID'],
            'clientCode': r['ucjbClientCode'],
            'clientName': client_name,
            'boxes': r['ucjbQty'] or 0,
            'childCount': child_count
        })

    print(f"\nUsing child DEL time: {child_used} ({child_used*100//(child_used+parent_used) if (child_used+parent_used) else 0}%)")
    print(f"Using parent time (no children): {parent_used}")

    output = {'14927': jobs}
    with open('/data/.openclaw/workspace/zimmer-difot/data.json', 'w') as f:
        json.dump(output, f)
    
    print(f"Saved {len(jobs)} jobs to data.json")
    conn.close()

if __name__ == '__main__':
    main()
