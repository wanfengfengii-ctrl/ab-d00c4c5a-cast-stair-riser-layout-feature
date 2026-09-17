import { useEffect, useMemo, useRef, useState } from 'react'

const FIELDS = [
  { key: 'floor_height_mm', label: '层高', testid: 'floor-height', hint: '上下楼层结构面标高差' },
  { key: 'run_length_mm', label: '水平可用长度', testid: 'run-length', hint: '梯段水平投影可用长度' },
  { key: 'riser_min_mm', label: '踏步高度下限', testid: 'riser-min' },
  { key: 'riser_max_mm', label: '踏步高度上限', testid: 'riser-max' },
  { key: 'tread_min_mm', label: '踏面深度下限', testid: 'tread-min' },
  { key: 'tread_max_mm', label: '踏面深度上限', testid: 'tread-max' },
  { key: 'target_riser_mm', label: '目标踏步高度', testid: 'target-riser' },
]

const DEFAULTS = {
  floor_height_mm: '3000',
  run_length_mm: '4800',
  riser_min_mm: '150',
  riser_max_mm: '190',
  tread_min_mm: '250',
  tread_max_mm: '320',
  target_riser_mm: '175',
}

const INTEGER_RE = /^\d+$/
// 浏览器安全整数上限 2^53 - 1：超出后 JSON 数字无法精确表示，必须明确拒绝而非静默舍入
const MAX_SAFE_INTEGER = 9007199254740991

function validate(values) {
  const errors = {}
  for (const f of FIELDS) {
    const raw = values[f.key].trim()
    if (raw === '') {
      errors[f.key] = '必填'
    } else if (!INTEGER_RE.test(raw) || parseInt(raw, 10) <= 0) {
      errors[f.key] = '必须为正整数（毫米）'
    } else if (!Number.isSafeInteger(Number(raw))) {
      errors[f.key] = `超出可精确表示的整数上限（${MAX_SAFE_INTEGER}）`
    }
  }
  const num = (k) => parseInt(values[k], 10)
  if (!errors.riser_min_mm && !errors.riser_max_mm && num('riser_min_mm') > num('riser_max_mm')) {
    errors.riser_max_mm = '区间下限不得大于上限'
  }
  if (!errors.tread_min_mm && !errors.tread_max_mm && num('tread_min_mm') > num('tread_max_mm')) {
    errors.tread_max_mm = '区间下限不得大于上限'
  }
  return errors
}

/** 展示用格式化：整数原样，否则保留两位小数。 */
function fmt(x) {
  if (typeof x !== 'number' || Number.isInteger(x)) return String(x)
  return x.toFixed(2).replace(/0$/, '').replace(/\.$/, '')
}

/** 从 422 响应中取出可定位字段的说明文本。 */
async function readErrorDetail(res) {
  try {
    const body = await res.json()
    if (Array.isArray(body.detail)) {
      return body.detail.map((d) => d.msg).filter(Boolean).join('；')
    }
    if (typeof body.detail === 'string') return body.detail
  } catch {
    // 响应体不是 JSON：退回通用提示
  }
  return ''
}

