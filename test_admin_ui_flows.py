#!/usr/bin/env python3
"""
End-to-end test script for Flask Admin UI flows.
Tests actual user flows as specified in requirements.
"""

import requests
import time
import json
from typing import Dict, List, Tuple

BASE_URL = "http://127.0.0.1:5000"
PASSWORD = "admin12345"

class TestResult:
    def __init__(self):
        self.issues = []
        self.passed = []
    
    def add_issue(self, severity: str, test: str, actual: str, expected: str, fix: str):
        self.issues.append({
            "severity": severity,
            "test": test,
            "actual": actual,
            "expected": expected,
            "suggested_fix": fix
        })
    
    def add_passed(self, test: str):
        self.passed.append(test)
    
    def print_report(self):
        print("\n" + "="*80)
        print("FLASK ADMIN UI TEST REPORT")
        print("="*80)
        
        if self.issues:
            print(f"\n❌ ISSUES FOUND: {len(self.issues)}")
            print("-"*80)
            for i, issue in enumerate(self.issues, 1):
                print(f"\n{i}. [{issue['severity'].upper()}] {issue['test']}")
                print(f"   Actual: {issue['actual']}")
                print(f"   Expected: {issue['expected']}")
                print(f"   Suggested Fix: {issue['fix']}")
        
        if self.passed:
            print(f"\n✅ PASSED: {len(self.passed)}")
            print("-"*80)
            for test in self.passed:
                print(f"   • {test}")
        
        print("\n" + "="*80)
        if not self.issues:
            print("✅ ALL FLOWS PASSED - NO ISSUES FOUND")
        print("="*80 + "\n")

def create_session() -> requests.Session:
    """Create a session and login."""
    session = requests.Session()
    return session

def test_login(session: requests.Session, results: TestResult) -> bool:
    """Test 1: Login flow"""
    print("Testing: Login flow...")
    
    try:
        # First get login page
        resp = session.get(f"{BASE_URL}/login", timeout=5)
        if resp.status_code != 200:
            results.add_issue(
                "high",
                "Login Page Load",
                f"HTTP {resp.status_code}",
                "HTTP 200",
                "Check if /login route is properly configured in admin_app.py"
            )
            return False
        
        # Attempt login
        resp = session.post(
            f"{BASE_URL}/login",
            data={"password": PASSWORD},
            timeout=5,
            allow_redirects=False
        )
        
        if resp.status_code not in [200, 302]:
            results.add_issue(
                "high",
                "Login Authentication",
                f"HTTP {resp.status_code}",
                "HTTP 200 or 302 redirect",
                "Verify login POST handler and password validation logic"
            )
            return False
        
        # Check if we're authenticated by accessing admin page
        resp = session.get(f"{BASE_URL}/admin", timeout=5)
        if resp.status_code != 200:
            results.add_issue(
                "high",
                "Post-Login Access",
                f"Cannot access /admin (HTTP {resp.status_code})",
                "HTTP 200 with dashboard content",
                "Check session management and authentication decorator"
            )
            return False
        
        results.add_passed("Login flow successful")
        return True
        
    except Exception as e:
        results.add_issue(
            "high",
            "Login Flow Exception",
            str(e),
            "Successful login",
            "Check server is running and accessible"
        )
        return False

def test_dashboard_load(session: requests.Session, results: TestResult) -> Tuple[bool, Dict]:
    """Test 2: Dashboard loads with data"""
    print("Testing: Modern dashboard load...")
    
    try:
        resp = session.get(f"{BASE_URL}/admin", timeout=10)
        if resp.status_code != 200:
            results.add_issue(
                "high",
                "Dashboard Load",
                f"HTTP {resp.status_code}",
                "HTTP 200",
                "Check /admin route in admin_app.py"
            )
            return False, {}
        
        html = resp.text
        
        # Check for key elements
        required_elements = [
            ("Modern Dashboard", "Dashboard title missing"),
            ("Quick View", "Graph tab Quick View missing"),
            ("Advanced Lab", "Graph tab Advanced Lab missing"),
            ("chatSel", "Chat selector missing"),
            ("modeSel", "Mode selector missing"),
            ("labApplyFilters", "Apply & Render button missing"),
            ("labPredictConflicts", "Predict button missing"),
        ]
        
        for element, error_msg in required_elements:
            if element not in html:
                results.add_issue(
                    "medium",
                    "Dashboard Element Missing",
                    error_msg,
                    f"Element '{element}' should be present in HTML",
                    f"Check templates/admin/dashboard.html for {element}"
                )
                return False, {}
        
        results.add_passed("Dashboard loads with all required elements")
        return True, {}
        
    except Exception as e:
        results.add_issue(
            "high",
            "Dashboard Load Exception",
            str(e),
            "Dashboard loads successfully",
            "Check server logs for errors"
        )
        return False, {}

