# 混凝土楼梯支模放样工具

供施工现场测量放样员使用的全栈工具：输入层高与水平可用长度及踏步约束，自动算出唯一可行的踏步数、逐级踏步高度与踏面放样尺寸，并列出全部候选踏步数的淘汰原因。前端 React，后端 FastAPI，所有结果均由输入实时计算，无任何固定响应。

## 计算公式

记层高 `H`、水平可用长度 `L`、踏步高度闭区间 `[hmin, hmax]`、踏面深度闭区间 `[dmin, dmax]`、目标踏步高度 `t`（单位均为 mm，全部为正整数，且区间下限 ≤ 上限）。

- **候选**：踏步数 `n ∈ [2, 40]`，踏面数固定为 `n − 1`。
- **精确值**：精确踏步高度 `h = H / n`，精确踏面深度 `d = L / (n − 1)`，均以分数精确运算，不舍入。
- **可行**：当且仅当 `hmin ≤ h ≤ hmax` 且 `dmin ≤ d ≤ dmax`（闭区间，边界值有效）。
- **选择**：在可行候选中先取 `|h − t|` 最小者；并列时取踏步数 `n` 较小者，方案唯一。该结果始终作为**自动推荐**（`recommended_steps`）。
- **人工选用**：请求可携带可选的 `selected_steps`。领域计算仍先得出上述推荐项，再校验指定踏步数确属当前输入的可行候选（在 `2..40` 内且同时满足高度、深度闭区间），随后**按该踏步数**生成逐级高度、累计标高与踏面取值；推荐项标识始终保留。指定越界或已被约束淘汰时返回可定位到 `selected_steps` 的 422。
- **放样序列**：`q = ⌊H / n⌋`，`r = H mod n`；第 `1..r` 级取 `q + 1`，其余取 `q`（人工选用时 `n` 为选用踏步数）。
  总和 `= q·n + r = H`，任意两级高差 `≤ 1mm`。
- **中间标高控制点（可选）**：现场复测常得到平台下口或转折级的已知累计标高。自动推荐或人工选用踏步数确定后，可再携带若干控制点（级号 `s`、累计标高 `E`，整数毫米）。计算端把起点 `(0, 0)`、层高终点 `(n, H)` 与控制点按级号分段；每段第 `j` 级（`j=1..段级数 m`）的段内累计增量取 `⌈j × 段高差 ÷ m ÷ 0.5mm⌉ × 0.5mm`，即按半毫米向上取整。由此每个控制点精确命中，段内任一累计值相对该段理想直线的误差 `< 0.5mm`，逐级表按级号唯一确定，末级仍精确闭合到层高。控制点必须位于首末级之间（`1..n−1`）、级号与累计标高严格递增；按上述算法得到的每级高度还必须落入原踏步高度闭区间 `[hmin, hmax]`，否则返回定位到具体控制点的 422，并说明越界级与计算高度。
- **踏面显示**：`d` 四舍五入（ROUND_HALF_UP）到 1mm 仅用于展示；可行性判断始终使用未舍入的精确值。
- **无候选**：若 `2..40` 中无可行踏步数，接口返回 `no_solution`，页面只给出明确的无法放样结论。

## API 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查，返回 `{"status": "ok"}` |
| POST | `/api/layout` | 计算放样方案 |

请求体（JSON，全部为正整数，非法输入返回 422）：

```json
{
  "floor_height_mm": 3000,
  "run_length_mm": 4800,
  "riser_min_mm": 150,
  "riser_max_mm": 190,
  "tread_min_mm": 250,
  "tread_max_mm": 320,
  "target_riser_mm": 175,
  "selected_steps": 18,
  "control_points": [
    {"step": 6, "elevation_mm": 1057},
    {"step": 12, "elevation_mm": 2117}
  ]
}
```

其中 `selected_steps` 可选，为 `2..40` 之间的整数；缺省（或旧请求不带该字段）时按原排序自动推荐。携带但越界、类型错误，或指定值已被高度/深度约束淘汰时，返回 `422`，错误定位于 `body.selected_steps`。

`control_points` 可选，为控制点对象列表（缺省或 `[]` 时逐级表与旧版完全一致）：每项的 `step` 为级号（必须在首末级之间 `1..steps−1`），`elevation_mm` 为该级相对起点的累计标高（整数毫米，严格位于 `0` 与层高之间）；级号与标高都必须严格递增。任一项非法（含类型错误、级号重复/倒序、标高不递增）返回 `422`，错误定位于 `body.control_points[i].step` 或 `body.control_points[i].elevation_mm`。控制点本身合法、但按分段半毫米向上取整算出的某级高度超出踏步高度闭区间时，同样返回 `422`，定位于造成该越界段的控制点 `elevation_mm`，消息中给出越界级号与该级计算高度（整毫米或半毫米）。

