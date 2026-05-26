import JSEncrypt from 'jsencrypt';
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';

const RSA_PUBLIC_KEY =
  '-----BEGIN PUBLIC KEY-----MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEArq9XTUSeYr2+N1h3Afl/z8Dse/2yD0ZGrKwx+EEEcdsBLca9Ynmx3nIB5obmLlSfmskLpBo0UACBmB5rEjBp2Q2f3AG3Hjd4B+gNCG6BDaawuDlgANIhGnaTLrIqWrrcm4EMzJOnAOI1fgzJRsOOUEfaS318Eq9OVO3apEyCCt0lOQK6PuksduOjVxtltDav+guVAA068NrPYmRNabVKRNLJpL8w4D44sfth5RvZ3q9t+6RTArpEtc5sh5ChzvqPOzKGMXW83C95TxmXqpbK6olN4RevSfVjEAgCydH6HN6OhtOQEcnrU97r9H0iZOWwbw3pVrZiUkuRD1R56Wzs2wIDAQAB-----END PUBLIC KEY-----';

function utf8ToBase64(str: string) {
  return btoa(unescape(encodeURIComponent(str)));
}

function rsaEncrypt(password: string): string {
  const encryptor = new JSEncrypt();
  encryptor.setPublicKey(RSA_PUBLIC_KEY);
  const encrypted = encryptor.encrypt(utf8ToBase64(password));
  if (!encrypted) throw new Error('加密失败');
  return encrypted;
}

type Tab = 'login' | 'register';

