import { afterEach, expect, it, vi } from 'vitest'
import { desktopSession } from '../desktop'

afterEach(() => {
    delete window.pywebview
})

it('浏览器环境不能凭空建立桌面会话', async () => {
    await expect(desktopSession()).rejects.toThrow('尚未就绪')
})

it('桌面会话通过本机桥接取得，不写入浏览器长期存储', async () => {
    window.pywebview = {
        api: { session: vi.fn().mockResolvedValue({ token: 'fixture-desktop-token' }) },
    }
    const localWrite = vi.spyOn(Storage.prototype, 'setItem')
    expect(await desktopSession()).toBe('fixture-desktop-token')
    expect(localWrite).not.toHaveBeenCalled()
    localWrite.mockRestore()
})

it('拒绝结构异常的桌面会话响应', async () => {
    window.pywebview = { api: { session: vi.fn().mockResolvedValue({ token: 42 }) } }
    await expect(desktopSession()).rejects.toThrow('无法建立')
})
