"""Write frontend/server/rules.json: evidence code -> {label, severity} for the dashboard server.

The server reads Supabase directly and needs the same badge labels the agent's export uses.
Run after changing RULES in tools/nyp_safety_check.py (a unit test fails until you do):
    venv/Scripts/python scripts/export_rules.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ.setdefault("NYP_DB_PATH", os.path.join(ROOT, ".rules_export.db"))   # never touch a real store

import nyp_chat_views as views  # noqa: E402

out = os.path.join(ROOT, "frontend", "server", "rules.json")
with open(out, "w", encoding="utf-8") as f:
    json.dump(views.evidence_rules(), f, indent=2, sort_keys=True)
    f.write("\n")
print(f"wrote {out}")
