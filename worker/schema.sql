-- WebApp 部署服务数据库 Schema

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