def test_graph_api(session: requests.Session, results: TestResult, chat_id: str = "all") -> bool:
    """Test 3: Graph API endpoint"""
    print(f"Testing: Graph API for chat_id={chat_id}...")
    
    try:
        resp = session.get(
            f"{BASE_URL}/api/chat/{chat_id}/graph",
            params={"period": "7d"},
            timeout=10
        )
        
        if resp.status_code != 200:
            results.add_issue(
                "high",
                f"Graph API (chat={chat_id})",
                f"HTTP {resp.status_code}",
                "HTTP 200 with graph data",
                "Check /api/chat/<chat_id>/graph route and data availability"
            )
            return False
        
        data = resp.json()
        if not data.get("ok"):
            results.add_issue(
                "medium",
                f"Graph API Response (chat={chat_id})",
                f"ok=False, error: {data.get('error', 'unknown')}",
                "ok=True with graph data",
                "Check graph data generation logic in API handler"
            )
            return False
        
        graph = data.get("graph", {})
        if not isinstance(graph, dict):
            results.add_issue(
                "medium",
                f"Graph API Data Structure (chat={chat_id})",
                f"graph is {type(graph)}",
                "graph should be dict with nodes/edges/meta",
                "Ensure graph data structure matches expected format"
            )
            return False
        
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        meta = graph.get("meta", {})
        
        print(f"  Graph data: {len(nodes)} nodes, {len(edges)} edges")
        
        results.add_passed(f"Graph API returns valid data (chat={chat_id})")
        return True
        
    except Exception as e:
        results.add_issue(
            "high",
            f"Graph API Exception (chat={chat_id})",
            str(e),
            "Valid JSON graph data",
            "Check API endpoint implementation and error handling"
        )
        return False

def test_graph_lab_api(session: requests.Session, results: TestResult, chat_id: str = "all") -> bool:
    """Test 4: Graph Lab API with filters"""
    print(f"Testing: Graph Lab API for chat_id={chat_id}...")
    
    # Test with default filters
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
    
    try:
        resp = session.get(
            f"{BASE_URL}/api/chat/{chat_id}/graph-lab",
            params={
                "filters": json.dumps(default_filters),
                "period": "30d"
            },
            timeout=15
        )
        
        if resp.status_code != 200:
            results.add_issue(
                "high",
                f"Graph Lab API (chat={chat_id}, default filters)",
                f"HTTP {resp.status_code}",
                "HTTP 200 with filtered graph data",
                "Check /api/chat/<chat_id>/graph-lab route implementation"
            )
            return False
        
        data = resp.json()
        if not data.get("ok"):
            results.add_issue(
                "medium",
                f"Graph Lab API Response (chat={chat_id})",
                f"ok=False, error: {data.get('error', 'unknown')}",
                "ok=True with filtered graph",
                "Check graph lab filtering logic"
            )
            return False
        
        results.add_passed(f"Graph Lab API with default filters (chat={chat_id})")
        
        # Test with changed filter (min_degree = 1)
        changed_filters = default_filters.copy()
        changed_filters["min_degree"] = 1
        
        resp = session.get(
            f"{BASE_URL}/api/chat/{chat_id}/graph-lab",
            params={
                "filters": json.dumps(changed_filters),
                "period": "30d"
            },
            timeout=15
        )
        
        if resp.status_code != 200:
            results.add_issue(
                "medium",
                f"Graph Lab API (chat={chat_id}, min_degree=1)",
                f"HTTP {resp.status_code}",
                "HTTP 200 with filtered graph data",
                "Check filter application logic for min_degree"
            )
            return False
        
        data2 = resp.json()
        if not data2.get("ok"):
            results.add_issue(
                "medium",
                f"Graph Lab API with min_degree=1 (chat={chat_id})",
                f"ok=False, error: {data2.get('error', 'unknown')}",
                "ok=True with filtered graph",
                "Verify min_degree filter is applied correctly"
            )
            return False
        
        results.add_passed(f"Graph Lab API with min_degree=1 filter (chat={chat_id})")
        return True
        
    except Exception as e:
        results.add_issue(
            "high",
            f"Graph Lab API Exception (chat={chat_id})",
            str(e),
            "Valid JSON filtered graph data",
            "Check API endpoint and filtering implementation"
        )
        return False

