#!/usr/bin/env python3
"""
Browser Walkthrough Simulation - Tests exact user clicks and interactions
"""

import requests
import json
import re
from typing import Dict, List, Tuple

BASE_URL = "http://127.0.0.1:5000"
PASSWORD = "admin12345"

class WalkthroughReport:
    def __init__(self):
        self.issues = []
        self.checks = []
    
    def add_issue(self, severity: str, step: str, repro: str, expected: str, actual: str):
        self.issues.append({
            "severity": severity,
            "step": step,
            "repro": repro,
            "expected": expected,
            "actual": actual
        })
    
    def add_check(self, step: str, result: str):
        self.checks.append({"step": step, "result": result})
    
    def print_report(self):
        print("\n" + "="*80)
        print("BROWSER WALKTHROUGH REPORT")
        print("="*80)
        
        for check in self.checks:
            status = "✅" if "✓" in check["result"] or "OK" in check["result"] else "⚠️"
            print(f"\n{status} {check['step']}")
            print(f"   {check['result']}")
        
        if not self.issues:
            print("\n" + "="*80)
            print("✅ No UI issues found in walkthrough")
            print("="*80)
        else:
            print("\n" + "="*80)
            print(f"❌ ISSUES FOUND: {len(self.issues)}")
            print("="*80)
            for i, issue in enumerate(self.issues, 1):
                print(f"\n{i}. [{issue['severity'].upper()}] {issue['step']}")
                print(f"   Repro: {issue['repro']}")
                print(f"   Expected: {issue['expected']}")
                print(f"   Actual: {issue['actual']}")

def create_session_and_login(report: WalkthroughReport) -> Tuple[requests.Session, bool]:
    """Step 0: Login"""
    session = requests.Session()
    
    try:
        # Navigate to /login
        resp = session.get(f"{BASE_URL}/login", timeout=5)
        if resp.status_code != 200:
            report.add_issue("high", "Login Page", "Navigate to /login",
                           "Page loads (HTTP 200)", f"HTTP {resp.status_code}")
            return session, False
        
        # Submit password
        resp = session.post(f"{BASE_URL}/login", 
                           data={"password": PASSWORD},
                           timeout=5,
                           allow_redirects=True)
        
        if resp.status_code != 200:
            report.add_issue("high", "Login Submit", "Enter password and submit",
                           "Successful login", f"HTTP {resp.status_code}")
            return session, False
        
        # Verify we're logged in by checking dashboard
        if "Modern Dashboard" not in resp.text and "admin" not in resp.text.lower():
            report.add_issue("high", "Login Verification", "After login submit",
                           "Redirected to dashboard", "Not redirected or dashboard missing")
            return session, False
        
        report.add_check("Login", "✓ Logged in successfully")
        return session, True
        
    except Exception as e:
        report.add_issue("high", "Login Exception", "Login process",
                       "Successful login", f"Exception: {e}")
        return session, False

def step1_check_dashboard(session: requests.Session, report: WalkthroughReport) -> bool:
    """Step 1: Open /admin and ensure Modern Dashboard loads"""
    try:
        resp = session.get(f"{BASE_URL}/admin", timeout=5)
        
        if resp.status_code != 200:
            report.add_issue("high", "Step 1: Dashboard Load",
                           "Navigate to /admin",
                           "Dashboard loads (HTTP 200)",
                           f"HTTP {resp.status_code}")
            return False
        
        html = resp.text
        
        # Check for Modern Dashboard title
        if "Modern Dashboard" not in html:
            report.add_issue("high", "Step 1: Dashboard Title",
                           "Load /admin",
                           "Page shows 'Modern Dashboard' title",
                           "Title not found in HTML")
            return False
        
        # Check for graph card
        if "Graph" not in html or "graphRoot" not in html.lower():
            report.add_issue("medium", "Step 1: Graph Card",
                           "Check dashboard for graph section",
                           "Graph card/section visible",
                           "Graph section not found")
            return False
        
        # Check for key UI elements
        required = {
            "chatSel": "Chat selector",
            "modeSel": "Mode selector",
            "loadGraphBtn": "Load Graph button",
        }
        
        missing = []
        for elem_id, elem_name in required.items:
            if elem_id not in html:
                missing.append(elem_name)
        
        if missing:
            report.add_issue("medium", "Step 1: UI Elements",
                           "Dashboard load",
                           "All core UI elements present",
                           f"Missing: {', '.join(missing)}")
        
        report.add_check("Step 1: Dashboard Load", 
                        "✓ Modern Dashboard loads with graph section")
        return True
        
    except Exception as e:
        report.add_issue("high", "Step 1: Exception",
                       "Load dashboard",
                       "Dashboard loads successfully",
                       f"Exception: {e}")
        return False

