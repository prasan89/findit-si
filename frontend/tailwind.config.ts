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
        sidebar: {
          DEFAULT:    '#1E293B',
          active:     '#1D4ED8',
          hover:      '#334155',
          text:       '#CBD5E1',
          textActive: '#FFFFFF',
          border:     '#334155',
        },
        surface: {
          DEFAULT:  '#FFFFFF',
          alt:      '#F8FAFC',
          page:     '#F8FAFC',
          elevated: '#FFFFFF',
        },
        border: {
          DEFAULT: '#E2E8F0',
          strong:  '#CBD5E1',
          subtle:  '#F1F5F9',
          focus:   '#93C5FD',
        },
        text: {
          primary:   '#0F172A',
          secondary: '#475569',
          muted:     '#94A3B8',
          inverse:   '#FFFFFF',
        },
        success: { DEFAULT: '#16A34A', bg: '#F0FDF4', border: '#BBF7D0', text: '#15803D' },
        warning: { DEFAULT: '#D97706', bg: '#FFFBEB', border: '#FDE68A', text: '#B45309' },
        danger:  { DEFAULT: '#DC2626', bg: '#FEF2F2', border: '#FECACA', text: '#B91C1C' },
        info:    { DEFAULT: '#0284C7', bg: '#F0F9FF', border: '#BAE6FD', text: '#0369A1' },
      },
      fontFamily: {
        sans: ['var(--font-inter)', 'Inter', 'system-ui', 'sans-serif'],
      },
      fontSize: {
        '2xs': '11px',
        xs:    '12px',
        sm:    '13px',
        base:  '14px',
        md:    '15px',
        lg:    '16px',
        xl:    '18px',
        '2xl': '20px',
        '3xl': '24px',
        '4xl': '30px',
      },
      boxShadow: {
        sm: '0 1px 2px 0 rgb(0 0 0 / 0.05)',
        md: '0 4px 6px -1px rgb(0 0 0 / 0.07), 0 2px 4px -2px rgb(0 0 0 / 0.05)',
        lg: '0 10px 15px -3px rgb(0 0 0 / 0.08), 0 4px 6px -4px rgb(0 0 0 / 0.05)',
      },
      borderRadius: {
        sm:   '0.375rem',
        md:   '0.5rem',
        lg:   '0.75rem',
        xl:   '1rem',
        '2xl': '1.5rem',
      },
    },
  },
  plugins: [],
}
export default config
