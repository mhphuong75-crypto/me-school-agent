#!/bin/bash
# Run this every time you want to use the agent.

cd "$(dirname "$0")"
source .venv/bin/activate

echo ""
echo "================================================"
echo "  ME School Agent — Đang khởi động..."
echo "================================================"
echo ""
echo "Trình duyệt sẽ tự mở sau vài giây."
echo "Để tắt: nhấn Ctrl + C trong cửa sổ này."
echo ""

streamlit run app.py
