import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './index.css';
import { useThemeStore } from './store/themeStore';

// Apply persisted theme before first paint
useThemeStore.getState();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
