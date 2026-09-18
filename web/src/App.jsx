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

/** 从 422 响应中解析错误：优先定位到具体控制点字段，否则返回不可定位的聚合消息。 */
async function parseLayoutError(res) {
  let detail = null
  try {
    const body = await res.json()
    detail = body.detail
  } catch {
    // 响应体不是 JSON：返回 null，调用方使用 HTTP 状态兜底提示
    return null
  }
  if (Array.isArray(detail)) {
    const messages = detail.map((d) => d.msg).filter(Boolean)
    const cpErr = detail.find((d) => Array.isArray(d.loc) && d.loc.includes('control_points'))
    if (cpErr) {
      const loc = cpErr.loc
      const at = loc.indexOf('control_points')
      const index = typeof loc[at + 1] === 'number' ? loc[at + 1] : null
      const field = typeof loc[at + 2] === 'string' ? loc[at + 2] : null
      return { index, field, msg: cpErr.msg, allMessages: messages }
    }
    return { index: null, field: null, msg: messages.join('；'), allMessages: messages }
  }
  if (typeof detail === 'string') return { index: null, field: null, msg: detail, allMessages: [detail] }
  return null
}

export default function App() {
  const [values, setValues] = useState(DEFAULTS)
  const [result, setResult] = useState(null)
  const [apiError, setApiError] = useState(null)
  // 人工选用：{ steps, nonce }；null 表示自动推荐。nonce 允许对同一踏步数重新发起改选请求。
  const [selection, setSelection] = useState(null)
  const [switchError, setSwitchError] = useState(null)
  // 中间标高控制点：draft 为录入行（字符串），appliedPoints 为已应用、随请求发送的整数列表（null 表示未受控）
  const [draft, setDraft] = useState([{ step: '', elev: '' }])
  const [appliedPoints, setAppliedPoints] = useState(null)
  const [draftErrors, setDraftErrors] = useState([])
  const [controlError, setControlError] = useState(null) // { index, field, msg }
  const [loading, setLoading] = useState(false)
  const requestId = useRef(0)

  const errors = useMemo(() => validate(values), [values])
  const valid = Object.keys(errors).length === 0

  // 尺寸或踏步数变化均清空控制点（录入与已应用状态），按原链路重新计算
  const clearControlPoints = () => {
    setDraft([{ step: '', elev: '' }])
    setAppliedPoints(null)
    setDraftErrors([])
    setControlError(null)
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
    // 人工改选或应用控制点属于“在当前方案上操作”：失败时保留当前方案，避免误把失败当成功
    const preserveOnFailure = selection !== null || (appliedPoints && appliedPoints.length > 0)
    const timer = setTimeout(async () => {
      const payload = {}
      for (const f of FIELDS) payload[f.key] = parseInt(values[f.key], 10)
      if (selection) payload.selected_steps = selection.steps
      if (appliedPoints && appliedPoints.length > 0) {
        payload.control_points = appliedPoints.map((p) => ({
          step: p.step,
          cumulative_mm: p.elev,
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
          if (preserveOnFailure) {
            // 操作失败：保留当前有效方案，原位提示，避免现场误把失败当成功
            const parsed = await parseLayoutError(res)
            if (payload.control_points) {
              setControlError(
                parsed || {
                  index: null,
                  field: null,
                  msg: `控制点应用失败（HTTP ${res.status}），当前仍显示原方案。`,
                },
              )
            } else {
              const detail = parsed ? parsed.msg : ''
              const wanted = selection?.steps
              setSwitchError(
                `未能切换到 ${wanted} 级踏步方案（HTTP ${res.status}）${detail ? `：${detail}` : ''}，当前仍显示原方案。`,
              )
            }
          } else {
            setResult(null)
            setApiError(`计算请求失败（HTTP ${res.status}）`)
          }
        } else {
          setResult(await res.json())
          setApiError(null)
          setSwitchError(null)
          setControlError(null)
        }
      } catch {
        if (id !== requestId.current) return
        if (preserveOnFailure) {
          if (payload.control_points) {
            setControlError({ index: null, field: null, msg: '无法连接计算服务，控制点未应用；当前仍显示原方案。' })
          } else {
            setSwitchError('无法连接计算服务，未能切换踏步方案；当前仍显示原方案。')
          }
        } else {
          setResult(null)
          setApiError('无法连接计算服务')
        }
      } finally {
        if (id === requestId.current) setLoading(false)
      }
    }, 250)
    return () => clearTimeout(timer)
  }, [values, valid, selection, appliedPoints])

  const onChange = (key) => (e) => {
    setValues((v) => ({ ...v, [key]: e.target.value }))
    // 任一尺寸变化：清除人工选用与控制点，恢复自动推荐
    if (selection !== null) setSelection(null)
    clearControlPoints()
  }

  // 复用当前表单输入，仅携带 selected_steps 重新请求
  const adopt = (steps) => {
    setSwitchError(null)
    // 改选踏步数：清空控制点，先按原链路得到新方案，再由现场重新录入
    clearControlPoints()
    setSelection({ steps, nonce: Date.now() })
  }

  const updateDraftRow = (i, key) => (e) => {
    setDraft((rows) => rows.map((r, idx) => (idx === i ? { ...r, [key]: e.target.value } : r)))
    setControlError(null)
  }
  const addDraftRow = () => {
    setDraft((rows) => [...rows, { step: '', elev: '' }])
    setControlError(null)
  }
  const removeDraftRow = (i) => {
    setDraft((rows) => (rows.length === 1 ? [{ step: '', elev: '' }] : rows.filter((_, idx) => idx !== i)))
    setControlError(null)
    setDraftErrors([])
  }

  // 应用控制点：先做客户端表单校验，再携带 control_points 重新请求
  const applyControlPoints = () => {
    const floor = parseInt(values.floor_height_mm, 10)
    const maxStep = result && result.solution ? result.solution.steps - 1 : null
    const rowErrors = []
    const parsed = []
    let ok = true
    let previous = null // 上一个填写完整的行
    draft.forEach((r) => {
      const e = {}
      const stepRaw = r.step.trim()
      const elevRaw = r.elev.trim()
      // 整行留空：视为“未添加”，直接跳过，不阻断应用
      if (stepRaw === '' && elevRaw === '') {
        rowErrors.push(e)
        parsed.push({ step: null, elev: null })
        return
      }
      let step = null
      let elev = null
      if (stepRaw === '') {
        e.step = '需与标高成对填写'
        ok = false
      } else if (!INTEGER_RE.test(stepRaw) || parseInt(stepRaw, 10) <= 0) {
        e.step = '必须为正整数（级号）'
        ok = false
      } else {
        step = parseInt(stepRaw, 10)
        if (maxStep !== null && !(step >= 1 && step <= maxStep)) {
          e.step = `须位于首末级之间（1–${maxStep} 级）`
          ok = false
        }
      }
      if (elevRaw === '') {
        e.elev = '需与级号成对填写'
        ok = false
      } else if (!INTEGER_RE.test(elevRaw) || parseInt(elevRaw, 10) <= 0) {
        e.elev = '必须为正整数（毫米）'
        ok = false
      } else {
        elev = parseInt(elevRaw, 10)
        if (!(elev > 0 && elev < floor)) {
          e.elev = `须位于 0 与层高 ${floor}mm 之间`
          ok = false
        }
      }
      if (previous) {
        if (step !== null && step <= previous.step) {
          e.step = '级号须严格递增'
          ok = false
        }
        if (elev !== null && previous.elev !== null && elev <= previous.elev) {
          e.elev = '标高须严格递增'
          ok = false
        }
      }
      rowErrors.push(e)
      const current = { step, elev }
      parsed.push(current)
      if (step !== null && elev !== null) previous = current
    })
    setDraftErrors(rowErrors)
    if (!ok) {
      setControlError(null)
      return
    }
    setSwitchError(null)
    setControlError(null)
    // 仅发送填写完整的行；全部留空则视为清空受控
    const clean = parsed
      .filter((p) => p.step !== null && p.elev !== null)
      .map((p) => ({ step: p.step, elev: p.elev }))
    setAppliedPoints(clean.length > 0 ? clean : null)
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
        <Solution
          result={result}
          switching={loading && selection !== null}
          applying={loading && appliedPoints !== null && appliedPoints.length > 0}
          onAdopt={adopt}
          draft={draft}
          draftErrors={draftErrors}
          controlError={controlError}
          onDraftChange={updateDraftRow}
          onDraftAdd={addDraftRow}
          onDraftRemove={removeDraftRow}
          onApply={applyControlPoints}
          onClear={clearControlPoints}
        />
      )}
    </div>
  )
}

