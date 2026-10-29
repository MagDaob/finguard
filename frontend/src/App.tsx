import { lazy, Suspense, useEffect, useState, type FormEvent } from 'react'
import { NavLink, Navigate, Route, Routes, useNavigate, useLocation } from 'react-router-dom'
import {
    Beaker,
    ChevronRight,
    CircleUserRound,
    CreditCard,
    FolderKanban,
    Landmark,
    PlugZap,
    ShieldCheck,
    Wallet,
    ArrowRight,
} from 'lucide-react'
import { useSession } from './session'
import { ErrorNotice, Field, Loading } from './ui'
import { desktopSession, useDesktop } from './desktop'
import { TasksPage, TaskHistory } from './tasks'
import styles from './App.module.css'

const AccountsPage = lazy(() => import('./business').then((m) => ({ default: m.AccountsPage })))
const AuthorizationsPage = lazy(() =>
    import('./business').then((m) => ({ default: m.AuthorizationsPage })),
)
const ProductsPage = lazy(() => import('./business').then((m) => ({ default: m.ProductsPage })))
const ServicesPage = lazy(() => import('./business').then((m) => ({ default: m.ServicesPage })))
const SettingsPage = lazy(() => import('./settings').then((m) => ({ default: m.SettingsPage })))
const LabPage = lazy(() => import('./lab').then((m) => ({ default: m.LabPage })))

function Login() {
    const desktop = useDesktop()
    const { login } = useSession(),
        [token, setToken] = useState(''),
        [busy, setBusy] = useState(false),
        [error, setError] = useState<unknown>()
    const navigate = useNavigate(),
        location = useLocation()
    const connectDesktop = async () => {
        if (busy) return
        setBusy(true)
        setError(null)
        try {
            await login(await desktopSession())
            navigate(location.pathname === '/' ? '/tasks' : location.pathname)
        } catch (e) {
            setError(e)
        } finally {
            setBusy(false)
        }
    }
    const submit = async (e: FormEvent) => {
        e.preventDefault()
        if (busy) return
        setBusy(true)
        setError(null)
        const value = token.trim()
        setToken('')
        try {
            await login(value)
            navigate(location.pathname === '/' ? '/tasks' : location.pathname)
        } catch (e) {
            setError(e)
        } finally {
            setBusy(false)
        }
    }
    return (
        <div className={styles.login}>
            <div className={styles.loginCard}>
                <div className="section-symbol">
                    <ShieldCheck size={30} />
                </div>
                <span className="eyebrow">可信执行 · 结果可核实</span>
                <h1>
                    让金融任务
                    <br />
                    每一步都有依据。
                </h1>
                <p className="muted">
                    连接你的模拟工作台，确认计划、跟踪执行，查看真实产生的业务凭证。
                </p>
                {desktop ? (
                    <>
                        <p className="muted">已连接本机独立模拟服务。会话只保留在窗口内存中。</p>
                        <ErrorNotice error={error} />
                        <button
                            className="primary full"
                            disabled={busy}
                            onClick={() => void connectDesktop()}
                        >
                            {busy ? '正在建立会话…' : '进入本机工作台'}
                            <ArrowRight size={17} />
                        </button>
                    </>
                ) : (
                    <form onSubmit={submit}>
                        <Field
                            label="开发验收访问令牌"
                            hint="令牌仅保存在本次页面内存中，刷新或退出后需重新输入。"
                        >
                            <input
                                type="password"
                                autoComplete="off"
                                spellCheck={false}
                                value={token}
                                onChange={(e) => setToken(e.target.value)}
                                placeholder="输入 Bearer 访问令牌"
                                required
                                aria-label="访问令牌"
                            />
                        </Field>
                        <ErrorNotice error={error} />
                        <button className="primary full" disabled={busy || !token.trim()}>
                            {busy ? '正在验证会话…' : '进入工作台'}
                            <ArrowRight size={17} />
                        </button>
                    </form>
                )}
                <div className="login-foot">
                    <span className="status-dot" />
                    模拟环境 · 使用虚构账户与业务数据
                </div>
            </div>
        </div>
    )
}
export default function App() {
    const { me, epoch } = useSession()
    const [menuOpen, setMenuOpen] = useState(false)
    const location = useLocation()
    useEffect(() => setMenuOpen(false), [location.pathname])
    const links = [
        ['/tasks', '任务工作台', FolderKanban],
        ['/accounts', '账户与账单', Wallet],
        ['/authorizations', '授权管理', ShieldCheck],
        ['/products', '产品与持仓', Landmark],
        ['/services', '卡片与订阅', CreditCard],
        ...(me?.scopes.includes('evaluation.manage') ? [['/lab', '调试与评估', Beaker]] : []),
        ['/settings', '连接与设置', PlugZap],
    ] as const
    return (
        <div className={styles.shell}>
            <header className={styles.header}>
                <NavLink to="/tasks" className={styles.brand}>
                    <span className={styles.logo}>F</span>FinGuard
                </NavLink>
                {me && (
                    <button
                        className={styles.menu}
                        aria-expanded={menuOpen}
                        aria-label="切换导航"
                        onClick={() => setMenuOpen(!menuOpen)}
                    >
                        ☰
                    </button>
                )}
                <div className={styles.headerRight}>
                    <span>金融任务工作台</span>
                    <span className="environment">
                        <span className="status-dot" />
                        模拟环境
                    </span>
                    {me && (
                        <NavLink to="/settings" className="identity">
                            <CircleUserRound size={18} />
                            <span>{me.subject_ref}</span>
                            <ChevronRight size={14} />
                        </NavLink>
                    )}
                </div>
            </header>
            {!me ? (
                <Login />
            ) : (
                <>
                    <aside className={`${styles.sidebar} ${menuOpen ? styles.open : ''}`}>
                        <NavLink className={styles.newTask} to="/tasks">
                            ＋ 新建任务
                        </NavLink>
                        <Routes>
                            <Route path="/tasks/:taskRef?" element={<TaskHistory />} />
                            <Route path="*" element={<TaskHistory />} />
                        </Routes>
                        <details className={styles.resources}>
                            <summary>资金、授权与设置</summary>
                            <nav aria-label="主导航" className={styles.nav}>
                                {links.map(([to, title, Icon]) => (
                                    <NavLink key={String(to)} to={String(to)}>
                                        <Icon size={19} />
                                        <span>{String(title)}</span>
                                    </NavLink>
                                ))}
                            </nav>
                        </details>
                        <div className={styles.footnote}>
                            <ShieldCheck size={17} />
                            <br />
                            模型负责规划
                            <br />
                            服务端控制执行
                            <br />
                            业务证据核实结果
                        </div>
                    </aside>
                    <main className={styles.main} key={epoch}>
                        <Suspense fallback={<Loading />}>
                            <Routes>
                                <Route path="/tasks/:taskRef?" element={<TasksPage />} />
                                <Route path="/accounts/:accountRef?" element={<AccountsPage />} />
                                <Route path="/authorizations" element={<AuthorizationsPage />} />
                                <Route path="/products" element={<ProductsPage />} />
                                <Route path="/services" element={<ServicesPage />} />
                                <Route path="/settings/*" element={<SettingsPage />} />
                                <Route path="/lab/*" element={<LabPage />} />
                                <Route path="*" element={<Navigate to="/tasks" replace />} />
                            </Routes>
                        </Suspense>
                    </main>
                </>
            )}
        </div>
    )
}
