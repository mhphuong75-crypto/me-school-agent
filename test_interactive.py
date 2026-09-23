#!/usr/bin/env python3
"""
Tests for Interactive Learning Mode.
Part 1: Unit tests (no API needed) — detection, trimming, prompt selection.
Part 2: Live API test (needs ANTHROPIC_API_KEY) — response quality.

Usage:
    python test_interactive.py              # unit tests only
    python test_interactive.py --live       # unit + live API tests
    python test_interactive.py --verbose    # show details
"""

import json, sys, os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Import app modules ────────────────────────────────────────────────────

# We can't import Streamlit-dependent functions directly, so we test
# the pure logic from prompts.py and replicate the detection logic.

from prompts import (
    SYSTEM_PROMPT, INTERACTIVE_SYSTEM_PROMPT, LEARNING_SIGNALS,
)


# ── Replicate detection logic (same as app.py, no Streamlit dependency) ──

def _is_learning_query(query: str) -> bool:
    q = query.lower()
    if len(query.split()) <= 3:
        return False
    return any(signal in q for signal in LEARNING_SIGNALS)


def _trim_history(messages: list, max_turns: int = 5) -> list:
    if len(messages) <= max_turns * 2:
        return messages
    return messages[-(max_turns * 2):]


# ══════════════════════════════════════════════════════════════════════════
# PART 1: Unit Tests
# ══════════════════════════════════════════════════════════════════════════

def test_learning_detection(verbose: bool) -> tuple[int, int]:
    """Test that learning vs lookup queries are correctly classified."""
    cases = [
        # (query, expected_is_learning, description)
        # Learning queries — should return True
        ("Tại sao phải làm quy trình khẩn cấp sơ tán?", True,
         "tại sao + long"),
        ("Giải thích quy trình tuyển dụng giáo viên mới", True,
         "giải thích trigger"),
        ("Hướng dẫn tôi cách xử lý tai nạn học sinh", True,
         "hướng dẫn trigger"),
        ("Quy trình đăng ký học cho trẻ mới như thế nào?", True,
         "như thế nào trigger"),
        ("Cho tôi biết các bước onboarding nhân viên mới", True,
         "các bước trigger"),
        ("Khi nào cần báo cáo sự cố cho phụ huynh?", True,
         "khi nào trigger"),
        ("So sánh quy trình tuyển sinh giữa các campus", True,
         "so sánh trigger"),

        # Lookup queries — should return False
        ("học phí", False, "short lookup — 2 words"),
        ("PCCC", False, "short lookup — 1 word"),
        ("biểu mẫu nghỉ phép", False, "short lookup — 3 words"),
        ("số điện thoại khẩn cấp", False, "short factual lookup"),
    ]

    passed = 0
    failed = 0

    for query, expected, desc in cases:
        result = _is_learning_query(query)
        ok = result == expected
        if ok:
            passed += 1
        else:
            failed += 1

        if verbose or not ok:
            status = "PASS" if ok else "FAIL"
            mode = "learning" if result else "lookup"
            print(f"  [{status}] '{query[:40]}…' → {mode} (expected {'learning' if expected else 'lookup'}) — {desc}")

    return passed, failed


def test_trim_history(verbose: bool) -> tuple[int, int]:
    """Test that history trimming keeps only last N turns."""
    passed = 0
    failed = 0

    # Case 1: Short history (≤10 messages) — no trimming
    short = [{"role": "user", "content": f"q{i}"} if i % 2 == 0
             else {"role": "assistant", "content": f"a{i}"}
             for i in range(6)]
    result = _trim_history(short)
    ok = len(result) == 6
    if ok: passed += 1
    else: failed += 1
    if verbose or not ok:
        print(f"  [{'PASS' if ok else 'FAIL'}] Short history ({len(short)} msgs) → kept {len(result)} (expected 6)")

    # Case 2: Long history (20 messages) — trimmed to last 10
    long = [{"role": "user", "content": f"q{i}"} if i % 2 == 0
            else {"role": "assistant", "content": f"a{i}"}
            for i in range(20)]
    result = _trim_history(long)
    ok = len(result) == 10
    if ok: passed += 1
    else: failed += 1
    if verbose or not ok:
        print(f"  [{'PASS' if ok else 'FAIL'}] Long history ({len(long)} msgs) → kept {len(result)} (expected 10)")

    # Case 3: Trimmed history should be the LAST 10 messages
    ok = result[0]["content"] == "q10"
    if ok: passed += 1
    else: failed += 1
    if verbose or not ok:
        print(f"  [{'PASS' if ok else 'FAIL'}] Trimmed starts at msg 10 → got '{result[0]['content']}' (expected 'q10')")

    return passed, failed


