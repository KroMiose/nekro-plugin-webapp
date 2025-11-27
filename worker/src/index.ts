/**
 * Cloudflare Worker 主入口
 */

import { Env, CreatePageRequest, CreatePageResponse } from './types';
import { validateApiKey, hasPermission, generateApiKey } from './auth';
import { createPage, getPage, incrementAccessCount, deletePage, listPages, createApiKey, listApiKeys, deleteApiKey, getStats, getAdminKey, setAdminKey, getSetting, setSetting } from './storage';
import { ensureDatabaseInitialized } from './init';

/**
 * CORS 响应头
 */
function corsHeaders() {
	return {
		'Access-Control-Allow-Origin': '*',
		'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS',
		'Access-Control-Allow-Headers': 'Content-Type, Authorization',
	};
}

/**
 * JSON 响应
 */
function jsonResponse(data: any, status: number = 200): Response {
	return new Response(JSON.stringify(data), {
		status,
		headers: {
			'Content-Type': 'application/json',
			...corsHeaders(),
		},
	});
}

/**
 * 错误响应
 */
function errorResponse(message: string, status: number = 400): Response {
	return jsonResponse({ error: message }, status);
}

/**
 * HTML 响应
 */
function htmlResponse(html: string): Response {
	return new Response(html, {
		headers: {
			'Content-Type': 'text/html; charset=utf-8',
			...corsHeaders(),
		},
	});
}

export default {
	async fetch(request: Request, env: Env): Promise<Response> {
		// 确保数据库已初始化（首次访问时自动初始化）
		try {
			await ensureDatabaseInitialized(env);
		} catch (error: any) {
			console.error('Database initialization failed:', error);
			return errorResponse('Database initialization failed: ' + error.message, 500);
		}

		const url = new URL(request.url);
		const path = url.pathname;

		// OPTIONS 请求处理（CORS 预检）
		if (request.method === 'OPTIONS') {
			return new Response(null, { headers: corsHeaders() });
		}

		// API 路由
		if (path.startsWith('/api/')) {
			return handleAPI(request, env, path);
		}

		// 管理路由
		if (path.startsWith('/admin/')) {
			return handleAdmin(request, env, path);
		}

		// 根路径 - 返回简单说明页
		if (path === '/' || path === '/index.html') {
			return htmlResponse(WELCOME_PAGE_HTML);
		}

		// 页面访问 /{page_id}
		const pageId = path.substring(1);
		if (pageId) {
			return servePage(pageId, env);
		}

		return errorResponse('Not Found', 404);
	},
};

/**
 * 处理 API 请求
 */
