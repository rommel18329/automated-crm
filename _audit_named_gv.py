"""Audit named-contact GV files to see how many we can now match."""
from crm_engine import db
from pathlib import Path
from bs4 import BeautifulSoup
import re

DB = Path("crm.db")
TAKEOUT = Path(r"C:\Users\jimen\Desktop\Takeout\Voice\Calls")
MY_NUMBERS = {"+17732178686", "+15154618235"}

def normalize(raw):
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    return digits if len(digits) == 10 else None

def extract_phone(soup):
    for a in soup.find_all("a", href=re.compile(r"^tel:")):
        raw = a["href"].replace("tel:", "")
        if raw not in MY_NUMBERS:
            d = normalize(raw)
            if d:
                return d
    return None

# Build phone -> lead_id from primary phones
leads = db.fetch_leads(DB)
phone_map = {}
for l in leads:
    d = normalize(l["phone"])
    if d:
        phone_map[d] = l["id"]
    # Also parse alt phones from timeline
    if "Alt phones:" in (l["timeline"] or ""):
        for part in l["timeline"].split("Alt phones:")[-1].split(","):
            d2 = normalize(part.strip())
            if d2:
                phone_map[d2] = l["id"]

print(f"Phone map entries: {len(phone_map)}")

# Find all named-contact files (don't start with +1XXXXXXXXXX)
named_files = [f for f in TAKEOUT.glob("*.html")
               if not re.match(r"^\+1\d{10}", f.name)]

type_counts = {}
matchable = []
no_match = []

for fpath in named_files:
    m = re.search(r" - (Text|Placed|Received|Missed|Voicemail) - ", fpath.stem)
    if not m:
        continue
    ftype = m.group(1)
    if ftype == "Voicemail":
        continue
    type_counts[ftype] = type_counts.get(ftype, 0) + 1
    html = fpath.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    phone = extract_phone(soup)
    if phone and phone in phone_map:
        lead_name = next(l["name"] for l in leads if l["id"] == phone_map[phone])
        matchable.append((fpath.name[:60], phone, lead_name))
    else:
        no_match.append((fpath.name[:60], phone))

print(f"\nNamed files by type: {type_counts}")
print(f"Matchable: {len(matchable)}")
print(f"No match:  {len(no_match)}")
print("\nMatchable files (sample):")
for name, phone, lead in matchable[:20]:
    print(f"  {lead:<25} {phone}  {name}")
