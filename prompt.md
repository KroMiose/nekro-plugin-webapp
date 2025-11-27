# WebApp 快速部署插件 - 项目构建提示词

## 项目概述

这是一个为 NekroAgent 开发的 WebApp 快速部署插件，允许 AI 通过简单的 API 调用将 HTML 内容部署到 Cloudflare Workers 托管服务上，生成可在线访问的网页链接。

### 核心功能

1. **AI 调用接口**：提供 `create_web_app` 沙盒方法，AI 可直接调用部署网页
2. **密钥管理系统**：支持管理密钥和访问密钥，权限分离设计更安全
3. **Web 管理界面**：提供可视化的密钥和页面管理界面
4. **Cloudflare Workers 托管**：利用 Cloudflare 的全球 CDN 和 D1 数据库

### 设计理念

- **简单易用**：AI 只需提供 HTML、标题和描述即可部署
- **傻瓜化部署**：提供一键部署脚本和详细指南
- **安全可控**：密钥系统保护 API，内容大小限制防止滥用
- **可分享**：支持生成访问密钥，让其他用户无需部署即可使用

## 技术架构

### 整体架构图

```
┌─────────────────┐
│   NekroAgent    │
│   (Python)      │
│                 │
│  ┌──────────┐   │
│  │ Plugin   │   │ HTTP API
│  │  Layer   │───┼────────────┐
│  └──────────┘   │            │
└─────────────────┘            │
                               ▼
                    ┌──────────────────────┐
                    │ Cloudflare Worker    │
                    │   (TypeScript)       │
                    │                      │
                    │  ┌───────────────┐   │
                    │  │ API Router    │   │
                    │  │ - /api/*      │   │
                    │  │ - /admin/*    │   │
                    │  │ - /{page_id}  │   │
                    │  └───────────────┘   │
                    │                      │
                    │  ┌───────────────┐   │
                    │  │ Auth System   │   │
                    │  │ - Key Hash    │   │
                    │  │ - Validation  │   │
                    │  └───────────────┘   │
                    │                      │
                    │  ┌───────────────┐   │
                    │  │ D1 Database   │   │
                    │  │ - api_keys    │   │
                    │  │ - pages       │   │
                    │  │ - access_logs │   │
                    │  └───────────────┘   │
                    └──────────────────────┘
                               │
                               │ HTTP
                               ▼
                    ┌──────────────────────┐
                    │   End Users          │
                    │   (Browser)          │
                    └──────────────────────┘
```

### 技术栈

**插件端 (Python)**

- NekroAgent Plugin API
- httpx (HTTP 客户端)
- Pydantic (数据验证)

**Worker 端 (TypeScript)**

- Cloudflare Workers
- D1 Database (SQLite-based)
- Wrangler CLI (部署工具)

**管理界面 (前端)**

- 单文件 Vue.js 3 (CDN 引入)
- 原生 JavaScript
- 响应式 CSS

## 核心功能模块详解

### 1. 插件端实现 (Python)

#### 1.1 插件配置 (plugin.py)

```python
from pydantic import Field
from nekro_agent.api.plugin import ConfigBase, NekroPlugin

plugin = NekroPlugin(
    name="WebApp 快速部署",
    module_name="nekro_plugin_webapp",
    description="将 HTML 内容快速部署到 Cloudflare Workers 并生成在线访问链接",
    version="1.0.0",
    author="NekroAgent Team",
    url="https://github.com/nekro-agent/nekro-plugin-webapp",
)

@plugin.mount_config()
class WebAppConfig(ConfigBase):
    """WebApp 部署配置"""

    WORKER_URL: str = Field(
        default="",
        title="Worker 访问地址",
        description="Cloudflare Worker 的完整 URL (如: https://your-worker.workers.dev)",
    )

    ADMIN_API_KEY: str = Field(
        default="",
        title="管理员密钥",
        description="Worker 管理员密钥，用于管理操作",
        json_schema_extra={"is_secret": True},
    )

    ACCESS_KEY: str = Field(
        default="",
        title="访问密钥",
        description="用于创建页面的访问密钥（需在管理界面创建）",
        json_schema_extra={"is_secret": True},
    )
```

**配置说明**：

- `WORKER_URL`: Worker 部署后的访问地址，必填
- `ACCESS_KEY`: 访问密钥，用于创建页面（在 Worker 管理界面创建）

**页面配置**（在 Worker 管理界面"系统配置"中设置）：

- **页面过期天数**：页面自动过期时间，0 表示永久保留（默认 30 天）
- **HTML 最大大小**：单个 HTML 文件的最大大小限制（默认 500 KB）

