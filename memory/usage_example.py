#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
调用示例：演示如何使用 langgraph_memorey 模块
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
    
    # --- 示例 1: 基本对话 --- 
    print("\n=== 示例 1: 基本对话 ===")
    
    # 定义初始状态
    initial_state = {
        "messages": [
            HumanMessage(content="你好，我叫张三，今年25岁，在科技公司工作。")
        ],
        "summary": ""
    }
    
    # 配置
    config = {
        "configurable": {
            "user_id": "user_001",  # 每个用户使用不同的ID来区分记忆
            "thread_id": "thread_001"  # 每个对话线程使用不同的ID
        }
    }
    
    # 调用 app
    result = app.invoke(initial_state, config)
    
    # 打印结果
    print(f"用户输入: {initial_state['messages'][-1].content}")
    print(f"助手回复: {result['messages'][-1].content}")
    
    # --- 示例 2: 询问用户信息（测试记忆功能）---
    print("\n=== 示例 2: 询问用户信息（测试记忆功能）===")
    
    # 定义新的状态，包含之前的对话历史
    new_state = {
        "messages": result["messages"] + [
            HumanMessage(content="你还记得我叫什么名字吗？")
        ],
        "summary": result.get("summary", "")
    }
    
    # 调用 app
    result2 = app.invoke(new_state, config)
    
    # 打印结果
    print(f"用户输入: {new_state['messages'][-1].content}")
    print(f"助手回复: {result2['messages'][-1].content}")
    
    # --- 示例 3: 更新记忆 --- 
    print("\n=== 示例 3: 更新记忆 ===")
    
    # 定义新的状态，更新用户信息
    update_state = {
        "messages": result2["messages"] + [
            HumanMessage(content="我现在不在科技公司工作了，我换工作到教育行业了。")
        ],
        "summary": result2.get("summary", "")
    }
    
    # 调用 app
    result3 = app.invoke(update_state, config)
    
    # 打印结果
    print(f"用户输入: {update_state['messages'][-1].content}")
    print(f"助手回复: {result3['messages'][-1].content}")
    
    # --- 示例 4: 验证记忆更新 --- 
    print("\n=== 示例 4: 验证记忆更新 ===")
    
    # 定义新的状态，验证记忆更新
    verify_state = {
        "messages": result3["messages"] + [
            HumanMessage(content="我现在在哪个行业工作？")
        ],
        "summary": result3.get("summary", "")
    }
    
    # 调用 app
    result4 = app.invoke(verify_state, config)
    
    # 打印结果
    print(f"用户输入: {verify_state['messages'][-1].content}")
    print(f"助手回复: {result4['messages'][-1].content}")
    
    # --- 查看内存存储内容 --- 
    print("\n=== 查看内存存储内容 ===")
    print(f"用户 'user_001' 的记忆: {memory_store.get('user_001', {})}")
    
    print("\n🎉 所有示例运行完成！")
    
except Exception as e:
    print(f"❌ 调用过程中发生错误: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)