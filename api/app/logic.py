"""混凝土楼梯支模放样核心计算。

设计原则：
- 可行性判断与方案选择全部使用 Fraction 精确分数运算，不做任何舍入；
- 仅展示层的踏面深度按四舍五入（ROUND_HALF_UP）取整到 1mm；
- 放样序列以层高整除踏步数的商为基础，余数从第一级起每级 +1mm，
  保证总和等于层高且任意两级高度差不超过 1mm；
- 推荐项始终由目标偏差排序得出；现场可在可行候选中人工改选，
  改选不改变推荐项本身（响应同时保留推荐踏步数与选用来源）；
- 现场复测可提供若干中间标高控制点（平台下口、转折级）。受控时以起点
  （0 级 0mm）、控制点、层高终点按级号分段，每段第 j 级的段内累计增量取
  ceil_0.5(j×段高差÷段级数)（按半毫米向上取整），使控制点精确命中，
  段内任一累计值相对该段理想直线的误差不超过 0.5mm，且每级高度仍须落入
  踏步高度闭区间，否则按控制点定位报错。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction

MIN_STEPS = 2
MAX_STEPS = 40

SOURCE_AUTO = "auto"
SOURCE_MANUAL = "manual"


class InvalidSelectionError(ValueError):
    """人工指定的 selected_steps 不属于当前输入的可行候选（越界或已被约束淘汰）。"""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class ControlPointError(ValueError):
    """中间标高控制点非法：结构性越界，或受控后单级高度超出踏步高度闭区间。

    index 为控制点在请求列表中的下标（0 起），field 为出错字段名，
    供 API 组装可定位到 body.control_points.<index>.<field> 的 422。
    """

    def __init__(self, index: int, field: str, message: str):
        self.index = index
        self.field = field
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class LayoutParams:
    """放样输入，所有尺寸均为正整数毫米，区间为闭区间。"""

    floor_height_mm: int   # 层高
    run_length_mm: int     # 水平可用长度
    riser_min_mm: int      # 踏步高度下限
    riser_max_mm: int      # 踏步高度上限
    tread_min_mm: int      # 踏面深度下限
    tread_max_mm: int      # 踏面深度上限
    target_riser_mm: int   # 目标踏步高度


@dataclass(frozen=True)
class LayoutControlPoint:
    """现场复测的中间标高控制点：第 step 级处的累计标高 cumulative_mm。"""

    step: int
    cumulative_mm: int


def _fmt(value: Fraction) -> str:
    """分数的简短文本：整数不带小数点，否则保留两位小数。"""
    if value.denominator == 1:
        return str(value.numerator)
    return f"{float(value):.2f}"


def _round_half_up_mm(value: Fraction) -> int:
    """四舍五入到 1mm（仅用于踏面深度的展示取值）。"""
    decimal_value = Decimal(value.numerator) / Decimal(value.denominator)
    return int(decimal_value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _half_mm_number(value2: int):
    """以半毫米为最小单位的整数（2×毫米）转输出数值：偶数为 int，奇数为 x.5。"""
    if value2 % 2 == 0:
        return value2 // 2
    return value2 / 2


def _build_controlled_sequence(params: LayoutParams, steps: int,
                               control_points: list[LayoutControlPoint]):
    """按中间标高控制点分段生成逐级高度（半毫米网格）。

    以起点（0 级、0mm）、控制点（按级号升序）、层高终点（n 级、Hmm）为锚点分段。
    对级号 a→b、标高 ea→eb 的一段（段级数 m=b-a，段高差 Δ=eb-ea），
    段内第 j 级相对段起点的累计增量取 ceil_0.5(j·Δ/m)（按半毫米向上取整）：
      - 段末 j=m 时恰为 Δ，控制点精确命中；
      - 段内任一累计值落在 [理想直线, 理想直线+0.5mm)，线性误差不超过 0.5mm。
    返回 (sequence, cumulative, hits, max_diff)：sequence/cumulative 元素为数值
    （int 或 x.5），hits 为每个控制点的 {step, requested_mm, hit_mm, error_mm}，
    max_diff 为逐级最大高差（数值，可为 0.5 的倍数）。
    任一级高度越出踏步高度闭区间时抛 ControlPointError（定位到段末控制点）。
    """
    anchors: list[tuple[int, int]] = [(0, 0)]
    anchors += [(cp.step, cp.cumulative_mm) for cp in control_points]
    anchors.append((steps, params.floor_height_mm))

    cumulative2: list[int] = []  # 各级累计标高，单位为半毫米（2×mm）
    for seg in range(len(anchors) - 1):
        a_step, a_elev = anchors[seg]
        b_step, b_elev = anchors[seg + 1]
        seg_steps = b_step - a_step
        delta = b_elev - a_elev  # 严格递增校验后必为正整数
        for j in range(1, seg_steps + 1):
            # ceil_0.5(j·Δ/m)：在半毫米单位上即 ceil(2·j·Δ/m)
            ideal2 = (2 * j * delta + seg_steps - 1) // seg_steps
            cumulative2.append(2 * a_elev + ideal2)

    sequence2: list[int] = []
    prev2 = 0
    for total2 in cumulative2:
        sequence2.append(total2 - prev2)
        prev2 = total2

    # 单级高度闭区间校验：越界定位到该级所属段的段末控制点
    # （最后一段的段末为层高终点，改定位到该段起点处的最后一个控制点）。
    for level, h2 in enumerate(sequence2, start=1):
        height = Fraction(h2, 2)
        if height < params.riser_min_mm or height > params.riser_max_mm:
            cp_index = _anchor_index_for_level(control_points, level)
            cp = control_points[cp_index]
            if height < params.riser_min_mm:
                direction = f"低于踏步高度下限 {params.riser_min_mm}mm"
            else:
                direction = f"高于踏步高度上限 {params.riser_max_mm}mm"
            raise ControlPointError(
                cp_index,
                "cumulative_mm",
                f"控制点（第 {cp.step} 级累计标高 {cp.cumulative_mm}mm）使第 {level} 级"
                f"计算高度 {_half_mm_number(h2)}mm{direction}，请调整该控制点标高",
            )

    sequence = [_half_mm_number(v) for v in sequence2]
    cumulative = [_half_mm_number(v) for v in cumulative2]
    hits = [
        {
            "step": cp.step,
            "requested_mm": cp.cumulative_mm,
            # 段末锚点 j=m 时取整恰为 2·Δ，故命中值与录入值完全一致
            "hit_mm": _half_mm_number(cumulative2[cp.step - 1]),
            "error_mm": _half_mm_number(cumulative2[cp.step - 1] - 2 * cp.cumulative_mm),
        }
        for cp in control_points
    ]
    max_diff = _half_mm_number(max(sequence2) - min(sequence2))
    return sequence, cumulative, hits, max_diff


def _anchor_index_for_level(control_points: list[LayoutControlPoint],
                            level: int) -> int:
    """越界级所属段的控制点下标：段末为控制点时取该点，最后一段取最后一个控制点。"""
    for index, cp in enumerate(control_points):
        if level <= cp.step:
            return index
    return len(control_points) - 1


def _validate_control_points(control_points, steps: int,
                             floor_height_mm: int) -> list[LayoutControlPoint]:
    """控制点结构校验：级号位于首末级之间且级号、标高相对首末锚点严格递增。"""
    if control_points is None:
        return []
    normalized: list[LayoutControlPoint] = []
    for index, cp in enumerate(control_points):
        step, elev = cp.step, cp.cumulative_mm
        if not isinstance(step, int) or isinstance(step, bool):
            raise ControlPointError(index, "step", "控制点级号必须为整数")
        if not (1 <= step <= steps - 1):
            raise ControlPointError(
                index, "step",
                f"控制点级号 {step} 必须位于首末级之间（1–{steps - 1} 级）",
            )
        if not isinstance(elev, int) or isinstance(elev, bool):
            raise ControlPointError(index, "cumulative_mm", "控制点累计标高必须为整数（毫米）")
        if not (0 < elev < floor_height_mm):
            raise ControlPointError(
                index, "cumulative_mm",
                f"控制点累计标高 {elev}mm 必须位于起点 0mm 与层高终点 "
                f"{floor_height_mm}mm 之间",
            )
        normalized.append(LayoutControlPoint(step=step, cumulative_mm=elev))

    # 按提交顺序校验：级号、标高均须严格递增（下标与请求列表逐行对应，便于字段定位）
    for index in range(1, len(normalized)):
        cp = normalized[index]
        prev = normalized[index - 1]
        if cp.step <= prev.step:
            raise ControlPointError(
                index, "step",
                f"控制点级号必须严格递增且不重复：第 {index + 1} 行级号 {cp.step} "
                f"不大于前一行的 {prev.step}",
            )
        if cp.cumulative_mm <= prev.cumulative_mm:
            raise ControlPointError(
                index, "cumulative_mm",
                f"控制点累计标高必须严格递增：第 {cp.step} 级的 {cp.cumulative_mm}mm "
                f"不大于第 {prev.step} 级的 {prev.cumulative_mm}mm",
            )
    return normalized


def _build_solution(params: LayoutParams, steps: int, riser: Fraction,
                    deviation: Fraction, run: Fraction,
                    control_points: list[LayoutControlPoint] | None = None) -> dict:
    """按指定踏步数生成逐级高度、累计标高与踏面取值。

    无控制点时按余数前置序列生成；携带控制点时改按控制点分段（半毫米网格）生成，
    并附加受控标识与各控制点命中值。
    """
    hits: list[dict] = []
    if control_points:
        sequence, cumulative, hits, max_diff = _build_controlled_sequence(
            params, steps, control_points
        )
        total = cumulative[-1]
    else:
        # 放样序列：商为基础，余数从第一级起每级 +1mm
        quotient, remainder = divmod(params.floor_height_mm, steps)
        sequence = [quotient + 1 if i < remainder else quotient for i in range(steps)]
        cumulative = []
        total = 0
        for step_height in sequence:
            total += step_height
            cumulative.append(total)
        max_diff = max(sequence) - min(sequence)

    exact_tread = run / (steps - 1)
    solution = {
        "steps": steps,
        "treads": steps - 1,
        "exact_riser_mm": float(riser),
        "target_riser_mm": params.target_riser_mm,
        "deviation_mm": float(deviation),
        "riser_sequence_mm": sequence,
        "cumulative_height_mm": cumulative,
        "total_height_mm": total,
        "max_riser_diff_mm": max_diff,
        "exact_tread_mm": float(exact_tread),
        "tread_display_mm": _round_half_up_mm(exact_tread),
        "run_length_mm": params.run_length_mm,
    }
    if control_points:
        solution["controlled"] = True
        solution["control_points"] = hits
    return solution


def compute_layout(params: LayoutParams, selected_steps: int | None = None,
                   control_points: list[LayoutControlPoint] | None = None) -> dict:
    """计算放样方案。

    返回 {"status": "ok"|"no_solution", "solution": ..., "candidates": [...],
    "recommended_steps": int|None, "selection_source": "auto"|"manual"|None}。
    受控成功时 solution 另含 controlled=True 与 control_points 命中信息。
    candidates 覆盖 2..40 全部踏步数，含每个候选的可行性结论、推荐/选中标记与淘汰原因。

    始终先按目标偏差排序得出原推荐项；若传入 selected_steps，再校验其确属当前
    输入的可行候选，并以该踏步数生成放样序列。越界或不可行时抛 InvalidSelectionError。
    若传入 control_points，则在确定踏步数后校验其级号/标高结构，再按控制点分段
    重新生成逐级表；结构非法或受控单级高度越出闭区间时抛 ControlPointError。
    """
    height = Fraction(params.floor_height_mm)
    run = Fraction(params.run_length_mm)
    target = Fraction(params.target_riser_mm)

    candidates: list[dict] = []
    feasible: list[tuple[int, Fraction, dict]] = []
    for steps in range(MIN_STEPS, MAX_STEPS + 1):
        treads = steps - 1  # 踏面数固定为踏步数减一
        riser = height / steps
        tread = run / treads
        reasons: list[str] = []
        # 闭区间：边界值有效
        if riser < params.riser_min_mm:
            reasons.append(f"精确踏步高度 {_fmt(riser)}mm 低于下限 {params.riser_min_mm}mm")
        if riser > params.riser_max_mm:
            reasons.append(f"精确踏步高度 {_fmt(riser)}mm 高于上限 {params.riser_max_mm}mm")
        if tread < params.tread_min_mm:
            reasons.append(f"精确踏面深度 {_fmt(tread)}mm 低于下限 {params.tread_min_mm}mm")
        if tread > params.tread_max_mm:
            reasons.append(f"精确踏面深度 {_fmt(tread)}mm 高于上限 {params.tread_max_mm}mm")
        entry = {
            "steps": steps,
            "treads": treads,
            "exact_riser_mm": float(riser),
            "exact_tread_mm": float(tread),
            "deviation_mm": float(abs(riser - target)),
            "feasible": not reasons,
            "recommended": False,
            "selected": False,
            "reasons": reasons,
        }
        candidates.append(entry)
        if not reasons:
            feasible.append((steps, riser, entry))

    if not feasible:
        if selected_steps is not None:
            raise InvalidSelectionError(
                f"指定的 {selected_steps} 级踏步不可采用：当前输入在 "
                f"{MIN_STEPS}–{MAX_STEPS} 级范围内无可行候选"
            )
        return {
            "status": "no_solution",
            "solution": None,
            "candidates": candidates,
            "recommended_steps": None,
            "selection_source": None,
        }

    # 推荐项不变：先按 |精确踏步高度 - 目标值| 最小选择，平局取踏步数较小者（精确分数比较）
    best_steps, best_riser, best_entry = min(
        feasible, key=lambda item: (abs(item[1] - target), item[0])
    )
    best_deviation = abs(best_riser - target)
    best_entry["recommended"] = True

    # 可行但未获推荐者，给出相对推荐项的排序说明（与是否人工改选无关）
    for steps, riser, entry in feasible:
        if steps == best_steps:
            continue
        deviation = abs(riser - target)
        if deviation > best_deviation:
            entry["reasons"] = [
                f"可行，但与目标偏差 {_fmt(deviation)}mm 大于推荐的 "
                f"{best_steps} 级方案（偏差 {_fmt(best_deviation)}mm）"
            ]
        else:
            entry["reasons"] = [
                f"与目标偏差相同（{_fmt(deviation)}mm），但踏步数多于推荐的 {best_steps} 级方案"
            ]

    # 推荐项先得出后，再校验人工选用值
    if selected_steps is None:
        chosen_steps = best_steps
        source = SOURCE_AUTO
    else:
        chosen_steps = _validate_selected(selected_steps, candidates)
        source = SOURCE_MANUAL

    chosen_entry = next(entry for _, _, entry in feasible if entry["steps"] == chosen_steps)
    chosen_entry["selected"] = True
    chosen_riser = next(riser for steps, riser, _ in feasible if steps == chosen_steps)
    chosen_deviation = abs(chosen_riser - target)

    # 控制点在踏步数确定后校验：级号必须位于该方案首末级之间，标高严格递增
    normalized_points = _validate_control_points(
        control_points, chosen_steps, params.floor_height_mm
    )

    solution = _build_solution(
        params, chosen_steps, chosen_riser, chosen_deviation, run, normalized_points
    )
    return {
        "status": "ok",
        "solution": solution,
        "candidates": candidates,
        "recommended_steps": best_steps,
        "selection_source": source,
    }


def _validate_selected(selected_steps: int, candidates: list[dict]) -> int:
    """校验人工踏步数：必须是 2..40 内的整数且属于当前输入的可行候选。"""
    if not isinstance(selected_steps, int) or isinstance(selected_steps, bool):
        raise InvalidSelectionError(
            f"踏步数必须为 {MIN_STEPS}–{MAX_STEPS} 之间的整数"
        )
    if not (MIN_STEPS <= selected_steps <= MAX_STEPS):
        raise InvalidSelectionError(
            f"踏步数 {selected_steps} 超出允许范围 {MIN_STEPS}–{MAX_STEPS} 级"
        )
    entry = next(c for c in candidates if c["steps"] == selected_steps)
    if not entry["feasible"]:
        reason_text = "；".join(entry["reasons"])
        suffix = f"：{reason_text}" if reason_text else ""
        raise InvalidSelectionError(
            f"{selected_steps} 级踏步不是当前输入的可行候选{suffix}"
        )
    return selected_steps
