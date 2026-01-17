#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sqlite3
import gradio as gr
from typing import List, Tuple, Optional, Dict
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from langgraph_memorey import app

DB_PATH = "ai_memory.db"

def get_formatted_memories(user_id: str) -> str:
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT memory_id, content FROM user_memories WHERE user_id = ? ORDER BY updated_at DESC", 
            (user_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        if not rows: return "📭 目前数据库中无记录。"
        return "\n\n".join([f"📌 {r['memory_id']}\n   └ {r['content']}" for r in rows])
    except Exception as e:
        return f"读取记忆出错: {str(e)}"

# --- 使用元组格式：[(user, bot), (user, bot)] ---
def chat_stream(user_id: str, user_input: str, history: List[Dict[str, str]]):
    history = history or []
    # 初始状态：用户说了话，助手还在思考
    history.append({"role": "user", "content": user_input})
    history.append({"role": "assistant", "content": "🔄 正在思考..."})
    
    trace_steps = []
    config = {"configurable": {"user_id": user_id, "thread_id": f"thread_{user_id}"}}
    input_state = {"messages": [HumanMessage(content=user_input)]}

    yield history, "🚀 工作流启动...", get_formatted_memories(user_id), ""

    try:
        for chunk in app.stream(input_state, config, stream_mode="updates"):
            for node_name, node_data in chunk.items():
                trace_steps.append(f"📍 节点: {node_name}")
                
                if "messages" in node_data:
                    for msg in node_data["messages"]:
                        if isinstance(msg, AIMessage) and msg.content:
                            # 更新最后一项的助手回复
                            history[-1]["content"] = msg.content
                        elif hasattr(msg, "tool_calls") and msg.tool_calls:
                            trace_steps.append(f"🔧 提取事实中...")
                
                yield history, "\n".join(trace_steps), get_formatted_memories(user_id), ""

        # 检查是否依然是初始提示，若是则更新
        if history[-1]["content"] == "🔄 正在思考...":
            history[-1]["content"] = "处理完成。"
        
        yield history, "\n".join(trace_steps), get_formatted_memories(user_id), ""

    except Exception as e:
        history[-1]["content"] = f"❌ 错误: {str(e)}"
        yield history, "\n".join(trace_steps), get_formatted_memories(user_id), ""

# --- 界面构建 ---
with gr.Blocks(title="AI 长期记忆助理") as demo:
    gr.Markdown("# 🧠 LangGraph 长期记忆智能体")
    
    with gr.Row():
        u_id = gr.Textbox(label="用户 ID", value="user_001")
        
    with gr.Row():
        with gr.Column(scale=3):
            # 彻底去掉 type 参数，确保任何版本都能初始化
            chatbot = gr.Chatbot(label="对话历史", height=500)
            msg_in = gr.Textbox(label="输入消息", placeholder="输入内容...", scale=4)
            send_btn = gr.Button("发送", variant="primary")
        
        with gr.Column(scale=2):
            trace_out = gr.Textbox(label="执行轨迹", interactive=False, lines=12)
            memo_out = gr.Textbox(label="长期事实库 (SQLite)", interactive=False, lines=15)

    send_btn.click(chat_stream, [u_id, msg_in, chatbot], [chatbot, trace_out, memo_out, msg_in])
    msg_in.submit(chat_stream, [u_id, msg_in, chatbot], [chatbot, trace_out, memo_out, msg_in])
    demo.load(get_formatted_memories, [u_id], [memo_out])

if __name__ == "__main__":
    # 如果 launch 里的 theme 还报错，可以尝试去掉它
    demo.launch(server_name="0.0.0.0", server_port=7861)