export default function App() {
  const [values, setValues] = useState(DEFAULTS)
  const [result, setResult] = useState(null)
  const [apiError, setApiError] = useState(null)
  // 人工选用：{ steps, nonce }；null 表示自动推荐。nonce 允许对同一踏步数重新发起改选请求。
  const [selection, setSelection] = useState(null)
  const [switchError, setSwitchError] = useState(null)
  const [loading, setLoading] = useState(false)
  const requestId = useRef(0)

  const errors = useMemo(() => validate(values), [values])
  const valid = Object.keys(errors).length === 0

  useEffect(() => {
    if (!valid) {
      // 字段非法：立即清除旧结果
      setResult(null)
      setApiError(null)
      setSwitchError(null)
      setLoading(false)
      return
    }
    setLoading(true)
    const id = ++requestId.current
    const manual = selection !== null // 本次请求是否为人工改选（决定失败时是否保留当前方案）
    const timer = setTimeout(async () => {
      const payload = {}
      for (const f of FIELDS) payload[f.key] = parseInt(values[f.key], 10)
      if (selection) payload.selected_steps = selection.steps
      try {
        const res = await fetch('/api/layout', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        })
        if (id !== requestId.current) return // 已有更新的请求，丢弃过期响应
        if (!res.ok) {
          if (manual) {
            // 改选失败：保留当前有效方案，原位提示未能切换，避免现场误把失败当成功
            const detail = await readErrorDetail(res)
            const wanted = selection?.steps
            setSwitchError(
              `未能切换到 ${wanted} 级踏步方案（HTTP ${res.status}）${detail ? `：${detail}` : ''}，当前仍显示原方案。`,
            )
          } else {
            setResult(null)
            setApiError(`计算请求失败（HTTP ${res.status}）`)
          }
        } else {
          setResult(await res.json())
          setApiError(null)
          setSwitchError(null)
        }
      } catch {
        if (id !== requestId.current) return
        if (manual) {
          setSwitchError('无法连接计算服务，未能切换踏步方案；当前仍显示原方案。')
        } else {
          setResult(null)
          setApiError('无法连接计算服务')
        }
      } finally {
        if (id === requestId.current) setLoading(false)
      }
    }, 250)
    return () => clearTimeout(timer)
  }, [values, valid, selection])

  const onChange = (key) => (e) => {
    setValues((v) => ({ ...v, [key]: e.target.value }))
    // 任一尺寸变化：清除人工选用，恢复自动推荐
    if (selection !== null) setSelection(null)
  }

  // 复用当前表单输入，仅携带 selected_steps 重新请求
  const adopt = (steps) => {
    setSwitchError(null)
    setSelection({ steps, nonce: Date.now() })
  }

  return (
    <div className="page">
      <header>
        <h1>混凝土楼梯支模放样工具</h1>
        <p className="subtitle">所有尺寸单位均为毫米（mm），须为正整数；踏步高度与踏面深度区间为闭区间，边界值有效。</p>
      </header>

      <section className="form-grid" data-testid="input-form">
        {FIELDS.map((f) => (
          <label key={f.key} className={`field${errors[f.key] ? ' field-error' : ''}`}>
            <span className="field-label">{f.label}（mm）</span>
            <input
              data-testid={f.testid}
              value={values[f.key]}
              onChange={onChange(f.key)}
              inputMode="numeric"
              autoComplete="off"
            />
            {f.hint && !errors[f.key] && <span className="field-hint">{f.hint}</span>}
            {errors[f.key] && (
              <span className="field-error-text" data-testid={`${f.testid}-error`}>
                {errors[f.key]}
              </span>
            )}
          </label>
        ))}
      </section>

      {loading && <p className="loading" data-testid="loading">计算中…</p>}
      {apiError && <p className="api-error" data-testid="api-error">{apiError}</p>}
      {switchError && <p className="api-error" data-testid="switch-error">{switchError}</p>}

      {result && result.status === 'no_solution' && (
        <section className="conclusion no-solution" data-testid="no-solution">
          <h2>无法放样</h2>
          <p>
            在 2–40 级踏步范围内，没有任何踏步数能同时满足踏步高度区间
            [{values.riser_min_mm}, {values.riser_max_mm}]mm 与踏面深度区间
            [{values.tread_min_mm}, {values.tread_max_mm}]mm。请调整区间或现场尺寸后重新放样。
          </p>
        </section>
      )}

      {result && result.status === 'ok' && result.solution && (
        <Solution result={result} switching={loading && selection !== null} onAdopt={adopt} />
      )}
    </div>
  )
}

