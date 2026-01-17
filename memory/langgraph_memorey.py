# conda activate unimernet
import sqlite3
from typing import Annotated, TypedDict, Literal, Dict, Optional, Any, List
from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, RemoveMessage, ToolMessage
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.store.sqlite import SqliteStore

# --- 1. 定义状态与工具 ---

class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    summary: str  # 存放压缩后的上下文

# 创建独立的SQLite连接
# 连接1：用于工作流的checkpoint和存储
workflow_conn = sqlite3.connect("ai_memory.db", check_same_thread=False)
checkpointer = SqliteSaver(workflow_conn)
sqlite_store = SqliteStore(workflow_conn)

# 连接2：用于用户记忆管理（避免嵌套事务问题）
memory_conn = sqlite3.connect("ai_memory.db", check_same_thread=False)

# 确保记忆表存在（如果不存在则创建）
try:
    # 创建记忆表
    memory_conn.execute("""
    CREATE TABLE IF NOT EXISTS user_memories (
        user_id TEXT NOT NULL,
        memory_id TEXT NOT NULL,
        content TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, memory_id)
    )
    """)
    memory_conn.commit()
except sqlite3.Error as e:
    print(f"SQLite表创建错误: {e}")

# 内存缓存，用于提高性能
memory_cache: Dict[str, Dict[str, Dict[str, str]]] = {}

# 对话历史缓存，存储每个用户的最近对话
conversation_history: Dict[str, List[Dict[str, str]]] = {}

@tool
def manage_memory(content: Any, action: Literal['upsert', 'delete'], memory_id: str):
    """
    管理长期事实记忆。
    - action='upsert': 当发现用户偏好、身份、重要事实或纠正旧信息时使用。
    - action='delete': 当用户明确要求删除某项信息时使用。
    - memory_id: 简短的键，如 'user_diet', 'work_address'。
    - content: 记忆内容（自动转换为字符串）。
    """
    # 将内容转换为字符串
    content_str = str(content)
    return f"Memory {memory_id} {action}ed with content: {content_str}"

# --- 2. 节点逻辑实现 ---

# 假设你的 vLLM 服务运行在 http://localhost:8000
llm = ChatOpenAI(
    model="",  # 设置一个默认模型名称
    temperature=0.7,
    openai_api_key="EMPTY",  # vLLM 不需要实际 Key，但字段不能为 None
    openai_api_base="http://192.168.1.159:7022/v1",  # 指向 vLLM 的服务地址
    max_tokens=4000,  # 设置默认的最大token数
    timeout=30  # 设置超时时间
)

def call_model_stream(state: State, config: RunnableConfig):
    """简化的模型调用节点，返回完整内容"""
    # 获取用户信息
    user_id = config["configurable"].get("user_id", "default_user")
    
    # 从SQLite存储中检索长期记忆
    user_memories = {}
    try:
        # 先从缓存中获取
        if user_id in memory_cache:
            user_memories = memory_cache[user_id]
        else:
            # 从SQLite数据库查询
            cursor = memory_conn.execute("SELECT memory_id, content FROM user_memories WHERE user_id = ?", (user_id,))
            for row in cursor.fetchall():
                memory_id, content = row
                user_memories[memory_id] = {"data": content}
            # 更新缓存
            memory_cache[user_id] = user_memories
    except sqlite3.Error as e:
        print(f"从SQLite检索记忆错误: {e}")
    
    memories_list = []
    for mem_id, mem_data in user_memories.items():
        memories_list.append(f"- {mem_id}: {mem_data['data']}")
    info = "\n".join(memories_list)
    
    system_prompt = f"""你是一个友好的AI助手，具备长期记忆功能。

    【用户记忆】：
    {info if info else "暂无记录"}
    
    请自然、友好地回答用户的问题。
    
    对于复杂问题，请在回答开头用 <thinking>思考过程</thinking> 来展示推理过程，然后给出最终回答。
    如果用户提到新的个人信息（如姓名、爱好、工作等），请记住它。
    """
    
    messages = [SystemMessage(content=system_prompt)] + state["messages"]
    
    # 检查是否需要记忆功能
    user_message = state['messages'][-1].content.lower() if state['messages'] else ""
    needs_memory = any(keyword in user_message for keyword in ['我叫', '我是', '我的名字', '我住在', '我工作', '我喜欢'])
    
    try:
        print(f"🔍 调用模型...")
        
        # 使用普通调用，在前端实现流式效果
        if needs_memory:
            print("🧠 启用记忆工具...")
            response = llm.bind_tools([manage_memory]).invoke(messages)
        else:
            response = llm.invoke(messages)
        
        print(f"🔍 模型响应完成，长度: {len(response.content) if response.content else 0}")
        return {"messages": [response]}
        
    except Exception as e:
        print(f"❌ 模型调用失败: {e}")
        import traceback
        traceback.print_exc()
        # 返回一个默认回复
        from langchain_core.messages import AIMessage
        fallback_response = AIMessage(content="抱歉，我遇到了一些技术问题。请稍后再试。")
        return {"messages": [fallback_response]}