export default function CLogin() {
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>('login');

  // Login state
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loginError, setLoginError] = useState('');
  const [loginLoading, setLoginLoading] = useState(false);
  const [loginSuccess, setLoginSuccess] = useState(false);

  // Register state
  const [regEmail, setRegEmail] = useState('');
  const [regNickname, setRegNickname] = useState('');
  const [regPassword, setRegPassword] = useState('');
  const [regError, setRegError] = useState('');
  const [regLoading, setRegLoading] = useState(false);
  const [regSuccess, setRegSuccess] = useState(false);

  // Inject styles once
  useEffect(() => {
    const id = 'c-login-styles';
    if (document.getElementById(id)) return;
    const style = document.createElement('style');
    style.id = id;
    style.textContent = `
      .login-bg-cs {
        background: #f8f6f3 !important;
        position: relative;
        min-height: 100vh;
        min-height: 100dvh;
      }
      .login-bg-cs::before {
        content: '';
        position: absolute;
        inset: 0;
        background:
          radial-gradient(ellipse 50% 60% at 30% 40%, rgba(124, 92, 252, 0.08) 0%, transparent 60%),
          radial-gradient(ellipse 40% 50% at 70% 60%, rgba(244, 114, 182, 0.06) 0%, transparent 55%),
          radial-gradient(ellipse 30% 40% at 50% 80%, rgba(46, 196, 182, 0.05) 0%, transparent 50%);
        pointer-events: none;
      }
      .login-card-cs {
        background: #FFFFFF;
        border: 1px solid rgba(124, 92, 252, 0.08);
        box-shadow: 0 24px 80px -12px rgba(124, 92, 252, 0.10);
        border-radius: 20px;
      }
      .login-input-cs {
        transition: border-color 0.2s, box-shadow 0.2s, background-color 0.2s;
        border-radius: 12px;
      }
      .login-input-cs:focus {
        outline: none;
        border-color: #7c5cfc;
        background: #FFFFFF;
        box-shadow: 0 0 0 3px rgba(124, 92, 252, 0.1);
      }
      .login-btn-cs {
        border-radius: 50px;
        transition: background-color 0.2s, transform 0.15s, box-shadow 0.2s;
      }
      .login-btn-cs:active { transform: scale(0.97); }
    `;
    document.head.appendChild(style);
  }, []);

  const handleLogin = async () => {
    if (!email || !password) {
      setLoginError('请输入邮箱和密码');
      return;
    }
    setLoginLoading(true);
    setLoginError('');
    try {
      const encryptedPwd = rsaEncrypt(password);
      const resp = await fetch('/api/v1/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password: encryptedPwd }),
      });
      const authHeader =
        resp.headers.get('Authorization') || resp.headers.get('authorization');
      const result = await resp.json();
      if (result.code !== 0) throw new Error(result.message || '登录失败');
      const t = authHeader || result.data?.access_token;
      if (!t) throw new Error('登录响应中未获取到令牌');
      const authorization = t.startsWith('Bearer ') ? t : 'Bearer ' + t;
      const info = {
        id: result.data.id || result.data.user_id,
        email: result.data.email || email,
        nickname:
          result.data.nickname || result.data.name || email.split('@')[0],
        avatar: result.data.avatar,
      };
      localStorage.setItem('Authorization', authorization);
      localStorage.setItem(
        'token',
        result.data?.access_token || t.replace('Bearer ', ''),
      );
      localStorage.setItem('userInfo', JSON.stringify(info));
      setLoginSuccess(true);
      navigate('/home');
    } catch (e: any) {
      setLoginError(e.message || '登录失败，请检查网络连接');
    } finally {
      setLoginLoading(false);
    }
  };

  const handleRegister = async () => {
    if (!regEmail || !regPassword || !regNickname) {
      setRegError('请填写所有字段');
      return;
    }
    setRegLoading(true);
    setRegError('');
    try {
      const encryptedPwd = rsaEncrypt(regPassword);
      const resp = await fetch('/api/v1/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: regEmail,
          password: encryptedPwd,
          nickname: regNickname,
        }),
      });
      const result = await resp.json();
      if (result.code !== 0) {
        if (
          result.message &&
          result.message.includes('registration is disabled')
        ) {
          throw new Error('注册功能已关闭，请联系管理员');
        }
        throw new Error(result.message || '注册失败');
      }
      setRegSuccess(true);
      // Auto switch to login tab after 2s
      setTimeout(() => {
        setRegSuccess(false);
        setTab('login');
        setEmail(regEmail);
      }, 2000);
    } catch (e: any) {
      setRegError(e.message || '注册失败');
    } finally {
      setRegLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 h-screen login-bg-cs flex items-center justify-center">
      {/* Decorative elements */}
      <div className="absolute top-20 left-1/4 w-72 h-72 rounded-full bg-gradient-to-br from-[#7c5cfc]/10 to-[#a78bfa]/5 blur-3xl pointer-events-none" />
      <div className="absolute bottom-20 right-1/4 w-64 h-64 rounded-full bg-gradient-to-br from-[#f472b6]/10 to-[#fb923c]/5 blur-3xl pointer-events-none" />

      <div className="login-card-cs p-10 w-full max-w-[400px] relative z-10">
        {/* Header */}
        <div className="text-center mb-8">
          <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-[#7c5cfc] to-[#a78bfa] mx-auto flex items-center justify-center mb-5 shadow-lg shadow-[rgba(124,92,252,0.2)]">
            <svg
              className="w-7 h-7 text-white"
              fill="none"
              stroke="currentColor"
              strokeWidth={1.5}
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
              />
            </svg>
          </div>
          <h1 className="text-xl font-bold text-[#1c1c2e] tracking-[-0.02em]">
            标书分析助手
          </h1>
          <p className="text-[#5a5a7a] text-sm mt-1 tracking-[-0.01em]">
            智能招标文件分析与决策支持
          </p>
        </div>

        {/* Tab Switch */}
        <div className="flex bg-[#f4f1fb] rounded-[50px] p-1 mb-6">
          <button
            className={`flex-1 py-2 text-sm tracking-[-0.01em] rounded-[50px] transition-all ${
              tab === 'login'
                ? 'bg-white text-[#7c5cfc] shadow-sm font-medium'
                : 'text-[#5a5a7a] hover:text-[#1c1c2e]'
            }`}
            onClick={() => {
              setTab('login');
              setLoginError('');
              setRegError('');
            }}
          >
            登录
          </button>
          <button
            className={`flex-1 py-2 text-sm tracking-[-0.01em] rounded-[50px] transition-all ${
              tab === 'register'
                ? 'bg-white text-[#7c5cfc] shadow-sm font-medium'
                : 'text-[#5a5a7a] hover:text-[#1c1c2e]'
            }`}
            onClick={() => {
              setTab('register');
              setLoginError('');
              setRegError('');
            }}
          >
            注册
          </button>
        </div>

        {/* Login Form */}
        {tab === 'login' && (
          <>
            {loginError && (
              <div className="bg-[#fef2f2] border border-[#fecaca] text-[#dc2626] text-sm p-3 rounded-xl mb-5">
                {loginError}
              </div>
            )}
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-[#5a5a7a] mb-1.5 tracking-[-0.01em]">
                  邮箱地址
                </label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="请输入注册邮箱"
                  className="login-input-cs w-full px-4 py-3 bg-[#f8f6f3] border border-[rgba(124,92,252,0.08)] text-sm text-[#1c1c2e] placeholder:text-[#9494b5] tracking-[-0.01em]"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-[#5a5a7a] mb-1.5 tracking-[-0.01em]">
                  登录密码
                </label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleLogin();
                  }}
                  placeholder="请输入密码"
                  className="login-input-cs w-full px-4 py-3 bg-[#f8f6f3] border border-[rgba(124,92,252,0.08)] text-sm text-[#1c1c2e] placeholder:text-[#9494b5] tracking-[-0.01em]"
                />
              </div>
              <button
                onClick={handleLogin}
                disabled={loginLoading}
                className="login-btn-cs w-full bg-gradient-to-r from-[#7c5cfc] to-[#a78bfa] hover:from-[#6b4ce0] hover:to-[#9678e8] text-white py-3 font-medium text-sm disabled:opacity-50 flex items-center justify-center gap-2 shadow-md shadow-[rgba(124,92,252,0.2)] hover:shadow-lg hover:shadow-[rgba(124,92,252,0.3)] tracking-[-0.01em]"
              >
                {loginLoading && (
                  <svg
                    className="w-4 h-4 animate-spin"
                    viewBox="0 0 24 24"
                    fill="none"
                  >
                    <circle
                      cx="12"
                      cy="12"
                      r="10"
                      stroke="currentColor"
                      strokeWidth="3"
                      opacity="0.25"
                    />
                    <path
                      d="M12 2a10 10 0 019.95 9"
                      stroke="currentColor"
                      strokeWidth="3"
                      strokeLinecap="round"
                    />
                  </svg>
                )}
                {loginLoading ? '正在登录...' : '登录系统'}
              </button>
            </div>
          </>
        )}

        {/* Register Form */}
        {tab === 'register' && (
          <>
            {regSuccess ? (
              <div className="bg-[#f0fdf4] border border-[#bbf7d0] text-[#16a34a] text-sm p-4 rounded-xl text-center">
                <p className="font-medium mb-1">注册成功！</p>
                <p className="text-[#16a34a]/70 text-xs">
                  即将跳转到登录页面...
                </p>
              </div>
            ) : (
              <>
                {regError && (
                  <div className="bg-[#fef2f2] border border-[#fecaca] text-[#dc2626] text-sm p-3 rounded-xl mb-5">
                    {regError}
                  </div>
                )}
                <div className="space-y-4">
                  <div>
                    <label className="block text-sm font-medium text-[#5a5a7a] mb-1.5 tracking-[-0.01em]">
                      昵称
                    </label>
                    <input
                      type="text"
                      value={regNickname}
                      onChange={(e) => setRegNickname(e.target.value)}
                      placeholder="请输入您的昵称"
                      className="login-input-cs w-full px-4 py-3 bg-[#f8f6f3] border border-[rgba(124,92,252,0.08)] text-sm text-[#1c1c2e] placeholder:text-[#9494b5] tracking-[-0.01em]"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-[#5a5a7a] mb-1.5 tracking-[-0.01em]">
                      邮箱地址
                    </label>
                    <input
                      type="email"
                      value={regEmail}
                      onChange={(e) => setRegEmail(e.target.value)}
                      placeholder="请输入邮箱地址"
                      className="login-input-cs w-full px-4 py-3 bg-[#f8f6f3] border border-[rgba(124,92,252,0.08)] text-sm text-[#1c1c2e] placeholder:text-[#9494b5] tracking-[-0.01em]"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-[#5a5a7a] mb-1.5 tracking-[-0.01em]">
                      登录密码
                    </label>
                    <input
                      type="password"
                      value={regPassword}
                      onChange={(e) => setRegPassword(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') handleRegister();
                      }}
                      placeholder="请设置密码（至少6位）"
                      className="login-input-cs w-full px-4 py-3 bg-[#f8f6f3] border border-[rgba(124,92,252,0.08)] text-sm text-[#1c1c2e] placeholder:text-[#9494b5] tracking-[-0.01em]"
                    />
                  </div>
                  <button
                    onClick={handleRegister}
                    disabled={regLoading}
                    className="login-btn-cs w-full bg-gradient-to-r from-[#7c5cfc] to-[#a78bfa] hover:from-[#6b4ce0] hover:to-[#9678e8] text-white py-3 font-medium text-sm disabled:opacity-50 shadow-md shadow-[rgba(124,92,252,0.2)] hover:shadow-lg hover:shadow-[rgba(124,92,252,0.3)] tracking-[-0.01em]"
                  >
                    {regLoading ? '注册中...' : '注册账号'}
                  </button>
                </div>
              </>
            )}
          </>
        )}

        <p className="text-center text-xs text-[#9494b5] mt-6 tracking-[-0.01em]">
          RAGFlow Powered
        </p>
      </div>

      {/* Login success full-screen overlay */}
      {loginSuccess && (
        <div className="fixed inset-0 bg-white/80 backdrop-blur-sm flex flex-col items-center justify-center z-50">
          <svg
            className="w-8 h-8 animate-spin text-[#7c5cfc] mb-4"
            viewBox="0 0 24 24"
            fill="none"
          >
            <circle
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="3"
              opacity="0.25"
            />
            <path
              d="M12 2a10 10 0 019.95 9"
              stroke="currentColor"
              strokeWidth="3"
              strokeLinecap="round"
            />
          </svg>
          <p className="text-sm text-[#5a5a7a] font-medium tracking-[-0.01em]">
            登录成功，正在进入...
          </p>
        </div>
      )}
    </div>
  );
}