#### 1.2 数据模型 (models.py)

```python
from pydantic import BaseModel, Field

class CreatePageRequest(BaseModel):
    """创建页面请求"""
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1, max_length=1000)
    html_content: str = Field(..., min_length=1)
    expires_in_days: int = Field(default=30, ge=0)

class CreatePageResponse(BaseModel):
    """创建页面响应"""
    page_id: str
    url: str
    title: str
    created_at: int
    expires_at: int | None

class PageInfo(BaseModel):
    """页面信息"""
    page_id: str
    title: str
    description: str
    created_at: int
    expires_at: int | None
    access_count: int
    is_active: bool

class ApiKeyInfo(BaseModel):
    """API 密钥信息"""
    key_id: str
    key_name: str
    created_at: int
    expires_at: int | None
    is_active: bool
    usage_count: int
    max_pages: int
    permissions: str
```

**模型说明**：

- 使用 Pydantic 进行数据验证
- 标题和描述设置为必填，符合用户需求
- 所有时间戳使用 Unix 时间戳（整数）

#### 1.3 核心沙盒方法 (**init**.py)

```python
@plugin.mount_sandbox_method(SandboxMethodType.TOOL, "创建网页应用")
async def create_web_app(
    _ctx: AgentCtx,
    html_content: str,
    title: str,
    description: str,
) -> str:
    """将 HTML 内容部署为在线可访问的网页

    Args:
        html_content: 完整的 HTML 内容，包括 CSS 和 JavaScript
        title: 页面标题（必填，用于标识和管理）
        description: 页面描述（必填，说明页面用途）

    Returns:
        str: 可访问的网页 URL 和相关信息
    """

    # 1. 验证参数
    if not title.strip():
        raise ValueError("页面标题不能为空")
    if not description.strip():
        raise ValueError("页面描述不能为空")

    # 2. 验证配置
    if not config.WORKER_URL:
        raise ValueError("未配置 Worker 地址，请先在插件配置中设置 WORKER_URL")

    api_key = config.ACCESS_KEY
    if not api_key:
        raise ValueError("未配置访问密钥，请先在管理界面创建访问密钥并填写到 ACCESS_KEY 配置中")

    # 3. 构造请求（不指定过期天数，使用 Worker 端配置的默认值）
    request_data = CreatePageRequest(
        title=title.strip(),
        description=description.strip(),
        html_content=html_content,
    )

    # 4. 调用 Worker API
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{config.WORKER_URL.rstrip('/')}/api/pages",
                json=request_data.model_dump(),
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()

            result = CreatePageResponse.model_validate(response.json())

            # 6. 返回友好的结果信息
            # JavaScript Date.now() 返回毫秒级时间戳，需要除以 1000 转换为秒
            created_time = datetime.fromtimestamp(result.created_at / 1000).strftime('%Y-%m-%d %H:%M:%S')
            expires_info = f"📅 过期时间: {datetime.fromtimestamp(result.expires_at / 1000).strftime('%Y-%m-%d %H:%M:%S')}" if result.expires_at else "♾️  永久保留"

            return (
                f"✅ 网页部署成功！\n"
                f"📄 标题: {result.title}\n"
                f"🔗 访问链接: {result.url}\n"
                f"🆔 页面ID: {result.page_id}\n"
                f"⏰ 创建时间: {created_time}\n"
                f"{expires_info}"
            )

    except httpx.HTTPStatusError as e:
        error_detail = e.response.text
        raise Exception(f"部署失败: {error_detail}")
    except Exception as e:
        raise Exception(f"部署出错: {str(e)}")
```

**实现要点**：

- 严格的参数验证（标题和描述必填）
- HTML 大小限制检查
- 配置完整性检查
- 友好的错误提示
- 返回格式化的成功信息

#### 1.4 Web 路由 (handlers.py)

```python
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pathlib import Path

def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def index():
        """返回管理界面"""
        static_path = Path(__file__).parent / "static" / "index.html"
        if not static_path.exists():
            raise HTTPException(404, "管理界面未找到")
        return HTMLResponse(static_path.read_text(encoding="utf-8"))

    @router.get("/health")
    async def health_check():
        """健康检查"""
        if not config.WORKER_URL:
            return {"status": "not_configured", "message": "Worker 未配置"}

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    f"{config.WORKER_URL.rstrip('/')}/api/health"
                )
                response.raise_for_status()
                return {"status": "healthy", "worker": response.json()}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    return router
```

### 2. Worker 端实现 (TypeScript)

#### 2.1 类型定义 (worker/src/types.ts)

