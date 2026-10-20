import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = join(dirname(fileURLToPath(import.meta.url)), '..')
const useOnboardingSource = readFileSync(join(here, 'src', 'hooks', 'useOnboarding.ts'), 'utf-8')
const dashboardSource = readFileSync(join(here, 'src', 'pages', 'DashboardPage.tsx'), 'utf-8')
const zhSource = readFileSync(join(here, 'src', 'i18n', 'zh.ts'), 'utf-8')

test('verify tracking uses a polling state machine instead of a one-shot check', () => {
  assert.match(useOnboardingSource, /const \[verifyPhase, setVerifyPhase\] = useState<'idle' \| 'polling' \| 'success' \| 'timeout'>\('idle'\)/)
  assert.match(useOnboardingSource, /const pollingRef = useRef<ReturnType<typeof setInterval> \| null>\(null\)/)
  assert.match(useOnboardingSource, /const pollingStartRef = useRef<number>\(0\)/)
  assert.doesNotMatch(useOnboardingSource, /const \[verifying, setVerifying\]/)
})

test('verify tracking polls immediately, every 2 seconds, and times out after 45 seconds', () => {
  assert.match(useOnboardingSource, /void checkForEvent\(\)/)
  assert.match(useOnboardingSource, /pollingRef\.current = setInterval\(checkForEvent, 2000\)/)
  assert.match(useOnboardingSource, /Date\.now\(\) - pollingStartRef\.current >= 45000/)
  assert.match(useOnboardingSource, /setVerifyPhase\('timeout'\)/)
})

test('verify tracking stops polling on duplicate click, success, timeout, and unmount', () => {
  assert.match(useOnboardingSource, /stopVerificationPolling\(\)\s*\n\s*setVerifyPhase\('polling'\)/)
  assert.match(useOnboardingSource, /if \(total > 0\) \{[\s\S]*stopVerificationPolling\(\)/)
  assert.match(useOnboardingSource, /if \(Date\.now\(\) - pollingStartRef\.current >= 45000\) \{[\s\S]*stopVerificationPolling\(\)/)
  assert.match(useOnboardingSource, /useEffect\(\(\) => stopVerificationPolling, \[stopVerificationPolling\]\)/)
})

test('verify tracking UI exposes polling, timeout, and success states without manual CTA', () => {
  assert.match(dashboardSource, /verifyPhase === 'polling'\s*\? t\('Waiting for your first event\.\.\.'\)/)
  assert.match(dashboardSource, /verifyPhase === 'timeout'\s*\? t\(verifyTimeoutGuidance\)/)
  assert.doesNotMatch(dashboardSource, /disabled=\{verifyPhase === 'polling'\}/)
  assert.doesNotMatch(dashboardSource, /verifyPhase === 'polling' \? `⌛ \$\{t\('Waiting\.\.\.'\)\}`/)
  assert.match(dashboardSource, /\{t\('Tracking works\. Your first request is recorded\.'\)\}/)
})

test('verify tracking strings have Chinese translations', () => {
  assert.match(zhSource, /'Waiting for your first event\.\.\.': '正在等待你的第一个请求\.\.\.'/)
  assert.match(zhSource, /'No event found yet': '还没找到事件'/)
  assert.match(zhSource, /'Tracking works\. Your first request is recorded\.':/)
})
