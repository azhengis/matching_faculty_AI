#!/usr/bin/env python3
"""
inspect_bios.py
---------------
Looks at the pages already saved in ./bio_cache (no re-downloading) and tells us
what section headings DePaul actually uses on bio pages. This shows whether we
missed research text that lives under a heading other than "Research Interests".

Run:  python3 inspect_bios.py
"""
import os, re, glob
from collections import Counter
from bs4 import BeautifulSoup

CACHE = "bio_cache"
files = glob.glob(os.path.join(CACHE, "*.html"))
print(f"Scanning {len(files)} cached pages...\n")

headings = Counter()
pages_with_research_word = 0
pages_caught_by_current = 0

for fp in files:
    with open(fp, encoding="utf-8") as f:
        html = f.read()
    soup = BeautifulSoup(html, "html.parser")

    # Collect short bold/heading labels -- these are the section titles.
    for tag in soup.find_all(["strong", "b", "h2", "h3", "h4"]):
        t = " ".join(tag.get_text().split())
        if t and len(t.split()) <= 5:        # headings are short
            headings[t] += 1

    body = soup.get_text(" ").lower()
    if "research" in body:
        pages_with_research_word += 1
    if "research interests" in body:
        pages_caught_by_current += 1

print(f"Pages that contain the word 'research' anywhere : {pages_with_research_word}")
print(f"Pages with the exact heading 'Research Interests': {pages_caught_by_current}")
print(f"  -> potential extra pages we might be missing  : {pages_with_research_word - pages_caught_by_current}\n")

print("Top 40 section headings found across all pages:")
print("-" * 50)
for label, n in headings.most_common(40):
    print(f"{n:5}  {label}")