def test_conflict_prediction_api(session: requests.Session, results: TestResult, chat_id: str = "all") -> bool:
    """Test 5: Conflict Prediction API"""
    print(f"Testing: Conflict Prediction API for chat_id={chat_id}...")
    
    try:
        resp = session.get(
            f"{BASE_URL}/api/chat/{chat_id}/conflict-prediction",
            params={
                "threshold": "0.5",
                "days": "30"
            },
            timeout=15
        )
        
        if resp.status_code != 200:
            results.add_issue(
                "high",
                f"Conflict Prediction API (chat={chat_id})",
                f"HTTP {resp.status_code}",
                "HTTP 200 with conflict prediction data",
                "Check /api/chat/<chat_id>/conflict-prediction route"
            )
            return False
        
        data = resp.json()
        if not data.get("ok"):
            results.add_issue(
                "medium",
                f"Conflict Prediction Response (chat={chat_id})",
                f"ok=False, error: {data.get('error', 'unknown')}",
                "ok=True with risks array",
                "Check conflict prediction logic implementation"
            )
            return False
        
        risks = data.get("risks", [])
        print(f"  Conflict risks found: {len(risks)}")
        
        results.add_passed(f"Conflict Prediction API (chat={chat_id})")
        return True
        
    except Exception as e:
        results.add_issue(
            "high",
            f"Conflict Prediction API Exception (chat={chat_id})",
            str(e),
            "Valid JSON conflict prediction data",
            "Check API endpoint and ML model availability"
        )
        return False

def test_user_profile_route(session: requests.Session, results: TestResult, user_id: int = 123456789) -> bool:
    """Test 6: User Profile Route"""
    print(f"Testing: User Profile route for user_id={user_id}...")
    
    try:
        resp = session.get(
            f"{BASE_URL}/admin/user/{user_id}",
            params={"chat": "all"},
            timeout=10
        )
        
        # 200 = profile exists, 404 = user not found (both are valid responses)
        if resp.status_code not in [200, 404]:
            results.add_issue(
                "medium",
                f"User Profile Route (user_id={user_id})",
                f"HTTP {resp.status_code}",
                "HTTP 200 (user found) or 404 (user not found)",
                "Check /admin/user/<user_id> route implementation"
            )
            return False
        
        if resp.status_code == 404:
            print(f"  User {user_id} not found (expected for test user)")
            results.add_passed(f"User Profile route works (returns 404 for non-existent user)")
        else:
            html = resp.text
            if "user" not in html.lower() or "profile" not in html.lower():
                results.add_issue(
                    "low",
                    f"User Profile Content (user_id={user_id})",
                    "Profile page missing expected content",
                    "Page should contain user/profile information",
                    "Check templates/admin/user_profile.html content"
                )
                return False
            results.add_passed(f"User Profile route loads successfully (user_id={user_id})")
        
        return True
        
    except Exception as e:
        results.add_issue(
            "medium",
            f"User Profile Route Exception (user_id={user_id})",
            str(e),
            "Valid user profile page or 404",
            "Check route implementation and template"
        )
        return False

def test_chat_list(session: requests.Session, results: TestResult) -> List[str]:
    """Get available chat IDs from dashboard"""
    print("Testing: Chat list availability...")
    
    try:
        # Try to get chats from API or by parsing dashboard
        resp = session.get(f"{BASE_URL}/admin", timeout=5)
        html = resp.text
        
        # Look for chat options in select dropdown
        import re
        chat_ids = []
        matches = re.findall(r'<option value="(-?\d+)"', html)
        for match in matches:
            if match != "all":
                chat_ids.append(match)
        
        print(f"  Found {len(chat_ids)} chats")
        if len(chat_ids) > 0:
            results.add_passed(f"Chat list available ({len(chat_ids)} chats)")
        else:
            results.add_issue(
                "low",
                "Chat List",
                "No chats found in selector",
                "At least one chat should be available for testing",
                "Ensure bot has monitored chats or add test data"
            )
        
        return chat_ids[:3]  # Return up to 3 chats for testing
        
    except Exception as e:
        results.add_issue(
            "low",
            "Chat List Exception",
            str(e),
            "List of available chats",
            "Check dashboard rendering and chat data"
        )
        return []

