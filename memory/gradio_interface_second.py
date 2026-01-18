#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import gradio as gr
import time
import sqlite3
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from langgraph_memorey_second import app, parse_thinking_content

DB_PATH = "ai_memory.db"

def get_formatted_memories(user_id: str) -> str:
    try:
        # 使用只读连接避免与写事务冲突
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        cursor = conn.execute("SELECT memory_id, content FROM user_memories WHERE user_id = ? ORDER BY updated_at DESC", (user_id,))
        rows = cursor.fetchall()
        conn.close()
        return "\n\n".join([f"📌 {r[0]}\n   └ {r[1]}" for r in rows]) or "📭 目前数据库中无记录。"
    except Exception as e:
        return f"📭 暂无记忆记录 ({str(e)})"

def chat_stream_real(user_id: str, user_input: str, history: list, enable_search: bool):
    history = history or []
    history.append({"role": "user", "content": user_input})
    history.append({"role": "assistant", "content": ""})
    
    trace_steps = ["🚀 LangGraph 工作流启动..."]
    config = {
        "configurable": {
            "user_id": user_id, 
            "thread_id": f"thread_{user_id}", 
            "enable_search": enable_search
        }
    }
    
    yield history, "\n".join(trace_steps), get_formatted_memories(user_id), ""
    
    # 跟踪累积的助手回答
    accumulated_content = ""

    try:
        # 1. 使用 stream_mode="messages" 获取真正的 Token 级流式输出
        for msg, metadata in app.stream(
            {"messages": [HumanMessage(content=user_input)]}, 
            config, 
            stream_mode="messages"  # 关键改动：切换到 messages 模式
        ):
            # 2. 从 metadata 中获取节点信息（用于追踪执行轨迹）
            node_name = metadata.get("langgraph_node")
            if node_name and f"📍 节点: {node_name}" not in trace_steps:
                trace_steps.append(f"📍 节点: {node_name}")
                yield history, "\n".join(trace_steps), get_formatted_memories(user_id), ""
            
            # 3. 处理工具调用（agent决定调用工具时）
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_name = tc['name']
                    tool_args = tc['args']
                    trace_steps.append(f"🔧 触发工具: {tool_name}")
                    
                    # 显示工具参数详情
                    if tool_args:
                        args_str = ", ".join([f"{k}={v}" for k, v in tool_args.items()])
                        trace_steps.append(f"   📋 参数: {args_str}")
                    
                    # 如果是网络搜索，立即给用户显示开始搜索的反馈
                    if tool_name == "web_search":
                        # 显示具体的搜索关键词
                        search_queries = tool_args.get("queries", [])
                        if search_queries:
                            query_str = ", ".join(search_queries)
                            history[-1]["content"] = f"🔧 开始网络搜索: {query_str}..."
                        else:
                            history[-1]["content"] = "🔧 开始网络搜索相关信息..."
                        # 立即返回反馈给用户
                        yield history, "\n".join(trace_steps), get_formatted_memories(user_id), ""
            
            # 4. 处理工具执行结果
            elif isinstance(msg, ToolMessage) and msg.content:
                tool_name = msg.name
                trace_steps.append(f"✅ 工具 '{tool_name}' 执行完成")
                
                # 如果是网络搜索结果，给用户显示搜索完成
                if tool_name == "web_search":
                    history[-1]["content"] = "📊 搜索完成，正在生成回答..."
                
                # 在轨迹中显示工具返回的简要信息
                if len(msg.content) > 100:
                    brief_result = msg.content[:100] + "..."
                    trace_steps.append(f"   📤 结果(简要): {brief_result}")
                else:
                    trace_steps.append(f"   📤 结果: {msg.content}")
                
                yield history, "\n".join(trace_steps), get_formatted_memories(user_id), ""
            
            # 5. 处理模型回答的实时 Token（真正的流式输出）
            elif isinstance(msg, AIMessage) and msg.content:
                # 过滤掉工具调用产生的消息（避免乱码）
                if not hasattr(msg, "tool_calls") or not msg.tool_calls:
                    # 使用自己的累积变量来确保正确追加
                    accumulated_content += msg.content
                    history[-1]["content"] = accumulated_content
                    yield history, "\n".join(trace_steps), get_formatted_memories(user_id), ""

        # 最终处理思考内容折叠
        thinking, final_ans = parse_thinking_content(history[-1]["content"])
        if thinking:
            history[-1]["content"] = f"<details><summary>🤔 思考过程 (点击展开)</summary>\n\n{thinking}\n\n</details>\n\n{final_ans}"
        
        trace_steps.append("✅ 响应生成完毕")
        yield history, "\n".join(trace_steps), get_formatted_memories(user_id), ""

    except Exception as e:
        error_msg = f"❌ 运行错误: {str(e)}"
        trace_steps.append(error_msg)
        history[-1]["content"] = error_msg
        yield history, "\n".join(trace_steps), get_formatted_memories(user_id), ""

# --- Gradio UI 构建 ---
with gr.Blocks(title="AI 长期记忆助理") as demo:
    gr.Markdown("# 🧠 LangGraph 智能记忆助手\n基于 `app.stream()` 推荐架构实现。")
    
    with gr.Row():
        with gr.Column(scale=4):
            chatbot = gr.Chatbot(label="对话历史", height=500)
            msg_in = gr.Textbox(label="发送消息", placeholder="输入内容并按回车...", container=False)
            with gr.Row():
                send_btn = gr.Button("📤 发送", variant="primary")
                clear_btn = gr.Button("🗑️ 清空")
        
        with gr.Column(scale=2):
            u_id = gr.Textbox(label="用户 ID", value="user_001")
            search_en = gr.Checkbox(label="🔍 启用网络搜索", value=False)
            trace_out = gr.Textbox(label="🔍 执行轨迹 (Trace)", interactive=False, lines=10)
            memo_out = gr.Textbox(label="🧠 长期事实库", interactive=False, lines=12)

    send_btn.click(chat_stream_real, [u_id, msg_in, chatbot, search_en], [chatbot, trace_out, memo_out, msg_in])
    msg_in.submit(chat_stream_real, [u_id, msg_in, chatbot, search_en], [chatbot, trace_out, memo_out, msg_in])
    clear_btn.click(lambda: ([], "", ""), None, [chatbot, trace_out, msg_in])
    demo.load(get_formatted_memories, inputs=[u_id], outputs=[memo_out])

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=8000, theme=gr.themes.Soft())