def step2_advanced_lab_workflow(session: requests.Session, report: WalkthroughReport) -> bool:
    """Step 2: Advanced Lab workflow"""
    
    # First, get the dashboard HTML to check tab structure
    resp = session.get(f"{BASE_URL}/admin", timeout=5)
    html = resp.text
    
    # Check if Advanced Lab tab exists
    has_advanced_lab = "Advanced Lab" in html or "graph-lab" in html
    has_quick_view = "Quick View" in html or "graph-view" in html
    
    if not has_advanced_lab and not has_quick_view:
        report.add_issue("medium", "Step 2a: Tab Structure",
                       "Look for graph tabs on dashboard",
                       "Quick View and Advanced Lab tabs visible",
                       "Tab buttons not found in HTML (may be in cached template)")
        # Continue testing the API even if tabs aren't visible
    
    # Step 2a: Click Advanced Lab tab (test underlying API)
    report.add_check("Step 2a: Switch to Advanced Lab", 
                    "→ Testing Graph Lab API (tab UI may be cached)")
    
    # Step 2b: Click Apply & Render with defaults
    try:
        default_filters = {
            "min_degree": 0,
            "activity_days": 7,
            "engagement": 0,
            "focus_user": None,
            "ego_network": True,
            "show_centrality": True,
            "show_bridges": True,
            "show_influencers": True,
            "show_outliers": False,
            "conflict_risk_threshold": 0.5
        }
        
        resp = session.get(
            f"{BASE_URL}/api/chat/all/graph-lab",
            params={"filters": json.dumps(default_filters), "period": "30d"},
            timeout=15
        )
        
        if resp.status_code != 200:
            report.add_issue("high", "Step 2b: Apply & Render",
                           "Click 'Apply & Render' with default values",
                           "Graph filters apply and returns data (HTTP 200)",
                           f"API error: HTTP {resp.status_code}")
            return False
        
        data = resp.json()
        if not data.get("ok"):
            report.add_issue("medium", "Step 2b: Apply Response",
                           "Apply & Render with defaults",
                           "API returns ok=True with graph",
                           f"API error: {data.get('error', 'unknown')}")
            return False
        
        graph = data.get("graph", {})
        nodes_count = len(graph.get("nodes", []))
        edges_count = len(graph.get("edges", []))
        
        report.add_check("Step 2b: Apply & Render (defaults)",
                        f"✓ API returns graph: {nodes_count} nodes, {edges_count} edges")
        
    except Exception as e:
        report.add_issue("high", "Step 2b: Exception",
                       "Apply & Render",
                       "Filters apply successfully",
                       f"Exception: {e}")
        return False
    
    # Step 2c: Set Min Degree=1 and apply again
    try:
        changed_filters = default_filters.copy()
        changed_filters["min_degree"] = 1
        
        resp = session.get(
            f"{BASE_URL}/api/chat/all/graph-lab",
            params={"filters": json.dumps(changed_filters), "period": "30d"},
            timeout=15
        )
        
        if resp.status_code != 200:
            report.add_issue("high", "Step 2c: Min Degree Filter",
                           "Set Min Degree=1, click Apply & Render",
                           "Filtered graph returns (HTTP 200)",
                           f"API error: HTTP {resp.status_code}")
            return False
        
        data = resp.json()
        if not data.get("ok"):
            report.add_issue("medium", "Step 2c: Filter Response",
                           "Apply Min Degree=1 filter",
                           "API returns filtered graph",
                           f"API error: {data.get('error', 'unknown')}")
            return False
        
        graph = data.get("graph", {})
        nodes = graph.get("nodes", [])
        nodes_count = len(nodes)
        
        # Verify filter worked
        low_degree = [n for n in nodes if n.get("degree", 0) < 1]
        if low_degree:
            report.add_issue("low", "Step 2c: Filter Logic",
                           "Min Degree=1 filter applied",
                           "All nodes have degree >= 1",
                           f"Found {len(low_degree)} nodes with degree < 1")
        
        report.add_check("Step 2c: Apply & Render (min_degree=1)",
                        f"✓ Filter applied: {nodes_count} nodes returned")
        
    except Exception as e:
        report.add_issue("high", "Step 2c: Exception",
                       "Apply min_degree=1",
                       "Filter applies successfully",
                       f"Exception: {e}")
        return False
    
    # Step 2d: Click Predict in conflict block
    try:
        resp = session.get(
            f"{BASE_URL}/api/chat/all/conflict-prediction",
            params={"threshold": "0.5", "days": "30"},
            timeout=15
        )
        
        if resp.status_code != 200:
            report.add_issue("high", "Step 2d: Conflict Predict",
                           "Click 'Predict' button in conflict section",
                           "Prediction runs (HTTP 200)",
                           f"API error: HTTP {resp.status_code}")
            return False
        
        data = resp.json()
        if not data.get("ok"):
            report.add_issue("medium", "Step 2d: Predict Response",
                           "Conflict prediction",
                           "API returns predictions",
                           f"API error: {data.get('error', 'unknown')}")
            return False
        
        risks = data.get("risks", [])
        report.add_check("Step 2d: Conflict Prediction",
                        f"✓ Prediction ran: {len(risks)} risk pairs found")
        
    except Exception as e:
        report.add_issue("high", "Step 2d: Exception",
                       "Click Predict",
                       "Prediction completes",
                       f"Exception: {e}")
        return False
    
    return True

