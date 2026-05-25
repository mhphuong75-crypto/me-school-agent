#!/bin/bash
# Use this when you DELETE files from the manual folder.
# This wipes the database and rebuilds from scratch.
# (For adding or editing files, use 2_build_database.sh instead — it's faster)

cd "$(dirname "$0")"
source .venv/bin/activate

echo ""
echo "================================================"
echo "  ME School Agent — Xây lại toàn bộ database"
echo "================================================"
echo ""
echo "⚠️  Đang xoá database cũ và xây lại từ đầu..."
echo "(Dùng lệnh này khi bạn XOÁ file khỏi bộ tài liệu)"
echo ""

python3 ingest.py --reset

echo ""
echo "================================================"
echo "  Xong! Database đã được cập nhật."
echo "================================================"
echo ""
