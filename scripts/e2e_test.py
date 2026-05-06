#!/usr/bin/env python3
import requests
import time
import json
import sseclient

PIPELINE_URL = "http://localhost:8000"
VULNERABLE_REPO = "https://github.com/mock/intentionally-vulnerable-repo"

def test_e2e_flow():
    print(f"1. Initiating scan against {VULNERABLE_REPO}...")
    
    # Start Scan
    try:
        res = requests.post(f"{PIPELINE_URL}/scan", json={
            "trigger": VULNERABLE_REPO,
            "target_branch": "main"
        }, timeout=5)
        res.raise_for_status()
        scan_data = res.json()
        scan_id = scan_data["scan_id"]
        print(f"   [OK] Scan initialized. ID: {scan_id}")
    except Exception as e:
        print(f"   [FAIL] Failed to initiate scan: {e}")
        return

    print("2. Connecting to SSE stream...")
    stream_url = f"{PIPELINE_URL}/scan/{scan_id}/stream"
    
    try:
        response = requests.get(stream_url, stream=True, timeout=10)
        client = sseclient.SSEClient(response)
        
        for event in client.events():
            data = json.loads(event.data)
            print(f"   -> [SSE Event] {event.event} from {data.get('agent', 'unknown')}")
            if event.event == "pipeline_complete":
                print(f"   [OK] Pipeline completed! PR URL: {data.get('data', {}).get('pr_url')}")
                break
    except Exception as e:
        print(f"   [FAIL] SSE Stream failure: {e}")
        return

    print("3. Validating State API...")
    try:
        res = requests.get(f"{PIPELINE_URL}/scan/{scan_id}")
        res.raise_for_status()
        state = res.json()
        fast_count = len(state.get("fast_findings", []))
        deep_count = len(state.get("deep_findings", []))
        print(f"   [OK] State verified. Fast Found: {fast_count}, Deep Found: {deep_count}")
    except Exception as e:
        print(f"   [FAIL] State API failure: {e}")

    print("4. Fetching Downloadable Audit Report...")
    try:
        res = requests.get(f"{PIPELINE_URL}/scan/{scan_id}/report")
        res.raise_for_status()
        print(f"   [OK] Markdown Report Size: {len(res.text)} bytes")
    except Exception as e:
        print(f"   [FAIL] Report API failure: {e}")

    print("\n[SUCCESS] End-to-End Validation Complete!")

if __name__ == "__main__":
    # Ensure sseclient-py is installed before running
    # pip install requests sseclient-py
    test_e2e_flow()
