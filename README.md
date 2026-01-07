# WebApp 快速部署插件

> 通过 Cloudflare Workers 将 HTML 内容快速部署为在线可访问的网页，支持多 Agent 异步协作开发

## ✨ 功能特性

### 🤖 多 Agent 异步协作 (v2.0 新功能)

- **独立子 Agent**：创建专门的网页开发 Agent 异步工作，主 Agent 只负责需求和反馈
- **智能难度评估**：自动评估任务难度 (1-10)，复杂任务自动使用高级模型
- **实时状态感知**：主 Agent 通过提示词注入实时查看子 Agent 工作进度
- **双向通信**：主 Agent 和子 Agent 可以相互发送消息、询问和反馈
- **会话隔离**：每个会话的 Agent 互相独立，互不干扰

### 🚀 核心功能

- **AI 一键部署**：AI 自动将生成的 HTML 部署为在线网页
- **全球加速**：基于 Cloudflare Workers，享受全球 CDN 加速
- **可视化管理**：简洁的 Web 管理界面，轻松管理页面和密钥
- **权限分离设计**：管理密钥和访问密钥分离，安全可控

---

## 🎯 使用方法

### 多 Agent 协作模式 (推荐)

当用户需要创建网页时，AI 会自动创建一个专门的网页开发 Agent：

```
用户：帮我创建一个个人简历页面，要求现代简约风格，深色主题

AI：✅ 网页开发 Agent [WEB-a3f8] 已创建并开始工作
📝 任务需求: 帮我创建一个个人简历页面，要求现代简约风格，深色主题
📊 难度评估: 🟡 中等 (5/10)

Agent 正在分析需求并开始开发...
```

子 Agent 完成后会通知主 Agent：

```
[系统] ✅ [WebDev Agent WEB-a3f8] (成果)
网页已部署完成！
预览链接: https://your-worker.pages.dev/abc12345

如需修改，请使用 send_to_webapp_agent("WEB-a3f8", "修改意见") 发送反馈。
```

### AI 可用操作

| 方法                                                | 说明                   |
| --------------------------------------------------- | ---------------------- |
| `create_webapp_agent(requirement, difficulty)`      | 创建网页开发 Agent     |
| `send_to_webapp_agent(agent_id, message, msg_type)` | 向 Agent 发送消息/反馈 |
| `confirm_webapp_agent(agent_id)`                    | 确认任务完成           |
| `cancel_webapp_agent(agent_id, reason)`             | 取消 Agent             |
| `get_webapp_preview(agent_id)`                      | 获取预览链接           |
| `list_webapp_agents()`                              | 列出活跃 Agent         |

### 管理员命令

| 命令                 | 说明                     |
| -------------------- | ------------------------ |
| `webapp-list`        | 列出当前会话活跃的 Agent |
| `webapp-info <ID>`   | 查看 Agent 详细信息      |
| `webapp-stats`       | 查看会话统计             |
| `webapp-cancel <ID>` | 取消指定 Agent           |
| `webapp-history`     | 查看历史任务             |
| `webapp-clean`       | 清理已完成记录           |
| `webapp-help`        | 显示帮助                 |

---

## ⚙️ 配置说明

### 基础配置

| 配置项                 | 说明                 | 必填 |
| ---------------------- | -------------------- | ---- |
| `WORKER_URL`           | Worker 访问地址      | ✅   |
| `ACCESS_KEY`           | 访问密钥（创建页面） | ✅   |
| `ENABLE_BASE64_IMAGES` | 允许 Base64 图片嵌入 | ❌   |

### 模型配置 (v2.0)

| 配置项                 | 说明                       | 默认值  |
| ---------------------- | -------------------------- | ------- |
| `WEBDEV_MODEL_GROUP`   | 标准开发模型组             | default |
| `ADVANCED_MODEL_GROUP` | 高级开发模型组（复杂任务） | 空      |
| `DIFFICULTY_THRESHOLD` | 使用高级模型的难度阈值     | 7       |

**说明**：当任务难度评分 ≥ 阈值时，将使用高级模型处理。

### 并发控制

| 配置项                           | 说明                    | 默认值 |
| -------------------------------- | ----------------------- | ------ |
| `MAX_CONCURRENT_AGENTS_PER_CHAT` | 单会话最大并发 Agent 数 | 3      |
| `MAX_COMPLETED_HISTORY`          | 保留的历史任务数        | 10     |
| `MAX_ITERATIONS`                 | 单 Agent 最大迭代次数   | 10     |
| `AGENT_TIMEOUT_MINUTES`          | Agent 超时时间（分钟）  | 30     |

---

## 📦 快速开始

### 第一步：部署 Worker

请查看完整的部署指南：

