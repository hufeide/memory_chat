#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
测试脚本：验证系统是否能提供合理的回复
"""

import sys
import os

# 添加当前目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    # 导入所需的模块和类
    from langchain_core.messages import HumanMessage
    from langgraph_memorey import app
    
    print("✅ 成功导入模块")
    
    # 定义测试用例
    test_cases = [
        "你好，我叫李四",
        "我今年30岁了",
        "我在医院工作",
        "你还记得我叫什么名字吗？"
    ]
    
    # 配置
    user_id = "test_user_001"
    config = {
        "configurable": {
            "user_id": user_id,
            "thread_id": f"thread_{user_id}"
        }
    }
    
    # 初始状态
    state = {"messages": [], "summary": ""}
    
    print("\n=== 开始测试 ===")
    
    for i, user_input in enumerate(test_cases):
        print(f"\n--- 测试用例 {i+1} ---")
        print(f"用户输入: {user_input}")
        
        # 更新状态
        state["messages"].append(HumanMessage(content=user_input))
        
        # 调用系统
        output = app.invoke(state, config)
        
        # 提取助手回复
        assistant_reply = None
        
        # 首先查找AI消息
        for msg in reversed(output["messages"]):
            if msg.type == "ai":
                assistant_reply = msg.content
                break
        
        # 如果没有AI回复，尝试从工具调用中生成回复
        if not assistant_reply:
            tool_calls = []
            for msg in output["messages"]:
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    tool_calls.extend(msg.tool_calls)
            
            if tool_calls:
                for tool_call in tool_calls:
                    if tool_call["name"] == "manage_memory":
                        args = tool_call["args"]
                        if args["action"] == "upsert":
                            memory_id = args["memory_id"].lower()
                            content = str(args["content"])
                            
                            if memory_id in ["user_name", "name"]:
                                assistant_reply = f"我已经记住您的名字是{content}了。"
                            elif memory_id in ["user_age", "age"]:
                                assistant_reply = f"我已经记住您今年{content}岁了。"
                            elif memory_id in ["user_work", "user_job", "user_workplace", "work", "job"]:
                                assistant_reply = f"我已经记住您的工作信息了：{content}。"
                            else:
                                assistant_reply = f"我已经记住了您的信息：{content}。"
                            break
        
        if not assistant_reply:
            assistant_reply = "未找到助手回复"
        
        print(f"助手回复: {assistant_reply}")
        
        # 检查是否有工具调用
        tool_calls = []
        for msg in output["messages"]:
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                tool_calls.extend(msg.tool_calls)
        
        if tool_calls:
            print(f"工具调用: 有")
            for call in tool_calls:
                print(f"  - 工具名: {call['name']}")
                print(f"    参数: {call['args']}")
        else:
            print(f"工具调用: 无")
        
        # 更新状态
        state = output
    
    print("\n=== 测试完成 ===")
    
except Exception as e:
    print(f"❌ 测试过程中发生错误: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)