响应要点：

- `status`：`"ok"` 或 `"no_solution"`。
- `solution`（仅 `ok` 时）：`steps`（**当前选用**的踏步数，自动时等于推荐值，人工时等于请求的 `selected_steps`）、`treads`、`exact_riser_mm`、
  `riser_sequence_mm`（逐级高度）、`cumulative_height_mm`、`total_height_mm`、
  `max_riser_diff_mm`、`exact_tread_mm`、`tread_display_mm`（四舍五入到 1mm）。
  携带控制点并校验通过时，额外返回 `controlled: true` 与 `control_points`（各控制点的级号与**命中值**累计标高）；逐级高度与累计标高此时为整毫米或半毫米。不带控制点的旧请求不出现这两个字段，序列与旧版完全一致。
- `recommended_steps`：自动推荐踏步数（人工选用或控制点受控时也保留）；`no_solution` 时为 `null`。
- `selection_source`：`"auto"`（未携带 `selected_steps`）或 `"manual"`（人工选用成功）；`no_solution` 时为 `null`。
- `candidates`：2–40 级全部候选的精确高度/深度、是否可行、`recommended`（推荐标记，人工改选后仍在原推荐行）、`selected`（当前选用）及淘汰原因。

## 运行方式

依赖：Docker 与 Docker Compose。

```bash
# 启动 Web 与 API 两个应用组件（默认 Web 8080、API 8000）
docker compose up --build

# 覆盖宿主端口
WEB_PORT=9000 API_PORT=9001 docker compose up --build
```

浏览器访问 `http://localhost:8080`（或自定义的 `WEB_PORT`）。前端经 nginx 同源反代 `/api` 到 API 服务，无跨域问题。

一次性验收（构建镜像、启动依赖、用真实 Chromium 经 Web 代理跑通主链路后退出）：

```bash
docker compose --profile verify up --build --exit-code-from verify
```

退出码为 0 即验收通过；随后可用 `docker compose down` 清理。

## 本地开发

```bash
# API（http://localhost:8000，交互文档 /docs）
cd api && pip install -r requirements.txt
uvicorn app.main:app --reload

# Web（http://localhost:5173，/api 已代理到 8000）
cd web && npm ci && npm run dev

# 单元测试（纯逻辑，仅标准库）
cd api && python -m pytest tests -v

# 验收测试（需 API 已启动；e2e 另需 Web 已启动）
cd verify && pip install -r requirements.txt && playwright install chromium
API_BASE_URL=http://localhost:8000 WEB_BASE_URL=http://localhost:5173 \
  python -m pytest tests -v
```

## 项目结构

```
├── docker-compose.yml      # web + api 两个应用组件，verify 为一次性验收（profile）
├── api/                    # FastAPI：app/logic.py 纯分数精确计算，app/main.py 路由
│   └── tests/              # 核心逻辑单元测试
├── web/                    # React + Vite，nginx 静态托管并反代 /api
└── verify/                 # 验收：Playwright 真实浏览器 e2e + 活 API HTTP 测试
```

## 行为约定

- 表单字段非法（非正整数、下限大于上限、超出浏览器安全整数上限 9007199254740991）时，页面**立即清除**旧结果并提示具体字段；超限值明确拒绝，绝不静默舍入后计算。API 本身使用任意精度整数，按原值精确计算。
- 逐级高度表给出每级高度与累计标高，余数毫米从第一级起分配，总和恒等于层高。
- 候选表中仅**可行候选**显示“采用此方案”按钮；点击后复用当前表单输入、仅携带 `selected_steps` 重新请求，结论、逐级放样表与选中标记原位替换，原推荐行保留“自动推荐”标识。
- 任一尺寸变化都会清除人工选择并恢复自动推荐；改选请求失败（含接口拒绝不可行值）时保留当前有效方案并原位提示“未能切换”，不把失败操作呈现为成功。
- 结论区提供中间标高控制点录入（若干“级号、累计标高”）与“应用控制点”操作：成功后逐级表按控制点分段重算，显示“控制点受控”标识与各控制点命中值，自动推荐、人工选用来源与候选标记原样保留；修改任一尺寸或改选踏步数时清空控制点（含未应用的录入）并按原链路计算。应用失败（含单级高度越界的 422）时保留当前有效方案并原位提示后端给出的具体控制点、越界级与计算高度，不呈现为成功。
- 无候选时页面**只**展示明确的无法放样结论，不展示方案与候选表，人工选用与控制点均无入口。