async function handleAPI(request: Request, env: Env, path: string): Promise<Response> {
	// 健康检查
	if (path === '/api/health') {
		const adminKey = await getAdminKey(env);
		return jsonResponse({ 
			status: 'healthy', 
			timestamp: Date.now(),
			initialized: !!adminKey
		});
	}

	// 初始化管理密钥（仅在未设置时可用）
	if (path === '/api/init' && request.method === 'POST') {
		try {
			const adminKey = await getAdminKey(env);
			if (adminKey) {
				return errorResponse('管理密钥已设置，无法重复初始化', 400);
			}

			const body = (await request.json()) as { admin_key: string };
			if (!body.admin_key || body.admin_key.trim().length < 8) {
				return errorResponse('管理密钥至少需要 8 个字符', 400);
			}

			await setAdminKey(body.admin_key.trim(), env);
			return jsonResponse({ message: '管理密钥设置成功' }, 201);
		} catch (e: any) {
			return errorResponse(e.message || '初始化失败', 500);
		}
	}

	// 验证 API 密钥
	const auth = await validateApiKey(request.headers.get('Authorization'), env);
	if (!auth.valid) {
		return errorResponse('Unauthorized', 401);
	}

	// POST /api/pages - 创建页面
	if (path === '/api/pages' && request.method === 'POST') {
		if (!hasPermission(auth.permissions!, 'create')) {
			return errorResponse('Forbidden', 403);
		}

		try {
			const body = (await request.json()) as CreatePageRequest;

			// 验证必填字段
			if (!body.title || !body.title.trim()) {
				return errorResponse('标题不能为空');
			}
			if (!body.description || !body.description.trim()) {
				return errorResponse('描述不能为空');
			}
			if (!body.html_content || !body.html_content.trim()) {
				return errorResponse('HTML 内容不能为空');
			}

			// 获取配置的大小限制
			const maxHtmlSizeStr = (await getSetting('max_html_size', env)) || '500';
			const maxHtmlSize = parseInt(maxHtmlSizeStr) * 1024; // KB 转字节
			if (body.html_content.length > maxHtmlSize) {
				return errorResponse(`HTML 内容过大，最大允许 ${maxHtmlSizeStr} KB`);
			}

			// 如果未指定过期天数，使用配置的默认值
			if (!body.expires_in_days) {
				const defaultExpireDays = (await getSetting('page_expire_days', env)) || '30';
				body.expires_in_days = parseInt(defaultExpireDays);
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
			return errorResponse(e.message || '创建页面失败', 500);
		}
	}

	// GET /api/pages/{id} - 获取页面信息
	const pageIdMatch = path.match(/^\/api\/pages\/([a-zA-Z0-9-]+)$/);
	if (pageIdMatch && request.method === 'GET') {
		const pageId = pageIdMatch[1];
		const page = await getPage(pageId, env);

		if (!page) {
			return errorResponse('页面不存在或已过期', 404);
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
	if (pageIdMatch && request.method === 'DELETE') {
		if (!hasPermission(auth.permissions!, 'delete')) {
			return errorResponse('Forbidden', 403);
		}

		const pageId = pageIdMatch[1];
		const success = await deletePage(pageId, env);

		if (!success) {
			return errorResponse('删除失败', 500);
		}

		return jsonResponse({ message: '删除成功' });
	}

	return errorResponse('Not Found', 404);
}

/**
 * 处理管理请求
 */
async function handleAdmin(request: Request, env: Env, path: string): Promise<Response> {
	// 验证管理员权限
	const auth = await validateApiKey(request.headers.get('Authorization'), env);
	if (!auth.valid || !hasPermission(auth.permissions!, 'manage')) {
		return errorResponse('Unauthorized', 401);
	}

	// GET /admin/pages - 列出所有页面
	if (path === '/admin/pages' && request.method === 'GET') {
		const pages = await listPages(env);
		return jsonResponse(pages);
	}

	// POST /admin/keys - 创建新密钥
	if (path === '/admin/keys' && request.method === 'POST') {
		try {
			const body = (await request.json()) as { key_name: string };

			if (!body.key_name || !body.key_name.trim()) {
				return errorResponse('密钥名称不能为空');
			}

		// 生成新密钥（简化版 - 直接明文）
		const newKey = generateApiKey();

		const apiKey = await createApiKey(body.key_name, newKey, auth.keyId!, env);

		// 返回密钥（明文存储，随时可见）
		return jsonResponse(apiKey, 201);
		} catch (e: any) {
			return errorResponse(e.message || '创建密钥失败', 500);
		}
	}

	// GET /admin/keys - 列出所有密钥
	if (path === '/admin/keys' && request.method === 'GET') {
		const keys = await listApiKeys(env);
		return jsonResponse(keys);
	}

	// DELETE /admin/keys/{id} - 删除密钥
	const keyIdMatch = path.match(/^\/admin\/keys\/([a-zA-Z0-9-]+)$/);
	if (keyIdMatch && request.method === 'DELETE') {
		const keyId = keyIdMatch[1];
		const success = await deleteApiKey(keyId, env);

		if (!success) {
			return errorResponse('删除失败', 500);
		}

		return jsonResponse({ message: '删除成功' });
	}

	// DELETE /admin/pages/{id} - 删除页面
	const pageIdMatch = path.match(/^\/admin\/pages\/([a-zA-Z0-9-]+)$/);
	if (pageIdMatch && request.method === 'DELETE') {
		const pageId = pageIdMatch[1];
		const success = await deletePage(pageId, env);

		if (!success) {
			return errorResponse('删除失败', 500);
		}

		return jsonResponse({ message: '删除成功' });
	}

	// GET /admin/stats - 获取统计信息
	if (path === '/admin/stats' && request.method === 'GET') {
		const stats = await getStats(env);
		return jsonResponse(stats);
	}

	// PUT /admin/password - 修改管理密钥（需要提供旧密钥）
	if (path === '/admin/password' && request.method === 'PUT') {
		try {
			const body = (await request.json()) as { old_key: string; new_key: string };
			
			if (!body.old_key || !body.new_key) {
				return errorResponse('旧密钥和新密钥都不能为空', 400);
			}

			if (body.new_key.trim().length < 8) {
				return errorResponse('新密钥至少需要 8 个字符', 400);
			}

			// 验证旧密钥（这里 auth 已经是管理员）
			const currentAdminKey = await getAdminKey(env);
			if (currentAdminKey !== body.old_key.trim()) {
				return errorResponse('旧密钥不正确', 403);
			}

			await setAdminKey(body.new_key.trim(), env);
			return jsonResponse({ message: '管理密钥修改成功' });
		} catch (e: any) {
			return errorResponse(e.message || '修改失败', 500);
		}
	}

	// GET /admin/settings - 获取系统配置
	if (path === '/admin/settings' && request.method === 'GET') {
		const pageExpireDays = (await getSetting('page_expire_days', env)) || '30';
		const maxHtmlSize = (await getSetting('max_html_size', env)) || '500';
		
		return jsonResponse({
			page_expire_days: parseInt(pageExpireDays),
			max_html_size: parseInt(maxHtmlSize),
		});
	}

	// PUT /admin/settings - 更新系统配置
	if (path === '/admin/settings' && request.method === 'PUT') {
		try {
			const body = (await request.json()) as { page_expire_days?: number; max_html_size?: number };
			
			if (body.page_expire_days !== undefined) {
				if (body.page_expire_days < 0) {
					return errorResponse('页面过期天数不能为负数', 400);
				}
				await setSetting('page_expire_days', String(body.page_expire_days), env);
			}
			
			if (body.max_html_size !== undefined) {
				if (body.max_html_size <= 0) {
					return errorResponse('HTML 最大大小必须大于 0', 400);
				}
				await setSetting('max_html_size', String(body.max_html_size), env);
			}
			
			return jsonResponse({ message: '配置更新成功' });
		} catch (e: any) {
			return errorResponse(e.message || '更新失败', 500);
		}
	}

	return errorResponse('Not Found', 404);
}

/**
 * 提供页面服务
 */
async function servePage(pageId: string, env: Env): Promise<Response> {
	const page = await getPage(pageId, env);

	if (!page) {
		return htmlResponse(`
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>页面不存在</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
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
            padding: 2rem;
        }
        h1 {
            font-size: 6em;
            margin: 0;
            font-weight: 700;
        }
        p {
            font-size: 1.5em;
            margin-top: 1rem;
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

/**
 * 欢迎页面 HTML
 */
const WELCOME_PAGE_HTML = `
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>WebApp 快速部署服务 - AI 驱动的静态网页托管平台</title>
    <meta name="description" content="基于 Cloudflare Workers 的轻量级 HTML 托管服务，由 NekroAgent AI 智能体快速部署网页。支持全球 CDN 加速、API 接口、密钥认证保护。">
    <meta name="keywords" content="HTML托管,静态网页部署,Cloudflare Workers,AI部署,NekroAgent,网页托管,CDN加速">
    <meta property="og:title" content="WebApp 快速部署服务">
    <meta property="og:description" content="AI 驱动的静态网页快速部署平台，基于 Cloudflare Workers">
    <meta property="og:type" content="website">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        :root {
            --primary: #10b981;
            --primary-dark: #059669;
            --primary-light: #d1fae5;
            --bg-main: #f9fafb;
            --bg-card: #ffffff;
            --text-primary: #111827;
            --text-secondary: #6b7280;
            --border: #e5e7eb;
            --shadow: 0 1px 3px 0 rgb(0 0 0 / 0.1), 0 1px 2px -1px rgb(0 0 0 / 0.1);
            --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1);
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Oxygen', 'Ubuntu', sans-serif;
            background: var(--bg-main);
            color: var(--text-primary);
            line-height: 1.6;
        }
        .header {
            background: var(--primary);
            color: white;
            padding: 3rem 1rem;
            text-align: center;
        }
        .header h1 {
            font-size: 2.5rem;
            font-weight: 700;
            margin-bottom: 0.5rem;
        }
        .header p {
            font-size: 1.125rem;
            opacity: 0.95;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 2rem 1rem;
        }
        .status-badge {
            display: inline-block;
            padding: 0.5rem 1rem;
            background: var(--primary-light);
            color: var(--primary-dark);
            border-radius: 20px;
            font-size: 0.875rem;
            font-weight: 600;
            margin-top: 1rem;
        }
        .section {
            background: var(--bg-card);
            border-radius: 12px;
            padding: 2rem;
            margin-bottom: 2rem;
            box-shadow: var(--shadow);
            border: 1px solid var(--border);
        }
        .section h2 {
            font-size: 1.5rem;
            font-weight: 600;
            margin-bottom: 1rem;
            color: var(--text-primary);
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }
        .section p {
            color: var(--text-secondary);
            margin-bottom: 1rem;
            line-height: 1.8;
        }
        .features {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 1rem;
            margin-top: 1.5rem;
        }
        .feature-card {
            padding: 1.5rem;
            background: var(--bg-main);
            border-radius: 8px;
            border: 1px solid var(--border);
        }
        .feature-card h3 {
            font-size: 1rem;
            font-weight: 600;
            margin-bottom: 0.5rem;
            color: var(--text-primary);
        }
        .feature-card p {
            font-size: 0.875rem;
            color: var(--text-secondary);
            margin: 0;
        }
        .api-list {
            margin-top: 1rem;
        }
        .api-item {
            background: var(--bg-main);
            padding: 0.75rem 1rem;
            border-radius: 6px;
            margin: 0.5rem 0;
            font-family: 'Monaco', 'Menlo', monospace;
            font-size: 0.875rem;
            border: 1px solid var(--border);
        }
        .api-method {
            display: inline-block;
            padding: 0.125rem 0.5rem;
            background: var(--primary);
            color: white;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 600;
            margin-right: 0.5rem;
        }
        .link-section {
            display: flex;
            gap: 1rem;
            margin-top: 1.5rem;
            flex-wrap: wrap;
        }
        .btn {
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
            padding: 0.75rem 1.5rem;
            background: var(--primary);
            color: white;
            text-decoration: none;
            border-radius: 6px;
            font-weight: 500;
            transition: all 0.2s;
        }
        .btn:hover {
            background: var(--primary-dark);
            transform: translateY(-2px);
            box-shadow: var(--shadow-lg);
        }
        .btn-secondary {
            background: var(--bg-card);
            color: var(--text-primary);
            border: 1px solid var(--border);
        }
        .btn-secondary:hover {
            background: var(--bg-main);
        }
        .footer {
            text-align: center;
            padding: 2rem 1rem;
            color: var(--text-secondary);
            font-size: 0.875rem;
        }
        .footer a {
            color: var(--primary);
            text-decoration: none;
        }
        .footer a:hover {
            text-decoration: underline;
        }
        @media (max-width: 768px) {
            .header h1 {
                font-size: 2rem;
            }
            .features {
                grid-template-columns: 1fr;
            }
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🚀 WebApp 快速部署服务</h1>
        <p>AI 驱动的静态网页快速部署平台 · 基于 Cloudflare Workers</p>
        <div class="status-badge">✅ 服务运行正常</div>
    </div>

    <div class="container">
        <div class="section">
            <h2>🌟 关于本服务</h2>
            <p>
                WebApp 快速部署服务是一个基于 <strong>Cloudflare Workers</strong> 的轻量级 HTML 托管平台，
                由 <strong>NekroAgent</strong> AI 智能体驱动，能够快速将 HTML 内容部署为在线可访问的网页。
            </p>
            <p>
                本服务完全开源免费，任何人都可以在几分钟内部署自己的实例，无需服务器，无需域名，
                依托 Cloudflare 的全球 CDN 网络实现超快访问速度。
            </p>
            <div class="features">
                <div class="feature-card">
                    <h3>⚡ 全球加速</h3>
                    <p>依托 Cloudflare CDN，全球 300+ 节点加速访问</p>
                </div>
                <div class="feature-card">
                    <h3>🤖 AI 驱动</h3>
                    <p>与 NekroAgent 深度集成，AI 自动生成和部署网页</p>
                </div>
                <div class="feature-card">
                    <h3>🔒 安全可靠</h3>
                    <p>API 密钥认证，支持权限管理和访问控制</p>
                </div>
                <div class="feature-card">
                    <h3>💰 完全免费</h3>
                    <p>开源项目，免费使用 Cloudflare 免费套餐即可</p>
                </div>
            </div>
        </div>

        <div class="section">
            <h2>🔗 项目生态</h2>
            <p>
                本服务是 <strong>NekroAgent</strong> 生态的重要组成部分：
            </p>
            <ul style="margin-left: 2rem; color: var(--text-secondary);">
                <li style="margin: 0.5rem 0;"><strong>NekroAgent</strong> - 开源的 AI 智能体框架，让 AI 能够执行复杂任务</li>
                <li style="margin: 0.5rem 0;"><strong>nekro-plugin-webapp</strong> - 本插件，为 NekroAgent 提供快速部署网页的能力</li>
            </ul>
            <div class="link-section">
                <a href="https://github.com/KroMiose/nekro-agent" class="btn" target="_blank" rel="noopener">
                    📦 NekroAgent 仓库
                </a>
                <a href="https://github.com/KroMiose/nekro-plugin-webapp" class="btn btn-secondary" target="_blank" rel="noopener">
                    🔌 WebApp 插件仓库
                </a>
            </div>
        </div>

        <div class="section">
            <h2>🔌 API 接口</h2>
            <p>本服务提供以下 RESTful API 接口：</p>
            <div class="api-list">
                <div class="api-item">
                    <span class="api-method">GET</span>
                    <code>/api/health</code> - 健康检查
                </div>
                <div class="api-item">
                    <span class="api-method">POST</span>
                    <code>/api/pages</code> - 创建页面（需要认证）
                </div>
                <div class="api-item">
                    <span class="api-method">GET</span>
                    <code>/api/pages/{id}</code> - 获取页面信息
                </div>
                <div class="api-item">
                    <span class="api-method">DELETE</span>
                    <code>/api/pages/{id}</code> - 删除页面（需要认证）
                </div>
            </div>
        </div>

        <div class="section">
            <h2>🚀 快速开始</h2>
            <ol style="margin-left: 2rem; color: var(--text-secondary);">
                <li style="margin: 0.75rem 0;"><strong>部署服务：</strong>参考插件文档，将本服务部署到 Cloudflare Workers</li>
                <li style="margin: 0.75rem 0;"><strong>安装插件：</strong>在 NekroAgent 中安装 nekro-plugin-webapp 插件</li>
                <li style="margin: 0.75rem 0;"><strong>配置密钥：</strong>在管理界面创建 API 密钥，配置到插件中</li>
                <li style="margin: 0.75rem 0;"><strong>开始使用：</strong>让 AI 帮你创建和部署网页！</li>
            </ol>
        </div>

        <div class="section">
            <h2>💡 使用场景</h2>
            <div class="features">
                <div class="feature-card">
                    <h3>📊 数据可视化</h3>
                    <p>快速生成图表和数据展示页面</p>
                </div>
                <div class="feature-card">
                    <h3>📝 内容发布</h3>
                    <p>生成文章、报告等内容页面</p>
                </div>
                <div class="feature-card">
                    <h3>🎨 原型设计</h3>
                    <p>快速创建 UI 原型和演示页面</p>
                </div>
                <div class="feature-card">
                    <h3>📱 临时页面</h3>
                    <p>活动页面、问卷调查等临时需求</p>
                </div>
            </div>
        </div>
    </div>

    <div class="footer">
        <p>
            由 <a href="https://github.com/KroMiose" target="_blank" rel="noopener">KroMiose</a> 开发维护 · 
            基于 <a href="https://github.com/KroMiose/nekro-agent" target="_blank" rel="noopener">NekroAgent</a> 生态 · 
            开源协议：MIT License
        </p>
        <p style="margin-top: 0.5rem;">
            ⭐ 觉得有用？请在 <a href="https://github.com/KroMiose/nekro-agent" target="_blank" rel="noopener">GitHub</a> 上给我们一个 Star！
        </p>
    </div>
</body>
</html>
`;

