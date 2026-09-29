from pathlib import Path
from bs4 import BeautifulSoup
import re
from crm_engine import db

DB = Path("crm.db")
TAKEOUT = Path(r"C:\Users\jimen\Desktop\Takeout\Voice\Calls")

# Read one of his GV files
zach_files = [f for f in TAKEOUT.glob("*.html") if "303 1st" in f.name]
print(f"GV files for Zach 303 1st St: {len(zach_files)}")
for fpath in zach_files[:2]:
    print(f"\n--- {fpath.name} ---")
    html = fpath.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=re.compile(r"^tel:")):
        print(f"  tel link: {a['href']}")

# Check Zach's DB timeline for alt phones
lead = db.fetch_lead(44, DB)
print(f"\nZach timeline: {lead['timeline']}")
print(f"Zach phone: {lead['phone']}")
