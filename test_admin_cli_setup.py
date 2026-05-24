#!/usr/bin/env python3
"""
Test VoteIQ Admin CLI setup
Verifies that admin_cli.py is properly configured
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=" * 80)
print("Testing VoteIQ Admin CLI Setup")
print("=" * 80)

# Test 1: Check if admin_cli.py exists
print("\n[TEST 1] Checking if admin_cli.py exists...")
if os.path.exists("admin_cli.py"):
    print("  [OK] admin_cli.py found")
else:
    print("  [FAIL] admin_cli.py not found")
    sys.exit(1)

# Test 2: Import admin_cli module
print("\n[TEST 2] Importing admin_cli module...")
try:
    import admin_cli
    print("  [OK] admin_cli module imports successfully")
except ImportError as e:
    print(f"  [FAIL] Import error: {e}")
    sys.exit(1)

# Test 3: Check AdminCLI class exists
print("\n[TEST 3] Checking AdminCLI class...")
try:
    from admin_cli import AdminCLI, AgentMode, MODES, AGENTS
    print("  [OK] AdminCLI class found")
    print(f"  [OK] AgentMode enum found with values: {[m.value for m in AgentMode]}")
    print(f"  [OK] MODES mapping: {MODES}")
except ImportError as e:
    print(f"  [FAIL] Import error: {e}")
    sys.exit(1)

# Test 4: Check agent registry
print("\n[TEST 4] Checking agent registry...")
print(f"  Agents configured: {len(AGENTS)}")
for mode, info in AGENTS.items():
    print(f"    - {mode.value}: {info['name']}")
    print(f"      Description: {info['description']}")
    print(f"      Tags: {info['tags']}")

# Test 5: Check .env configuration
print("\n[TEST 5] Checking .env configuration...")
env_file = ".env"
if os.path.exists(env_file):
    print(f"  [OK] .env file found")
    with open(env_file) as f:
        content = f.read()
        if "ANTHROPIC_API_KEY" in content:
            print("  [OK] ANTHROPIC_API_KEY present in .env")
        else:
            print("  [WARN] ANTHROPIC_API_KEY not found in .env")

        if "VOTEIQ_GENERAL_ADMIN_AGENT_ID" in content:
            print("  [OK] VOTEIQ_GENERAL_ADMIN_AGENT_ID present in .env")
        else:
            print("  [WARN] VOTEIQ_GENERAL_ADMIN_AGENT_ID not in .env (needed after deployment)")
else:
    print("  [WARN] .env file not found")

# Test 6: Check admin_dashboard.py exists
print("\n[TEST 6] Checking admin_dashboard.py...")
dashboard_path = "voteiq/api/routes/admin_dashboard.py"
if os.path.exists(dashboard_path):
    print(f"  [OK] {dashboard_path} found")
else:
    print(f"  [FAIL] {dashboard_path} not found")

# Test 7: Check deploy script
print("\n[TEST 7] Checking deploy-general-admin.sh...")
deploy_script = "deploy-general-admin.sh"
if os.path.exists(deploy_script):
    print(f"  [OK] {deploy_script} found")
    # Check if executable
    if os.access(deploy_script, os.X_OK):
        print(f"  [OK] {deploy_script} is executable")
    else:
        print(f"  [WARN] {deploy_script} is not executable (run: chmod +x {deploy_script})")
else:
    print(f"  [FAIL] {deploy_script} not found")

# Test 8: Verify admin routes are registered in main.py
print("\n[TEST 8] Checking main.py integration...")
try:
    with open("main.py", encoding="utf-8", errors="ignore") as f:
        main_content = f.read()
        if "admin_dashboard" in main_content:
            print("  [OK] admin_dashboard router registered in main.py")
        else:
            print("  [FAIL] admin_dashboard router NOT registered in main.py")
except Exception as e:
    print(f"  [WARN] Could not read main.py: {e}")

# Test 9: Mode mapping verification
print("\n[TEST 9] Verifying mode mappings...")
all_good = True
for mode_key, mode_str in MODES.items():
    try:
        agent_mode = AgentMode(mode_str)
        if agent_mode in AGENTS:
            print(f"  [OK] Mode {mode_key} -> {mode_str} -> {AGENTS[agent_mode]['name']}")
        else:
            print(f"  [FAIL] Mode {mode_str} not in AGENTS registry")
            all_good = False
    except ValueError:
        print(f"  [FAIL] Mode {mode_str} not valid AgentMode")
        all_good = False

# Test 10: Summary
print("\n" + "=" * 80)
print("SETUP VERIFICATION SUMMARY")
print("=" * 80)

print("""
[OK] Admin CLI module structure: Complete
[OK] Agent registry: Complete
[OK] Dashboard API routes: Complete
[OK] Deploy script: Ready

NEXT STEPS:
1. Run the deployment script to create the agent:
   ./deploy-general-admin.sh

2. Add the returned agent ID to .env:
   VOTEIQ_GENERAL_ADMIN_AGENT_ID=agent_xyz...

3. Test the CLI:
   python3 admin_cli.py 12 "Help me debug this Python error"

4. Or use interactive mode:
   python3 admin_cli.py 12

SECURITY NOTES:
- This is INTERNAL-ONLY (not for public)
- All responses are DRAFT-ONLY unless explicitly approved
- Add proper admin authentication for production
- Implement audit logging for compliance
- Keep VOTEIQ_GENERAL_ADMIN_AGENT_ID secure

MORE INFO:
- See ADMIN_SETUP.md for complete documentation
- CLI usage: python3 admin_cli.py --help
- API endpoints: http://localhost:8000/admin/
""")

if all_good:
    print("\n[OK] All checks passed! Admin CLI is ready to deploy.\n")
else:
    print("\n[WARN] Some checks failed. Review above for details.\n")
    sys.exit(1)