def call_model(state: State, config: RunnableConfig):
    # 获取用户信息
    user_id = config["configurable"].get("user_id", "default_user")
    
    # 从SQLite存储中检索长期记忆
    user_memories = {}
    try:
        # 先从缓存中获取
        if user_id in memory_cache:
            user_memories = memory_cache[user_id]
        else:
            # 从SQLite数据库查询
            cursor = memory_conn.execute("SELECT memory_id, content FROM user_memories WHERE user_id = ?", (user_id,))
            for row in cursor.fetchall():
                memory_id, content = row
                user_memories[memory_id] = {"data": content}
            # 更新缓存
            memory_cache[user_id] = user_memories
    except sqlite3.Error as e:
        print(f"从SQLite检索记忆错误: {e}")
    
    memories_list = []
    for mem_id, mem_data in user_memories.items():
        memories_list.append(f"- {mem_id}: {mem_data['data']}")
    info = "\n".join(memories_list)
    
    system_prompt = f"""你是一个友好的AI助手，具备长期记忆功能。

    【用户记忆】：
    {info if info else "暂无记录"}
    
    请自然、友好地回答用户的问题。
    
    对于复杂问题，你可以用 <thinking>思考过程</thinking> 来展示推理过程。
    如果用户提到新的个人信息（如姓名、爱好、工作等），请记住它。
    """
    
    messages = [SystemMessage(content=system_prompt)] + state["messages"]
    # 使用流式调用
    try:
        print(f"🔍 调用模型，消息数量: {len(messages)}")
        print(f"🔍 最后一条用户消息: {state['messages'][-1].content if state['messages'] else 'None'}")
        
        # 检查是否需要记忆功能（如果用户提到个人信息）
        user_message = state['messages'][-1].content.lower() if state['messages'] else ""
        needs_memory = any(keyword in user_message for keyword in ['我叫', '我是', '我的名字', '我住在', '我工作', '我喜欢'])
        
        if needs_memory:
            print("🧠 检测到可能需要记忆的信息，启用工具...")
            response = llm.bind_tools([manage_memory]).invoke(messages)
        else:
            response = llm.invoke(messages)
        
        print(f"🔍 模型响应类型: {type(response)}")
        print(f"🔍 模型响应长度: {len(response.content) if hasattr(response, 'content') and response.content else 0}")
        
        return {"messages": [response]}
    except Exception as e:
        print(f"❌ 模型调用失败: {e}")
        import traceback
        traceback.print_exc()
        # 返回一个默认回复
        from langchain_core.messages import AIMessage
        fallback_response = AIMessage(content="抱歉，我遇到了一些技术问题。请稍后再试。")
        return {"messages": [fallback_response]}

def tool_node(state: State, config: RunnableConfig):
    """工具执行节点：执行工具调用并返回ToolMessage"""
    last_msg = state["messages"][-1]
    
    if not hasattr(last_msg, 'tool_calls') or not last_msg.tool_calls:
        return {"messages": []}
    
    tool_messages = []
    for tool_call in last_msg.tool_calls:
        if tool_call["name"] == "manage_memory":
            args = tool_call["args"]
            
            # 执行工具调用
            result = manage_memory.invoke(args)
            
            # 创建ToolMessage
            tool_msg = ToolMessage(
                content=result,
                tool_call_id=tool_call["id"],
                name=tool_call["name"]
            )
            tool_messages.append(tool_msg)
    
    return {"messages": tool_messages}


