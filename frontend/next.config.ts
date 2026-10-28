import type { NextConfig } from 'next'
const nextConfig: NextConfig = {
  async rewrites() {
    const api = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
    return [
      { source: '/api/:path*', destination: `${api}/api/:path*` },
      { source: '/frames/:path*', destination: `${api}/frames/:path*` },
      { source: '/thumbnails/:path*', destination: `${api}/thumbnails/:path*` },
    ]
  },
}
export default nextConfig
