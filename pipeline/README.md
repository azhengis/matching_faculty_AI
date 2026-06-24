# Data Pipeline

Run these scripts **in order** to build the database from scratch.
Each script is safe to re-run — it skips work already done.

```
python3 pipeline/1_extract_faculty.py    # scrape DePaul faculty directory
python3 pipeline/2_enrich_bios.py        # fetch individual bio pages
python3 pipeline/3_enrich_openalex.py    # add OpenAlex research topics
python3 pipeline/4_db_setup.py           # load JSON → faculty.db
python3 pipeline/5_fix_data.py           # clean data quality issues
python3 pipeline/6_fetch_papers.py       # fetch publications (OpenAlex)
python3 pipeline/7_fetch_papers_s2.py    # fetch publications (Semantic Scholar + CrossRef)
python3 pipeline/8_clean_papers.py       # remove misattributed papers
```

After running all 8, rebuild the search index:
```
rm -f faculty_index.pkl paper_index.pkl
python3 search.py
```

Scripts 1–5 only need to run once (or when DePaul updates its faculty directory).
Scripts 6–8 can be re-run periodically to pick up new publications.
