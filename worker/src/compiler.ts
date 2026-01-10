import * as esbuild from 'esbuild-wasm';
import { Env } from './types';

// Shim location for esbuild-wasm in Cloudflare Workers
if (typeof self !== 'undefined' && !(self as any).location) {
    (self as any).location = { href: '/', protocol: 'https:', host: 'localhost' };
}

// Initialize esbuild once
let initialized = false;

/**
 * Initialize esbuild with WASM module
 * We point to the WASM file served from a CDN or local asset
 */
async function ensureEsbuildInitialized(env: Env) {
    if (initialized) return;

    // Use a reliable CDN for the WASM binary or bundle it if possible.
    // In CF Workers, it's often better to import the wasm as a module if configured,
    // or fetch it. For simplicity here, we assume standard initialization.
    try {
        await esbuild.initialize({
            wasmURL: 'https://unpkg.com/esbuild-wasm@0.20.0/esbuild.wasm',
            worker: false
        });
        initialized = true;
    } catch (e) {
        // Ignore "already initialized" errors
        if (!(e instanceof Error && e.message.includes('initialize'))) {
            throw e;
        }
    }
}

interface CompileResult {
    success: boolean;
    output?: string;
    error?: string;
}

/**
 * Compile a virtual project bundle
 * @param files Map of filename to content
 */
export async function compileProject(files: Record<string, string>, env: Env): Promise<CompileResult> {
    await ensureEsbuildInitialized(env);

    try {
        const result = await esbuild.build({
            entryPoints: ['src/main.tsx'], // Assumption: Entry point is always src/main.tsx or similar
            bundle: true,
            format: 'esm',
            target: 'es2022',
            // Externalize heavy dependencies to be loaded via import-map from CDN
            external: ['react', 'react-dom', 'framer-motion', 'lucide-react', 'recharts'], 
            plugins: [
                {
                    name: 'virtual-fs',
                    setup(build) {
                        // Resolve all non-external paths
                        build.onResolve({ filter: /.*/ }, args => {
                            // Logic to resolve relative paths in the virtual FS
                            // Simple case: just look up in the map or assume it's relative
                            return { path: args.path, namespace: 'vfs' };
                        });

                        // Load content from virtual FS map
                        build.onLoad({ filter: /.*/, namespace: 'vfs' }, args => {
                            // Normalize path slightly if needed (e.g. remove leading ./ or /)
                            let p = args.path;
                            if (p.startsWith('./')) p = p.substring(2);
                            if (p.startsWith('/')) p = p.substring(1);
                            
                            // Try exact match
                            let content = files[p];
                            
                            // Try adding extension
                            if (!content) content = files[p + '.tsx'];
                            if (!content) content = files[p + '.ts'];
                            if (!content) content = files[p + '.jsx'];
                            if (!content) content = files[p + '.js'];

                            if (!content) {
                                return { errors: [{ text: `File not found: ${args.path}` }] };
                            }

                            return {
                                contents: content,
                                loader: 'tsx', // Default all to TSX for simplicity
                            };
                        });
                    },
                },
            ],
            write: false,
        });

        if (result.outputFiles && result.outputFiles.length > 0) {
            const code = result.outputFiles[0].text;
            return { success: true, output: code };
        } 
        
        return { success: false, error: 'No output generated' };

    } catch (e: any) {
        // Format esbuild errors
        if (e.errors && Array.isArray(e.errors)) {
            const messages = e.errors.map((err: any) => {
                let msg = `❌ [Error] ${err.text}`;
                if (err.location) {
                    msg += `\n   File: ${err.location.file || 'unknown'}:${err.location.line}:${err.location.column}`;
                    msg += `\n   Line: ${err.location.lineText}`;
                }
                return msg;
            });
            return { success: false, error: messages.join('\n\n') };
        }
        return { success: false, error: e.message || String(e) };
    }
}
