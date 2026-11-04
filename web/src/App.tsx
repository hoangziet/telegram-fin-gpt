import { useEffect, useState } from 'react';
import { ApiError, getCurrentUser, getCsrfToken, login, logout, type AuthUser } from './api/client';
import Finances from './screens/Finances';
import { t, useLanguage } from './i18n';
import './auth.css';
import brandIcon from './assets/brand-icon.png';

type ScreenState = 'checking' | 'signed-out' | 'signed-in' | 'unavailable';

export default function App() {
  const { language } = useLanguage();
  const label = (value: string) => t(language, value);
  const [screen, setScreen] = useState<ScreenState>('checking');
  const [user, setUser] = useState<AuthUser | null>(null);
  const [csrfToken, setCsrfToken] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');
  const [invalidCredentials, setInvalidCredentials] = useState(false);

  useEffect(() => {
    let isMounted = true;

    async function restoreSession() {
      try {
        const authenticatedUser = await getCurrentUser();
        const token = await getCsrfToken();
        if (isMounted) {
          setUser(authenticatedUser);
          setCsrfToken(token);
          setScreen('signed-in');
        }
      } catch (error) {
        if (!isMounted) return;
        if (error instanceof ApiError && error.statusCode === 401) {
          setScreen('signed-out');
        } else {
          setErrorMessage('Could not check your session. Reload the page and try again.');
          setScreen('unavailable');
        }
      }
    }

    void restoreSession();
    return () => {
      isMounted = false;
    };
  }, []);

  if (screen === 'signed-in' && user && csrfToken) {
    return (
      <Finances
        csrfToken={csrfToken}
        username={user.username}
        onLogout={handleLogout}
        isSubmitting={isSubmitting}
        errorMessage={errorMessage}
      />
    );
  }

  async function handleLogin(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    const username = String(formData.get('username') ?? '').trim();
    const password = String(formData.get('password') ?? '');
    setIsSubmitting(true);
    setErrorMessage('');
    setInvalidCredentials(false);

    try {
      const result = await login(username, password);
      setUser(result.user);
      setCsrfToken(result.csrfToken);
      setScreen('signed-in');
    } catch (error) {
      setErrorMessage(messageFor(error));
      setInvalidCredentials(error instanceof ApiError && error.statusCode === 401);
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleLogout() {
    if (!csrfToken) {
      setErrorMessage('Could not verify sign-out. Reload the page and try again.');
      return;
    }

    setIsSubmitting(true);
    setErrorMessage('');
    try {
      await logout(csrfToken);
      setUser(null);
      setCsrfToken(null);
      setScreen('signed-out');
    } catch (error) {
      setErrorMessage(messageFor(error));
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="auth-shell" lang={language}>
      <header className="auth-header">
        <a className="wordmark" href="/" aria-label="Where my bucks go? home">
          <img className="auth-brand-icon" src={brandIcon} alt="" />
          Where my bucks go?
        </a>
        <span className="header-context">Personal finance, made clearer.</span>
      </header>

      <main className="auth-main">
        <section className="welcome-copy" aria-labelledby="page-title">
          <h1 id="page-title">Your personal finance assistant.</h1>
          <p className="welcome-description">
            Understand your spending, build better habits, and feel more confident with money.
          </p>
        </section>

        <section
          className="auth-panel"
          aria-labelledby="form-title"
          aria-busy={screen === 'checking'}
        >
          {screen === 'checking' && (
            <div className="form-content">
              <h2 id="form-title">{label('Kiểm tra phiên đăng nhập')}</h2>
              <p className="form-status" role="status">
                {label('Đang kiểm tra phiên đăng nhập…')}
              </p>
            </div>
          )}

          {screen === 'unavailable' && (
            <div className="form-content">
              <h2 id="form-title">{label('Chưa thể đăng nhập')}</h2>
              <p className="form-description" role="alert">
                {label(errorMessage)}
              </p>
              <button
                className="primary-button"
                type="button"
                onClick={() => window.location.reload()}
              >
                {label('Thử lại')}
              </button>
            </div>
          )}

          {screen === 'signed-out' && (
            <form className="login-form" onSubmit={handleLogin}>
              <div className="form-heading">
                <h2 id="form-title">{label('Đăng nhập')}</h2>
                <p className="form-description">
                  {label('Dùng tài khoản đã được thiết lập cho Where my bucks go?.')}
                </p>
              </div>

              <label className="field" htmlFor="username">
                <span>{label('Tên đăng nhập')}</span>
                <input
                  id="username"
                  name="username"
                  type="text"
                  autoComplete="username"
                  autoCapitalize="none"
                  spellCheck={false}
                  maxLength={128}
                  required
                  aria-required="true"
                  aria-invalid={invalidCredentials}
                  aria-describedby={invalidCredentials ? 'login-error' : undefined}
                  onChange={() => {
                    setInvalidCredentials(false);
                    setErrorMessage('');
                  }}
                  autoFocus
                />
              </label>

              <label className="field" htmlFor="password">
                <span>{label('Mật khẩu')}</span>
                <input
                  id="password"
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  minLength={15}
                  maxLength={128}
                  required
                  aria-required="true"
                  aria-invalid={invalidCredentials}
                  aria-describedby={invalidCredentials ? 'login-error' : undefined}
                  onChange={() => {
                    setInvalidCredentials(false);
                    setErrorMessage('');
                  }}
                />
              </label>

              <p className="form-error" id="login-error" role="alert">
                {label(errorMessage)}
              </p>

              <button className="primary-button" type="submit" disabled={isSubmitting}>
                {label(isSubmitting ? 'Đang đăng nhập…' : 'Đăng nhập')}
              </button>
            </form>
          )}

          {screen === 'signed-in' && user && (
            <div className="form-content">
              <h2 id="form-title">{label('Đã đăng nhập')}</h2>
              <p className="form-description">
                Your session is active for <strong>{user.username}</strong>.
              </p>
              <p className="form-error" role="alert" aria-live="polite">
                {label(errorMessage)}
              </p>
              <button
                className="primary-button"
                type="button"
                onClick={handleLogout}
                disabled={isSubmitting}
              >
                {label(isSubmitting ? 'Đang đăng xuất…' : 'Đăng xuất')}
              </button>
            </div>
          )}
        </section>
      </main>

      <footer className="auth-footer">
        <span>Where my bucks go?</span>
        <span>Secure sign-in with username and password.</span>
      </footer>
    </div>
  );
}

function messageFor(error: unknown): string {
  if (!(error instanceof ApiError)) return 'Could not connect. Check your network and try again.';
  if (error.statusCode === 401) return 'Incorrect username or password.';
  if (error.statusCode === 429) {
    return 'Too many sign-in attempts. Wait and try again.';
  }
  if (error.statusCode === 503) return 'Sign-in is temporarily unavailable. Try again later.';
  return 'Could not sign in. Try again.';
}
