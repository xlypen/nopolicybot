#!/usr/bin/env python3
"""
Frontend JavaScript Analysis - Detecting potential UI issues
"""

import re
from typing import List, Dict

class FrontendIssue:
    def __init__(self):
        self.issues = []
    
    def add(self, severity: str, component: str, issue: str, fix: str, line_context: str = ""):
        self.issues.append({
            "severity": severity,
            "component": component,
            "issue": issue,
            "fix": fix,
            "context": line_context
        })
    
    def print_report(self):
        print("\n" + "="*80)
        print("FRONTEND JAVASCRIPT ANALYSIS REPORT")
        print("="*80)
        
        if not self.issues:
            print("\n✅ NO FRONTEND ISSUES DETECTED")
        else:
            print(f"\n⚠️ POTENTIAL ISSUES FOUND: {len(self.issues)}")
            print("-"*80)
            for i, issue in enumerate(self.issues, 1):
                print(f"\n{i}. [{issue['severity'].upper()}] {issue['component']}")
                print(f"   Issue: {issue['issue']}")
                print(f"   Fix: {issue['fix']}")
                if issue['context']:
                    print(f"   Context: {issue['context'][:100]}...")
        
        print("\n" + "="*80 + "\n")

def analyze_dashboard_js():
    """Analyze dashboard.html JavaScript for issues"""
    issues = FrontendIssue()
    
    with open('/opt/telegram-political-monitor-bot/templates/admin/dashboard.html', 'r') as f:
        content = f.read()
    
    # Extract JavaScript section
    js_match = re.search(r'<script>(.*?)</script>', content, re.DOTALL)
    if not js_match:
        issues.add("high", "Dashboard JS", "No JavaScript found in dashboard", "Ensure <script> block exists")
        return issues
    
    js_code = js_match.group(1)
    
    # Check 1: Tab switching logic
    if 'setGraphTab' not in js_code:
        issues.add("high", "Graph Tabs", "setGraphTab function missing", "Add setGraphTab function")
    else:
        # Check if tab buttons are properly wired
        if '.tab-btn' not in js_code or 'addEventListener' not in js_code:
            issues.add("medium", "Graph Tab Buttons", "Tab button event listeners might not be set up", 
                      "Ensure .tab-btn elements have click listeners")
    
    # Check 2: Apply & Render button
    if 'labApplyFilters' not in js_code:
        issues.add("high", "Advanced Lab Apply Button", "labApplyFilters button handler missing", 
                  "Add click handler for Apply & Render button")
    
    # Check 3: Predict button
    if 'labPredictConflicts' not in js_code:
        issues.add("high", "Conflict Predict Button", "labPredictConflicts button handler missing",
                  "Add click handler for Predict button")
    
    # Check 4: Chat selector change handler
    if "getElementById('chatSel')" not in js_code:
        issues.add("medium", "Chat Selector", "chatSel element reference missing",
                  "Ensure chat selector is properly referenced")
    
    # Check 5: Check for common JS errors
    # Missing semicolons after function definitions can cause issues
    function_without_semicolon = re.findall(r'}\s*\n(?!\s*;|\s*\)|\s*,|\s*\])', js_code)
    
    # Check 6: Async/await error handling
    async_without_catch = re.findall(r'async function.*?\{.*?\}(?!.*?\.catch)', js_code, re.DOTALL)
    
    # Check 7: DOM element access without null checks
    getelementbyid_lines = re.findall(r"(document\.getElementById\([^)]+\)\.(?!addEventListener|value|checked|textContent|innerHTML))", js_code)
    if getelementbyid_lines:
        issues.add("low", "DOM Access", f"Found {len(getelementbyid_lines)} potential null-pointer issues",
                  "Add null checks after getElementById calls before accessing properties")
    
    # Check 8: Event listener setup timing
    if 'DOMContentLoaded' not in js_code and 'window.addEventListener' not in js_code:
        # Check if script is at the end of body
        if '<script>' in content and '</body>' in content:
            script_pos = content.index('<script>')
            body_pos = content.index('</body>')
            if script_pos > body_pos:
                issues.add("low", "Script Placement", "Script after </body> tag",
                          "Move script before </body> or use DOMContentLoaded")
    
    # Check 9: API error handling
    api_calls = re.findall(r'(fetch\([^)]+\))', js_code)
    catch_blocks = len(re.findall(r'\.catch\(', js_code))
    try_blocks = len(re.findall(r'try\s*\{', js_code))
    
    if len(api_calls) > (catch_blocks + try_blocks):
        issues.add("medium", "API Error Handling", 
                  f"Found {len(api_calls)} fetch calls but only {catch_blocks + try_blocks} error handlers",
                  "Add .catch() or try/catch to all fetch calls")
    
    # Check 10: Module imports
    required_modules = [
        ('GraphRenderPipeline', 'graph_render_pipeline.js'),
        ('GraphVisualizations', 'graph_visualizations.jsx'),
        ('createGraphRealtimeClient', 'realtime_graph_client.js')
    ]
    
    for module, filename in required_modules:
        if filename in content:
            # Module is imported
            if f'window.{module}' not in js_code and f'{module}.' not in js_code:
                issues.add("low", f"Module Usage: {module}", 
                          f"{module} is imported but may not be used correctly",
                          f"Check if window.{module} is properly accessed")
    
    return issues

