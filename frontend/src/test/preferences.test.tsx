import { render, screen, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { it, expect, afterEach } from 'vitest'
import { usePreferences } from '../preferences'
function Fixture() {
    const { value, save, defaults } = usePreferences()
    return (
        <>
            <label>
                <input
                    type="checkbox"
                    checked={value.largeText}
                    onChange={(e) => save({ ...value, largeText: e.target.checked })}
                />
                放大文字
            </label>
            <button onClick={() => save(defaults)}>恢复默认</button>
        </>
    )
}
afterEach(() => localStorage.clear())
it('persists preferences across mounts and restores defaults', async () => {
    render(<Fixture />)
    await userEvent.click(screen.getByLabelText('放大文字'))
    expect(document.documentElement.dataset.largeText).toBe('true')
    cleanup()
    render(<Fixture />)
    expect(screen.getByLabelText('放大文字')).toBeChecked()
    await userEvent.click(screen.getByText('恢复默认'))
    expect(document.documentElement.dataset.largeText).toBe('false')
    expect(screen.getByLabelText('放大文字')).not.toBeChecked()
})
it('recovers from malformed saved preferences', () => {
    localStorage.setItem('finguard.preferences.v1', 'null')
    render(<Fixture />)
    expect(screen.getByLabelText('放大文字')).not.toBeChecked()
})