def test_ops_controls_api(session: requests.Session, results: TestResult, chat_id: str) -> bool:
    """Test 7: Ops Controls APIs"""
    if not chat_id or chat_id == "all":
        print("Skipping Ops Controls test (no specific chat)")
        return True
    
    print(f"Testing: Ops Controls APIs for chat_id={chat_id}...")
    
    # Test chat mode API
    try:
        resp = session.get(
            f"{BASE_URL}/api/chat-mode",
            params={"chat_id": chat_id},
            timeout=5
        )
        
        if resp.status_code != 200:
            results.add_issue(
                "medium",
                f"Chat Mode API GET (chat={chat_id})",
                f"HTTP {resp.status_code}",
                "HTTP 200 with current mode",
                "Check /api/chat-mode GET handler"
            )
            return False
        
        data = resp.json()
        if not data.get("ok"):
            results.add_issue(
                "low",
                f"Chat Mode API Response (chat={chat_id})",
                f"ok=False, error: {data.get('error', 'unknown')}",
                "ok=True with mode info",
                "Check chat mode retrieval logic"
            )
        else:
            results.add_passed(f"Chat Mode API GET (chat={chat_id})")
        
        return True
        
    except Exception as e:
        results.add_issue(
            "medium",
            f"Ops Controls API Exception (chat={chat_id})",
            str(e),
            "Valid API responses",
            "Check ops control API implementations"
        )
        return False

def test_leaderboard_api(session: requests.Session, results: TestResult, chat_id: str = "all") -> bool:
    """Test 8: Leaderboard API"""
    print(f"Testing: Leaderboard API for chat_id={chat_id}...")
    
    metrics = ["engagement", "influence", "retention", "viral", "churn"]
    
    for metric in metrics[:2]:  # Test first 2 metrics
        try:
            resp = session.get(
                f"{BASE_URL}/api/admin/leaderboard",
                params={
                    "chat_id": chat_id,
                    "metric": metric,
                    "limit": "12",
                    "days": "30"
                },
                timeout=10
            )
            
            if resp.status_code != 200:
                results.add_issue(
                    "medium",
                    f"Leaderboard API (metric={metric}, chat={chat_id})",
                    f"HTTP {resp.status_code}",
                    "HTTP 200 with leaderboard data",
                    f"Check /api/admin/leaderboard route for metric={metric}"
                )
                return False
            
            data = resp.json()
            if not data.get("ok"):
                results.add_issue(
                    "low",
                    f"Leaderboard Response (metric={metric})",
                    f"ok=False, error: {data.get('error', 'unknown')}",
                    "ok=True with users array",
                    "Check leaderboard calculation logic"
                )
            else:
                results.add_passed(f"Leaderboard API metric={metric} (chat={chat_id})")
        
        except Exception as e:
            results.add_issue(
                "medium",
                f"Leaderboard API Exception (metric={metric})",
                str(e),
                "Valid leaderboard data",
                "Check API implementation"
            )
            return False
    
    return True

def main():
    """Run all tests"""
    print("\n" + "="*80)
    print("STARTING FLASK ADMIN UI END-TO-END TESTS")
    print("="*80 + "\n")
    
    results = TestResult()
    session = create_session()
    
    # Test 1: Login
    if not test_login(session, results):
        print("\n❌ Login failed - cannot continue with other tests")
        results.print_report()
        return
    
    # Test 2: Dashboard Load
    dashboard_ok, dashboard_data = test_dashboard_load(session, results)
    
    # Test 3: Get chat list
    chat_ids = test_chat_list(session, results)
    test_chat = chat_ids[0] if chat_ids else "all"
    
    # Test 4: Graph API
    test_graph_api(session, results, "all")
    if test_chat != "all":
        test_graph_api(session, results, test_chat)
    
    # Test 5: Graph Lab API
    test_graph_lab_api(session, results, "all")
    if test_chat != "all":
        test_graph_lab_api(session, results, test_chat)
    
    # Test 6: Conflict Prediction API
    test_conflict_prediction_api(session, results, "all")
    
    # Test 7: User Profile Route
    test_user_profile_route(session, results)
    
    # Test 8: Ops Controls
    if test_chat != "all":
        test_ops_controls_api(session, results, test_chat)
    
    # Test 9: Leaderboard
    test_leaderboard_api(session, results, "all")
    
    # Print final report
    results.print_report()

if __name__ == "__main__":
    main()