function Solution({
  result,
  switching,
  applying,
  onAdopt,
  draft,
  draftErrors,
  controlError,
  onDraftChange,
  onDraftAdd,
  onDraftRemove,
  onApply,
  onClear,
}) {
  const sol = result.solution
  const isManual = result.selection_source === 'manual'
  const controlled = sol.controlled === true
  const hits = sol.control_points || []
  const hitSteps = new Set(hits.map((h) => h.step))

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
            <strong>{fmt(sol.total_height_mm)} mm</strong>
          </div>
        </div>
      </section>

      <section className="control-panel" data-testid="control-points">
        <h3>中间标高控制点（平台下口 / 转折级复测）</h3>
        <p className="control-hint">
          在当前 {sol.steps} 级方案上录入若干“级号、累计标高”，级号取 1–{sol.steps - 1} 级、
          累计标高取 0–{fmt(sol.total_height_mm)}mm 之间，且级号与标高均严格递增。
          应用后按控制点分段重新生成逐级表：每段段内累计增量按半毫米向上取整，控制点精确命中，
          段内任一累计值相对理想直线误差不超过 0.5mm；修改尺寸或改选踏步数会清空控制点。
        </p>
        <div className="table-wrap">
          <table data-testid="cp-table">
            <thead>
              <tr>
                <th>序号</th>
                <th>级号（1–{sol.steps - 1}）</th>
                <th>累计标高（mm，0–{fmt(sol.total_height_mm)}）</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {draft.map((row, i) => {
                const rowErr = draftErrors[i] || {}
                const serverStepErr = controlError && controlError.index === i && controlError.field === 'step'
                const serverElevErr = controlError && controlError.index === i && controlError.field === 'cumulative_mm'
                return (
                  <tr key={i} data-testid={`cp-row-${i}`}>
                    <td>{i + 1}</td>
                    <td>
                      <input
                        className={`cp-input${rowErr.step || serverStepErr ? ' cp-input-error' : ''}`}
                        data-testid={`cp-step-${i}`}
                        value={row.step}
                        onChange={onDraftChange(i, 'step')}
                        inputMode="numeric"
                        autoComplete="off"
                      />
                      {(rowErr.step || serverStepErr) && (
                        <span className="field-error-text" data-testid={`cp-step-${i}-error`}>
                          {rowErr.step || (serverStepErr ? controlError.msg : '')}
                        </span>
                      )}
                    </td>
                    <td>
                      <input
                        className={`cp-input${rowErr.elev || serverElevErr ? ' cp-input-error' : ''}`}
                        data-testid={`cp-elev-${i}`}
                        value={row.elev}
                        onChange={onDraftChange(i, 'elev')}
                        inputMode="numeric"
                        autoComplete="off"
                      />
                      {(rowErr.elev || serverElevErr) && (
                        <span className="field-error-text" data-testid={`cp-elev-${i}-error`}>
                          {rowErr.elev || (serverElevErr ? controlError.msg : '')}
                        </span>
                      )}
                    </td>
                    <td>
                      <button
                        type="button"
                        className="cp-remove-btn"
                        data-testid={`cp-remove-${i}`}
                        onClick={() => onDraftRemove(i)}
                      >
                        删除
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        <div className="control-actions">
          <button type="button" className="cp-add-btn" data-testid="cp-add" onClick={onDraftAdd}>
            添加控制点
          </button>
          <button
            type="button"
            className="cp-apply-btn"
            data-testid="cp-apply"
            disabled={applying}
            onClick={onApply}
          >
            应用控制点
          </button>
          {controlled && (
            <button type="button" className="cp-clear-btn" data-testid="cp-clear" onClick={onClear}>
              清除控制点
            </button>
          )}
        </div>
        {controlError && controlError.index === null && (
          <p className="api-error" data-testid="cp-error">{controlError.msg}</p>
        )}
        {controlError && controlError.index !== null && (
          <p className="api-error" data-testid="cp-error">
            控制点未应用（定位到第 {controlError.index + 1} 行
            {controlError.field === 'step' ? '“级号”' : '“累计标高”'}字段），当前仍显示原方案。
          </p>
        )}
        {controlled && (
          <div className="cp-hits" data-testid="cp-hits">
            <h4>控制点命中值</h4>
            <ul>
              {hits.map((h, i) => (
                <li key={h.step} data-testid={`cp-hit-${i}`}>
                  第 <strong>{h.step}</strong> 级：录入累计标高 <strong>{fmt(h.requested_mm)}</strong> mm，
                  命中 <strong data-testid={`cp-hit-value-${i}`}>{fmt(h.hit_mm)}</strong> mm，
                  误差 <strong>{fmt(h.error_mm)}</strong> mm
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      <section>
        <h3>
          {controlled
            ? '逐级踏步高度（控制点分段，段内累计按半毫米向上取整）'
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
              {sol.riser_sequence_mm.map((h, i) => (
                <tr
                  key={i}
                  data-testid={`riser-row-${i + 1}`}
                  className={hitSteps.has(i + 1) ? 'row-controlled' : undefined}
                >
                  <td>
                    {i + 1}
                    {hitSteps.has(i + 1) && (
                      <span className="cp-marker" data-testid={`cp-marker-${i + 1}`}>控制点</span>
                    )}
                  </td>
                  <td>{fmt(h)}</td>
                  <td>{fmt(sol.cumulative_height_mm[i])}</td>
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
                      <>
                        <span data-testid="candidate-selected">
                          ✓ 选中{isManual ? '（人工选用）' : '（自动推荐）'}
                        </span>
                        {/* 受控方案是推荐结果的现场调整：推荐标记保留，不与选中合并 */}
                        {controlled && c.recommended && (
                          <span className="badge badge-auto" data-testid="candidate-recommended">☆ 自动推荐</span>
                        )}
                      </>
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

