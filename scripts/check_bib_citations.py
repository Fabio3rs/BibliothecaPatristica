#!/usr/bin/env python3
"""Check biblical citation formats in keywords table.

Flags entries that contain chapter/verse patterns using invalid separators
(e.g. ':' or '.' or en-dash) instead of the required comma between chapter
and verse. Produces a JSON Lines report with offending rows and suggested
normalizations.

Usage:
  python3 scripts/check_bib_citations.py --db data/patristica_keywords.db --out bad_citations.jsonl --limit 5000
"""
import sqlite3
import re
import json
import argparse
import sys
import os

# Matches chapter/verse with various separators. Captures chapter, sep, verse
CHV_RE = re.compile(r"(\b\d+)\s*([:,\.\u2013\u2014\-])\s*(\d+)", re.UNICODE)
# en-dash \u2013, em-dash \u2014

# We'll treat ':' '.' en-dash (\u2013) and em-dash (\u2014) as invalid separators
INVALID_SEPARATORS = {':', '.', '\u2013', '\u2014', '–', '—'}


def find_invalid_matches(text):
    """Return list of (chapter, sep, verse, span) for invalid separators found."""
    out = []
    if not text:
        return out
    for m in CHV_RE.finditer(text):
        chap, sep, verse = m.group(1), m.group(2), m.group(3)
        # normalize sep to literal char if escaped
        # consider hyphen '-' and en/em dash as range separators (allowed only as hyphen '-')
        if sep == ',':
            continue
        if sep in ('-', '\u2013', '\u2014', '–', '—'):
            # hyphen/en/em dash between numbers is sometimes used for ranges; only accept ASCII hyphen '-'
            if sep != '-':
                out.append((chap, sep, verse, m.span()))
            else:
                # ASCII hyphen is allowed when indicating interval but not as chapter:verse separator
                # (e.g., 3-5 is not chapter,verse but verse range; but CHV_RE matches digits-digit as chapter:verse too)
                # We treat hyphen as invalid if it appears immediately after chapter number and before verse, instead of comma
                out.append((chap, sep, verse, m.span()))
        else:
            # any other sep (like ':' or '.') is invalid
            out.append((chap, sep, verse, m.span()))
    return out


def suggest_fix(text):
    # Replace colon or dot between numbers with comma, replace en/em-dash with hyphen
    if not text:
        return text
    s = text
    s = re.sub(r"(\b\d+)\s*[:\.]\s*(\d+)", r"\1,\2", s)
    s = s.replace('\u2013','-').replace('\u2014','-')
    s = s.replace('–','-').replace('—','-')
    return s


def check_db(db_path, table='keywords', out_path=None, limit=None, order_by='keyword_norm'):
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"DB not found: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    order_clause = f" ORDER BY {order_by}" if order_by else ''
    limit_clause = f" LIMIT {int(limit)}" if limit is not None else ''
    query = f"SELECT id, keyword_norm, keyword_original, word FROM {table}{order_clause}{limit_clause}"

    total = 0
    bad = 0
    outf = None
    if out_path:
        outf = open(out_path, 'w', encoding='utf-8')

    for row in cur.execute(query):
        total += 1
        text_candidates = []
        # check both normalized and original using local vars (sqlite3.Row has no .get)
        keyword_norm = row['keyword_norm']
        keyword_original = row['keyword_original']
        if keyword_norm:
            text_candidates.append(('keyword_norm', keyword_norm))
        if keyword_original and keyword_original != keyword_norm:
            text_candidates.append(('keyword_original', keyword_original))

        row_bad = []
        for col, txt in text_candidates:
            matches = find_invalid_matches(txt)
            if matches:
                row_bad.append({'col': col, 'text': txt, 'matches': [{'chapter': m[0], 'sep': m[1], 'verse': m[2], 'span': m[3]} for m in matches]})
        if row_bad:
            bad += 1
            rec = {'id': row['id'], 'bad_parts': row_bad, 'suggestion': suggest_fix(row['keyword_original'] or row['keyword_norm'])}
            if outf:
                outf.write(json.dumps(rec, ensure_ascii=False) + '\n')
            else:
                print(json.dumps(rec, ensure_ascii=False))

    if outf:
        outf.close()
    conn.close()
    return {'checked': total, 'violations': bad}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Check biblical citation separators in keywords table.')
    parser.add_argument('--db', required=True, help='Path to sqlite DB file')
    parser.add_argument('--table', default='keywords', help='Table name')
    parser.add_argument('--out', default=None, help='Output JSONL file for violations (one JSON per line)')
    parser.add_argument('--limit', type=int, default=None, help='Limit rows to check (optional)')
    parser.add_argument('--order-by', default='keyword_norm', help='ORDER BY column')
    args = parser.parse_args()

    res = check_db(args.db, table=args.table, out_path=args.out, limit=args.limit, order_by=args.order_by)
    print(json.dumps(res, ensure_ascii=False))
