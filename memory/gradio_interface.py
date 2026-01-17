#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sqlite3
import gradio as gr
import time
import asyncio
from typing import List, Tuple, Optional, Dict
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from langgraph_memorey import app, stream_with_timeout, parse_thinking_content

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

# --- 真正的流式聊天函数 ---
def chat_stream_real(user_id: str, user_input: str, history: List[Dict[str, str]]):
    """真正的流式聊天，边推理边打字"""
    history = history or []
    # 初始状态：用户说了话，助手开始回答
    history.append({"role": "user", "content": user_input})
    history.append({"role": "assistant", "content": ""})
    
    trace_steps = ["🚀 开始流式推理..."]
    
    yield history, "\n".join(trace_steps), get_formatted_memories(user_id), ""
    
    start_time = time.time()
    accumulated_content = ""
    thinking_content = ""
    final_answer = ""
    in_thinking = False
    
    try:
        # 使用真正的流式响应
        from langgraph_memorey import get_streaming_response
        
        chunk_count = 0
        for chunk in get_streaming_response(user_id, user_input):
            if chunk:
                chunk_count += 1
                accumulated_content += chunk
                
                # 简化处理逻辑，先不处理thinking标签
                # 直接显示累积的内容
                history[-1]["content"] = accumulated_content
                
                # 实时更新界面
                elapsed_time = time.time() - start_time
                current_trace = [
                    "🚀 开始流式推理...",
                    f"⚡ 实时生成中... (耗时: {elapsed_time:.1f}s)",
                    f"📦 已收到 {chunk_count} 个chunk",
                    f"📝 当前长度: {len(accumulated_content)} 字符"
                ]
                yield history, "\n".join(current_trace), get_formatted_memories(user_id), ""
                
                # 添加小延迟，让用户能看到打字效果
                time.sleep(0.02)
        
        # 处理thinking标签（在流式完成后）
        if '<thinking>' in accumulated_content and '</thinking>' in accumulated_content:
            thinking_start = accumulated_content.find('<thinking>')
            thinking_end = accumulated_content.find('</thinking>')
            thinking_content = accumulated_content[thinking_start + 10:thinking_end]
            final_answer = accumulated_content[thinking_end + 11:].strip()
            
            # 重新格式化显示
            display_content = f"""
<details>
<summary>🤔 思考过程 (点击展开/折叠)</summary>

{thinking_content}

</details>

**💡 最终回答：**

{final_answer}"""
            history[-1]["content"] = display_content
        
        # 完成
        total_time = time.time() - start_time
        final_trace = [
            "🚀 开始流式推理...",
            f"✅ 生成完成，总耗时: {total_time:.2f}秒",
            f"📝 总字符数: {len(accumulated_content)}",
            f"📦 总chunk数: {chunk_count}"
        ]
        
        # 使用AI判断是否需要记忆更新
        from langgraph_memorey import check_if_needs_memory_update
        has_memory_info = check_if_needs_memory_update(user_input)
        
        if has_memory_info:
            final_trace.append("🧠 AI检测到个人信息，正在后台更新记忆...")
            
        yield history, "\n".join(final_trace), get_formatted_memories(user_id), ""
        
        # 等待一下让记忆更新完成，然后刷新记忆显示
        if has_memory_info:
            time.sleep(3)  # 给AI分析和记忆更新更多时间
            final_trace[-1] = "✅ 智能记忆更新完成"
            yield history, "\n".join(final_trace), get_formatted_memories(user_id), ""
        
    except Exception as e:
        error_msg = f"❌ 流式生成出错: {str(e)}"
        history[-1]["content"] = error_msg
        trace_steps.append(error_msg)
        import traceback
        traceback.print_exc()
        yield history, "\n".join(trace_steps), get_formatted_memories(user_id), ""

