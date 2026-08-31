import type { NextConfig } from 'next';

const isGitHubPages = process.env.GITHUB_PAGES === 'true';
const githubPagesBasePath = '/agentauth';

const nextConfig: NextConfig = {
  turbopack: { root: process.cwd() },
  output: isGitHubPages ? 'export' : undefined,
  basePath: isGitHubPages ? githubPagesBasePath : undefined,
  assetPrefix: isGitHubPages ? `${githubPagesBasePath}/` : undefined,
  trailingSlash: isGitHubPages,
  images: isGitHubPages ? { unoptimized: true } : undefined,
};

export default nextConfig;
