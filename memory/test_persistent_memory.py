#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
测试脚本：验证持久化记忆功能
直接询问用户信息，不包含初始自我介绍，测试是否能记住之前的对话内容
"""

import sys
import os

# 添加当前目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    # 导入所需的模块和类
    from langchain_core.messages import HumanMessage, SystemMessage
    from langgraph_memorey import app, memory_cache as memory_store
    
    print("✅ 成功导入模块")
    
    # --- 测试持久化记忆功能 --- 
    print("\n=== 测试持久化记忆功能 ===")
    print("注意：此测试将直接询问用户信息，不包含初始自我介绍")
    print("如果系统能够正确回答，说明记忆已成功持久化到SQLite数据库\n")
    
    # 定义初始状态（直接询问，不包含自我介绍）
    initial_state = {
        "messages": [
            HumanMessage(content="你还记得我叫什么名字吗？")
        ],
        "summary": ""
    }
    
    # 配置：使用与之前相同的user_id和thread_id
    config = {
        "configurable": {
            "user_id": "user_001",  # 与之前测试相同的用户ID
            "thread_id": "thread_001"  # 与之前测试相同的线程ID
        }
    }
    
    # 调用 app
    result = app.invoke(initial_state, config)
    
    # 打印结果
    print(f"用户输入: {initial_state['messages'][-1].content}")
    print(f"助手回复: {result['messages'][-1].content}")
    
    # 进一步测试：询问更多之前的信息
    print("\n=== 进一步测试：询问更多信息 ===")
    
    # 定义新的状态
    new_state = {
        "messages": result["messages"] + [
            HumanMessage(content="我现在在哪个行业工作？")
        ],
        "summary": result.get("summary", "")
    }
    
    # 调用 app
    result2 = app.invoke(new_state, config)
    
    # 打印结果
    print(f"用户输入: {new_state['messages'][-1].content}")
    print(f"助手回复: {result2['messages'][-1].content}")
    
    # 查看SQLite数据库中的实际内容（验证物理存储）
    print("\n=== 查看SQLite数据库中的实际内容 ===")
    import sqlite3
    
    # 连接到数据库
    conn = sqlite3.connect("ai_memory.db", check_same_thread=False)
    cursor = conn.cursor()
    
    # 查询用户记忆
    cursor.execute("SELECT user_id, memory_id, content, updated_at FROM user_memories")
    memories = cursor.fetchall()
    
    print(f"数据库中存储的记忆总数: {len(memories)}")
    for memory in memories:
        user_id, memory_id, content, updated_at = memory
        print(f"- 用户ID: {user_id}")
        print(f"  记忆ID: {memory_id}")
        print(f"  内容: {content}")
        print(f"  更新时间: {updated_at}")
    
    # 关闭数据库连接
    conn.close()
    
    print("\n🎉 持久化记忆测试完成！")
    
except Exception as e:
    print(f"❌ 测试过程中发生错误: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)