#!/bin/zsh
# Run this right before recording. Every line must say OK; if one fails, fix it before you hit record.
cd ~/sdk1 || { echo "FAIL  ~/sdk1 not found"; exit 1; }
export PATH="$HOME/.local/bin:$PATH"
ok=1
check() { if eval "$2" >/dev/null 2>&1; then echo "OK    $1"; else echo "FAIL  $1"; ok=0; fi; }

check "jev-harness is installed"            "command -v jev-harness"
check ".env is here"                        "test -f .env"
check "Jev key works"                       "jev-harness judge check"
check "/tmp/demo-bugrepo-2 is free (you create it on camera)"   "test ! -e /tmp/demo-bugrepo-2"
uv run --quiet python - <<'PY' || ok=0
import httpx, sys
from pathlib import Path
from jev_harness.core.config import read_env_file
tok = read_env_file(Path(".env")).get("GITHUB_TOKEN", "")
h = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}
r = "https://api.github.com/repos/Adityakk9031/jev-harness-sandbox"
info = httpx.get(r, headers=h, timeout=20)
if info.status_code != 200:
    print(f"FAIL  GitHub token cannot see the sandbox ({info.status_code})"); sys.exit(1)
branches = [b["name"] for b in httpx.get(f"{r}/branches", headers=h, timeout=20).json()]
prs = httpx.get(f"{r}/pulls", headers=h, params={"state": "open"}, timeout=20).json()
print("OK    GitHub token sees the sandbox")
good = info.json().get("default_branch") == "main" and branches == ["main"] and not prs
print(("OK    " if good else "FAIL  ") + f"sandbox is clean (branches={branches}, open PRs={len(prs)})")
sys.exit(0 if good else 1)
PY
[ $ok = 1 ] && echo "\nAll good. Hit record." || { echo "\nFix the FAIL lines first."; exit 1; }