# --- 保留原来的函数作为备用 ---
def chat_stream_backup(user_id: str, user_input: str, history: List[Dict[str, str]]):
    history = history or []
    # 初始状态：用户说了话，助手还在思考
    history.append({"role": "user", "content": user_input})
    history.append({"role": "assistant", "content": "🔄 正在思考..."})
    
    trace_steps = []
    config = {"configurable": {"user_id": user_id, "thread_id": f"thread_{user_id}"}}
    input_state = {"messages": [HumanMessage(content=user_input)]}

    yield history, "🚀 工作流启动...", get_formatted_memories(user_id), ""

    start_time = time.time()
    
    try:
        # 使用带超时的流式处理
        stream_generator = stream_with_timeout(input_state, config, timeout_seconds=20)
        
        # 处理流式结果
        has_valid_response = False
        chunk_count = 0
        final_content = ""
        
        for stream_result in stream_generator:
            if stream_result is None:
                # 处理超时或错误
                history[-1]["content"] = "⏰ 处理时间过长或出现错误，为了更好的用户体验，请重新提问。"
                trace_steps.append("⚠️ 处理超时或错误")
                yield history, "\n".join(trace_steps), get_formatted_memories(user_id), ""
                return
            
            chunk, is_timeout = stream_result
            
            if is_timeout:
                # 处理超时情况
                history[-1]["content"] = "⏰ 思考时间过长，为了更好的用户体验，我将提供一个快速回答。如果您需要更详细的分析，请重新提问。"
                trace_steps.append("⚠️ 处理超时 (>20秒)")
                yield history, "\n".join(trace_steps), get_formatted_memories(user_id), ""
                return
            
            chunk_count += 1
            trace_steps.append(f"🔍 处理块 {chunk_count}")
            
            for node_name, node_data in chunk.items():
                elapsed_time = time.time() - start_time
                trace_steps.append(f"📍 节点: {node_name} (耗时: {elapsed_time:.1f}s)")
                
                if "messages" in node_data:
                    for j, msg in enumerate(node_data["messages"]):
                        trace_steps.append(f"  📝 消息 {j+1}: {type(msg).__name__}")
                        if isinstance(msg, AIMessage):
                            if msg.content and msg.content.strip():
                                has_valid_response = True
                                final_content = msg.content
                                trace_steps.append(f"  ✅ 找到有效AI回复，长度: {len(msg.content)}")
                                
                                # 开始流式显示
                                trace_steps.append("  🎬 开始流式显示...")
                                for partial_content in simulate_streaming_display(msg.content):
                                    history[-1]["content"] = partial_content
                                    yield history, "\n".join(trace_steps), get_formatted_memories(user_id), ""
                            else:
                                trace_steps.append(f"  ⚠️ AI消息内容为空")
                            
                        elif hasattr(msg, "tool_calls") and msg.tool_calls:
                            trace_steps.append(f"🔧 正在提取和存储事实信息...")
                
                # 实时更新界面
                if not final_content:  # 只有在还没有最终内容时才更新
                    yield history, "\n".join(trace_steps), get_formatted_memories(user_id), ""

        # 检查是否有有效回复
        if not has_valid_response and history[-1]["content"] == "🔄 正在思考...":
            trace_steps.append("⚠️ 未收到有效的AI回复")
            history[-1]["content"] = "抱歉，我没有收到有效的回复。请检查模型服务是否正常运行。"
        
        total_time = time.time() - start_time
        trace_steps.append(f"✅ 总耗时: {total_time:.2f}秒")
        yield history, "\n".join(trace_steps), get_formatted_memories(user_id), ""

    except Exception as e:
        error_msg = f"❌ 处理出错: {str(e)}"
        history[-1]["content"] = error_msg
        trace_steps.append(error_msg)
        yield history, "\n".join(trace_steps), get_formatted_memories(user_id), ""

# --- 界面构建 ---
with gr.Blocks(title="AI 长期记忆助理") as demo:
    gr.Markdown("""
    # 🧠 LangGraph 长期记忆智能体
    
    **功能特点：**
    - 🔄 **流式输出**：实时显示思考过程和最终回答
    - 🤔 **思考过程**：可折叠的详细推理过程
    - ⏰ **超时保护**：超过20秒自动提供快速回答
    - 💾 **长期记忆**：自动存储和管理用户信息
    """)
    
    with gr.Row():
        u_id = gr.Textbox(label="用户 ID", value="user_001", info="用于区分不同用户的记忆")
        
    with gr.Row():
        with gr.Column(scale=3):
            # 使用原生的聊天界面组件
            chatbot = gr.Chatbot(
                label="💬 对话历史", 
                height=500,
                show_label=True
            )
            
            with gr.Row():
                msg_in = gr.Textbox(
                    label="输入消息", 
                    placeholder="请输入您的问题或告诉我一些关于您的信息... (按Enter发送)", 
                    scale=4,
                    lines=1,
                    interactive=True,
                    show_label=False,
                    container=False
                )
                send_btn = gr.Button("📤 发送", variant="primary", scale=1, size="sm")
            
            with gr.Row():
                clear_btn = gr.Button("🗑️ 清空对话", variant="secondary", scale=1)
        
        with gr.Column(scale=2):
            trace_out = gr.Textbox(
                label="🔍 执行轨迹", 
                interactive=False, 
                lines=12,
                info="显示AI的处理步骤和耗时"
            )
            memo_out = gr.Textbox(
                label="🧠 长期事实库 (SQLite)", 
                interactive=False, 
                lines=15,
                info="AI记住的关于您的信息"
            )

    # 清空对话功能
    def clear_chat():
        return [], "", ""
    
    clear_btn.click(clear_chat, outputs=[chatbot, trace_out, msg_in])
    
    # 绑定发送按钮
    send_event = send_btn.click(
        chat_stream_real, 
        inputs=[u_id, msg_in, chatbot], 
        outputs=[chatbot, trace_out, memo_out, msg_in]
    )
    
    # 绑定Enter键发送消息 - 简化版本
    msg_in.submit(
        fn=chat_stream_real,
        inputs=[u_id, msg_in, chatbot], 
        outputs=[chatbot, trace_out, memo_out, msg_in],
        show_progress=True
    )
    
    demo.load(get_formatted_memories, inputs=[u_id], outputs=[memo_out])

if __name__ == "__main__":
    print("🚀 启动 AI 长期记忆助理...")
    print("📝 功能特点：")
    print("   - 流式输出显示思考过程")
    print("   - 思考内容可折叠查看")
    print("   - 20秒超时保护机制")
    print("   - 长期记忆自动管理")
    print(f"🌐 访问地址: http://0.0.0.0:7864")
    
    try:
        demo.launch(
            server_name="0.0.0.0", 
            server_port=8000,  # 再换个端口
            share=False,
            show_error=True,
            theme=gr.themes.Soft() if hasattr(gr, 'themes') else None
        )
    except Exception as e:
        print(f"❌ 启动失败: {e}")
        print("💡 请检查端口是否被占用或依赖是否正确安装")