function Solution({ result, switching, onAdopt }) {
  const sol = result.solution
  const isManual = result.selection_source === 'manual'
  return (
    <>
      <section className="conclusion" data-testid="solution">
        <h2>
          放样结论：<span data-testid="step-count">{sol.steps}</span> 级踏步（{sol.treads} 个踏面）
          {isManual ? (
            <span className="badge badge-manual" data-testid="selection-source">人工选用</span>
          ) : (
            <span className="badge badge-auto" data-testid="selection-source">自动推荐</span>
          )}
        </h2>
        {isManual && (
          <p className="selection-note" data-testid="selection-note">
            当前为现场人工选用方案；系统自动推荐为
            <strong data-testid="recommended-steps"> {result.recommended_steps} </strong>
            级。修改任一尺寸即恢复自动推荐。
          </p>
        )}
        <div className="summary-grid">
          <div>
            <span className="summary-label">精确踏步高度</span>
            <strong>{fmt(sol.exact_riser_mm)} mm</strong>
          </div>
          <div>
            <span className="summary-label">与目标 {sol.target_riser_mm}mm 偏差</span>
            <strong>{fmt(sol.deviation_mm)} mm</strong>
          </div>
          <div>
            <span className="summary-label">精确踏面深度</span>
            <strong>{fmt(sol.exact_tread_mm)} mm</strong>
          </div>
          <div>
            <span className="summary-label">踏面放样取值（四舍五入到 1mm）</span>
            <strong data-testid="tread-display">{sol.tread_display_mm} mm</strong>
          </div>
          <div>
            <span className="summary-label">逐级最大高差</span>
            <strong>{sol.max_riser_diff_mm} mm</strong>
          </div>
          <div>
            <span className="summary-label">高度合计校验</span>
            <strong>{sol.total_height_mm} mm</strong>
          </div>
        </div>
      </section>

      <section>
        <h3>逐级踏步高度（余数从第一级起各 +1mm）</h3>
        <div className="table-wrap">
          <table data-testid="riser-table">
            <thead>
              <tr>
                <th>级号</th>
                <th>踏步高度（mm）</th>
                <th>累计标高（mm）</th>
              </tr>
            </thead>
            <tbody>
              {sol.riser_sequence_mm.map((h, i) => (
                <tr key={i} data-testid={`riser-row-${i + 1}`}>
                  <td>{i + 1}</td>
                  <td>{h}</td>
                  <td>{sol.cumulative_height_mm[i]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h3>候选踏步数（2–40）可行性结论</h3>
        <div className="table-wrap">
          <table data-testid="candidate-table">
            <thead>
              <tr>
                <th>踏步数</th>
                <th>踏面数</th>
                <th>精确踏步高度（mm）</th>
                <th>精确踏面深度（mm）</th>
                <th>与目标偏差（mm）</th>
                <th>结论 / 淘汰原因</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {result.candidates.map((c) => (
                <tr
                  key={c.steps}
                  data-testid={`candidate-row-${c.steps}`}
                  className={c.selected ? 'row-selected' : c.feasible ? 'row-feasible' : 'row-rejected'}
                >
                  <td>{c.steps}</td>
                  <td>{c.treads}</td>
                  <td>{fmt(c.exact_riser_mm)}</td>
                  <td>{fmt(c.exact_tread_mm)}</td>
                  <td>{fmt(c.deviation_mm)}</td>
                  <td>
                    {c.selected ? (
                      <span data-testid="candidate-selected">
                        ✓ 选中{isManual ? '（人工选用）' : '（自动推荐）'}
                      </span>
                    ) : c.recommended ? (
                      <>
                        <span className="badge badge-auto" data-testid="candidate-recommended">☆ 自动推荐</span>
                        <span className="cell-reason">{c.reasons.join('；')}</span>
                      </>
                    ) : (
                      c.reasons.join('；')
                    )}
                  </td>
                  <td>
                    {c.feasible && !c.selected && (
                      <button
                        type="button"
                        className="adopt-btn"
                        data-testid={`adopt-${c.steps}`}
                        disabled={switching}
                        onClick={() => onAdopt(c.steps)}
                      >
                        采用此方案
                      </button>
                    )}
                    {c.feasible && c.selected && (
                      <button type="button" className="adopt-btn" disabled>
                        当前采用
                      </button>
                    )}
                    {!c.feasible && <span className="muted">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  )
}
