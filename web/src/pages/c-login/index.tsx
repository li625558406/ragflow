import JSEncrypt from 'jsencrypt';
import { Loader2 } from 'lucide-react';
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
        background: #FFFFFF !important;
        min-height: 100vh;
        min-height: 100dvh;
      }
      .login-card-cs {
        background: #FFFFFF;
        border: 1px solid #D4D4D4;
        border-radius: 16px;
      }
      .login-input-cs {
        transition: border-color 0.15s, background-color 0.15s;
        border-radius: 10px;
      }
      .login-input-cs:focus {
        outline: none;
        border-color: #000000;
        background: #FFFFFF;
      }
      .login-btn-cs {
        border-radius: 10px;
        transition: background-color 0.15s;
      }
      .login-btn-cs:active { opacity: 0.9; }
      @keyframes login-card-in { from { opacity: 0; transform: scale(0.95) translateY(12px); } to { opacity: 1; transform: scale(1) translateY(0); } }
      .login-card-cs { animation: login-card-in 0.5s cubic-bezier(0.22, 1, 0.36, 1) both; }
      @keyframes login-tab-slide { from { transform: scaleX(0); } to { transform: scaleX(1); } }
      .login-tab-indicator { animation: login-tab-slide 0.3s cubic-bezier(0.22, 1, 0.36, 1); transform-origin: center; }
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
      <div className="login-card-cs p-10 w-full max-w-[420px]">
        {/* Header */}
        <div className="text-center mb-8">
          <h1 className="text-xl font-bold text-[#000000] tracking-tight">
            标书分析助手
          </h1>
          <p className="text-[#333333] text-sm mt-1.5">
            智能招标文件分析与决策支持
          </p>
        </div>

        {/* Tab Switch — underline style */}
        <div className="flex border-b border-[#D4D4D4] mb-6">
          <button
            className={`flex-1 py-2.5 text-sm font-medium transition-colors relative ${
              tab === 'login'
                ? 'text-[#000000]'
                : 'text-[#333333] hover:text-[#000000]'
            }`}
            onClick={() => {
              setTab('login');
              setLoginError('');
              setRegError('');
            }}
          >
            登录
            {tab === 'login' && (
              <span className="absolute bottom-0 left-1/4 right-1/4 h-0.5 bg-[#000000]" />
            )}
          </button>
          <button
            className={`flex-1 py-2.5 text-sm font-medium transition-colors relative ${
              tab === 'register'
                ? 'text-[#000000]'
                : 'text-[#333333] hover:text-[#000000]'
            }`}
            onClick={() => {
              setTab('register');
              setLoginError('');
              setRegError('');
            }}
          >
            注册
            {tab === 'register' && (
              <span className="absolute bottom-0 left-1/4 right-1/4 h-0.5 bg-[#000000]" />
            )}
          </button>
        </div>

        {/* Login Form */}
        {tab === 'login' && (
          <>
            {loginError && (
              <div className="bg-[#FEF2F2] border border-[#FECACA] text-[#DC2626] text-sm p-3 rounded-[10px] mb-5">
                {loginError}
              </div>
            )}
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                  邮箱地址
                </label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="请输入注册邮箱"
                  className="login-input-cs w-full px-4 py-3 bg-[#FFFFFF] border border-[#D4D4D4] text-sm text-[#000000] placeholder:text-[#A3A3A3]"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
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
                  className="login-input-cs w-full px-4 py-3 bg-[#FFFFFF] border border-[#D4D4D4] text-sm text-[#000000] placeholder:text-[#A3A3A3]"
                />
              </div>
              <button
                onClick={handleLogin}
                disabled={loginLoading}
                className="login-btn-cs w-full bg-[#000000] hover:bg-[#000000] text-white py-3 font-medium text-sm disabled:opacity-50 flex items-center justify-center gap-2"
              >
                {loginLoading && (
                  <Loader2 className="w-4 h-4 animate-spin" strokeWidth={3} />
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
              <div className="bg-[#F0FDF4] border border-[#BBF7D0] text-[#16A34A] text-sm p-4 rounded-[10px] text-center">
                <p className="font-medium mb-1">注册成功！</p>
                <p className="text-[#16A34A]/70 text-xs">
                  即将跳转到登录页面...
                </p>
              </div>
            ) : (
              <>
                {regError && (
                  <div className="bg-[#FEF2F2] border border-[#FECACA] text-[#DC2626] text-sm p-3 rounded-[10px] mb-5">
                    {regError}
                  </div>
                )}
                <div className="space-y-4">
                  <div>
                    <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                      昵称
                    </label>
                    <input
                      type="text"
                      value={regNickname}
                      onChange={(e) => setRegNickname(e.target.value)}
                      placeholder="请输入您的昵称"
                      className="login-input-cs w-full px-4 py-3 bg-[#FFFFFF] border border-[#D4D4D4] text-sm text-[#000000] placeholder:text-[#A3A3A3]"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
                      邮箱地址
                    </label>
                    <input
                      type="email"
                      value={regEmail}
                      onChange={(e) => setRegEmail(e.target.value)}
                      placeholder="请输入邮箱地址"
                      className="login-input-cs w-full px-4 py-3 bg-[#FFFFFF] border border-[#D4D4D4] text-sm text-[#000000] placeholder:text-[#A3A3A3]"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-[#1a1a1a] mb-1.5">
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
                      className="login-input-cs w-full px-4 py-3 bg-[#FFFFFF] border border-[#D4D4D4] text-sm text-[#000000] placeholder:text-[#A3A3A3]"
                    />
                  </div>
                  <button
                    onClick={handleRegister}
                    disabled={regLoading}
                    className="login-btn-cs w-full bg-[#000000] hover:bg-[#000000] text-white py-3 font-medium text-sm disabled:opacity-50"
                  >
                    {regLoading ? '注册中...' : '注册账号'}
                  </button>
                </div>
              </>
            )}
          </>
        )}

        <p className="text-center text-xs text-[#525252] mt-6">
          RAGFlow Powered
        </p>
      </div>

      {/* Login success full-screen overlay */}
      {loginSuccess && (
        <div className="fixed inset-0 bg-white flex flex-col items-center justify-center z-50">
          <Loader2
            className="w-8 h-8 animate-spin text-[#000000] mb-4"
            strokeWidth={3}
          />
          <p className="text-sm text-[#333333] font-medium">
            登录成功，正在进入...
          </p>
        </div>
      )}
    </div>
  );
}
