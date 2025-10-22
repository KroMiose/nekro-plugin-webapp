/**
 * 类型定义
 */

export interface Env {
	DB: D1Database;
	ADMIN_KEY_HASH: string;
	DB_INITIALIZED?: string;
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

export interface AuthResult {
	valid: boolean;
	keyId?: string;
	permissions?: string[];
}