def reflect_and_store(state: State, config: RunnableConfig):
    """后台反思节点：解析工具调用结果并更新SQLite存储"""
    user_id = config["configurable"].get("user_id", "default_user")
    
    # 获取所有工具消息
    tool_messages = [msg for msg in state["messages"] if isinstance(msg, ToolMessage)]
    if not tool_messages:
        return {"messages": []}
    
    # 解析工具调用结果并更新数据库
    for tool_msg in tool_messages:
        # 找到对应的工具调用
        for msg in reversed(state["messages"]):
            if hasattr(msg, 'tool_calls') and msg.tool_calls:
                for tool_call in msg.tool_calls:
                    if tool_call["id"] == tool_msg.tool_call_id and tool_call["name"] == "manage_memory":
                        args = tool_call["args"]
                        
                        try:
                            if args["action"] == "upsert":
                                # 更新SQLite数据库（使用独立连接）
                                memory_conn.execute(
                                    "INSERT OR REPLACE INTO user_memories (user_id, memory_id, content) VALUES (?, ?, ?)",
                                    (user_id, args["memory_id"], args["content"])
                                )
                                memory_conn.commit()
                                
                                # 更新内存缓存
                                if user_id not in memory_cache:
                                    memory_cache[user_id] = {}
                                memory_cache[user_id][args["memory_id"]] = {"data": args["content"]}
                                
                            elif args["action"] == "delete":
                                # 从SQLite数据库删除（使用独立连接）
                                memory_conn.execute(
                                    "DELETE FROM user_memories WHERE user_id = ? AND memory_id = ?",
                                    (user_id, args["memory_id"])
                                )
                                memory_conn.commit()
                                
                                # 更新内存缓存
                                if user_id in memory_cache and args["memory_id"] in memory_cache[user_id]:
                                    del memory_cache[user_id][args["memory_id"]]
                                    # 如果用户没有记忆了，从缓存中删除用户
                                    if not memory_cache[user_id]:
                                        del memory_cache[user_id]
                                        
                        except sqlite3.Error as e:
                            print(f"SQLite更新错误: {e}")
                        break
        
    return {"messages": [SystemMessage(content="[System: Memory Database Updated]")]}

def summarize_cleanup(state: State):
    """自动清理节点：如果消息过长，压缩历史并删除旧消息"""
    if len(state["messages"]) <= 10:
        return {"messages": [], "summary": state.get("summary", "")}

    # 生成摘要逻辑
    summary_prompt = "请根据对话历史更新总结，确保剔除已被纠正的错误，只保留最新事实。"
    response = llm.invoke(state["messages"] + [HumanMessage(content=summary_prompt)], max_tokens=150)
    
    # 物理删除旧消息（RemoveMessage 指令）
    delete_old_msgs = [RemoveMessage(id=m.id) for m in state["messages"][:-3]]
    
    return {
        "summary": response.content,
        "messages": delete_old_msgs
    }

# --- 3. 构建工作流图 ---

def route_after_agent(state: State):
    last_msg = state["messages"][-1]
    if hasattr(last_msg, 'tool_calls') and last_msg.tool_calls:
        return "tool"
    return "cleanup"

# 注册节点
workflow = StateGraph(State)
workflow.add_node("agent", call_model_stream)  # 使用流式节点
workflow.add_node("tool", tool_node)  # 添加工具执行节点
workflow.add_node("reflect", reflect_and_store)
workflow.add_node("reply_after_tool", call_model_stream)  # 工具后回复也使用流式
workflow.add_node("cleanup", summarize_cleanup)

# 设定连线
workflow.add_edge(START, "agent")

# 条件路由：如果有工具调用则到tool节点，否则到cleanup节点
workflow.add_conditional_edges(
    "agent",
    route_after_agent,
    {
        "tool": "tool",  # 有工具调用时先到tool节点
        "cleanup": "cleanup"
    }
)

# 工具执行后到reflect节点处理结果
workflow.add_edge("tool", "reflect")

# 反思后调用模型生成回复
workflow.add_edge("reflect", "reply_after_tool")

# 回复后到cleanup节点
workflow.add_edge("reply_after_tool", "cleanup")

# 清理后结束
workflow.add_edge("cleanup", END)

# 编译应用，使用SQLite作为checkpointer和存储
app = workflow.compile(
    checkpointer=checkpointer,
    store=sqlite_store
)

