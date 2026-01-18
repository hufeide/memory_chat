import sqlite3
import time
from typing import Annotated, TypedDict, Literal, Dict, List, Any
from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, RemoveMessage, ToolMessage, AIMessage
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.prebuilt import ToolNode
import concurrent.futures
from typing import List, Dict, Any
import time
from ddgs import DDGS

## --- 数据库与状态定义 ---
DB_PATH = "ai_memory.db"
workflow_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
checkpointer = SqliteSaver(workflow_conn)

# 确保用户记忆表存在
workflow_conn.execute("""
CREATE TABLE IF NOT EXISTS user_memories (
    user_id TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    content TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, memory_id)
)
""")
workflow_conn.commit()

class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    summary: str

# --- 工具定义 ---
@tool
def manage_memory(content: str, action: Literal['upsert', 'delete'], memory_id: str):
    """管理长期事实记忆。用于记录用户偏好、身份或重要事实。"""
    return f"Memory {memory_id} {action}ed."

@tool
def web_search(queries: List[str], max_results: int = 3):
    """
    高效的网络搜索工具，支持并发查询和结果去重。
    - queries: 搜索关键词列表，建议针对同一个问题提供 2-3 个不同侧重点的关键词。
    - max_results: 每个关键词返回的结果数量（建议保持在 3-5 之间）。
    """

    def _safe_single_search(query: str, max_retries: int = 2) -> List[Dict[str, Any]]:
        """执行单个搜索，带重试逻辑和基础反爬延迟"""
        for attempt in range(max_retries + 1):
            try:
                # 每次搜索稍微随机延迟，降低被封概率
                time.sleep(0.2 * (attempt + 1)) 
                with DDGS() as ddgs:
                    # 使用 list 强转生成器，捕获可能的 API 错误
                    search_results = list(ddgs.text(query, max_results=max_results))
                    return search_results if search_results else []
            except Exception as e:
                if "Ratelimit" in str(e) and attempt < max_retries:
                    time.sleep(1) # 遇到频率限制多等一会
                    continue
                print(f"⚠️ 搜索 '{query}' 第 {attempt+1} 次尝试失败: {e}")
        return []

    # 1. 使用线程池并发执行搜索，显著提升速度
    all_raw_results = []
    # 限制总搜索词条数，防止任务过重
    active_queries = queries[:3] 
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(active_queries)) as executor:
        future_to_query = {executor.submit(_safe_single_search, q): q for q in active_queries}
        for future in concurrent.futures.as_completed(future_to_query):
            all_raw_results.extend(future.result())

    # 2. 结果去重 (基于 URL)
    unique_results = {}
    for res in all_raw_results:
        url = res.get('href')
        if url and url not in unique_results:
            unique_results[url] = res

    # 3. 格式化输出
    if not unique_results:
        return "❌ 联网搜索未找到相关结果，请尝试更换关键词或稍后再试。"

    formatted_parts = [f"🌐 联网搜索完成，找到 {len(unique_results)} 条唯一来源：\n"]
    for i, res in enumerate(unique_results.values(), 1):
        title = res.get('title', '无标题')
        snippet = res.get('body', '无描述')
        url = res.get('href', '#')
        
        # 限制摘要长度，防止撑爆 Token
        clean_snippet = snippet.replace('\n', ' ').strip()[:250]
        
        formatted_parts.append(f"[{i}] {title}")
        formatted_parts.append(f"    内容: {clean_snippet}...")
        formatted_parts.append(f"    来源: {url}\n")

    return "\n".join(formatted_parts)
# ---web 节点逻辑 ---
llm = ChatOpenAI(
    model="gpt-4o", 
    temperature=0.7, 
    openai_api_base="http://192.168.1.159:7022/v1", 
    openai_api_key="EMPTY",
    streaming=True
)

