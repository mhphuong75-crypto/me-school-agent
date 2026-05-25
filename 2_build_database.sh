#!/bin/bash
# Run this ONCE after setup, and again whenever you add new documents.
# This reads all the manual files and builds the search database.

cd "$(dirname "$0")"
source .venv/bin/activate

echo ""
echo "================================================"
echo "  ME School Agent — Lập chỉ mục tài liệu"
echo "================================================"
echo ""
echo "Đang đọc tất cả file trong bộ tài liệu..."
echo "(Lần đầu sẽ tải model AI ~120MB và mất khoảng 5-10 phút)"
echo ""

python3 ingest.py

echo ""
echo "================================================"
echo "  Xong! Bây giờ bạn có thể chạy 3_start.sh"
echo "================================================"
echo ""
