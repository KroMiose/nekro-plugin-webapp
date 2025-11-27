/**
 * 数据库初始化模块
 */

import { Env } from './types';

/**
 * 数据库 Schema SQL
 */
const SCHEMA_SQL = `
-- Settings 表（存储系统配置，包括管理密钥）
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

-- API Keys 表（简化版 - 明文存储）
CREATE TABLE IF NOT EXISTS api_keys (
    key_id TEXT PRIMARY KEY,
    api_key TEXT NOT NULL UNIQUE,
    key_name TEXT NOT NULL,
    created_by TEXT,
    created_at INTEGER NOT NULL,
    expires_at INTEGER,
    is_active INTEGER DEFAULT 1,
    usage_count INTEGER DEFAULT 0,
    permissions TEXT DEFAULT 'create,view',
    metadata TEXT
);

-- Pages 表
CREATE TABLE IF NOT EXISTS pages (
    page_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    html_content TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER,
    access_count INTEGER DEFAULT 0,
    last_accessed INTEGER,
    is_active INTEGER DEFAULT 1,
    metadata TEXT
);

-- Access Logs 表（可选，用于审计）
CREATE TABLE IF NOT EXISTS access_logs (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id TEXT NOT NULL,
    accessed_at INTEGER NOT NULL,
    ip_address TEXT,
    user_agent TEXT
);

-- 创建索引以提高查询性能
CREATE INDEX IF NOT EXISTS idx_pages_created_by ON pages(created_by);
CREATE INDEX IF NOT EXISTS idx_pages_expires_at ON pages(expires_at);
CREATE INDEX IF NOT EXISTS idx_pages_is_active ON pages(is_active);
CREATE INDEX IF NOT EXISTS idx_api_keys_is_active ON api_keys(is_active);
CREATE INDEX IF NOT EXISTS idx_api_keys_api_key ON api_keys(api_key);
`;

/**
 * 检查数据库是否已初始化
 */
async function isDatabaseInitialized(env: Env): Promise<boolean> {
	try {
		// 尝试查询 api_keys 表
		const result = await env.DB.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='api_keys'").first();

		return result !== null;
	} catch {
		return false;
	}
}

/**
 * 初始化数据库
 */
export async function initializeDatabase(env: Env): Promise<void> {
	// 检查是否已经初始化
	const isInit = await isDatabaseInitialized(env);
	if (isInit) {
		console.log('Database already initialized');
		return;
	}

	console.log('Initializing database...');

	// 分割 SQL 语句并逐个执行
	const statements = SCHEMA_SQL
		.split(';')
		.map((s) => s.trim())
		.filter((s) => s.length > 0);

	for (const statement of statements) {
		try {
			await env.DB.prepare(statement).run();
		} catch (error) {
			console.error('Failed to execute statement:', statement);
			throw error;
		}
	}

	console.log('Database initialized successfully');
}

/**
 * 确保数据库已初始化（惰性初始化）
 */
export async function ensureDatabaseInitialized(env: Env): Promise<void> {
	// 如果环境变量标记已初始化，跳过检查
	if (env.DB_INITIALIZED === 'true') {
		return;
	}

	await initializeDatabase(env);
}