def analyze_graph_tab_flow():
    """Specific analysis for graph tab switching flow"""
    issues = FrontendIssue()
    
    with open('/opt/telegram-political-monitor-bot/templates/admin/dashboard.html', 'r') as f:
        html = f.read()
    
    # Check HTML structure
    if 'id="tab-graph-view"' not in html:
        issues.add("high", "Graph View Tab", "tab-graph-view element missing", "Add div with id='tab-graph-view'")
    
    if 'id="tab-graph-lab"' not in html:
        issues.add("high", "Graph Lab Tab", "tab-graph-lab element missing", "Add div with id='tab-graph-lab'")
    
    if 'data-tab="graph-view"' not in html:
        issues.add("medium", "Graph View Tab Button", "Tab button missing data-tab attribute",
                  "Add data-tab='graph-view' to Quick View button")
    
    if 'data-tab="graph-lab"' not in html:
        issues.add("medium", "Graph Lab Tab Button", "Tab button missing data-tab attribute",
                  "Add data-tab='graph-lab' to Advanced Lab button")
    
    # Check CSS classes
    if 'class="graph-tab' not in html:
        issues.add("low", "Graph Tab Styling", "graph-tab class might be missing",
                  "Ensure tab panels have 'graph-tab' class")
    
    # Check if active class logic exists
    js_match = re.search(r'<script>(.*?)</script>', html, re.DOTALL)
    if js_match:
        js_code = js_match.group(1)
        if '.classList.toggle' not in js_code and '.classList.add' not in js_code:
            issues.add("medium", "Tab Active State", "CSS class toggle logic might be missing",
                      "Add classList.toggle('active') logic in setGraphTab")
    
    return issues

def analyze_filter_application():
    """Analyze filter application flow"""
    issues = FrontendIssue()
    
    with open('/opt/telegram-political-monitor-bot/templates/admin/dashboard.html', 'r') as f:
        html = f.read()
    
    # Check filter input elements
    filter_inputs = [
        'labFilterMinDegree',
        'labFilterActivityDays',
        'labFilterEngagement',
        'labFocusUser',
        'labFocusEgoNetwork',
        'labShowCentrality',
        'labShowBridges',
        'labShowInfluencers',
        'labShowOutliers',
        'labConflictThreshold'
    ]
    
    for input_id in filter_inputs:
        if f'id="{input_id}"' not in html:
            issues.add("medium", f"Filter Input: {input_id}", f"Input element {input_id} missing",
                      f"Add input with id='{input_id}' in Advanced Lab controls")
    
    # Check if currentLabFilters function exists
    js_match = re.search(r'<script>(.*?)</script>', html, re.DOTALL)
    if js_match:
        js_code = js_match.group(1)
        if 'function currentLabFilters' not in js_code and 'currentLabFilters()' not in js_code:
            issues.add("high", "Filter Collection", "currentLabFilters() function missing",
                      "Add function to collect filter values from inputs")
        
        if 'applyGraphLabFilters' not in js_code:
            issues.add("high", "Filter Application", "applyGraphLabFilters() function missing",
                      "Add function to apply filters and fetch filtered graph")
    
    return issues

def main():
    print("\n" + "="*80)
    print("ANALYZING FRONTEND JAVASCRIPT CODE")
    print("="*80 + "\n")
    
    print("1. Analyzing dashboard JavaScript...")
    dashboard_issues = analyze_dashboard_js()
    
    print("2. Analyzing graph tab flow...")
    tab_issues = analyze_graph_tab_flow()
    
    print("3. Analyzing filter application...")
    filter_issues = analyze_filter_application()
    
    # Combine all issues
    all_issues = FrontendIssue()
    all_issues.issues.extend(dashboard_issues.issues)
    all_issues.issues.extend(tab_issues.issues)
    all_issues.issues.extend(filter_issues.issues)
    
    all_issues.print_report()
    
    if not all_issues.issues:
        print("✅ STATIC ANALYSIS: All frontend JavaScript structure looks good!")
        print("\nNote: This is static analysis. Runtime issues may still exist.")
        print("Recommendation: Test in actual browser for complete validation.")

if __name__ == "__main__":
    main()