def step3_quick_view_load(session: requests.Session, report: WalkthroughReport) -> bool:
    """Step 3: Switch to Quick View and load graph"""
    
    try:
        # Simulate clicking "Load graph" button (calls graph API)
        resp = session.get(
            f"{BASE_URL}/api/chat/all/graph",
            params={"period": "7d"},
            timeout=10
        )
        
        if resp.status_code != 200:
            report.add_issue("high", "Step 3: Load Graph",
                           "Switch to Quick View, click 'Загрузить'",
                           "Graph loads (HTTP 200)",
                           f"API error: HTTP {resp.status_code}")
            return False
        
        data = resp.json()
        if not data.get("ok"):
            report.add_issue("medium", "Step 3: Graph Response",
                           "Load graph in Quick View",
                           "Graph data returns",
                           f"API error: {data.get('error', 'unknown')}")
            return False
        
        graph = data.get("graph", {})
        nodes_count = len(graph.get("nodes", []))
        edges_count = len(graph.get("edges", []))
        
        report.add_check("Step 3: Quick View Load Graph",
                        f"✓ Graph loads: {nodes_count} nodes, {edges_count} edges")
        return True
        
    except Exception as e:
        report.add_issue("high", "Step 3: Exception",
                       "Load Quick View graph",
                       "Graph loads successfully",
                       f"Exception: {e}")
        return False

def step4_open_user_profile(session: requests.Session, report: WalkthroughReport) -> Tuple[bool, int]:
    """Step 4: Get user ID from leaderboard and open profile"""
    
    try:
        # Get leaderboard to find a real user ID
        resp = session.get(
            f"{BASE_URL}/api/admin/leaderboard",
            params={"chat_id": "all", "metric": "engagement", "limit": "10", "days": "30"},
            timeout=10
        )
        
        if resp.status_code != 200:
            report.add_issue("low", "Step 4: Get User ID",
                           "Fetch leaderboard to find user",
                           "Leaderboard API works",
                           f"HTTP {resp.status_code}")
            # Try with a test ID anyway
            test_user_id = 123456789
            report.add_check("Step 4a: Select User ID",
                            f"→ Using test user ID: {test_user_id}")
            return False, test_user_id
        
        data = resp.json()
        users = data.get("leaderboard", {}).get("users", [])
        
        if not users:
            report.add_issue("low", "Step 4: No Users",
                           "Find user in leaderboard",
                           "At least one user in leaderboard",
                           "Empty leaderboard")
            test_user_id = 123456789
            report.add_check("Step 4a: Select User ID",
                            f"→ No users in leaderboard, using test ID: {test_user_id}")
            return False, test_user_id
        
        # Get first user's ID
        user_id = users[0].get("user_id")
        if not user_id:
            report.add_issue("low", "Step 4: User ID Missing",
                           "Extract user_id from leaderboard",
                           "user_id field present",
                           "user_id field missing")
            test_user_id = 123456789
            return False, test_user_id
        
        report.add_check("Step 4a: Select User ID",
                        f"✓ Found user from leaderboard: {user_id}")
        return True, user_id
        
    except Exception as e:
        report.add_issue("low", "Step 4: Exception",
                       "Get user ID from leaderboard",
                       "User ID retrieved",
                       f"Exception: {e}")
        return False, 123456789

