#!/bin/bash
# Run this script ONCE to install everything.
# After this, you only need to run 2_start.sh

cd "$(dirname "$0")"

echo ""
echo "================================================"
echo "  ME School Agent — Cài đặt lần đầu"
echo "================================================"
echo ""

# Step 1: create virtual environment
echo "Bước 1/3: Tạo môi trường Python..."
python3 -m venv .venv
source .venv/bin/activate

# Step 2: install libraries
echo ""
echo "Bước 2/3: Cài thư viện (có thể mất 3-5 phút)..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
echo "✅ Thư viện đã cài xong."

# Step 3: check for .env
echo ""
echo "Bước 3/3: Kiểm tra file cấu hình..."
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo "⚠️  Chưa có file .env — đã tạo tự động."
    echo ""
    echo "  Bạn cần mở file .env và điền API key:"
    echo "  → Mở Finder > ME School > me-school-agent > .env"
    echo "  → Tìm dòng ANTHROPIC_API_KEY= và điền key vào sau dấu ="
    echo ""
    open -e .env   # mở bằng TextEdit
else
    echo "✅ File .env đã có sẵn."
fi

echo ""
echo "================================================"
echo "  Cài đặt xong!"
echo ""
echo "  Bước tiếp theo:"
echo "  1. Điền ANTHROPIC_API_KEY vào file .env (nếu chưa làm)"
echo "  2. Chạy file 2_build_database.sh để lập chỉ mục tài liệu"
echo "================================================"
echo ""
