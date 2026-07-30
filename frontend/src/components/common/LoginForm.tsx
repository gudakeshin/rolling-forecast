import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '../../store/authStore';
import { useVersionStore } from '../../store/versionStore';
import { apiPost } from '../../api/client';
import type { TokenResponse } from '../../types/auth';
import { TrendingUp } from 'lucide-react';

export function LoginForm() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const login = useAuthStore((s) => s.login);
  const fetchMe = useAuthStore((s) => s.fetchMe);
  const hydrateVersions = useVersionStore((s) => s.hydrate);
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      const response = await apiPost<TokenResponse>('/auth/login', {
        username,
        password,
      });
      login(response);
      await fetchMe();
      void hydrateVersions();
      navigate('/');
    } catch (err: any) {
      setError(err.message || 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-black">
      {/* Background accent */}
      <div className="absolute inset-0 overflow-hidden">
        <div className="absolute -top-40 -right-40 w-96 h-96 bg-deloitte-green/5 rounded-full blur-3xl" />
        <div className="absolute -bottom-40 -left-40 w-96 h-96 bg-accent-500/5 rounded-full blur-3xl" />
      </div>

      <div className="relative w-full max-w-md p-8">
        {/* Logo */}
        <div className="text-center mb-10">
          <div className="inline-flex items-center gap-3 mb-4">
            <div className="w-1.5 h-10 bg-deloitte-green rounded-full" />
            <div className="text-left">
              <h1 className="text-2xl font-bold text-white tracking-tight">
                Rolling Forecast
              </h1>
              <p className="text-deloitte-green text-xs font-semibold tracking-[0.2em] uppercase">
                Deloitte
              </p>
            </div>
          </div>
          <p className="text-surface-400 mt-4 text-sm">
            AI-powered FP&A forecasting assistant
          </p>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-5">
          <div>
            <label htmlFor="login-username" className="block text-sm font-medium text-surface-300 mb-1.5">
              Username
            </label>
            <input
              id="login-username"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full px-4 py-3 bg-surface-800/80 border border-surface-700 rounded-xl text-white placeholder-surface-500 focus:outline-none focus:border-deloitte-green/60 focus:ring-1 focus:ring-deloitte-green/30 transition-all"
              placeholder="Enter username"
              autoComplete="username"
              required
            />
          </div>

          <div>
            <label htmlFor="login-password" className="block text-sm font-medium text-surface-300 mb-1.5">
              Password
            </label>
            <input
              id="login-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-4 py-3 bg-surface-800/80 border border-surface-700 rounded-xl text-white placeholder-surface-500 focus:outline-none focus:border-deloitte-green/60 focus:ring-1 focus:ring-deloitte-green/30 transition-all"
              placeholder="Enter password"
              autoComplete="current-password"
              required
            />
          </div>

          {error && (
            <div className="p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 text-sm">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full py-3 px-4 bg-deloitte-green hover:bg-deloitte-green/90 disabled:bg-surface-700 disabled:text-surface-500 text-white font-semibold rounded-xl transition-all glow-green"
          >
            {loading ? 'Signing in...' : 'Sign In'}
          </button>

          <a
            href="/api/auth/oidc/login"
            className="block w-full text-center py-2.5 px-4 mt-3 border border-surface-600 text-surface-300 hover:text-white hover:border-deloitte-green/40 rounded-xl text-sm transition-all"
          >
            Sign in with SSO
          </a>

          <p className="text-center text-sm text-surface-500 mt-6">
            Demo credentials:{' '}
            <code className="text-deloitte-green/80 bg-deloitte-green/10 px-1.5 py-0.5 rounded">analyst</code>
            {' / '}
            <code className="text-deloitte-green/80 bg-deloitte-green/10 px-1.5 py-0.5 rounded">analyst</code>
          </p>
        </form>
      </div>
    </div>
  );
}
