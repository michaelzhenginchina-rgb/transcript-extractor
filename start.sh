#!/bin/bash
# 快速启动 YouTube 字幕提取器
# Quick start script for YouTube Transcript Extractor

cd /Users/mengnanzheng/Desktop/cursor/youtube-transcript-extractor

echo "🚀 Starting YouTube Transcript Extractor..."

# 检查 server 是否已经运行
if lsof -Pi :8002 -sTCP > /dev/null 2>&1 ; then
    echo "✅ Server is already running"
else
    echo "📡 Starting server..."
    ./venv/bin/python server.py &
    sleep 3
    echo "✅ Server started"
fi

# 打开 HTML 界面
echo "📝 Opening interface..."
open index.html

echo "✨ Ready to use!"
