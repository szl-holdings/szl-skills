#!/usr/bin/env python3
"""Self-check for szl-skills. Stdlib only. Exit 1 on any failure."""
import json, pathlib, re, sys, unittest
import skill_inventory

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
SECRET = re.compile(r"(ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{40,}|hf_[A-Za-z0-9]{30,}|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)")
PERSONAL = re.compile(r"([A-Za-z]:\\Users\\[A-Za-z0-9._-]+|/home/[a-z][a-z0-9_-]*/|/Users/[A-Za-z][A-Za-z0-9._-]*/|\b100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]+\.[0-9]+\b)")
REF = re.compile(r"`([A-Za-z0-9_./-]+\.(?:py|sh|js|ts|json|yaml|yml|toml|txt|csv))`")
NOT = re.compile(r"(?i)\b(does not|doesn't|do not|don't|not for|never|is not)\b")
fails = []

if not (ROOT / "LICENSE").is_file():
    fails.append("LICENSE missing at repo root")
try:
    json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
except Exception as e:
    fails.append("marketplace.json invalid: %s" % e)
try:
    skill_inventory.check(ROOT)
except (OSError, ValueError, KeyError, TypeError) as e:
    fails.append("source skill inventory: %s" % e)

if not SKILLS.is_dir():
    fails.append("skills/ folder missing")
else:
    size = sum(p.stat().st_size for p in SKILLS.rglob("*") if p.is_file())
    if size > 1000000:
        fails.append("skills/ is %d bytes (limit 1,000,000)" % size)
    names = set()
    for d in sorted(p for p in SKILLS.iterdir() if p.is_dir()):
        md = d / "SKILL.md"
        if not md.is_file():
            fails.append("%s: SKILL.md missing" % d.name); continue
        t = md.read_text(encoding="utf-8")
        m = re.match(r"^---\s*\n(.*?)\n---", t, re.S)
        if not m:
            fails.append("%s: no YAML frontmatter" % d.name); continue
        fm = m.group(1)
        nm = re.search(r"(?m)^name:\s*(\S+)", fm)
        if not nm: fails.append("%s: frontmatter has no name" % d.name)
        elif nm.group(1) in names: fails.append("%s: duplicate skill name %s" % (d.name, nm.group(1)))
        else: names.add(nm.group(1))
        if not re.search(r"(?m)^description:", fm): fails.append("%s: frontmatter has no description" % d.name)
        if not NOT.search(t): fails.append("%s: SKILL.md never says what it does NOT do" % d.name)
        for ref in sorted(set(REF.findall(t))):
            if not (d / ref).exists():
                fails.append("%s: SKILL.md references %s, which is not in the skill folder" % (d.name, ref))

for p in ROOT.rglob("*"):
    if not p.is_file() or ".git" in p.parts or p.name == "selfcheck.py":
        continue
    try: t = p.read_text(encoding="utf-8")
    except Exception: continue
    rel = p.relative_to(ROOT).as_posix()
    if SECRET.search(t): fails.append("%s: secret-shaped string" % rel)
    if PERSONAL.search(t): fails.append("%s: personal path or private IP" % rel)

suite = unittest.defaultTestLoader.discover(str(ROOT / "tools"), pattern="test_*.py")
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful():
    fails.append("behavioral tests failed")
for f in fails: print("FAIL ", f)
print("selfcheck: %s (%d failure(s))" % ("PASS" if not fails else "FAIL", len(fails)))
sys.exit(1 if fails else 0)
