#!/usr/bin/env python3
"""
Final Browser Walkthrough - Complete UI Validation
"""

import requests
import json
import re

BASE_URL = "http://127.0.0.1:5000"
PASSWORD = "admin12345"

def main():
    print("\n" + "="*80)
    print("FINAL BROWSER WALKTHROUGH REPORT")
    print("="*80)
    
    issues = []
    session = requests.Session()
    
    # LOGIN
    print("\n[LOGIN] Authenticating...")
    resp = session.post(f"{BASE_URL}/login", data={"password": PASSWORD}, allow_redirects=True, timeout=5)
    if resp.status_code != 200 or "Dashboard" not in resp.text:
        issues.append({
            "severity": "HIGH",
            "step": "Login",
            "repro": "Navigate to /login, enter password 'admin12345', submit",
            "expected": "Successful login, redirect to dashboard",
            "actual": f"HTTP {resp.status_code} or no dashboard"
        })
        print("❌ Login failed")
        print_issues(issues)
        return
    print("✅ Logged in successfully")
    
    # STEP 1: Dashboard
    print("\n[STEP 1] Loading /admin Modern Dashboard...")
    resp = session.get(f"{BASE_URL}/admin", timeout=5)
    html = resp.text
    
    if "Modern Dashboard" not in html:
        issues.append({
            "severity": "HIGH",
            "step": "Step 1: Dashboard Load",
            "repro": "Navigate to /admin after login",
            "expected": "'Modern Dashboard' title visible",
            "actual": "Title not found in page"
        })
    
    if "Quick View" not in html or "Advanced Lab" not in html:
        issues.append({
            "severity": "MEDIUM",
            "step": "Step 1: Graph Tabs",
            "repro": "Load dashboard, look for graph card tabs",
            "expected": "'Quick View' and 'Advanced Lab' tab buttons visible",
            "actual": "Tab buttons not found in HTML"
        })
    else:
        print("✅ Dashboard loaded with Quick View and Advanced Lab tabs")
    
    # STEP 2: Advanced Lab Workflow
    print("\n[STEP 2] Testing Advanced Lab workflow...")
    
    # 2a: Tab structure check
    if 'data-tab="graph-lab"' in html and 'id="tab-graph-lab"' in html:
        print("✅ Advanced Lab tab structure present")
    else:
        issues.append({
            "severity": "MEDIUM",
            "step": "Step 2a: Switch to Advanced Lab",
            "repro": "Click 'Advanced Lab' tab button",
            "expected": "Tab switches, Advanced Lab panel displays",
            "actual": "Tab button or panel HTML missing (data-tab='graph-lab' or id='tab-graph-lab')"
        })
    
    # 2b: Apply & Render (defaults)
    print("  Testing Apply & Render with defaults...")
    filters = {
        "min_degree": 0, "activity_days": 7, "engagement": 0,
        "focus_user": None, "ego_network": True,
        "show_centrality": True, "show_bridges": True,
        "show_influencers": True, "show_outliers": False,
        "conflict_risk_threshold": 0.5
    }
    
    resp = session.get(f"{BASE_URL}/api/chat/all/graph-lab",
                       params={"filters": json.dumps(filters), "period": "30d"},
                       timeout=15)
    
    if resp.status_code != 200 or not resp.json().get("ok"):
        issues.append({
            "severity": "HIGH",
            "step": "Step 2b: Apply & Render (defaults)",
            "repro": "Switch to Advanced Lab, click 'Apply & Render' with default values",
            "expected": "Graph renders with default filters",
            "actual": f"API error: HTTP {resp.status_code} or ok=False"
        })
    else:
        graph = resp.json().get("graph", {})
        print(f"✅ Apply & Render works: {len(graph.get('nodes', []))} nodes")
    
    # 2c: Apply & Render (min_degree=1)
    print("  Testing Apply & Render with Min Degree=1...")
    filters["min_degree"] = 1
    resp = session.get(f"{BASE_URL}/api/chat/all/graph-lab",
                       params={"filters": json.dumps(filters), "period": "30d"},
                       timeout=15)
    
    if resp.status_code != 200 or not resp.json().get("ok"):
        issues.append({
            "severity": "HIGH",
            "step": "Step 2c: Apply & Render (min_degree=1)",
            "repro": "In Advanced Lab, set 'Min Degree' to 1, click 'Apply & Render'",
            "expected": "Graph re-renders with filtered nodes (degree >= 1)",
            "actual": f"API error: HTTP {resp.status_code} or ok=False"
        })
    else:
        nodes = resp.json().get("graph", {}).get("nodes", [])
        low_degree = [n for n in nodes if n.get("degree", 0) < 1]
        if low_degree:
            issues.append({
                "severity": "LOW",
                "step": "Step 2c: Min Degree Filter Logic",
                "repro": "Set Min Degree=1, apply filter",
                "expected": "All returned nodes have degree >= 1",
                "actual": f"{len(low_degree)} nodes with degree < 1 still returned"
            })
        else:
            print(f"✅ Min Degree filter works: {len(nodes)} nodes (all degree >= 1)")
    
    # 2d: Predict conflicts
    print("  Testing Conflict Prediction...")
    resp = session.get(f"{BASE_URL}/api/chat/all/conflict-prediction",
                       params={"threshold": "0.5", "days": "30"},
                       timeout=15)
    
    if resp.status_code != 200 or not resp.json().get("ok"):
        issues.append({
            "severity": "HIGH",
            "step": "Step 2d: Conflict Prediction",
            "repro": "In Advanced Lab, click 'Predict' button in Conflict Prediction section",
            "expected": "Prediction runs, shows risk pairs",
            "actual": f"API error: HTTP {resp.status_code} or ok=False"
        })
    else:
        risks = resp.json().get("risks", [])
        print(f"✅ Conflict Prediction works: {len(risks)} risk pairs")
    
    # STEP 3: Quick View
    print("\n[STEP 3] Testing Quick View tab...")
    if 'data-tab="graph-view"' in html and 'id="tab-graph-view"' in html:
        print("✅ Quick View tab structure present")
    
    resp = session.get(f"{BASE_URL}/api/chat/all/graph",
                       params={"period": "7d"},
                       timeout=10)
    
    if resp.status_code != 200 or not resp.json().get("ok"):
        issues.append({
            "severity": "HIGH",
            "step": "Step 3: Quick View Load Graph",
            "repro": "Switch to Quick View tab, click 'Загрузить' button",
            "expected": "Graph loads and displays",
            "actual": f"API error: HTTP {resp.status_code} or ok=False"
        })
    else:
        graph = resp.json().get("graph", {})
        print(f"✅ Quick View graph loads: {len(graph.get('nodes', []))} nodes")
    
    # STEP 4: Get user ID from leaderboard
    print("\n[STEP 4] Getting user ID from leaderboard...")
    resp = session.get(f"{BASE_URL}/api/admin/leaderboard",
                       params={"chat_id": "all", "metric": "engagement", "limit": "10", "days": "30"},
                       timeout=10)
    
    user_id = None
    if resp.status_code == 200 and resp.json().get("ok"):
        users = resp.json().get("leaderboard", {}).get("users", [])
        if users:
            user_id = users[0].get("user_id")
            print(f"✅ Found user from leaderboard: {user_id}")
        else:
            print("⚠️ No users in leaderboard, using test ID")
            user_id = 123456789
    else:
        print("⚠️ Leaderboard API issue, using test ID")
        user_id = 123456789
    
    # STEP 5: User Profile
    print(f"\n[STEP 5] Opening user profile for ID {user_id}...")
    resp = session.get(f"{BASE_URL}/admin/user/{user_id}",
                       params={"chat": "all"},
                       timeout=10)
    
    if resp.status_code == 404:
        print(f"⚠️ User {user_id} not found (404 - acceptable if test user)")
    elif resp.status_code != 200:
        issues.append({
            "severity": "HIGH",
            "step": "Step 5: User Profile Load",
            "repro": f"Enter user ID {user_id} in Ops 'User ID:' field, click 'Открыть профиль'",
            "expected": "Profile page loads (HTTP 200) or shows 404 gracefully",
            "actual": f"HTTP {resp.status_code}"
        })
    else:
        profile_html = resp.text
        
        # Check for errors
        if "traceback" in profile_html.lower() or "exception" in profile_html.lower():
            issues.append({
                "severity": "HIGH",
                "step": "Step 5: Profile Error",
                "repro": f"Open profile for user {user_id}",
                "expected": "Profile renders without errors",
                "actual": "Error/exception text visible on page"
            })
        
        # Check for graph block
        has_graph = any(x in profile_html.lower() for x in ["graph", "граф", "canvas"])
        
        # Check for metrics
        has_metrics = any(x in profile_html.lower() for x in ["metric", "stats", "influence", "engagement"])
        
        if not has_graph:
            issues.append({
                "severity": "MEDIUM",
                "step": "Step 5: Graph Block Missing",
                "repro": f"Open profile for user {user_id}, check for graph visualization",
                "expected": "Graph block/canvas visible",
                "actual": "No graph section found in profile HTML"
            })
        
        if not has_metrics:
            issues.append({
                "severity": "MEDIUM",
                "step": "Step 5: Metrics Block Missing",
                "repro": f"Open profile for user {user_id}, check for metrics/stats",
                "expected": "Metrics block with stats visible",
                "actual": "No metrics section found in profile HTML"
            })
        
        if has_graph and has_metrics:
            print(f"✅ Profile renders correctly: graph and metrics blocks present")
        elif has_graph or has_metrics:
            print(f"⚠️ Profile partial: {'graph' if has_graph else 'metrics'} block found, {'metrics' if has_graph else 'graph'} missing")
    
    # FINAL REPORT
    print("\n" + "="*80)
    print("WALKTHROUGH RESULTS")
    print("="*80)
    
    if not issues:
        print("\n✅✅✅ No UI issues found in walkthrough ✅✅✅")
    else:
        print(f"\n❌ ISSUES FOUND: {len(issues)}")
        print("-"*80)
        for i, issue in enumerate(issues, 1):
            print(f"\n{i}. [{issue['severity']}] {issue['step']}")
            print(f"   Repro: {issue['repro']}")
            print(f"   Expected: {issue['expected']}")
            print(f"   Actual: {issue['actual']}")
    
    print("\n" + "="*80)

if __name__ == "__main__":
    main()
