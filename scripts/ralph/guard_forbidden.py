import fnmatch, json, subprocess, sys

rules = json.load(open(".ralph/ralph_rules.json"))
forbidden = rules.get("forbidden_globs", [])

def changed_files():
    p = subprocess.run("git diff --name-only", shell=True, text=True, capture_output=True)
    if p.returncode != 0:
        print("git diff failed:", p.stderr)
        sys.exit(3)
    return [x.strip() for x in p.stdout.splitlines() if x.strip()]

bad = []
for f in changed_files():
    for g in forbidden:
        if fnmatch.fnmatch(f, g):
            bad.append(f)
            break

if bad:
    print("[FORBIDDEN] Patch touched forbidden paths:")
    for f in bad:
        print(" -", f)
    sys.exit(1)

print("[FORBIDDEN] OK")

