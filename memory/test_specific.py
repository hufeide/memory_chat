# 测试特定情况："你想知道我主要工作吗"
import sqlite3
import sys
import os

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.langgraph_memorey import app, memory_conn
from memory.gradio_interface import memory_cache

print("✅ 成功导入模块")

# 清空测试数据
print("\n🔄 清空测试数据...")
memory_conn.execute("DELETE FROM user_memories WHERE user_id = 'test_user_specific'")
memory_conn.commit()

# 清空缓存
if 'test_user_specific' in memory_cache:
    del memory_cache['test_user_specific']

print("✅ 测试数据已清空")

# 测试用例："你想知道我主要工作吗"
print("\n=== 开始特定测试 ===")

# 准备测试输入
user_id = "test_user_specific"
user_input = "你想知道我主要工作吗"

# 构造初始状态
state = {
    "messages": [],
    "summary": ""
}

# 配置
config = {
    "configurable": {
        "user_id": user_id,
        "thread_id": f"thread_{user_id}"
    }
}

print(f"\n--- 测试用例: {user_input} ---")
print(f"用户输入: {user_input}")

# 调用工作流
print("\n📤 调用工作流...")
assistant_reply = ""

for chunk in app.stream(state, config):
    # 提取助手回复
    for msg in reversed(chunk["messages"]):
        if hasattr(msg, "type") and msg.type == "ai":
            if msg.content and msg.content != assistant_reply:
                assistant_reply = msg.content
                print(f"💬 助手回复: {assistant_reply}")
    
    # 检查工具调用
    for msg in chunk["messages"]:
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            print("🔧 工具调用: 有")
            for tool_call in msg.tool_calls:
                print(f"  - 工具名: {tool_call['name']}")
                print(f"    参数: {tool_call['args']}")

# 验证结果
print("\n=== 测试完成 ===")
print(f"✅ 最终回复: {assistant_reply}")

# 检查记忆是否被保存
cursor = memory_conn.execute("SELECT memory_id, content FROM user_memories WHERE user_id = ?", (user_id,))
memories = cursor.fetchall()
if memories:
    print("\n📝 记忆保存结果:")
    for memory_id, content in memories:
        print(f"  - {memory_id}: {content}")
else:
    print("\n📝 没有保存的记忆")

# 关闭数据库连接
memory_conn.close()
print("\n✅ SQLite数据库连接已关闭")