👉 **[部署文档（DEPLOYMENT.md）](https://github.com/KroMiose/nekro-plugin-webapp/blob/main/DEPLOYMENT.md)**

### 第二步：配置插件

1. 打开 NekroAgent 插件配置页面
2. 找到 **WebApp 快速部署** 插件
3. 填写基础配置：
   - **Worker URL**：你的 Worker 地址
   - **ACCESS_KEY**：访问密钥
4. （可选）配置高级模型：
   - **ADVANCED_MODEL_GROUP**：高级模型组名称
   - **DIFFICULTY_THRESHOLD**：难度阈值
5. 保存配置

### 第三步：创建访问密钥

1. 访问管理界面：[点击跳转](/plugins/KroMiose.nekro_plugin_webapp/)
2. 使用管理密钥登录
3. 在"密钥管理"中创建访问密钥
4. 将访问密钥填入插件配置

✅ **配置完成**！

---

## 📊 难度评估说明

插件会根据需求描述自动评估任务难度：

| 难度 | 等级    | 描述                 | 示例             |
| ---- | ------- | -------------------- | ---------------- |
| 1-3  | 🟢 简单 | 静态展示页、简单介绍 | 公告页、名片页   |
| 4-6  | 🟡 中等 | 响应式布局、基础交互 | 简历页、产品介绍 |
| 7-10 | 🔴 困难 | 复杂动画、数据可视化 | 游戏、数据大屏   |

**自动评估因素**：

- 需求长度和复杂度
- 关键词检测（动画、交互、3D、游戏等）

**手动指定**：

```python
create_webapp_agent("创建一个实时数据大屏", difficulty=8)
```

---

## 🔐 密钥说明

### 密钥类型

**管理密钥（Admin Key）**：

- ✅ 拥有完全管理权限
- ❌ 不能用于创建页面
- ⚠️ 不要分享给其他人

**访问密钥（Access Key）**：

- ✅ 用于创建和查看页面
- ❌ 不能管理系统或其他密钥
- ✅ 可以安全分享给 AI 使用

---

## ❓ 常见问题

<details>
<summary><strong>Q: 多 Agent 模式和直接创建页面有什么区别？</strong></summary>

多 Agent 模式的优势：

- **异步工作**：子 Agent 独立工作，不阻塞主对话
- **迭代优化**：可以多次发送反馈，逐步完善页面
- **状态追踪**：实时查看开发进度
- **智能模型选择**：复杂任务自动使用更强的模型

</details>

<details>
<summary><strong>Q: 高级模型配置有什么作用？</strong></summary>

当配置了 `ADVANCED_MODEL_GROUP` 后：

- 难度 ≥ 阈值的任务会使用高级模型
- 高级模型通常有更强的代码生成能力
- 适合处理复杂的交互、动画、可视化需求

</details>

<details>
<summary><strong>Q: 会话隔离是什么意思？</strong></summary>

- 每个聊天会话的 Agent 是独立的
- 不同会话之间互不干扰
- Agent 数据只在当前会话可见
- 这样可以避免不同用户的任务混淆

</details>

<details>
<summary><strong>Q: 提示 401 Unauthorized 错误？</strong></summary>

**可能原因**：

1. 访问密钥输入错误
2. 访问密钥已被删除
3. Worker URL 配置错误

**解决方案**：

1. 检查 `ACCESS_KEY` 配置是否正确
2. 在管理界面确认密钥是否存在且活跃
3. 确认 `WORKER_URL` 配置正确

</details>

---

## 📚 相关文档

- [📖 部署指南（DEPLOYMENT.md）](https://github.com/KroMiose/nekro-plugin-webapp/blob/main/DEPLOYMENT.md)
- [💻 开发文档（DEVELOPMENT.md）](https://github.com/KroMiose/nekro-plugin-webapp/blob/main/DEVELOPMENT.md)
- [🌐 Cloudflare Workers 文档](https://developers.cloudflare.com/workers/)

---

## 🛡️ 安全建议

1. **设置强密钥**：管理密钥和访问密钥都应使用强随机字符串
2. **权限分离**：不要把管理密钥用于创建页面
3. **定期轮换**：定期更换访问密钥以提高安全性
4. **会话隔离**：不同用户使用不同会话，避免数据混淆

---

## 📄 许可证

MIT License - 详见 [LICENSE](./LICENSE) 文件

---

## 🤝 贡献

欢迎贡献代码、报告问题或提出建议！

- 🐛 [报告 Bug](https://github.com/KroMiose/nekro-plugin-webapp/issues/new)
- 💡 [提出功能建议](https://github.com/KroMiose/nekro-plugin-webapp/issues/new)

---

**Made with ❤️ by NekroAgent Team**

如果觉得这个插件有用，欢迎给个 ⭐ Star！
