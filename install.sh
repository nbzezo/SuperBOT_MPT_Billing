#!/bin/bash
# install.sh — Cài đặt MPT SuperBot chạy nền tự động (Systemd)

cd "$(dirname "$0")"

echo "⚡ Cài đặt MPT SuperBot Systemd Service..."
echo "Yêu cầu quyền sudo..."

sudo cp mpt-superbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable mpt-superbot.service

echo ""
echo "✅ Đã cài đặt thành công!"
echo "Sử dụng các lệnh sau để quản lý:"
echo "  - Bật server:    sudo systemctl start mpt-superbot"
echo "  - Tắt server:    sudo systemctl stop mpt-superbot"
echo "  - Xem trạng thái: sudo systemctl status mpt-superbot"
echo "  - Xem log:       sudo journalctl -u mpt-superbot -f"