def test_prompt_content(verbose: bool) -> tuple[int, int]:
    """Test that prompts contain expected markers."""
    passed = 0
    failed = 0

    # Standard prompt should NOT have Socratic elements
    ok = "Socratic" not in SYSTEM_PROMPT and "💡" not in SYSTEM_PROMPT
    if ok: passed += 1
    else: failed += 1
    if verbose or not ok:
        print(f"  [{'PASS' if ok else 'FAIL'}] Standard prompt has no Socratic markers")

    # Interactive prompt SHOULD have learning interaction markers
    ok = "💡" in INTERACTIVE_SYSTEM_PROMPT and "📚" in INTERACTIVE_SYSTEM_PROMPT
    if ok: passed += 1
    else: failed += 1
    if verbose or not ok:
        print(f"  [{'PASS' if ok else 'FAIL'}] Interactive prompt has 💡 and 📚 markers")

    # Both prompts should reference [CONTEXT] rules
    ok = "[CONTEXT]" in SYSTEM_PROMPT and "[CONTEXT]" in INTERACTIVE_SYSTEM_PROMPT
    if ok: passed += 1
    else: failed += 1
    if verbose or not ok:
        print(f"  [{'PASS' if ok else 'FAIL'}] Both prompts reference [CONTEXT]")

    # Interactive prompt should still enforce context-only answers
    ok = "KHÔNG dùng kiến thức huấn luyện" in INTERACTIVE_SYSTEM_PROMPT
    if ok: passed += 1
    else: failed += 1
    if verbose or not ok:
        print(f"  [{'PASS' if ok else 'FAIL'}] Interactive prompt enforces context-only answers")

    return passed, failed


# ══════════════════════════════════════════════════════════════════════════
# PART 2: Live API Test
# ══════════════════════════════════════════════════════════════════════════

def test_interactive_response(verbose: bool) -> tuple[int, int]:
    """Test that interactive mode produces follow-up questions in responses."""
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        print("  SKIP: No ANTHROPIC_API_KEY set")
        return 0, 0

    try:
        import anthropic
    except ImportError:
        print("  SKIP: anthropic package not installed")
        return 0, 0

    passed = 0
    failed = 0

    # Load some real context from metadata
    meta_path = Path("metadata.json")
    if not meta_path.exists():
        print("  SKIP: metadata.json not found (needed for real context)")
        return 0, 0

    with open(meta_path, encoding="utf-8") as f:
        records = json.load(f)

    # Pick a chunk about safety procedures (likely to exist)
    context_chunks = []
    for rec in records:
        fn = rec.get("file_name", "").lower()
        if any(kw in fn for kw in ["an toàn", "khẩn cấp", "sơ cứu", "tai nạn", "an_toan"]):
            context_chunks.append(rec.get("text", ""))
            if len(context_chunks) >= 3:
                break

    if not context_chunks:
        # Fallback: use first 3 chunks
        context_chunks = [r.get("text", "") for r in records[:3]]

    context = "\n\n".join(f"--- Đoạn {i+1} ---\n{c}" for i, c in enumerate(context_chunks))

    client = anthropic.Anthropic(api_key=key)

    # Test 1: Interactive mode should produce a follow-up question
    query = "Giải thích quy trình xử lý khi có tai nạn xảy ra tại trường"
    system = INTERACTIVE_SYSTEM_PROMPT + f"\n\n[CONTEXT]\n{context}\n[/CONTEXT]"

    if verbose:
        print(f"  Testing interactive response for: '{query}'")

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": query}],
        )
        answer = resp.content[0].text

        # Check for follow-up markers (💡, 📚, ✅, 🔍, or question mark at the end)
        has_followup = any(marker in answer for marker in ["💡", "📚", "✅", "🔍"])
        has_question = "?" in answer.split("\n")[-3:]  # question in last few lines

        ok = has_followup or has_question
        if ok: passed += 1
        else: failed += 1

        if verbose:
            print(f"  [{'PASS' if ok else 'FAIL'}] Interactive response contains follow-up")
            if verbose:
                # Show last 3 lines of response
                lines = answer.strip().split("\n")
                print(f"    Last lines: {lines[-3:] if len(lines) >= 3 else lines}")

    except Exception as e:
        print(f"  [FAIL] API error: {e}")
        failed += 1

    # Test 2: Standard mode should NOT produce follow-up questions
    system_std = SYSTEM_PROMPT + f"\n\n[CONTEXT]\n{context}\n[/CONTEXT]"
    query_std = "Quy trình xử lý tai nạn học sinh"

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1024,
            system=system_std,
            messages=[{"role": "user", "content": query_std}],
        )
        answer_std = resp.content[0].text

        has_followup_std = any(marker in answer_std for marker in ["💡", "📚", "✅", "🔍"])
        ok = not has_followup_std
        if ok: passed += 1
        else: failed += 1

        if verbose:
            print(f"  [{'PASS' if ok else 'FAIL'}] Standard response has NO follow-up markers")

    except Exception as e:
        print(f"  [FAIL] API error: {e}")
        failed += 1

    return passed, failed


# ══════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════

def main():
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    live = "--live" in sys.argv

    total_passed = 0
    total_failed = 0

    print("=== Interactive Mode Tests ===\n")

    # Unit tests (always run)
    print("1. Learning query detection:")
    p, f = test_learning_detection(verbose)
    total_passed += p
    total_failed += f
    print(f"   → {p} passed, {f} failed\n")

    print("2. History trimming:")
    p, f = test_trim_history(verbose)
    total_passed += p
    total_failed += f
    print(f"   → {p} passed, {f} failed\n")

    print("3. Prompt content checks:")
    p, f = test_prompt_content(verbose)
    total_passed += p
    total_failed += f
    print(f"   → {p} passed, {f} failed\n")

    # Live API tests (only with --live)
    if live:
        print("4. Live API — interactive response quality:")
        p, f = test_interactive_response(verbose)
        total_passed += p
        total_failed += f
        print(f"   → {p} passed, {f} failed\n")
    else:
        print("4. Live API tests: SKIPPED (use --live to enable)\n")

    print(f"=== Total: {total_passed} passed, {total_failed} failed ===")

    if total_failed > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
