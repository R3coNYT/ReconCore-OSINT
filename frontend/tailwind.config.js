/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Palette sombre type console SOC.
        base: { 900: '#0b0f14', 800: '#111823', 700: '#18212e', 600: '#1f2937' },
        line: '#26303d',
        accent: { DEFAULT: '#38bdf8', soft: '#0ea5e9' },
        ok: '#22c55e',
        warn: '#f59e0b',
        danger: '#ef4444',
        hypo: '#a855f7',
      },
      fontFamily: {
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
}
