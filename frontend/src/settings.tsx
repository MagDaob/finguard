import { useState, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, NavLink, useLocation } from 'react-router-dom'
import { usePreferences } from './preferences'
import { CheckCircle2, Link2, LogOut, PlugZap, ShieldCheck } from 'lucide-react'
import { objectSchema } from './api'
import { useSession } from './session'
import { Confirm, ErrorNotice, Field, Notice, Panel, Raw } from './ui'

export function SettingsPage() {
    const location = useLocation(),
        tab = location.pathname.split('/')[2] ?? 'model'
    const { value, save, defaults } = usePreferences()
    const [preferenceError, setPreferenceError] = useState<unknown>()
    const changePreferences = (next: typeof value) => {
        try {
            save(next)
            setPreferenceError(null)
        } catch {
            setPreferenceError(new Error('浏览器无法保存偏好设置，请检查存储权限。'))
        }
    }
    const { api, me, logout } = useSession(),
        [kind, setKind] = useState('model'),
        [provider, setProvider] = useState('custom-model'),
        [connection, setConnection] = useState('my-model'),
        [url, setUrl] = useState('https://model.example/v1'),
        [model, setModel] = useState('my-model'),
        [protocol, setProtocol] = useState('chat_completions'),
        [credential, setCredential] = useState(''),
        [environment, setEnvironment] = useState('external'),
        [manifest, setManifest] = useState(
            '{\n  "plugin_id": "personal-report",\n  "version": "1.0.0",\n  "kind": "report"\n}',
        ),
        [configuration, setConfiguration] = useState('{}'),
        [result, setResult] = useState<Record<string, unknown> | null>(null),
        [error, setError] = useState<unknown>(),
        [busy, setBusy] = useState(false),
        [revoke, setRevoke] = useState(false)
    useEffect(() => {
        setKind(tab === 'plugins' ? 'plugin' : tab === 'bank' ? 'bank' : 'model')
        setResult(null)
        setError(null)
    }, [tab])
    const providers = useQuery({
        queryKey: [me?.subject_ref, 'providers'],
        enabled: !!api,
        queryFn: () => api!.get('/v1/integrations/providers', objectSchema),
    })
    const validate = async () => {
        if (busy || !api) return
        setBusy(true)
        setError(null)
        setResult(null)
        try {
            if (
                credential &&
                !/^(env:[A-Z][A-Z0-9_]{0,127}|secret:[A-Za-z][A-Za-z0-9_.:-]{0,127})$/.test(
                    credential,
                )
            )
                throw new Error('请填写密钥引用，不能输入真实 API key。')
            let path: string, body: unknown
            if (kind === 'plugin') {
                path = '/v1/integrations/plugins/validate'
                body = { manifest: JSON.parse(manifest), configuration: JSON.parse(configuration) }
                const hasSecret = (v: unknown): boolean =>
                    !!v &&
                    typeof v === 'object' &&
                    Object.entries(v).some(
                        ([k, x]) =>
                            (['api_key', 'password', 'secret', 'authorization', 'token'].includes(
                                k,
                            ) &&
                                typeof x === 'string' &&
                                x.length > 0) ||
                            hasSecret(x),
                    )
                if (hasSecret(body)) throw new Error('请移除明文凭据，仅使用服务端密钥引用。')
            } else {
                path = `/v1/integrations/${kind === 'model' ? 'model' : 'bank'}-connections/validate`
                body = {
                    connection_ref: connection,
                    provider,
                    ...(credential ? { credential_ref: credential } : {}),
                    ...(kind === 'model'
                        ? { base_url: url, model_id: model, protocol }
                        : { environment }),
                }
            }
            setResult(await api.post(path, body, objectSchema))
        } catch (e) {
            setError(e)
        } finally {
            setBusy(false)
        }
    }
    const revokeToken = async () => {
        setBusy(true)
        try {
            await api!.post('/v1/tokens/revoke-current', undefined, objectSchema)
            logout()
        } catch (e) {
            setError(e)
        } finally {
            setBusy(false)
            setRevoke(false)
        }
    }
    return (
        <>
            <div className="page-title">
                <div>
                    <span className="eyebrow">SESSION & INTEGRATIONS</span>
                    <h1>设置</h1>
                    <p>管理 AI 接口、显示偏好与插件接口。</p>
                </div>
            </div>
            <nav className="settings-tabs" aria-label="设置分类">
                {[
                    ['model', 'AI 接口'],
                    ['personalization', '个性化'],
                    ['plugins', '插件接口'],
                    ['bank', '银行连接'],
                    ['session', '会话与权限'],
                ].map(([path, label]) => (
                    <NavLink
                        key={path}
                        to={'/settings/' + path}
                        aria-current={tab === path ? 'page' : undefined}
                    >
                        {label}
                    </NavLink>
                ))}
            </nav>
            {tab === 'personalization' && (
                <Panel title="个性化" tag="PREFERENCES">
                    <p>保存在当前浏览器，刷新后继续生效。</p>
                    {[
                        ['largeText', '放大文字'],
                        ['compact', '紧凑布局'],
                        ['reduceMotion', '减少动态效果'],
                    ].map(([field, label]) => (
                        <label className="check" key={field}>
                            <input
                                type="checkbox"
                                checked={value[field as keyof typeof value]}
                                onChange={(e) =>
                                    changePreferences({ ...value, [field]: e.target.checked })
                                }
                            />
                            {label}
                        </label>
                    ))}
                    <ErrorNotice error={preferenceError} />
                    <button className="secondary" onClick={() => changePreferences(defaults)}>
                        恢复默认设置
                    </button>
                </Panel>
            )}
            {tab === 'session' && (
                <div className="business-grid">
                    <Panel title="当前访问会话" tag="IDENTITY">
                        <div className="identity-card">
                            <ShieldCheck size={28} />
                            <h3>{me?.subject_ref}</h3>
                            <p>访问令牌仅保存在当前页面内存中。</p>
                        </div>
                        <details className="raw">
                            <summary>当前权限（{me?.scopes.length}）</summary>
                            <ul>
                                {me?.scopes.map((s) => (
                                    <li key={s}>{s}</li>
                                ))}
                            </ul>
                        </details>
                        <div className="actions">
                            <button className="secondary" onClick={logout}>
                                <LogOut size={16} />
                                退出本机会话
                            </button>
                            <button
                                className="text-button danger-text"
                                disabled={busy}
                                onClick={() => setRevoke(true)}
                            >
                                撤销当前令牌
                            </button>
                        </div>
                        <small className="muted">
                            普通退出只清除本机会话。撤销会使服务端令牌失效。
                        </small>
                    </Panel>
                    <Panel title="扩展接口状态" tag="CONTRACTS">
                        <div className="integration-boundary">
                            <PlugZap size={31} />
                            <h3>接口已准备，连接由后续适配实现</h3>
                            <p>
                                本地校验不保存、不激活连接，也不探测外部服务。插件清单通过校验不表示获得权限或执行能力。
                            </p>
                        </div>
                        <ErrorNotice error={providers.error} />
                        {providers.data && (
                            <Raw value={providers.data} title="查看已注册提供方与兼容配置" />
                        )}
                    </Panel>
                </div>
            )}
            {tab === 'session' && (
                <Panel title="资金与权限管理" tag="MANAGEMENT">
                    <div className="actions">
                        <Link className="secondary" to="/authorizations">
                            授权与委托
                        </Link>
                        <Link className="secondary" to="/products">
                            产品与持仓
                        </Link>
                        {me?.scopes.includes('evaluation.manage') && (
                            <Link className="secondary" to="/lab">
                                开发工具与评估
                            </Link>
                        )}
                    </div>
                </Panel>
            )}
            {['model', 'plugins', 'bank'].includes(tab) && (
                <Panel
                    title={
                        tab === 'plugins' ? '插件接口' : tab === 'bank' ? '银行连接' : 'AI API 接口'
                    }
                    tag="LOCAL VALIDATION"
                >
                    <Notice>
                        当前支持配置格式校验。校验不会保存或切换运行中的接口；API
                        密钥由服务端管理，此处填写密钥引用。
                    </Notice>
                    <form
                        onSubmit={(e) => {
                            e.preventDefault()
                            void validate()
                        }}
                    >
                        {kind !== 'plugin' ? (
                            <div className="form-grid">
                                <Field label="连接编号">
                                    <input
                                        value={connection}
                                        onChange={(e) => setConnection(e.target.value)}
                                        required
                                    />
                                </Field>
                                <Field label="提供方标识">
                                    <input
                                        value={provider}
                                        onChange={(e) => setProvider(e.target.value)}
                                        required
                                    />
                                </Field>
                                {kind === 'model' ? (
                                    <>
                                        <Field label="API 基础地址">
                                            <input
                                                type="url"
                                                value={url}
                                                onChange={(e) => setUrl(e.target.value)}
                                                required
                                            />
                                        </Field>
                                        <Field label="调用协议">
                                            <select
                                                value={protocol}
                                                onChange={(e) => setProtocol(e.target.value)}
                                            >
                                                {[
                                                    'chat_completions',
                                                    'responses',
                                                    'messages',
                                                    'planning_gateway',
                                                    'deepseek_legacy',
                                                ].map((p) => (
                                                    <option key={p}>{p}</option>
                                                ))}
                                            </select>
                                        </Field>
                                        <Field label="模型名称">
                                            <input
                                                value={model}
                                                onChange={(e) => setModel(e.target.value)}
                                                required
                                            />
                                        </Field>
                                    </>
                                ) : (
                                    <Field label="环境">
                                        <select
                                            value={environment}
                                            onChange={(e) => setEnvironment(e.target.value)}
                                        >
                                            <option value="external">外部环境契约</option>
                                            <option value="simulation">模拟环境</option>
                                        </select>
                                    </Field>
                                )}
                                <Field
                                    label="密钥引用（可选）"
                                    hint="例如 env:MY_MODEL_KEY 或 secret:my-key。不要填写真实密钥。"
                                >
                                    <input
                                        value={credential}
                                        onChange={(e) => setCredential(e.target.value)}
                                        autoComplete="off"
                                        placeholder="secret:my-key"
                                    />
                                </Field>
                            </div>
                        ) : (
                            <div className="form-grid">
                                <Field label="插件清单 JSON">
                                    <textarea
                                        className="code-input"
                                        rows={9}
                                        value={manifest}
                                        onChange={(e) => setManifest(e.target.value)}
                                        spellCheck={false}
                                    />
                                </Field>
                                <Field label="配置 JSON">
                                    <textarea
                                        className="code-input"
                                        rows={9}
                                        value={configuration}
                                        onChange={(e) => setConfiguration(e.target.value)}
                                        spellCheck={false}
                                    />
                                </Field>
                            </div>
                        )}
                        <ErrorNotice error={error} />
                        <button className="primary" disabled={busy}>
                            <CheckCircle2 size={17} />
                            {busy ? '正在检查…' : '校验配置格式'}
                        </button>
                    </form>
                    {result && (
                        <div className="validation-result">
                            <h3>本地格式检查已返回</h3>
                            <Notice kind="warning">
                                这不是连接成功，也不会启用任何插件。请核对注册、协议和运行能力字段。
                            </Notice>
                            <Raw value={result} title="查看校验结果" />
                        </div>
                    )}
                </Panel>
            )}
            {revoke && (
                <Confirm
                    title="撤销当前访问令牌？"
                    description="此令牌将无法继续访问服务端；退出后需重新取得有效令牌。"
                    onClose={() => setRevoke(false)}
                    onConfirm={() => void revokeToken()}
                    busy={busy}
                />
            )}
        </>
    )
}
