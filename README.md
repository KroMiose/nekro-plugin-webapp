# WebApp 快速部署插件

> 通过 Cloudflare Workers 将 HTML 内容快速部署为在线可访问的网页

## ✨ 功能特性

- 🚀 **AI 一键部署**：AI 自动将生成的 HTML 部署为在线网页
- 🔑 **权限分离设计**：管理密钥管理系统，访问密钥创建页面
- 🌐 **全球加速**：基于 Cloudflare Workers，享受全球 CDN 加速
- 📊 **可视化管理**：简洁的 Web 管理界面，轻松管理页面和密钥
- ♾️ **灵活配置**：支持自定义过期时间、大小限制等

---

## 📦 快速开始

### 第一步：部署 Worker

请查看完整的部署指南：

👉 **[部署文档（DEPLOYMENT.md）](https://github.com/KroMiose/nekro-plugin-webapp/blob/main/DEPLOYMENT.md)**

部署完成后，你将获得：

- ✅ Worker URL（如：`https://your-worker.pages.dev`）
- ✅ 管理密钥（用于管理界面登录）

### 第二步：配置插件

1. 打开 NekroAgent 插件配置页面
2. 找到 **WebApp 快速部署** 插件
3. 填写 **Worker URL**：你的 Worker 地址
4. 保存配置

### 第三步：创建访问密钥

1. 访问管理界面：[点击跳转](/plugins/KroMiose.nekro_plugin_webapp/)
2. 使用管理密钥登录或创建管理密钥(仅首次访问时需要创建)
3. 在"密钥管理"中点击"创建访问密钥"
4. 输入密钥名称（如：`nekro-agent-access`）
5. 复制生成的访问密钥

### 第四步：配置访问密钥

回到插件配置页面，填写 **ACCESS_KEY**（刚才创建的访问密钥），保存配置。

✅ **配置完成**！现在 AI 可以自动部署网页了。

---

## 🎯 使用方法

### AI 自动调用

AI 会自动调用插件部署网页，你只需要描述需求：

**示例 1：创建简单网页**

```
用户：帮我创建一个显示当前时间的网页

AI：我来创建一个显示实时时间的网页...
已部署：https://your-worker.pages.dev/abc12345
```

**示例 2：创建交互式工具**

```
用户：做个计算器网页

AI：我来创建一个功能完整的计算器...
已部署：https://your-worker.pages.dev/xyz67890
```

**示例 3：创建多页面网站**

```
用户：帮我创建一个简单的个人网站，包含首页、关于和联系页面

AI：好的，我将创建一个多页面网站...
[AI 先创建关于和联系页面，获取URL]
[AI 再创建包含导航链接的首页]
网站已部署：https://your-worker.pages.dev/main001
（首页包含指向其他页面的导航链接）
```

### 管理界面功能

访问管理界面可以：

- 📊 **查看统计信息**：页面总数、访问次数、密钥数量
- 🔑 **管理访问密钥**：创建、查看、删除访问密钥
- 📄 **管理页面**：查看所有部署的页面，一键访问或删除
- 🔗 **快速访问**：点击页面链接直接跳转

---

## ⚙️ 配置说明

| 配置项       | 说明                 | 必填 |
| ------------ | -------------------- | ---- |
| `WORKER_URL` | Worker 访问地址      | ✅   |
| `ACCESS_KEY` | 访问密钥（创建页面） | ✅   |

**说明：**

- `WORKER_URL`：从 Cloudflare Pages 获取的 URL
- `ACCESS_KEY`：在管理界面创建的访问密钥，用于 AI 创建页面

**页面配置**（在 Worker 管理界面中设置）：

- **页面过期天数**：在管理界面"系统配置"中设置（默认 30 天，0 = 永久保留）
- **HTML 最大大小**：在管理界面"系统配置"中设置（默认 500 KB）

---

## 🔐 密钥说明

### 密钥类型

**管理密钥（Admin Key）**：

- ✅ 拥有完全管理权限（创建访问密钥、查看列表、删除等）
- ❌ **不能用于创建页面**（权限分离设计）
- ⚠️ 不要分享给其他人
- 🔧 在首次访问管理界面时设置

**访问密钥（Access Key）**：

- ✅ 用于创建和查看页面
- ❌ 不能管理系统或其他密钥
- ✅ 可以安全分享给 AI 或其他用户使用
- 🔧 在管理界面中创建

### 分享给其他用户

如果你想让其他人也能使用你的 Worker：

1. 在管理界面创建一个访问密钥
2. 将以下信息提供给其他用户：
   - Worker URL：`https://your-worker.pages.dev`
   - 访问密钥：`access-key-xxxxx`
3. 其他用户在自己的 NekroAgent 插件配置中填写即可使用

---

## 📝 注意事项

- ⚠️ HTML 内容大小限制默认为 500KB，可在配置中调整
- ⚠️ 页面默认保留 30 天，可设置为永久保留（0 天）
- ⚠️ 生成的页面 URL 可公开访问，请勿包含敏感信息
- ⚠️ 管理密钥拥有完全权限，请妥善保管
- ⚠️ 数据存储在 Cloudflare D1 中，受 Cloudflare 服务条款约束

---

## ❓ 常见问题

<details>
<summary><strong>Q: 为什么要分离管理密钥和访问密钥？</strong></summary>

**安全设计理念：**

- 管理密钥权限过大，不应该用于日常操作（创建页面）
- 访问密钥只能创建页面，即使泄露也不会影响整个系统
- 符合最小权限原则，提高系统安全性

</details>

<details>
<summary><strong>Q: 提示未配置访问密钥？</strong></summary>

**解决步骤：**

1. 确认已在管理界面创建访问密钥
2. 复制密钥后在插件配置中填写到 `ACCESS_KEY` 字段
3. 保存配置后重启 NekroAgent
4. 管理密钥不能用于创建页面，必须使用访问密钥

</details>

<details>
<summary><strong>Q: 提示 401 Unauthorized 错误？</strong></summary>

**可能原因：**

1. 访问密钥输入错误
2. 访问密钥已被删除
3. Worker URL 配置错误

**解决方案：**

1. 检查 `ACCESS_KEY` 配置是否正确
2. 在管理界面确认密钥是否存在且活跃
3. 确认 `WORKER_URL` 配置正确
4. 重新创建访问密钥并更新配置

</details>

<details>
<summary><strong>Q: 密钥丢失怎么办？</strong></summary>

**管理密钥丢失：**

1. 登录 Cloudflare Dashboard
2. 进入 Worker 的 D1 数据库
3. 查询 `settings` 表：`SELECT * FROM settings WHERE key = 'admin_api_key'`
4. 查看或更新管理密钥

**访问密钥丢失：**

1. 使用管理密钥登录管理界面
2. 删除旧密钥
3. 创建新的访问密钥
4. 在插件配置中更新

</details>

<details>
<summary><strong>Q: HTML 内容有什么限制？</strong></summary>

- **大小限制**：默认 500KB，可在 Worker 管理界面的"系统配置"中调整
- **安全性**：不进行内容过滤，请合理使用
- **外部资源**：可以引用 CDN 上的 CSS、JS 库
- **示例**：
  ```html
  <link
    href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css"
    rel="stylesheet"
  />
  ```

</details>

---

## 📚 相关文档

- [📖 部署指南（DEPLOYMENT.md）](https://github.com/KroMiose/nekro-plugin-webapp/blob/main/DEPLOYMENT.md) - 完整的部署步骤和配置说明
- [💻 开发文档（DEVELOPMENT.md）](https://github.com/KroMiose/nekro-plugin-webapp/blob/main/DEVELOPMENT.md) - 面向开发者的完整开发指南
- [🌐 Cloudflare Workers 文档](https://developers.cloudflare.com/workers/)
- [🗄️ D1 数据库文档](https://developers.cloudflare.com/d1/)

---

## 🛡️ 安全建议

1. **设置强密钥**：管理密钥和访问密钥都应使用强随机字符串（至少 8 个字符）
2. **权限分离**：不要把管理密钥用于创建页面，使用访问密钥
3. **定期轮换**：定期更换访问密钥以提高安全性
4. **妥善保管**：管理密钥拥有完全权限，丢失后只能通过数据库查看

---

## 📄 许可证

MIT License - 详见 [LICENSE](./LICENSE) 文件

---

## 🤝 贡献

欢迎贡献代码、报告问题或提出建议！

- 🐛 [报告 Bug](https://github.com/KroMiose/nekro-plugin-webapp/issues/new)
- 💡 [提出功能建议](https://github.com/KroMiose/nekro-plugin-webapp/issues/new)

---

## 📮 联系方式

- **GitHub**: [@KroMiose](https://github.com/KroMiose)
- **项目主页**: [nekro-plugin-webapp](https://github.com/KroMiose/nekro-plugin-webapp)

---

**Made with ❤️ by NekroAgent Team**

如果觉得这个插件有用，欢迎给个 ⭐ Star！
