#!/bin/bash

echo "=== Kiro Autopilot配置验证 ==="
echo ""

# 检查.kiro目录
if [ -d ".kiro" ]; then
    echo "✅ .kiro目录存在"
else
    echo "❌ .kiro目录不存在"
    exit 1
fi

# 检查steering目录
if [ -d ".kiro/steering" ]; then
    echo "✅ .kiro/steering目录存在"
    
    # 检查steering文件
    if [ -f ".kiro/steering/autopilot-mode.md" ]; then
        echo "✅ autopilot-mode.md steering文件存在"
    else
        echo "❌ autopilot-mode.md steering文件不存在"
    fi
    
    if [ -f ".kiro/steering/project-guidelines.md" ]; then
        echo "✅ project-guidelines.md steering文件存在"
    else
        echo "❌ project-guidelines.md steering文件不存在"
    fi
else
    echo "❌ .kiro/steering目录不存在"
    exit 1
fi

# 检查hooks目录
if [ -d ".kiro/hooks" ]; then
    echo "✅ .kiro/hooks目录存在"
    
    # 检查hook文件数量
    hook_count=$(ls .kiro/hooks/*.json 2>/dev/null | wc -l)
    echo "✅ 找到 $hook_count 个hook文件"
    
    # 列出hook文件
    echo "Hook文件列表:"
    for hook in .kiro/hooks/*.json; do
        if [ -f "$hook" ]; then
            hook_name=$(basename "$hook")
            echo "  - $hook_name"
        fi
    done
else
    echo "❌ .kiro/hooks目录不存在"
    exit 1
fi

# 检查README文件
if [ -f "AUTOPILOT-README.md" ]; then
    echo "✅ AUTOPILOT-README.md文件存在"
else
    echo "❌ AUTOPILOT-README.md文件不存在"
fi

echo ""
echo "=== 配置验证完成 ==="
echo ""
echo "配置状态: ✅ 完成"
echo "下次Kiro会话将启用Autopilot模式"
echo ""
echo "要测试配置，请重启Kiro会话并执行一个简单的文件编辑操作。"