#!/usr/bin/env python3
"""End-to-end test through Vite proxy - simulating what the browser does"""
import os, sys, json, urllib.request

HOST = 'http://localhost:3000'
API_BASE = f'{HOST}/api/v1'

# Step 1: Login through proxy
login_data = json.dumps({
    'username': 'admin',
    'password': 'Dbmonitor@123'
}).encode('utf-8')

req = urllib.request.Request(
    f'{API_BASE}/auth/login/',
    data=login_data,
    headers={'Content-Type': 'application/json'},
    method='POST'
)
try:
    resp = urllib.request.urlopen(req, timeout=10)
    body = json.loads(resp.read())
    print(f"[LOGIN] status={resp.status}")
    token = body.get('token', '')
    print(f"[LOGIN] token={'OK' if token else 'NONE'} (len={len(token)})")
except Exception as e:
    print(f"[LOGIN] FAILED: {e}")
    # Try older login endpoint
    req2 = urllib.request.Request(
        f'{API_BASE}/login/',
        data=login_data,
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    try:
        resp = urllib.request.urlopen(req2, timeout=10)
        body = json.loads(resp.read())
        print(f"[LOGIN/old] status={resp.status}")
        token = body.get('token', '')
        print(f"[LOGIN/old] token={'OK' if token else 'NONE'}")
    except Exception as e2:
        print(f"[LOGIN/old] FAILED: {e2}")
        sys.exit(1)

# Step 2: Get databases
req3 = urllib.request.Request(
    f'{API_BASE}/databases/',
    headers={'Authorization': f'Bearer {token}'},
    method='GET'
)
resp3 = urllib.request.urlopen(req3, timeout=10)
dbs = json.loads(resp3.read())
print(f"\n[DATABASES] count={len(dbs) if isinstance(dbs, list) else 0}")
for db in dbs if isinstance(dbs, list) else []:
    print(f"  ID={db.get('id')}: {db.get('name')} ({db.get('db_type')})")

# Step 3: Test tablespace metrics through proxy
print("\n[TABLESPACE PROXY TEST]")
for cfg_id in [3]:
    url = f'{API_BASE}/databases/{cfg_id}/metrics/?metric=tablespace_SYSTEM_used_pct&time=1h&_t=1'
    req4 = urllib.request.Request(
        url,
        headers={'Authorization': f'Bearer {token}'},
        method='GET'
    )
    try:
        resp4 = urllib.request.urlopen(req4, timeout=15)
        data4 = json.loads(resp4.read())
        metrics = data4.get('metrics', []) if isinstance(data4, dict) else (data4 if isinstance(data4, list) else [])
        print(f"  config_id={cfg_id}: count={len(metrics)}")
        if metrics:
            print(f"    First: ts={metrics[0].get('timestamp','?')[:19]}, val={metrics[0].get('value','?')}")
    except Exception as e:
        print(f"  config_id={cfg_id}: FAILED - {e}")

# Step 4: Test wait_event metrics through proxy
print("\n[WAIT_EVENT PROXY TEST]")
for cfg_id in [3]:
    url = f'{API_BASE}/databases/{cfg_id}/metrics/?metric=wait_event_db file sequential read&time=24h&_t=1'
    req5 = urllib.request.Request(
        url,
        headers={'Authorization': f'Bearer {token}'},
        method='GET'
    )
    try:
        resp5 = urllib.request.urlopen(req5, timeout=15)
        data5 = json.loads(resp5.read())
        metrics = data5.get('metrics', []) if isinstance(data5, dict) else (data5 if isinstance(data5, list) else [])
        print(f"  config_id={cfg_id}: count={len(metrics)}")
        if metrics:
            print(f"    First: ts={metrics[0].get('timestamp','?')[:19]}, val={metrics[0].get('value','?')}")
    except Exception as e:
        print(f"  config_id={cfg_id}: FAILED - {e}")

print("\n[DONE]")