```typescript
export interface Env {
  DB: D1Database;
  ADMIN_KEY_HASH: string;
}

export interface ApiKey {
  key_id: string;
  key_hash: string;
  key_name: string;
  created_by: string | null;
  created_at: number;
  expires_at: number | null;
  is_active: number;
  usage_count: number;
  max_pages: number;
  permissions: string;
  metadata: string | null;
}

export interface Page {
  page_id: string;
  title: string;
  description: string;
  html_content: string;
  created_by: string;
  created_at: number;
  expires_at: number | null;
  access_count: number;
  last_accessed: number | null;
  is_active: number;
  metadata: string | null;
}

export interface CreatePageRequest {
  title: string;
  description: string;
  html_content: string;
  expires_in_days?: number;
}

export interface CreatePageResponse {
  page_id: string;
  url: string;
  title: string;
  created_at: number;
  expires_at: number | null;
}
```

#### 2.2 密钥验证 (worker/src/auth.ts)

```typescript
import { Env, ApiKey } from "./types";

/**
 * 计算字符串的 SHA-256 哈希
 */
async function sha256(message: string): Promise<string> {
  const msgBuffer = new TextEncoder().encode(message);
  const hashBuffer = await crypto.subtle.digest("SHA-256", msgBuffer);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map((b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * 验证 API 密钥
 */
export async function validateApiKey(
  authorization: string | null,
  env: Env
): Promise<{ valid: boolean; keyId?: string; permissions?: string[] }> {
  if (!authorization || !authorization.startsWith("Bearer ")) {
    return { valid: false };
  }

  const apiKey = authorization.substring(7);
  const keyHash = await sha256(apiKey);

  // 检查是否是管理员密钥
  if (keyHash === env.ADMIN_KEY_HASH) {
    return {
      valid: true,
      keyId: "admin",
      permissions: ["create", "view", "delete", "manage"],
    };
  }

  // 检查数据库中的密钥
  const result = await env.DB.prepare(
    "SELECT * FROM api_keys WHERE key_hash = ? AND is_active = 1"
  )
    .bind(keyHash)
    .first<ApiKey>();

  if (!result) {
    return { valid: false };
  }

  // 检查是否过期
  if (result.expires_at && result.expires_at < Date.now()) {
    return { valid: false };
  }

  // 更新使用次数
  await env.DB.prepare(
    "UPDATE api_keys SET usage_count = usage_count + 1 WHERE key_id = ?"
  )
    .bind(result.key_id)
    .run();

  return {
    valid: true,
    keyId: result.key_id,
    permissions: result.permissions.split(","),
  };
}

/**
 * 检查权限
 */
export function hasPermission(
  permissions: string[],
  required: string
): boolean {
  return permissions.includes(required) || permissions.includes("manage");
}
```

#### 2.3 数据库操作 (worker/src/storage.ts)

