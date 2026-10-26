import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';
export default defineConfig({ plugins: [sveltekit({adapter: adapter({ pages: 'build', assets: 'build', strict: true })})], server: {host:'127.0.0.1',port:4173} });