def call_model_node(state: State, config: RunnableConfig):
    user_id = config["configurable"].get("user_id", "default_user")
    enable_search = config["configurable"].get("enable_search", False)
    
    # 修复 SyntaxError: 先在外部处理逻辑，避免在 f-string 中使用反斜杠
    cursor = workflow_conn.execute("SELECT memory_id, content FROM user_memories WHERE user_id = ?", (user_id,))
    memories_list = [f"- {r[0]}: {r[1]}" for r in cursor.fetchall()]
    memories_str = "\n".join(memories_list) if memories_list else "暂无记录"
    
    system_prompt = f"""你是一个具备长期记忆的助手。
【用户记忆】：
{memories_str}

1.仔细分析完整的消息历史，包括所有之前的工具调用和工具执行结果。
2.如果消息历史中已经包含任何web_search工具返回的搜索结果，请绝对不要再次调用web_search工具，必须基于已有的搜索结果直接回答用户问题。
3.只有在消息历史中完全没有相关搜索结果，且用户询问的是最新信息、实时数据、新闻事件或你不确定的信息时，才可以使用web_search工具搜索相关内容，搜索时请提供相关的关键词列表。
4.回答用户问题时，必须结合web_search工具返回的搜索结果，引用搜索到的相关信息。
5.复杂问题请使用 <thinking>标签记录思考。如果用户提到新个人信息，请调用 manage_memory 工具。
6.请严格使用与用户提问时完全相同的语言来回答问题，绝对不能使用其他语言。例如，如果用户用中文提问，就必须用中文回答；如果用户用英文提问，就必须用英文回答。"""
 
    # 动态绑定工具
    tools = [manage_memory]
    if enable_search:
        tools.append(web_search)
    
    bound_llm = llm.bind_tools(tools)
    response = bound_llm.invoke([SystemMessage(content=system_prompt)] + state["messages"])
    return {"messages": [response]}

from langchain_core.messages import RemoveMessage, AIMessage, ToolMessage, SystemMessage

def reflect_and_store_node(state: State, config: RunnableConfig):
    """
    解析工具调用并持久化。
    - 如果是搜索：保持沉默，不做额外处理（让流程自然流转回 agent）
    - 如果是记忆：执行入库，并抹除消息痕迹实现“静默”
    """
    user_id = config["configurable"].get("user_id", "default_user")
    messages = state["messages"]
    last_msg = messages[-1]
    
    # --- 1. 处理记忆工具 manage_memory ---
    if isinstance(last_msg, ToolMessage) and last_msg.name == "manage_memory":
        # 获取触发该工具的 AI 消息
        last_ai_msg = messages[-2] if len(messages) >= 2 else None
        
        if isinstance(last_ai_msg, AIMessage) and last_ai_msg.tool_calls:
            for tc in last_ai_msg.tool_calls:
                if tc["name"] == "manage_memory":
                    args = tc["args"]
                    # 数据库持久化
                    if args.get("action") == "upsert":
                        workflow_conn.execute(
                            "INSERT OR REPLACE INTO user_memories (user_id, memory_id, content) VALUES (?, ?, ?)",
                            (user_id, args["memory_id"], args["content"])
                        )
                    elif args.get("action") == "delete":
                        workflow_conn.execute(
                            "DELETE FROM user_memories WHERE user_id = ? AND memory_id = ?",
                            (user_id, args["memory_id"])
                        )
            workflow_conn.commit()

        # 核心：使用 RemoveMessage 抹除记忆相关的消息，实现静默
        # 这样回到 agent 节点时，它不知道自己刚刚存过记忆，也就不会回复“已更新”
        return {
            "messages": [
                RemoveMessage(id=last_msg.id),     # 删除 ToolMessage (记忆工具的结果)
                RemoveMessage(id=last_ai_msg.id)   # 删除 AIMessage (发起记忆请求的那条)
            ]
        }

    # --- 2. 处理搜索工具 web_search ---
    # 搜索工具的结果需要被保留，大模型才能根据结果回答问题
    # 我们不需要在这里 append SystemMessage，因为那会干扰模型，
    # 只需要返回空更新，模型会看到已有的 ToolMessage(搜索结果) 并自动结合。
    return {"messages": []}

def summarize_cleanup_node(state: State):
    """消息清理节点"""
    if len(state["messages"]) > 10:
        delete_msgs = [RemoveMessage(id=m.id) for m in state["messages"][:-5]]
        return {"messages": delete_msgs}
    return {"messages": []}

# --- 构建图 ---
def route_after_agent(state: State):
    if state["messages"][-1].tool_calls:
        return "action"
    return "summarize"

workflow = StateGraph(State)
workflow.add_node("agent", call_model_node)
workflow.add_node("action", ToolNode([web_search, manage_memory]))
workflow.add_node("reflect", reflect_and_store_node)
workflow.add_node("summarize", summarize_cleanup_node)

workflow.add_edge(START, "agent")
workflow.add_conditional_edges("agent", route_after_agent)
workflow.add_edge("action", "reflect")
workflow.add_edge("reflect", "agent") 
workflow.add_edge("summarize", END)

app = workflow.compile(checkpointer=checkpointer)

def parse_thinking_content(content: str):
    for s, e in [('<thinking>', '</thinking>'), ('<思考>', '</思考>')]:
        if s in content and e in content:
            thinking = content.split(s)[1].split(e)[0]
            answer = content.split(e)[1].strip()
            return thinking, answer
    return "", content
