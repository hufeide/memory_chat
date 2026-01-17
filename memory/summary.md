conda activate unimernet
这段代码实现了一个基于 **LangGraph** 和 **SQLite** 的**持久化智能体对话系统**，其核心功能可以概括为“多级记忆管理”。

以下是该系统实现的主要功能点：

---

### 1. 多层次记忆机制 (Memory Architecture)

该系统通过三种不同的维度来管理对话背景：

* **短期记忆 (Short-term Memory)**：通过 `State` 中的 `messages` 列表管理当前对话的上下文，支持 `add_messages` 自动追加。
* **中期记忆 (Summary/Context Compression)**：当对话消息超过 10 条时，`summarize_cleanup` 节点会触发，将旧对话压缩成摘要（Summary）并删除冗余消息，防止上下文窗口溢出。
* **长期记忆 (Long-term Fact Storage)**：通过 `user_memories` 数据库表存储用户事实（如偏好、地址等）。这是通过 LLM 自动反思并调用 `manage_memory` 工具实现的。

### 2. 自动反思与知识同步 (Self-Reflection)

* **工具触发式更新**：LLM 在 `agent` 节点中会根据系统提示词（System Prompt）自动判断是否发现了新事实或需要修正旧信息。
* **异步持久化**：如果 LLM 决定更新记忆，它会生成一个 `tool_calls`。随后工作流会跳转到 `reflect_and_store` 节点，将信息写入 SQLite 数据库并同步更新 `memory_cache` 缓存。

### 3. 基于 SQLite 的持久化存储

代码实现了两种不同的持久化逻辑：

* **状态检查点 (Checkpointer)**：使用 `SqliteSaver`。它负责保存整个图的状态（State），即使程序重启，对话也能从上次中断的地方继续。
* **自定义事实库 (Custom Store)**：手动创建了 `user_memories` 表，用于存储结构化的、跨会话的用户事实数据。

### 4. 性能与事务优化

* **双连接机制**：定义了 `workflow_conn` 和 `memory_conn` 两个连接。这种做法有效地规避了在 LangGraph 框架内部处理数据库时可能出现的“嵌套事务”或“数据库锁定”问题。
* **内存缓存 (Cache)**：引入了 `memory_cache` 字典。在 `call_model` 时优先读取缓存，减少对磁盘数据库的 I/O 压力，从而提高响应速度。

### 5. 自动清理与维护 (State Management)

* **消息物理删除**：通过 `RemoveMessage(id=m.id)` 明确指令 LangGraph 删除旧的消息对象，配合摘要功能，实现了高效的上下文管理。
* **优雅关闭**：利用 `atexit` 注册钩子函数，确保程序退出时 SQLite 数据库连接被正确关闭，防止数据库文件损坏。

---

### 工作流逻辑示意图

1. **START** -> **agent**: 模型根据当前消息、摘要和长期记忆生成回答。
2. **agent** (条件分支):
* 如果有 `tool_calls` (需更新记忆) -> **reflect** -> **cleanup**
* 如果没有 -> **cleanup**


3. **cleanup**: 判断是否需要压缩摘要和清理消息。
4. **END**: 输出结果。

### 建议与改进

* **并发安全**：虽然代码中设置了 `check_same_thread=False`，但在多用户并发请求时，手动操作 `memory_cache` 字典可能存在线程竞态风险，建议使用 `threading.Lock`。
* **工具反馈**：目前的 `reflect_and_store` 节点返回了一个 SystemMessage，但并未向 `manage_memory` 工具本身发送 `ToolMessage`。在标准的 LangGraph 开发中，通常需要一个 `ToolNode` 来闭环工具调用的执行结果。


根据你提供的代码逻辑，这个工作流展示了一个典型的**具备记忆反思能力的智能体循环**。

### 工作流拓扑图

---

### 节点逻辑详解

1. **START (开始)**：
* 接收用户的输入消息，并将其放入 `State`。


2. **agent (智能体节点)**：
* **核心逻辑**：LLM 接收当前消息、历史摘要和从 SQLite 读取的长期记忆。
* **决策点**：它会决定是直接回答用户，还是调用 `manage_memory` 工具来更新/删除记忆。


3. **Conditional Edges (条件路由)**：
* 通过 `route_after_agent` 函数判断。
* **路径 A (reflect)**：如果 LLM 生成了 `tool_calls`，则路由到 `tool` 节点。
* **路径 B (cleanup)**：如果 LLM 直接给出了回复，则跳过工具处理，直接准备清理和总结。


4. **tool (工具执行节点)**：
* 实际执行 `manage_memory` 函数。虽然在你的逻辑中反思是在 `reflect` 节点做的，但 `tool` 节点负责产生 `ToolMessage`，让状态保持完整。


5. **reflect (反思持久化节点)**：
* **数据库同步**：解析工具调用的参数，执行 `INSERT` 或 `DELETE` 操作来修改 `user_memories` 表。
* **缓存更新**：同步更新内存中的 `memory_cache`。


6. **cleanup (清理与压缩节点)**：
* 检查消息列表长度。如果过长，则调用 LLM 生成摘要，并使用 `RemoveMessage` 物理删除旧消息，确保下次对话不会超出 Token 限制。


7. **END (结束)**：
* 输出最终的回答，并将状态保存到 `SqliteSaver` 检查点中。



---

### 💡 核心亮点

该图体现了**“双轨记忆更新”**的设计：

* **水平方向**：通过 `agent -> cleanup` 快速响应用户。
* **垂直方向**：通过 `agent -> tool -> reflect` 实现对用户偏好的深度学习和持久化存储。

**需要我为你编写这个图中 `tool_node` 的具体实现代码吗？**