import type { Config } from 'tailwindcss'
const config: Config = {
  content: ['./src/**/*.{js,ts,jsx,tsx,mdx}'],
  theme: {
    extend: {
      colors: {
        primary: {
          DEFAULT: '#2563EB',
          hover:   '#1D4ED8',
          subtle:  '#EFF6FF',
          light:   '#BFDBFE',
        },
        surface: {
          DEFAULT: '#FFFFFF',
          alt:     '#F1F5F9',
          page:    '#F8FAFC',
        },
        border: {
          DEFAULT: '#E2E8F0',
          focus:   '#93C5FD',
        },
        text: {
          primary:   '#0F172A',
          secondary: '#64748B',
          muted:     '#94A3B8',
        },
        success: { DEFAULT: '#16A34A', bg: '#F0FDF4', border: '#BBF7D0' },
        warning: { DEFAULT: '#D97706', bg: '#FFFBEB', border: '#FDE68A' },
        danger:  { DEFAULT: '#DC2626', bg: '#FEF2F2', border: '#FECACA' },
        info:    { DEFAULT: '#0284C7', bg: '#F0F9FF', border: '#BAE6FD' },
      },
      fontFamily: {
        sans: ['var(--font-inter)', 'Inter', 'system-ui', 'sans-serif'],
      },
      boxShadow: {
        sm:  '0 1px 2px 0 rgb(0 0 0 / 0.05)',
        md:  '0 4px 6px -1px rgb(0 0 0 / 0.07), 0 2px 4px -2px rgb(0 0 0 / 0.05)',
        lg:  '0 10px 15px -3px rgb(0 0 0 / 0.08), 0 4px 6px -4px rgb(0 0 0 / 0.05)',
      },
      borderRadius: {
        sm:  '0.375rem',
        md:  '0.5rem',
        lg:  '0.75rem',
        xl:  '1rem',
        '2xl': '1.5rem',
      },
    },
  },
  plugins: [],
}
export default config