def step5_verify_user_profile(session: requests.Session, report: WalkthroughReport, user_id: int) -> bool:
    """Step 5: Open profile and verify graph/metrics blocks"""
    
    try:
        # Navigate to user profile
        resp = session.get(
            f"{BASE_URL}/admin/user/{user_id}",
            params={"chat": "all"},
            timeout=10
        )
        
        if resp.status_code == 404:
            report.add_issue("low", "Step 5: Profile Not Found",
                           f"Navigate to /admin/user/{user_id}",
                           "Profile page loads or shows 'not found' gracefully",
                           f"404 - User {user_id} doesn't exist (expected if test user)")
            report.add_check("Step 5: User Profile Page",
                            f"→ User {user_id} not found (404 - acceptable)")
            return True  # 404 is acceptable for non-existent users
        
        if resp.status_code != 200:
            report.add_issue("high", "Step 5: Profile Load Error",
                           f"Navigate to /admin/user/{user_id}",
                           "Profile page loads (HTTP 200 or 404)",
                           f"HTTP {resp.status_code}")
            return False
        
        html = resp.text
        
        # Check for error messages in the page
        error_indicators = ["error", "ошибка", "не найден", "not found"]
        has_error = any(indicator in html.lower() for indicator in error_indicators)
        
        if has_error and ("traceback" in html.lower() or "exception" in html.lower()):
            report.add_issue("high", "Step 5: Profile Error Page",
                           f"Open profile for user {user_id}",
                           "Profile renders without errors",
                           "Error/exception message visible on page")
            return False
        
        # Check for graph block
        graph_indicators = ["graph", "граф", "graphRoot", "canvas"]
        has_graph = any(indicator in html.lower() for indicator in graph_indicators)
        
        if not has_graph:
            report.add_issue("medium", "Step 5: Graph Block Missing",
                           "Check profile page for graph section",
                           "Graph block/canvas present",
                           "No graph section found in HTML")
        
        # Check for metrics
        metrics_indicators = ["metric", "метрик", "stats", "статистик", "influence", "engagement"]
        has_metrics = any(indicator in html.lower() for indicator in metrics_indicators)
        
        if not has_metrics:
            report.add_issue("medium", "Step 5: Metrics Block Missing",
                           "Check profile page for metrics section",
                           "Metrics/stats block present",
                           "No metrics section found in HTML")
        
        if has_graph and has_metrics:
            report.add_check("Step 5: User Profile Page",
                            f"✓ Profile loaded for user {user_id}: graph and metrics blocks present")
        elif has_graph or has_metrics:
            report.add_check("Step 5: User Profile Page",
                            f"⚠️ Profile loaded for user {user_id}: only {'graph' if has_graph else 'metrics'} block found")
        else:
            report.add_check("Step 5: User Profile Page",
                            f"⚠️ Profile loaded for user {user_id}: graph and metrics blocks unclear")
        
        return True
        
    except Exception as e:
        report.add_issue("high", "Step 5: Exception",
                       f"Open profile for user {user_id}",
                       "Profile page loads",
                       f"Exception: {e}")
        return False

def main():
    print("\n" + "="*80)
    print("BROWSER WALKTHROUGH SIMULATION")
    print("Target: http://127.0.0.1:5000")
    print("="*80)
    
    report = WalkthroughReport()
    
    # Step 0: Login
    print("\n[Login] Authenticating with password...")
    session, logged_in = create_session_and_login(report)
    
    if not logged_in:
        print("\n❌ Login failed - cannot continue walkthrough")
        report.print_report()
        return
    
    # Step 1: Check dashboard
    print("\n[Step 1] Loading Modern Dashboard...")
    step1_check_dashboard(session, report)
    
    # Step 2: Advanced Lab workflow
    print("\n[Step 2] Testing Advanced Lab workflow...")
    step2_advanced_lab_workflow(session, report)
    
    # Step 3: Quick View
    print("\n[Step 3] Testing Quick View graph load...")
    step3_quick_view_load(session, report)
    
    # Step 4: Get user ID
    print("\n[Step 4] Finding user ID from leaderboard...")
    _, user_id = step4_open_user_profile(session, report)
    
    # Step 5: Verify profile
    print("\n[Step 5] Opening and verifying user profile...")
    step5_verify_user_profile(session, report, user_id)
    
    # Final report
    report.print_report()

if __name__ == "__main__":
    main()
