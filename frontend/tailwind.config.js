/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Deloitte Brand Colors
        deloitte: {
          green: '#86BC25',        // Primary Deloitte Green
          'green-dark': '#046A38', // Dark Green
          'green-light': '#C4D600',// Light Green / Lime
          teal: '#0076A8',         // Deloitte Teal
          'teal-light': '#00A3E0', // Light Teal / Cyan
          blue: '#012169',         // Deloitte Blue (Navy)
          'blue-light': '#0097A9', // Light Blue
          black: '#000000',
          white: '#FFFFFF',
          'cool-gray': '#97999B',
          'warm-gray': '#53565A',
          'light-gray': '#D0D0CE',
        },
        // Semantic color mapping using Deloitte palette
        primary: {
          50: '#f3fae4',
          100: '#e5f5c4',
          200: '#cbe98e',
          300: '#aed95a',
          400: '#96c832',
          500: '#86BC25',   // Deloitte Green
          600: '#6d9a1e',
          700: '#567a18',
          800: '#3f5a12',
          900: '#2a3d0c',
        },
        accent: {
          50: '#e6f4fa',
          100: '#b3dff0',
          200: '#80cae6',
          300: '#4db5dc',
          400: '#26a5d4',
          500: '#0076A8',   // Deloitte Teal
          600: '#006590',
          700: '#004f70',
          800: '#003a52',
          900: '#002536',
        },
        navy: {
          500: '#012169',    // Deloitte Blue
          600: '#011a54',
          700: '#011340',
          800: '#000d2b',
          900: '#000816',
        },
        surface: {
          50: '#FAFAFA',
          100: '#F5F5F5',
          200: '#E8E8E8',
          300: '#D0D0CE',    // Deloitte Light Gray
          400: '#97999B',    // Deloitte Cool Gray
          500: '#75787B',
          600: '#53565A',    // Deloitte Warm Gray
          700: '#2D2D2D',
          800: '#1A1A1A',
          900: '#0D0D0D',
        },
      },
      fontFamily: {
        sans: ['Open Sans', 'system-ui', '-apple-system', 'sans-serif'],
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
      },
    },
  },
  plugins: [],
}
