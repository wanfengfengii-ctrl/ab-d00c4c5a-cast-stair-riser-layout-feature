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
  // 中间标高控制点：draft 为录入行，appliedCp 为最近一次成功应用并随请求发送的控制点
  const [cpDraft, setCpDraft] = useState([{ step: '', elevation: '' }])
  const [appliedCp, setAppliedCp] = useState(null)
  const [cpError, setCpError] = useState(null)
  const [loading, setLoading] = useState(false)
  const requestId = useRef(0)

  const errors = useMemo(() => validate(values), [values])
  const valid = Object.keys(errors).length === 0

  // 修改尺寸或改选踏步数：清空控制点（录入行与已应用值）并按原链路重新计算
  const resetControlPoints = () => {
    setCpDraft([{ step: '', elevation: '' }])
    setAppliedCp(null)
    setCpError(null)
  }

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
    const controlled = appliedCp !== null // 本次请求是否携带控制点
    const timer = setTimeout(async () => {
      const payload = {}
      for (const f of FIELDS) payload[f.key] = parseInt(values[f.key], 10)
      if (selection) payload.selected_steps = selection.steps
      if (appliedCp) {
        payload.control_points = appliedCp.points.map((p) => ({
          step: p.step,
          elevation_mm: p.elevation,
        }))
      }
      try {
        const res = await fetch('/api/layout', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        })
        if (id !== requestId.current) return // 已有更新的请求，丢弃过期响应
        if (!res.ok) {
          if (controlled) {
            // 控制点应用失败：保留当前有效方案，原位提示（含后端给出的越界级与计算高度）
            const detail = await readErrorDetail(res)
            setCpError(
              `未能应用控制点（HTTP ${res.status}）${detail ? `：${detail}` : ''}，当前仍显示原方案。`,
            )
          } else if (manual) {
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
          setCpError(null)
        }
      } catch {
        if (id !== requestId.current) return
        if (controlled) {
          setCpError('无法连接计算服务，未能应用控制点；当前仍显示原方案。')
        } else if (manual) {
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
  }, [values, valid, selection, appliedCp])

  const onChange = (key) => (e) => {
    setValues((v) => ({ ...v, [key]: e.target.value }))
    // 任一尺寸变化：清除人工选用与控制点（含未应用的录入），恢复自动推荐
    resetControlPoints()
    if (selection !== null) setSelection(null)
  }

  // 复用当前表单输入，仅携带 selected_steps 重新请求；改选踏步数同时清空控制点
  const adopt = (steps) => {
    setSwitchError(null)
    resetControlPoints()
    setSelection({ steps, nonce: Date.now() })
  }

  // 校验录入行并携带控制点重新请求；成功后逐级表按控制点分段重算
  const applyControlPoints = () => {
    const rows = cpDraft.map((r) => ({ step: r.step.trim(), elevation: r.elevation.trim() }))
    if (rows.length === 0 || rows.some((r) => r.step === '' || r.elevation === '')) {
      setCpError('请逐条填写控制点的级号与累计标高（毫米，整数）。')
      return
    }
    const parsed = []
    const maxStep = result.solution.steps - 1
    const floorHeight = parseInt(values.floor_height_mm, 10)
    for (let i = 0; i < rows.length; i += 1) {
      const r = rows[i]
      const label = `第 ${i + 1} 行控制点`
      if (!INTEGER_RE.test(r.step) || !INTEGER_RE.test(r.elevation)) {
        setCpError(`${label}：级号与累计标高必须为正整数。`)
        return
      }
      const step = parseInt(r.step, 10)
      const elevation = parseInt(r.elevation, 10)
      if (!(step >= 1 && step <= maxStep)) {
        setCpError(`${label}：级号必须位于首末级之间（1–${maxStep}）。`)
        return
      }
      if (!(elevation >= 1 && elevation < floorHeight)) {
        setCpError(`${label}：累计标高必须位于起点 0 与层高终点 ${floorHeight}mm 之间。`)
        return
      }
      if (i > 0 && step <= parsed[i - 1].step) {
        setCpError(`${label}：级号必须严格递增。`)
        return
      }
      if (i > 0 && elevation <= parsed[i - 1].elevation) {
        setCpError(`${label}：累计标高必须严格递增。`)
        return
      }
      parsed.push({ step, elevation })
    }
    setCpError(null)
    setAppliedCp({ points: parsed, nonce: Date.now() })
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
      {cpError && <p className="api-error" data-testid="control-point-error">{cpError}</p>}

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
        <Solution
          result={result}
          switching={loading && selection !== null && appliedCp === null}
          onAdopt={adopt}
          cpDraft={cpDraft}
          setCpDraft={setCpDraft}
          onApplyControlPoints={applyControlPoints}
          applying={loading && appliedCp !== null}
          onDraftChange={() => setCpError(null)}
        />
      )}
    </div>
  )
}

