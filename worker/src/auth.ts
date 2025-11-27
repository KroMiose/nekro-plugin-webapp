/**
 * 认证和授权模块 - 简化版（明文密钥）
 */

import { Env, ApiKey, AuthResult } from './types';
import { getAdminKey } from './storage';

/**
 * 验证 API 密钥（使用明文比对）
 */
export async function validateApiKey(authorization: string | null, env: Env): Promise<AuthResult> {
	if (!authorization || !authorization.startsWith('Bearer ')) {
		return { valid: false };
	}

	const apiKey = authorization.substring(7).trim();

	// 从数据库检查是否是管理员密钥
	const adminKey = await getAdminKey(env);
	if (adminKey && apiKey === adminKey) {
		return {
			valid: true,
			keyId: 'admin',
			permissions: ['view', 'delete', 'manage'],  // 管理密钥不包含 create 权限
		};
	}

	// 检查数据库中的密钥（明文）
	const result = await env.DB.prepare(
		'SELECT * FROM api_keys WHERE api_key = ? AND is_active = 1'
	).bind(apiKey).first<ApiKey>();

	if (!result) {
		return { valid: false };
	}

	// 检查是否过期
	if (result.expires_at && result.expires_at < Date.now()) {
		return { valid: false };
	}

	// 更新使用次数
	await env.DB.prepare(
		'UPDATE api_keys SET usage_count = usage_count + 1 WHERE key_id = ?'
	).bind(result.key_id).run();

	return {
		valid: true,
		keyId: result.key_id,
		permissions: result.permissions.split(','),
	};
}

/**
 * 检查权限
 */
export function hasPermission(permissions: string[], required: string): boolean {
	return permissions.includes(required) || permissions.includes('manage');
}

/**
 * 生成随机 API 密钥
 */
export function generateApiKey(): string {
	const array = new Uint8Array(32);
	crypto.getRandomValues(array);
	return btoa(String.fromCharCode(...array))
		.replace(/\+/g, '-')
		.replace(/\//g, '_')
		.replace(/=/g, '')
		.substring(0, 40);
}

