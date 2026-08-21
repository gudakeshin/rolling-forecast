/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ['class', '[data-theme="dark"]'],
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Brand colors (mockup-derived forest-green/cream identity)
        deloitte: {
          green: '#0E6E5C',        // Primary accent (forest green)
          'green-dark': '#123B33', // Deep forest (user bubbles, wordmark)
          'green-light': '#3D8F7A',// Lighter accent tint
          teal: '#3D6E8A',         // Secondary accent (info/blue)
          'teal-light': '#5B8AA6', // Lighter secondary accent
          blue: '#123B33',         // Deep accent (kept for back-compat)
          'blue-light': '#3D6E8A',
          black: '#000000',
          white: '#FFFFFF',
          'cool-gray': '#8B8880',
          'warm-gray': '#726F68',
          'light-gray': '#E4E0D6',
        },
        // Semantic color mapping using the new brand palette
        primary: {
          50: '#EAF4F1',
          100: '#CFE6DF',
          200: '#A0CDC0',
          300: '#6FB3A0',
          400: '#3D8F7A',
          500: '#0E6E5C',   // Primary accent
          600: '#0B5A4B',
          700: '#09473C',
          800: '#123B33',
          900: '#0A241F',
        },
        accent: {
          50: '#EAF1F4',
          100: '#C9DBE3',
          200: '#A3C2D0',
          300: '#7DA9BD',
          400: '#5B8AA6',
          500: '#3D6E8A',   // Secondary accent
          600: '#325A70',
          700: '#264656',
          800: '#1B333E',
          900: '#101E25',
        },
        navy: {
          500: '#123B33',    // Deep accent
          600: '#0F332C',
          700: '#0C2A24',
          800: '#09201B',
          900: '#061512',
        },
        surface: {
          50: 'var(--surface-50)',
          100: 'var(--surface-100)',
          200: 'var(--surface-200)',
          300: 'var(--surface-300)',
          400: 'var(--surface-400)',
          500: 'var(--surface-500)',
          600: 'var(--surface-600)',
          700: 'var(--surface-700)',
          800: 'var(--surface-800)',
          900: 'var(--surface-900)',
        },
      },
      fontFamily: {
        sans: ['Plus Jakarta Sans', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
      },
    },
  },
  plugins: [],
}