function Solution({ result, switching, onAdopt, cpDraft, setCpDraft,
                    onApplyControlPoints, applying, onDraftChange }) {
  const sol = result.solution
  const isManual = result.selection_source === 'manual'
  const controlled = sol.controlled === true
  const hits = sol.control_points || []

  const updateRow = (index, key) => (e) => {
    setCpDraft((rows) => rows.map((r, i) => (i === index ? { ...r, [key]: e.target.value } : r)))
    onDraftChange()
  }
  const addRow = () => {
    setCpDraft((rows) => [...rows, { step: '', elevation: '' }])
    onDraftChange()
  }
  const removeRow = (index) => {
    setCpDraft((rows) => (rows.length > 1 ? rows.filter((_, i) => i !== index) : rows))
    onDraftChange()
  }

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
          {controlled && (
            <span className="badge badge-controlled" data-testid="controlled-badge">控制点受控</span>
          )}
        </h2>
        {isManual && (
          <p className="selection-note" data-testid="selection-note">
            当前为现场人工选用方案；系统自动推荐为
            <strong data-testid="recommended-steps"> {result.recommended_steps} </strong>
            级。修改任一尺寸即恢复自动推荐。
          </p>
        )}
        {controlled && (
          <p className="selection-note controlled-note" data-testid="controlled-note">
            逐级高度已按现场复测控制点分段重算（各段按半毫米向上取整，控制点精确命中）：
            {hits.map((p) => (
              <span key={p.step} className="cp-hit" data-testid={`cp-hit-${p.step}`}>
                <strong> {p.step} </strong>级命中累计标高<strong> {fmt(p.elevation_mm)} </strong>mm；
              </span>
            ))}
            修改尺寸或改选踏步数将清空控制点。
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
            <strong>{fmt(sol.max_riser_diff_mm)} mm</strong>
          </div>
          <div>
            <span className="summary-label">高度合计校验</span>
            <strong>{sol.total_height_mm} mm</strong>
          </div>
        </div>

        <div className="cp-editor" data-testid="control-point-editor">
          <h4>中间标高控制点（平台下口 / 转折级复测）</h4>
          <p className="cp-hint">
            在 1–{sol.steps - 1} 级之间录入若干“级号、累计标高”（毫米，整数），级号与标高均须严格递增；
            应用后按控制点与起点、层高终点分段重算逐级表。
          </p>
          {cpDraft.map((row, i) => (
            <div className="cp-row" key={i} data-testid={`cp-row-${i}`}>
              <label className="cp-field">
                <span>级号</span>
                <input
                  data-testid={`cp-step-${i}`}
                  value={row.step}
                  onChange={updateRow(i, 'step')}
                  inputMode="numeric"
                  autoComplete="off"
                />
              </label>
              <label className="cp-field">
                <span>累计标高（mm）</span>
                <input
                  data-testid={`cp-elevation-${i}`}
                  value={row.elevation}
                  onChange={updateRow(i, 'elevation')}
                  inputMode="numeric"
                  autoComplete="off"
                />
              </label>
              <button
                type="button"
                className="cp-remove-btn"
                data-testid={`cp-remove-${i}`}
                onClick={() => removeRow(i)}
                disabled={cpDraft.length <= 1}
              >
                删除
              </button>
            </div>
          ))}
          <div className="cp-actions">
            <button type="button" className="cp-add-btn" data-testid="cp-add" onClick={addRow}>
              增加控制点
            </button>
            <button
              type="button"
              className="cp-apply-btn"
              data-testid="apply-control-points"
              onClick={onApplyControlPoints}
              disabled={applying}
            >
              {applying ? '应用中…' : '应用控制点'}
            </button>
          </div>
        </div>
      </section>

      <section>
        <h3>
          {controlled
            ? '逐级踏步高度（控制点分段，段内按半毫米向上取整）'
            : '逐级踏步高度（余数从第一级起各 +1mm）'}
        </h3>
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
              {sol.riser_sequence_mm.map((h, i) => {
                const stepNo = i + 1
                const isHit = hits.some((p) => p.step === stepNo)
                return (
                  <tr
                    key={i}
                    data-testid={`riser-row-${stepNo}`}
                    className={isHit ? 'row-control-point' : undefined}
                  >
                    <td>
                      {stepNo}
                      {isHit && <span className="cp-marker" data-testid={`cp-marker-${stepNo}`}>控制点</span>}
                    </td>
                    <td>{fmt(h)}</td>
                    <td>{fmt(sol.cumulative_height_mm[i])}</td>
                  </tr>
                )
              })}
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
                        disabled={switching || applying}
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
