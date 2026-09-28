import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './index.css';
import { getLocale } from './i18n';

// Apply persisted locale before first paint
document.documentElement.lang = getLocale();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
