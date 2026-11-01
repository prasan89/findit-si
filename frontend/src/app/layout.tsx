import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import './globals.css'

const inter = Inter({ subsets: ['latin'], variable: '--font-inter' })

export const metadata: Metadata = {
  title: 'RealityTrace',
  description: 'Evidence-backed CCTV investigation workspace',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={inter.variable}>
      <body className="bg-[#F8FAFC] min-h-screen" style={{ fontFamily: 'var(--font-inter, Inter, system-ui, sans-serif)' }}>
        {children}
      </body>
    </html>
  )
}
