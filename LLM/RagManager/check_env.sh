#!/bin/bash
"""
RAG Manager环境检查和修复脚本
"""

echo "检查RAG Manager运行环境..."

# 检查Python依赖
echo "检查Python依赖..."
python3 -c "import torch" 2>/dev/null
if [ $? -eq 0 ]; then
    echo "✅ torch已安装"
else
    echo "❌ torch未安装"
    echo "请运行: pip install torch"
fi

python3 -c "import PIL" 2>/dev/null  
if [ $? -eq 0 ]; then
    echo "✅ PIL已安装"
else
    echo "❌ PIL未安装"
    echo "请运行: pip install Pillow"
fi

python3 -c "from colpali_engine.models import ColPali" 2>/dev/null
if [ $? -eq 0 ]; then
    echo "✅ colpali_engine已安装"
else
    echo "❌ colpali_engine未安装"
    echo "请运行: pip install colpali-engine"
fi

echo ""
echo "建议安装命令："
echo "pip install torch torchvision torchaudio"
echo "pip install Pillow pdf2image"
echo "pip install colpali-engine"
echo "pip install pymilvus tqdm numpy"

echo ""
echo "如果在conda环境中："
echo "conda install pytorch torchvision torchaudio pytorch-cuda=11.8 -c pytorch -c nvidia"
echo "pip install colpali-engine pymilvus"