```typescript
import { Env, Page, ApiKey, CreatePageRequest } from "./types";

/**
 * 生成唯一的页面 ID
 */
function generatePageId(): string {
  return crypto.randomUUID().substring(0, 8);
}

/**
 * 创建页面
 */
export async function createPage(
  request: CreatePageRequest,
  createdBy: string,
  env: Env
): Promise<Page> {
  const now = Date.now();
  const pageId = generatePageId();
  const expiresAt =
    request.expires_in_days && request.expires_in_days > 0
      ? now + request.expires_in_days * 24 * 60 * 60 * 1000
      : null;

  await env.DB.prepare(
    `
            INSERT INTO pages (
                page_id, title, description, html_content,
                created_by, created_at, expires_at, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        `
  )
    .bind(
      pageId,
      request.title,
      request.description,
      request.html_content,
      createdBy,
      now,
      expiresAt
    )
    .run();

  return {
    page_id: pageId,
    title: request.title,
    description: request.description,
    html_content: request.html_content,
    created_by: createdBy,
    created_at: now,
    expires_at: expiresAt,
    access_count: 0,
    last_accessed: null,
    is_active: 1,
    metadata: null,
  };
}

/**
 * 获取页面
 */
export async function getPage(pageId: string, env: Env): Promise<Page | null> {
  const result = await env.DB.prepare(
    "SELECT * FROM pages WHERE page_id = ? AND is_active = 1"
  )
    .bind(pageId)
    .first<Page>();

  if (!result) {
    return null;
  }

  // 检查是否过期
  if (result.expires_at && result.expires_at < Date.now()) {
    return null;
  }

  return result;
}

/**
 * 更新访问计数
 */
export async function incrementAccessCount(
  pageId: string,
  env: Env
): Promise<void> {
  await env.DB.prepare(
    `
            UPDATE pages 
            SET access_count = access_count + 1, last_accessed = ?
            WHERE page_id = ?
        `
  )
    .bind(Date.now(), pageId)
    .run();
}

/**
 * 删除页面
 */
export async function deletePage(pageId: string, env: Env): Promise<boolean> {
  const result = await env.DB.prepare(
    "UPDATE pages SET is_active = 0 WHERE page_id = ?"
  )
    .bind(pageId)
    .run();

  return result.success;
}

/**
 * 列出所有页面
 */
export async function listPages(
  env: Env,
  limit: number = 100
): Promise<Page[]> {
  const result = await env.DB.prepare(
    `
            SELECT * FROM pages 
            WHERE is_active = 1 
            ORDER BY created_at DESC 
            LIMIT ?
        `
  )
    .bind(limit)
    .all<Page>();

  return result.results || [];
}

/**
 * 创建 API 密钥
 */
export async function createApiKey(
  keyName: string,
  apiKey: string,
  keyHash: string,
  createdBy: string,
  env: Env
): Promise<ApiKey> {
  const now = Date.now();
  const keyId = crypto.randomUUID().substring(0, 12);

  await env.DB.prepare(
    `
            INSERT INTO api_keys (
                key_id, key_hash, key_name, created_by,
                created_at, is_active, max_pages, permissions
            ) VALUES (?, ?, ?, ?, ?, 1, 100, 'create,view')
        `
  )
    .bind(keyId, keyHash, keyName, createdBy, now)
    .run();

  return {
    key_id: keyId,
    key_hash: keyHash,
    key_name: keyName,
    created_by: createdBy,
    created_at: now,
    expires_at: null,
    is_active: 1,
    usage_count: 0,
    max_pages: 100,
    permissions: "create,view",
    metadata: null,
  };
}

/**
 * 列出所有 API 密钥
 */
export async function listApiKeys(env: Env): Promise<ApiKey[]> {
  const result = await env.DB.prepare(
    "SELECT * FROM api_keys WHERE is_active = 1 ORDER BY created_at DESC"
  ).all<ApiKey>();

  return result.results || [];
}

/**
 * 删除 API 密钥
 */
export async function deleteApiKey(keyId: string, env: Env): Promise<boolean> {
  const result = await env.DB.prepare(
    "UPDATE api_keys SET is_active = 0 WHERE key_id = ?"
  )
    .bind(keyId)
    .run();

  return result.success;
}

/**
 * 获取统计信息
 */
export async function getStats(env: Env): Promise<any> {
  const pagesCount = await env.DB.prepare(
    "SELECT COUNT(*) as count FROM pages WHERE is_active = 1"
  ).first<{ count: number }>();

  const keysCount = await env.DB.prepare(
    "SELECT COUNT(*) as count FROM api_keys WHERE is_active = 1"
  ).first<{ count: number }>();

  const totalAccess = await env.DB.prepare(
    "SELECT SUM(access_count) as total FROM pages WHERE is_active = 1"
  ).first<{ total: number }>();

  return {
    pages_count: pagesCount?.count || 0,
    keys_count: keysCount?.count || 0,
    total_access: totalAccess?.total || 0,
  };
}
```

#### 2.4 主入口 (worker/src/index.ts)

```typescript
import { Env, CreatePageRequest, CreatePageResponse } from "./types";
import { validateApiKey, hasPermission } from "./auth";
import {
  createPage,
  getPage,
  incrementAccessCount,
  deletePage,
  listPages,
  createApiKey,
  listApiKeys,
  deleteApiKey,
  getStats,
} from "./storage";

/**
 * CORS 响应头
 */
function corsHeaders() {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
  };
}

/**
 * JSON 响应
 */
function jsonResponse(data: any, status: number = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json",
      ...corsHeaders(),
    },
  });
}

/**
 * 错误响应
 */
function errorResponse(message: string, status: number = 400) {
  return jsonResponse({ error: message }, status);
}

/**
 * HTML 响应
 */
function htmlResponse(html: string) {
  return new Response(html, {
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      ...corsHeaders(),
    },
  });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;

    // OPTIONS 请求处理（CORS 预检）
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders() });
    }

    // API 路由
    if (path.startsWith("/api/")) {
      return handleAPI(request, env, path);
    }

    // 管理路由
    if (path.startsWith("/admin/")) {
      return handleAdmin(request, env, path);
    }

    // 根路径 - 返回管理界面
    if (path === "/" || path === "/index.html") {
      return htmlResponse(MANAGEMENT_UI_HTML);
    }

    // 页面访问 /{page_id}
    const pageId = path.substring(1);
    if (pageId) {
      return servePage(pageId, env);
    }

    return errorResponse("Not Found", 404);
  },
};

/**
 * 处理 API 请求
 */
async function handleAPI(
  request: Request,
  env: Env,
  path: string
): Promise<Response> {
  // 健康检查
  if (path === "/api/health") {
    return jsonResponse({ status: "healthy", timestamp: Date.now() });
  }

  // 验证 API 密钥
  const auth = await validateApiKey(request.headers.get("Authorization"), env);
  if (!auth.valid) {
    return errorResponse("Unauthorized", 401);
  }

  // POST /api/pages - 创建页面
  if (path === "/api/pages" && request.method === "POST") {
    if (!hasPermission(auth.permissions!, "create")) {
      return errorResponse("Forbidden", 403);
    }

    try {
      const body = (await request.json()) as CreatePageRequest;

      // 验证必填字段
      if (!body.title || !body.title.trim()) {
        return errorResponse("标题不能为空");
      }
      if (!body.description || !body.description.trim()) {
        return errorResponse("描述不能为空");
      }
      if (!body.html_content || !body.html_content.trim()) {
        return errorResponse("HTML 内容不能为空");
      }

      const page = await createPage(body, auth.keyId!, env);

      const response: CreatePageResponse = {
        page_id: page.page_id,
        url: `${new URL(request.url).origin}/${page.page_id}`,
        title: page.title,
        created_at: page.created_at,
        expires_at: page.expires_at,
      };

      return jsonResponse(response, 201);
    } catch (e: any) {
      return errorResponse(e.message || "创建页面失败", 500);
    }
  }

  // GET /api/pages/{id} - 获取页面信息
  const pageIdMatch = path.match(/^\/api\/pages\/([a-zA-Z0-9-]+)$/);
  if (pageIdMatch && request.method === "GET") {
    const pageId = pageIdMatch[1];
    const page = await getPage(pageId, env);

    if (!page) {
      return errorResponse("页面不存在或已过期", 404);
    }

    // 不返回 HTML 内容，只返回元数据
    return jsonResponse({
      page_id: page.page_id,
      title: page.title,
      description: page.description,
      created_at: page.created_at,
      expires_at: page.expires_at,
      access_count: page.access_count,
    });
  }

  // DELETE /api/pages/{id} - 删除页面
  if (pageIdMatch && request.method === "DELETE") {
    if (!hasPermission(auth.permissions!, "delete")) {
      return errorResponse("Forbidden", 403);
    }

    const pageId = pageIdMatch[1];
    const success = await deletePage(pageId, env);

    if (!success) {
      return errorResponse("删除失败", 500);
    }

    return jsonResponse({ message: "删除成功" });
  }

  return errorResponse("Not Found", 404);
}

/**
 * 处理管理请求
 */
async function handleAdmin(
  request: Request,
  env: Env,
  path: string
): Promise<Response> {
  // 验证管理员权限
  const auth = await validateApiKey(request.headers.get("Authorization"), env);
  if (!auth.valid || !hasPermission(auth.permissions!, "manage")) {
    return errorResponse("Unauthorized", 401);
  }

  // GET /admin/pages - 列出所有页面
  if (path === "/admin/pages" && request.method === "GET") {
    const pages = await listPages(env);
    return jsonResponse(pages);
  }

  // POST /admin/keys - 创建新密钥
  if (path === "/admin/keys" && request.method === "POST") {
    try {
      const body = (await request.json()) as { key_name: string };

      if (!body.key_name || !body.key_name.trim()) {
        return errorResponse("密钥名称不能为空");
      }

      // 生成新密钥
      const newKey = crypto.randomUUID();
      const keyHash = await crypto.subtle.digest(
        "SHA-256",
        new TextEncoder().encode(newKey)
      );
      const keyHashHex = Array.from(new Uint8Array(keyHash))
        .map((b) => b.toString(16).padStart(2, "0"))
        .join("");

      const apiKey = await createApiKey(
        body.key_name,
        newKey,
        keyHashHex,
        auth.keyId!,
        env
      );

      // 返回密钥原文（仅此一次）
      return jsonResponse(
        {
          ...apiKey,
          api_key: newKey, // 原始密钥，仅显示一次
        },
        201
      );
    } catch (e: any) {
      return errorResponse(e.message || "创建密钥失败", 500);
    }
  }

  // GET /admin/keys - 列出所有密钥
  if (path === "/admin/keys" && request.method === "GET") {
    const keys = await listApiKeys(env);
    return jsonResponse(keys);
  }

  // DELETE /admin/keys/{id} - 删除密钥
  const keyIdMatch = path.match(/^\/admin\/keys\/([a-zA-Z0-9-]+)$/);
  if (keyIdMatch && request.method === "DELETE") {
    const keyId = keyIdMatch[1];
    const success = await deleteApiKey(keyId, env);

    if (!success) {
      return errorResponse("删除失败", 500);
    }

    return jsonResponse({ message: "删除成功" });
  }

  // GET /admin/stats - 获取统计信息
  if (path === "/admin/stats" && request.method === "GET") {
    const stats = await getStats(env);
    return jsonResponse(stats);
  }

  return errorResponse("Not Found", 404);
}

/**
 * 提供页面服务
 */
async function servePage(pageId: string, env: Env): Promise<Response> {
  const page = await getPage(pageId, env);

  if (!page) {
    return htmlResponse(`
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <title>页面不存在</title>
                <style>
                    body {
                        font-family: system-ui, -apple-system, sans-serif;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        height: 100vh;
                        margin: 0;
                        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                        color: white;
                    }
                    .error {
                        text-align: center;
                    }
                    h1 {
                        font-size: 4em;
                        margin: 0;
                    }
                </style>
            </head>
            <body>
                <div class="error">
                    <h1>404</h1>
                    <p>页面不存在或已过期</p>
                </div>
            </body>
            </html>
        `);
  }

  // 更新访问计数
  await incrementAccessCount(pageId, env);

  return htmlResponse(page.html_content);
}

// 管理界面 HTML（将在后面定义）
const MANAGEMENT_UI_HTML = `<!-- 管理界面将在 static/index.html 中定义 -->`;
```

### 3. 密钥系统详解

#### 3.1 密钥生成流程

```
1. 用户在管理界面点击"创建密钥"
   ↓
2. 输入密钥名称（用于标识）
   ↓
3. Worker 生成随机 UUID 作为密钥
   ↓
4. 计算 SHA-256 哈希值
   ↓
5. 存储哈希值到数据库
   ↓
6. 返回原始密钥给用户（仅此一次）
   ↓
7. 用户复制密钥并保存
```

#### 3.2 密钥验证流程

```
1. 插件/用户发起 API 请求
   ↓
2. 在 Authorization header 中携带密钥
   ↓
3. Worker 提取密钥并计算哈希
   ↓
4. 先检查是否匹配管理员密钥哈希
   ↓
5. 否则在数据库中查找匹配的密钥哈希
   ↓
6. 检查密钥是否有效（未过期、未禁用）
   ↓
7. 检查权限是否满足操作要求
   ↓
8. 更新密钥使用计数
   ↓
9. 允许访问或拒绝（401/403）
```

#### 3.3 密钥类型和权限

**管理员密钥**：

- 权限：`create`, `view`, `delete`, `manage`
- 用途：完全管理权限
- 配置：在 `wrangler.toml` 中设置环境变量

**访问密钥**：

- 权限：`create`, `view`
- 用途：只能创建和查看页面
- 特点：可分享给其他用户使用

### 4. 管理界面设计

#### 4.1 功能模块

**密钥管理**：

- 创建新密钥（输入名称）
- 显示密钥列表（隐藏哈希值）
- 复制密钥到剪贴板
- 删除密钥

**页面管理**：

- 显示所有页面列表
- 显示标题、描述、访问次数
- 页面预览链接
- 删除页面

**统计信息**：

- 总页面数
- 总访问次数
- 活跃密钥数

**配置指南**：

- Worker URL 配置说明
- 密钥配置说明
- 快速开始步骤

#### 4.2 UI 设计要点

- 单页应用，无需路由
- 使用 Vue 3 的 CDN 版本
- 响应式设计（移动端友好）
- 暗色主题为主
- 简洁明了，避免复杂操作
- 一键复制功能
- 清晰的错误提示

## 部署流程（傻瓜化）

### 方式一：一键部署脚本

提供 `deploy.sh` 脚本：

```bash
#!/bin/bash
# WebApp Worker 一键部署脚本

echo "🚀 WebApp Worker 部署向导"
echo "========================"

# 检查依赖
if ! command -v node &> /dev/null; then
    echo "❌ 未检测到 Node.js，请先安装"
    exit 1
fi

if ! command -v npm &> /dev/null; then
    echo "❌ 未检测到 npm，请先安装"
    exit 1
fi

# 检查 Wrangler
if ! command -v wrangler &> /dev/null; then
    echo "📦 安装 Wrangler CLI..."
    npm install -g wrangler
fi

# 登录 Cloudflare
echo "🔐 请登录 Cloudflare 账号..."
wrangler login

# 进入 worker 目录
cd worker

# 安装依赖
echo "📦 安装依赖..."
npm install

# 创建 D1 数据库
echo "🗄️  创建 D1 数据库..."
wrangler d1 create webapp-db

# 提示用户更新 wrangler.toml
echo ""
echo "⚠️  请按照提示更新 wrangler.toml 中的数据库配置"
echo "按回车继续..."
read

# 初始化数据库
echo "🔧 初始化数据库..."
wrangler d1 execute webapp-db --file=schema.sql

# 生成管理员密钥
ADMIN_KEY=$(openssl rand -base64 32)
ADMIN_KEY_HASH=$(echo -n "$ADMIN_KEY" | openssl dgst -sha256 | awk '{print $2}')

echo ""
echo "🔑 生成的管理员密钥："
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "$ADMIN_KEY"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "⚠️  请立即保存此密钥！它只会显示一次！"
echo ""

# 设置环境变量
echo "🔧 配置环境变量..."
wrangler secret put ADMIN_KEY_HASH <<< "$ADMIN_KEY_HASH"

# 部署
echo "🚀 部署 Worker..."
wrangler deploy

echo ""
echo "✅ 部署完成！"
echo ""
echo "📝 下一步："
echo "1. 在 NekroAgent 插件配置中填写 Worker URL"
echo "2. 在插件配置中填写上面的管理员密钥"
echo "3. 访问 Worker URL 打开管理界面"
```

### 方式二：分步手动部署

详见 `DEPLOYMENT.md` 文档。

## 安全考虑

### 1. 输入验证

- HTML 大小限制（防止滥用）
- 标题和描述长度限制
- SQL 注入防护（使用参数化查询）
- XSS 防护（内容存储时不转义，展示时依赖浏览器安全策略）

### 2. 访问控制

- 所有敏感操作需要密钥验证
- 密钥使用 SHA-256 哈希存储
- 不同密钥有不同权限级别
- 管理密钥与访问密钥分离（权限分离设计）

### 3. 速率限制

- 可在 Worker 层面配置速率限制
- 限制单个 IP 的请求频率
- 限制单个密钥的创建页面数量

### 4. 数据保护

- 密钥原文只在创建时显示一次
- 数据库存储密钥哈希值
- 过期页面自动失效
- 支持手动删除页面

### 5. CORS 配置

- 允许跨域访问（公共服务）
- 敏感操作需要验证
- OPTIONS 预检请求处理

## 使用示例

### AI 调用示例

**场景 1：创建简单网页**

```
用户：帮我创建一个显示当前时间的网页

AI：好的，我来创建一个显示实时时间的网页。

[调用 create_web_app 方法]
html_content = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>实时时钟</title>
    <style>
        body {
            margin: 0;
            height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            font-family: 'Arial', sans-serif;
        }
        .clock {
            font-size: 4em;
            color: white;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
        }
    </style>
</head>
<body>
    <div class="clock" id="clock"></div>
    <script>
        function updateClock() {
            const now = new Date();
            const time = now.toLocaleTimeString('zh-CN');
            document.getElementById('clock').textContent = time;
        }
        updateClock();
        setInterval(updateClock, 1000);
    </script>
</body>
</html>
'''

create_web_app(
    html_content=html_content,
    title="实时时钟",
    description="显示当前系统时间的网页"
)

返回：
✅ 网页部署成功！
📄 标题: 实时时钟
🔗 访问链接: https://your-worker.workers.dev/abc12345
🆔 页面ID: abc12345
⏰ 创建时间: 2025-10-22 15:30:00
📅 过期时间: 2025-11-21 15:30:00
```

**场景 2：创建数据可视化**

```
用户：帮我创建一个展示销售数据的图表页面

AI：我会创建一个使用 Chart.js 的销售数据可视化页面。

[调用 create_web_app 方法]
html_content = '''
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>销售数据</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 800px;
            margin: 50px auto;
            padding: 20px;
        }
        h1 {
            text-align: center;
            color: #333;
        }
    </style>
</head>
<body>
    <h1>月度销售数据</h1>
    <canvas id="salesChart"></canvas>
    <script>
        const ctx = document.getElementById('salesChart').getContext('2d');
        new Chart(ctx, {
            type: 'bar',
            data: {
                labels: ['1月', '2月', '3月', '4月', '5月', '6月'],
                datasets: [{
                    label: '销售额（万元）',
                    data: [12, 19, 3, 5, 2, 3],
                    backgroundColor: 'rgba(54, 162, 235, 0.5)',
                    borderColor: 'rgba(54, 162, 235, 1)',
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true,
                scales: {
                    y: {
                        beginAtZero: true
                    }
                }
            }
        });
    </script>
</body>
</html>
'''

create_web_app(
    html_content=html_content,
    title="月度销售数据图表",
    description="展示公司上半年月度销售额的柱状图"
)
```

### 用户管理操作

**创建访问密钥**：

1. 访问 Worker URL（管理界面）
2. 输入管理密钥登录
3. 点击"创建新密钥"
4. 输入名称（如："nekro-agent-access"）
5. 复制生成的密钥
6. 在插件配置中填写

**配置插件**：

1. 在 NekroAgent 插件配置中
2. 填写 `WORKER_URL`（如：`https://your-worker.workers.dev`）
3. 填写 `ACCESS_KEY`（访问密钥）
4. 保存配置

**查看已创建的页面**：

1. 访问管理界面
2. 查看"页面列表"
3. 点击链接预览
4. 查看访问统计

## 常见问题

### Q1: 如何获取 Cloudflare 账号？

访问 https://dash.cloudflare.com/sign-up 注册免费账号。

### Q2: D1 数据库有什么限制？

免费计划：

- 10 GB 存储
- 每天 500 万次读取
- 每天 10 万次写入

足够个人和小团队使用。

### Q3: Worker 部署后无法访问？

检查：

1. Worker 是否部署成功（`wrangler deploy`）
2. D1 数据库是否绑定（`wrangler.toml` 配置）
3. 管理员密钥是否正确设置
4. 访问 `/api/health` 检查状态

### Q4: 如何更新 Worker 代码？

```bash
cd worker
# 修改代码后
wrangler deploy
```

### Q5: 如何备份数据？

```bash
# 导出页面数据
wrangler d1 export webapp-db --output=backup.sql

# 恢复数据
wrangler d1 execute webapp-db --file=backup.sql
```

### Q6: 密钥丢失怎么办？

- 管理员密钥：重新生成并更新环境变量
- 访问密钥：在管理界面删除旧密钥，创建新密钥

### Q7: 如何删除过期页面？

可以在管理界面手动删除，或者添加定时任务自动清理：

```typescript
// 在 Worker 中添加 scheduled 处理器
export default {
  async scheduled(event: ScheduledEvent, env: Env) {
    // 删除过期页面
    await env.DB.prepare("UPDATE pages SET is_active = 0 WHERE expires_at < ?")
      .bind(Date.now())
      .run();
  },
};
```

### Q8: HTML 内容有什么限制？

- 大小：最大 500KB（可在配置中调整）
- 安全：不进行内容过滤，建议使用 CSP
- 外部资源：可以引用 CDN 资源

### Q9: 如何监控使用情况？

- 访问管理界面查看统计信息
- 查看 Worker 日志：`wrangler tail`
- 在 Cloudflare Dashboard 查看分析数据

### Q10: 如何自定义域名？

在 Cloudflare Dashboard 中：

1. 添加自定义域名
2. 绑定到 Worker
3. 配置 DNS
4. 更新插件配置中的 `WORKER_URL`

## 项目文件清单

```
nekro-plugin-webapp/
├── __init__.py              ✅ 主插件文件
├── plugin.py                ✅ 配置定义
├── models.py                ✅ 数据模型
├── handlers.py              ✅ Web 路由
├── worker/
│   ├── src/
│   │   ├── index.ts        ✅ Worker 入口
│   │   ├── storage.ts      ✅ 数据库操作
│   │   ├── auth.ts         ✅ 认证逻辑
│   │   └── types.ts        ✅ 类型定义
│   ├── schema.sql          ✅ 数据库 Schema
│   ├── wrangler.toml       ✅ Worker 配置
│   ├── package.json        ✅ Node 依赖
│   ├── tsconfig.json       ✅ TS 配置
│   └── deploy.sh           ✅ 部署脚本
├── static/
│   └── index.html          ✅ 管理界面
├── README.md               ✅ 使用文档
├── DEPLOYMENT.md           ✅ 部署指南
└── prompt.md               ✅ 本文档
```

## 开发注意事项

1. **严格的类型注解**：Python 和 TypeScript 都要求严格类型
2. **错误处理**：提供清晰的错误信息
3. **日志记录**：关键操作记录日志
4. **测试**：部署前充分测试
5. **文档**：保持文档与代码同步

## 后续优化方向

1. **批量操作**：支持批量创建/删除页面
2. **页面模板**：提供常用页面模板
3. **访问控制**：页面级别的访问密码
4. **自定义域名**：简化域名绑定流程
5. **监控告警**：使用量超限提醒
6. **数据导出**：支持导出页面数据
7. **版本管理**：页面内容版本控制

---

**文档版本**: 1.0.0  
**最后更新**: 2025-10-22  
**维护者**: NekroAgent Team
