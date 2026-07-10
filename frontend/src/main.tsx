import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './index.css';
import { useThemeStore } from './store/themeStore';
import { getLocale } from './i18n';

// Apply persisted theme + locale before first paint
useThemeStore.getState();
document.documentElement.lang = getLocale();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
