#!/usr/bin/env python3
"""
Smoke test for Sotana retrieval quality.
Runs after ingestion to verify that key documents are findable.

Usage:
    python smoke_test.py                  # run all tests
    python smoke_test.py --verbose        # show details for each test

Exit code 0 = all pass, 1 = some failures (blocks deployment).
"""

import json, sys, unicodedata
from pathlib import Path

TEST_FILE = Path("test_queries.json")
METADATA_FILE = Path("metadata.json")
TOC_FILE = Path("toc.json")


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s).lower()


def _strip_accents(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def test_keyword_search(query: str, expect: str, records: list, verbose: bool) -> bool:
    """Simulate keyword search and check if expected file appears in top results."""
    keywords = [w for w in _nfc(query).split() if len(w) >= 3]
    keywords_stripped = [_strip_accents(k) for k in keywords]
    expect_lower = expect.lower()
    expect_stripped = _strip_accents(expect)

    scored = []
    for i, rec in enumerate(records):
        fname = _nfc(rec.get("file_name", ""))
        fname_bare = _strip_accents(rec.get("file_name", ""))
        text = _nfc(rec.get("text", ""))

        fname_hits = sum(1 for k, ks in zip(keywords, keywords_stripped)
                         if k in fname or ks in fname_bare)
        text_hits = sum(1 for k in keywords if k in text)

        if fname_hits > 0 or text_hits >= 1:
            scored.append((-(fname_hits * 3 + text_hits), i))

    scored.sort(key=lambda x: x[0])

    # Check top 8 unique files
    seen = set()
    top_files = []
    for _, i in scored:
        fn = records[i].get("file_name", "")
        if fn not in seen:
            seen.add(fn)
            top_files.append(fn)
        if len(top_files) >= 8:
            break

    # Check if expected file appears (accent-insensitive)
    found = any(
        expect_lower in _nfc(f) or expect_stripped in _strip_accents(f)
        for f in top_files
    )

    if verbose:
        status = "PASS" if found else "FAIL"
        print(f"  Keyword [{status}]: expect '{expect}' in top 8")
        if not found and top_files:
            print(f"    Got: {top_files[:4]}")

    return found


def test_toc_search(query: str, expect: str, toc: list, verbose: bool) -> bool:
    """Check if expected file exists in TOC with relevant content."""
    expect_lower = expect.lower()
    expect_stripped = _strip_accents(expect)
    query_words = [w for w in _nfc(query).split() if len(w) >= 3]

    for entry in toc:
        fp = entry.get("file_path", "")
        fp_lower = _nfc(fp)
        fp_stripped = _strip_accents(fp)

        if expect_lower in fp_lower or expect_stripped in fp_stripped:
            # File exists in TOC — check if summary/questions are relevant
            summary = _nfc(entry.get("summary", ""))
            questions = " ".join(entry.get("questions", []))
            questions_lower = _nfc(questions)
            content = summary + " " + questions_lower

            # At least 2 query words should appear in TOC content
            matches = sum(1 for w in query_words if w in content)
            found = matches >= 2

            if verbose:
                status = "PASS" if found else "FAIL"
                fn = Path(fp).name
                print(f"  TOC    [{status}]: '{fn}' has {matches}/{len(query_words)} query word matches")

            return found

    if verbose:
        print(f"  TOC    [FAIL]: '{expect}' not found in toc.json at all")
    return False


def test_file_exists(expect: str, records: list, verbose: bool) -> bool:
    """Basic check: does the expected file exist in metadata at all?"""
    expect_lower = expect.lower()
    expect_stripped = _strip_accents(expect)

    for rec in records:
        fn = rec.get("file_name", "")
        if expect_lower in _nfc(fn) or expect_stripped in _strip_accents(fn):
            if verbose:
                print(f"  Exists [PASS]: '{fn}' found in metadata")
            return True

    if verbose:
        print(f"  Exists [FAIL]: '{expect}' not in metadata — file may not have been ingested")
    return False


def main():
    verbose = "--verbose" in sys.argv or "-v" in sys.argv

    if not TEST_FILE.exists():
        print("SKIP: test_queries.json not found")
        sys.exit(0)

    if not METADATA_FILE.exists():
        print("FAIL: metadata.json not found")
        sys.exit(1)

    with open(TEST_FILE, encoding="utf-8") as f:
        tests = json.load(f)

    with open(METADATA_FILE, encoding="utf-8") as f:
        records = json.load(f)

    toc = []
    if TOC_FILE.exists():
        with open(TOC_FILE, encoding="utf-8") as f:
            toc = json.load(f)

    print(f"=== Smoke Test: {len(tests)} queries, {len(records)} chunks, {len(toc)} TOC entries ===\n")

    passed = 0
    failed = 0
    warnings = 0

    for test in tests:
        query = test["query"]
        expect = test["expect_file"]
        desc = test.get("description", "")

        print(f"Query: \"{query}\"")
        if verbose and desc:
            print(f"  ({desc})")

        # Level 1: Does the file exist at all?
        exists = test_file_exists(expect, records, verbose)
        if not exists:
            print(f"  RESULT: FAIL — file not ingested\n")
            failed += 1
            continue

        # Level 2: Can keyword search find it?
        kw_found = test_keyword_search(query, expect, records, verbose)

        # Level 3: Is it in the TOC with relevant content?
        toc_found = test_toc_search(query, expect, toc, verbose) if toc else False

        if kw_found and toc_found:
            print(f"  RESULT: PASS\n")
            passed += 1
        elif kw_found or toc_found:
            method = "keyword" if kw_found else "TOC"
            other = "TOC" if kw_found else "keyword"
            print(f"  RESULT: WARN — found via {method} but not {other}\n")
            warnings += 1
            passed += 1  # still counts as pass
        else:
            print(f"  RESULT: FAIL — not found by keyword or TOC search\n")
            failed += 1

    print(f"=== Results: {passed} passed, {failed} failed, {warnings} warnings ===")

    if failed > 0:
        print(f"\nFAILED: {failed} queries cannot find their expected documents.")
        print("Fix retrieval or update test_queries.json before deploying.")
        sys.exit(1)
    else:
        print("\nAll smoke tests passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
