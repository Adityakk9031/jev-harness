#!/bin/sh
# Builds a tiny, made-up git repository with three real bug fixes (each with a test), for the
# reviewers demo. Nothing from your real projects is used, so it is safe to push to a sandbox.
set -eu
dir="${1:-/tmp/jev-demo-bugrepo}"
if [ -e "$dir" ]; then echo "$dir already exists; pass another path"; exit 1; fi
mkdir -p "$dir/src" "$dir/tests" && cd "$dir"
git init -q -b main
c() { git add -A && git -c user.name=demo -c user.email=demo@example.invalid commit -qm "$1"; }

cat > src/calc.py <<'PY'
def clamp(value, low, high):
    if value < low:
        return low
    if value > high + 1:
        return high
    return value
PY
cat > src/greet.py <<'PY'
def greeting(user, default="friend"):
    return f"Hello, {user.name}!"
PY
cat > src/perms.py <<'PY'
def can_delete(user, resource):
    return user.is_admin or resource.owner == user.id
PY
printf 'def test_placeholder():\n    assert True\n' > tests/test_basic.py
printf '# demo service\n' > README.md
c "Initial import"

sed -i.bak 's/high + 1/high/' src/calc.py && rm src/calc.py.bak
printf 'from src.calc import clamp\n\n\ndef test_upper_bound():\n    assert clamp(6, 1, 5) == 5\n' > tests/test_calc.py
c "Fix off-by-one in clamp upper bound (#12)"

cat > src/greet.py <<'PY'
def greeting(user, default="friend"):
    if user is None:
        return f"Hello, {default}!"
    return f"Hello, {user.name}!"
PY
printf 'from src.greet import greeting\n\n\ndef test_missing_user():\n    assert greeting(None) == "Hello, friend!"\n' > tests/test_greet.py
c "Fix crash when user is missing (#15)"

sed -i.bak 's/user.is_admin or resource.owner/user.is_admin and resource.owner/' src/perms.py && rm src/perms.py.bak
printf 'from types import SimpleNamespace as N\nfrom src.perms import can_delete\n\n\ndef test_admin_needs_ownership():\n    assert not can_delete(N(is_admin=True, id=1), N(owner=2))\n' > tests/test_perms.py
c "Fix permission check letting any admin delete (#18)"

printf '# demo service\n\nRun the tests with pytest.\n' > README.md
c "Update README"
echo "built $dir with $(git rev-list --count HEAD) commits"
