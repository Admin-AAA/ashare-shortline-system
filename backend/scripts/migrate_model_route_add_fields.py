#!/usr/bin/env python3
"""
数据库迁移脚本：为 model_route_config 表添加 max_tokens、temperature、timeout_seconds 字段

使用方法：
    cd backend
    python scripts/migrate_model_route_add_fields.py
"""

import sys
from pathlib import Path

# 添加 backend 目录到 Python 路径
backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from sqlalchemy import text
from app.db.session import engine


def migrate():
    """为 model_route_config 表添加三个新字段"""
    
    print("开始迁移：为 model_route_config 表添加新字段...")
    
    with engine.begin() as conn:
        # 检查表是否存在
        result = conn.execute(text("""
            SELECT COUNT(*) FROM information_schema.tables 
            WHERE table_schema = DATABASE() AND table_name = 'model_route_config'
        """))
        table_exists = result.scalar() > 0
        
        if not table_exists:
            print("⚠️  model_route_config 表不存在，跳过迁移")
            return
        
        # 检查字段是否已存在
        result = conn.execute(text("""
            SELECT COUNT(*) FROM information_schema.columns 
            WHERE table_schema = DATABASE() 
              AND table_name = 'model_route_config' 
              AND column_name IN ('max_tokens', 'temperature', 'timeout_seconds')
        """))
        existing_count = result.scalar()
        
        if existing_count == 3:
            print("✅ 所有字段已存在，无需迁移")
            return
        
        # 添加 max_tokens 字段
        try:
            conn.execute(text("""
                ALTER TABLE model_route_config 
                ADD COLUMN max_tokens INT NULL DEFAULT NULL
            """))
            print("✅ 已添加 max_tokens 字段")
        except Exception as e:
            if "Duplicate column name" in str(e):
                print("ℹ️  max_tokens 字段已存在，跳过")
            else:
                raise
        
        # 添加 temperature 字段
        try:
            conn.execute(text("""
                ALTER TABLE model_route_config 
                ADD COLUMN temperature FLOAT NULL DEFAULT NULL
            """))
            print("✅ 已添加 temperature 字段")
        except Exception as e:
            if "Duplicate column name" in str(e):
                print("ℹ️  temperature 字段已存在，跳过")
            else:
                raise
        
        # 添加 timeout_seconds 字段
        try:
            conn.execute(text("""
                ALTER TABLE model_route_config 
                ADD COLUMN timeout_seconds FLOAT NULL DEFAULT NULL
            """))
            print("✅ 已添加 timeout_seconds 字段")
        except Exception as e:
            if "Duplicate column name" in str(e):
                print("ℹ️  timeout_seconds 字段已存在，跳过")
            else:
                raise
    
    print("\n 迁移完成！model_route_config 表现在支持独立配置参数")
    print("   - max_tokens: 单次最大输出token数（可选）")
    print("   - temperature: 温度参数 0-2（可选）")
    print("   - timeout_seconds: 超时秒数（可选）")
    print("\n提示：重启后端服务后生效")


if __name__ == "__main__":
    try:
        migrate()
    except Exception as e:
        print(f"\n 迁移失败：{e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