# 添加一个专门的流式处理函数
def get_streaming_response(user_id: str, user_input: str):
    """直接的流式响应函数，绕过LangGraph工作流"""
    # 从SQLite存储中检索长期记忆
    user_memories = {}
    try:
        # 先从缓存中获取
        if user_id in memory_cache:
            user_memories = memory_cache[user_id]
        else:
            # 从SQLite数据库查询
            cursor = memory_conn.execute("SELECT memory_id, content FROM user_memories WHERE user_id = ?", (user_id,))
            for row in cursor.fetchall():
                memory_id, content = row
                user_memories[memory_id] = {"data": content}
            # 更新缓存
            memory_cache[user_id] = user_memories
    except sqlite3.Error as e:
        print(f"从SQLite检索记忆错误: {e}")
    
    memories_list = []
    for mem_id, mem_data in user_memories.items():
        memories_list.append(f"- {mem_id}: {mem_data['data']}")
    info = "\n".join(memories_list)
    
    # 获取用户的对话历史（最近5次）
    user_history = conversation_history.get(user_id, [])
    recent_history = user_history[-5:] if len(user_history) > 5 else user_history
    
    # 构建历史对话文本
    history_text = ""
    if recent_history:
        history_text = "\n【最近对话历史】：\n"
        for i, conv in enumerate(recent_history, 1):
            history_text += f"{i}. 用户: {conv['user']}\n   助手: {conv['assistant'][:100]}{'...' if len(conv['assistant']) > 100 else ''}\n"
    
    system_prompt = f"""你是一个友好的AI助手，具备长期记忆功能。

    【用户记忆】：
    {info if info else "暂无记录"}
    {history_text}
    
    请自然、友好地回答用户的问题。参考上述记忆和对话历史，保持对话的连贯性。
    
    对于复杂问题，你可以在回答开头展示思考过程，然后给出最终回答。
    思考过程可以用以下任一标签包围：
    - <thinking>思考过程</thinking>
    - <思考>思考过程</思考>
    - <recollection>思考过程</recollection>
    """
    
    from langchain_core.messages import SystemMessage, HumanMessage
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_input)]
    
    try:
        print(f"🔍 开始真正的流式调用...")
        print(f"📚 引用了 {len(recent_history)} 条历史对话")
        
        # 使用普通流式调用，不绑定工具以确保流畅输出
        stream = llm.stream(messages)
        
        # 实时返回流式内容
        full_response = ""
        for chunk in stream:
            if hasattr(chunk, 'content') and chunk.content:
                full_response += chunk.content
                yield chunk.content
        
        # 保存当前对话到历史记录
        if user_id not in conversation_history:
            conversation_history[user_id] = []
        
        conversation_history[user_id].append({
            "user": user_input,
            "assistant": full_response
        })
        
        # 只保留最近10次对话（用户+助手为一次）
        if len(conversation_history[user_id]) > 10:
            conversation_history[user_id] = conversation_history[user_id][-10:]
        
        print(f"💾 已保存对话历史，当前总数: {len(conversation_history[user_id])}")
        
        # 流式输出完成后，异步处理记忆更新
        import threading
        def delayed_memory_update():
            try:
                update_memory_from_conversation(user_id, user_input, full_response)
            except Exception as e:
                print(f"❌ 延迟记忆更新失败: {e}")
        
        # 启动后台线程处理记忆更新
        memory_thread = threading.Thread(target=delayed_memory_update)
        memory_thread.daemon = True
        memory_thread.start()
        
    except Exception as e:
        print(f"❌ 流式调用失败: {e}")
        import traceback
        traceback.print_exc()
        yield "抱歉，我遇到了一些技术问题。请稍后再试。"

def update_memory_from_conversation(user_id: str, user_input: str, ai_response: str):
    """从对话中提取并更新用户记忆 - 使用AI智能判断"""
    
    # 使用AI来判断是否包含需要记忆的信息
    analysis_prompt = f"""请分析用户的话，判断是否包含需要长期记忆的个人信息。

用户说: "{user_input}"

请判断这句话是否包含以下类型的个人信息，如果包含，请提取具体内容：

1. 姓名/称呼 (user_name)
2. 身份/职业 (user_identity) 
3. 居住地点 (user_location)
4. 工作相关 (user_job)
5. 兴趣爱好 (user_hobby)
6. 学习相关 (user_study)
7. 年龄信息 (user_age)
8. 家庭信息 (user_family)
9. 性格特点 (user_personality)
10. 其他重要个人信息 (user_other)

请按以下格式回答：
如果没有需要记忆的信息，回答：无
如果有需要记忆的信息，回答：
类型:具体内容

例如：
- "我叫张三" -> user_name:张三
- "我住在北京海淀区" -> user_location:北京海淀区
- "我是一名程序员" -> user_identity:程序员
- "我喜欢打篮球和看电影" -> user_hobby:打篮球和看电影

只返回最重要的一个信息，不要解释。"""
    
    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        analysis_messages = [SystemMessage(content=analysis_prompt)]
        
        # 使用AI分析用户输入
        analysis_response = llm.invoke(analysis_messages)
        analysis_result = analysis_response.content.strip()
        
        print(f"🧠 AI分析结果: {analysis_result}")
        
        if analysis_result and analysis_result != "无" and ":" in analysis_result:
            # 解析AI的分析结果
            try:
                memory_type, memory_content = analysis_result.split(":", 1)
                memory_type = memory_type.strip()
                memory_content = memory_content.strip()
                
                if memory_content and len(memory_content) < 200:  # 合理长度限制
                    print(f"🧠 提取到记忆信息: {memory_type} = {memory_content}")
                    
                    # 更新数据库
                    try:
                        memory_conn.execute(
                            "INSERT OR REPLACE INTO user_memories (user_id, memory_id, content) VALUES (?, ?, ?)",
                            (user_id, memory_type, memory_content)
                        )
                        memory_conn.commit()
                        
                        # 更新内存缓存
                        if user_id not in memory_cache:
                            memory_cache[user_id] = {}
                        memory_cache[user_id][memory_type] = {"data": memory_content}
                        
                        print(f"✅ 记忆已更新: {memory_type} -> {memory_content}")
                        
                    except sqlite3.Error as e:
                        print(f"❌ 数据库更新失败: {e}")
                        
            except ValueError as e:
                print(f"❌ 解析AI分析结果失败: {e}")
                
    except Exception as e:
        print(f"❌ AI记忆分析失败: {e}")

