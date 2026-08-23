#!/bin/bash
# run.sh — Khởi động MPT SuperBot
set -e
cd "$(dirname "$0")"

VENV_PYTHON=".venv/bin/python3"

echo "⚡ MPT SuperBot — Startup"
echo "========================="

if [ ! -f "$VENV_PYTHON" ]; then
  echo "❌ Không tìm thấy $VENV_PYTHON. Chạy lệnh trước để tạo venv."
  exit 1
fi

echo "🐍 Python: $($VENV_PYTHON --version)"
echo "📦 Đang cập nhật thư viện mới..."

if command -v uv &> /dev/null; then
  UV_CMD="uv"
elif [ -f "$HOME/.local/bin/uv" ]; then
  UV_CMD="$HOME/.local/bin/uv"
else
  echo "⚠️ Không tìm thấy uv, sẽ cố chạy bằng pip..."
  $VENV_PYTHON -m ensurepip
  UV_CMD="$VENV_PYTHON -m pip"
fi

$UV_CMD pip install -q -r requirements.txt 2>/dev/null || $VENV_PYTHON -m pip install -q -r requirements.txt

echo ""
echo "🚀 MPT SuperBot đang chạy tại: http://localhost:8765"
echo "   Nhấn CTRL+C để dừng"
echo ""

$VENV_PYTHON main.py
