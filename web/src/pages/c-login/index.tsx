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
      .login-bg {
        background: #FAFAF9;
        background-image: radial-gradient(ellipse 60% 50% at 50% 50%, rgba(99,102,241,0.05) 0%, transparent 70%);
      }
      .login-card {
        background: #FFFFFF;
        border: 1px solid #E7E5E4;
        box-shadow: 0 20px 60px -12px rgba(28,25,23,0.06);
      }
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
    <div className="fixed inset-0 login-bg flex items-center justify-center">
      <div className="login-card rounded-2xl p-10 w-full max-w-[400px]">
        {/* Header */}
        <div className="text-center mb-8">
          <div className="w-14 h-14 bg-indigo-500 rounded-2xl mx-auto flex items-center justify-center mb-5">
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
          <h1 className="text-xl font-bold text-stone-900 tracking-tight">
            标书分析助手
          </h1>
          <p className="text-stone-400 text-sm mt-1">
            智能招标文件分析与决策支持
          </p>
        </div>

        {/* Tab Switch */}
        <div className="flex bg-stone-100 rounded-xl p-1 mb-6">
          <button
            className={`flex-1 py-2 text-sm font-medium rounded-lg transition-colors ${
              tab === 'login'
                ? 'bg-white text-stone-900 shadow-sm'
                : 'text-stone-500 hover:text-stone-700'
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
            className={`flex-1 py-2 text-sm font-medium rounded-lg transition-colors ${
              tab === 'register'
                ? 'bg-white text-stone-900 shadow-sm'
                : 'text-stone-500 hover:text-stone-700'
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
              <div className="bg-red-50 border border-red-100 text-red-600 text-sm p-3 rounded-xl mb-5">
                {loginError}
              </div>
            )}
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-stone-700 mb-1.5">
                  邮箱地址
                </label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="请输入注册邮箱"
                  className="w-full px-4 py-3 bg-stone-50 border border-stone-200 rounded-xl focus:ring-0 focus:outline-none focus:border-indigo-300 focus:bg-white transition text-sm text-stone-900 placeholder:text-stone-300"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-stone-700 mb-1.5">
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
                  className="w-full px-4 py-3 bg-stone-50 border border-stone-200 rounded-xl focus:ring-0 focus:outline-none focus:border-indigo-300 focus:bg-white transition text-sm text-stone-900 placeholder:text-stone-300"
                />
              </div>
              <button
                onClick={handleLogin}
                disabled={loginLoading}
                className="w-full bg-indigo-500 hover:bg-indigo-600 text-white py-3 rounded-xl transition-colors font-medium text-sm disabled:opacity-50"
              >
                {loginLoading ? '登录中...' : '登录系统'}
              </button>
            </div>
          </>
        )}

        {/* Register Form */}
        {tab === 'register' && (
          <>
            {regSuccess ? (
              <div className="bg-emerald-50 border border-emerald-100 text-emerald-700 text-sm p-4 rounded-xl text-center">
                <p className="font-medium mb-1">注册成功！</p>
                <p className="text-emerald-600 text-xs">
                  即将跳转到登录页面...
                </p>
              </div>
            ) : (
              <>
                {regError && (
                  <div className="bg-red-50 border border-red-100 text-red-600 text-sm p-3 rounded-xl mb-5">
                    {regError}
                  </div>
                )}
                <div className="space-y-4">
                  <div>
                    <label className="block text-sm font-medium text-stone-700 mb-1.5">
                      昵称
                    </label>
                    <input
                      type="text"
                      value={regNickname}
                      onChange={(e) => setRegNickname(e.target.value)}
                      placeholder="请输入您的昵称"
                      className="w-full px-4 py-3 bg-stone-50 border border-stone-200 rounded-xl focus:ring-0 focus:outline-none focus:border-indigo-300 focus:bg-white transition text-sm text-stone-900 placeholder:text-stone-300"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-stone-700 mb-1.5">
                      邮箱地址
                    </label>
                    <input
                      type="email"
                      value={regEmail}
                      onChange={(e) => setRegEmail(e.target.value)}
                      placeholder="请输入邮箱地址"
                      className="w-full px-4 py-3 bg-stone-50 border border-stone-200 rounded-xl focus:ring-0 focus:outline-none focus:border-indigo-300 focus:bg-white transition text-sm text-stone-900 placeholder:text-stone-300"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-stone-700 mb-1.5">
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
                      className="w-full px-4 py-3 bg-stone-50 border border-stone-200 rounded-xl focus:ring-0 focus:outline-none focus:border-indigo-300 focus:bg-white transition text-sm text-stone-900 placeholder:text-stone-300"
                    />
                  </div>
                  <button
                    onClick={handleRegister}
                    disabled={regLoading}
                    className="w-full bg-indigo-500 hover:bg-indigo-600 text-white py-3 rounded-xl transition-colors font-medium text-sm disabled:opacity-50"
                  >
                    {regLoading ? '注册中...' : '注册账号'}
                  </button>
                </div>
              </>
            )}
          </>
        )}

        <p className="text-center text-xs text-stone-300 mt-6">
          RAGFlow Powered
        </p>
      </div>
    </div>
  );
}