def check_if_needs_memory_update(user_input: str):
    """使用AI判断是否需要记忆更新"""
    check_prompt = f"""请判断用户的这句话是否包含需要长期记忆的个人信息。

用户说: "{user_input}"

个人信息包括但不限于：姓名、年龄、职业、居住地、兴趣爱好、学习情况、家庭信息、性格特点等。

请只回答：是 或 否"""
    
    try:
        from langchain_core.messages import SystemMessage
        check_messages = [SystemMessage(content=check_prompt)]
        
        check_response = llm.invoke(check_messages)
        result = check_response.content.strip()
        
        return "是" in result
        
    except Exception as e:
        print(f"❌ AI记忆检查失败: {e}")
        return False

def stream_with_timeout(input_state, config, timeout_seconds=20):
    """
    带超时的流式处理函数 - 生成器版本，支持实时流式输出
    """
    import threading
    import queue
    
    result_queue = queue.Queue()
    exception_queue = queue.Queue()
    
    def run_stream():
        try:
            for chunk in app.stream(input_state, config, stream_mode="updates"):
                result_queue.put(('chunk', chunk))
            result_queue.put(('done', None))
        except Exception as e:
            exception_queue.put(e)
            result_queue.put(('error', str(e)))
    
    # 启动流式处理线程
    thread = threading.Thread(target=run_stream)
    thread.daemon = True
    thread.start()
    
    start_time = time.time()
    
    while True:
        try:
            # 检查是否超时
            elapsed = time.time() - start_time
            if elapsed > timeout_seconds:
                return None, True  # 超时
            
            # 尝试获取结果，设置短超时避免阻塞
            try:
                item_type, item_data = result_queue.get(timeout=0.1)
                
                if item_type == 'chunk':
                    yield item_data, False  # 返回chunk和非超时标志
                elif item_type == 'done':
                    return  # 正常完成
                elif item_type == 'error':
                    return None, False  # 错误，但不是超时
                    
            except queue.Empty:
                continue  # 继续等待
                
        except Exception as e:
            print(f"流式处理异常: {e}")
            return None, False

def parse_thinking_content(content):
    """
    解析思考内容，分离思考过程和最终回答
    支持多种思考标签：<thinking>、<思考>、<recollection>
    """
    if not content:
        return "", ""
    
    # 定义支持的思考标签对
    thinking_tags = [
        ('<thinking>', '</thinking>'),
        ('<思考>', '</思考>'),
        ('<recollection>', '</recollection>')
    ]
    
    # 尝试找到任何一种思考标签
    for start_tag, end_tag in thinking_tags:
        thinking_start = content.find(start_tag)
        thinking_end = content.find(end_tag)
        
        if thinking_start != -1 and thinking_end != -1:
            # 提取思考过程
            thinking_content = content[thinking_start + len(start_tag):thinking_end].strip()
            # 提取最终回答
            final_answer = content[thinking_end + len(end_tag):].strip()
            return thinking_content, final_answer
    
    # 如果没有找到任何思考标签，整个内容作为最终回答
    return "", content

# 添加退出处理函数，确保数据库连接被正确关闭
import atexit

def close_connections():
    try:
        workflow_conn.close()
        memory_conn.close()
        print("✅ SQLite数据库连接已关闭")
    except sqlite3.Error as e:
        print(f"❌ 关闭SQLite数据库连接时出错: {e}")

# 注册退出处理函数
atexit.register(close_connections)
