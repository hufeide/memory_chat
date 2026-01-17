# conda activate unimernet
import sqlite3
from typing import Annotated, TypedDict, Literal, Dict, Optional, Any
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
    model="", 
    temperature=0.6,
    openai_api_key="EMPTY",  # vLLM 不需要实际 Key，但字段不能为 None
    openai_api_base="http://192.168.1.159:7022/v1"  # 指向 vLLM 的服务地址
)

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
    
    system_prompt = f"""你是一个具备长期记忆和反思能力的助手。
    
    【长期事实库】（这些是已确认的真理）：
    {info if info else "目前尚无记录"}
    
    【当前摘要】：
    {state.get('summary', '暂无摘要')}
    
    任务逻辑：
    1. **正常回答**：无论是否需要调用工具，都必须提供自然、友好的用户回复。
    2. **自动反思**：如果用户提到了新事实，或纠正了【长期事实库】中的错误，必须调用 manage_memory 
       执行 upsert 来同步更新数据库。禁止让错误信息留在记忆库中。
    3. **回复要求**：
       - 如果只是简单的信息更新（如姓名、年龄、工作等），回复要简洁友好，如"我已经记住了您的名字"。
       - 如果用户询问问题，要给出详细、有用的回答。
       - 避免使用机械、生硬的语言。
    """
    
    messages = [SystemMessage(content=system_prompt)] + state["messages"]
    # 绑定工具
    response = llm.bind_tools([manage_memory]).invoke(messages)
    return {"messages": [response]}

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
    response = llm.invoke(state["messages"] + [HumanMessage(content=summary_prompt)])
    
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
workflow.add_node("agent", call_model)
workflow.add_node("tool", tool_node)  # 添加工具执行节点
workflow.add_node("reflect", reflect_and_store)
workflow.add_node("reply_after_tool", call_model)  # 添加工具后回复节点
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