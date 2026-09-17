"""混凝土楼梯支模放样核心计算。

设计原则：
- 可行性判断与方案选择全部使用 Fraction 精确分数运算，不做任何舍入；
- 仅展示层的踏面深度按四舍五入（ROUND_HALF_UP）取整到 1mm；
- 放样序列以层高整除踏步数的商为基础，余数从第一级起每级 +1mm，
  保证总和等于层高且任意两级高度差不超过 1mm；
- 携带中间标高控制点时改按控制点与起点、层高终点分段：每段第 j 级累计
  增量取 j×段高差÷段级数并按 0.5mm 向上取整，控制点精确命中、段内累计值
  相对理想直线误差 < 0.5mm，且每级高度仍须落入原踏步高度闭区间；
- 推荐项始终由目标偏差排序得出；现场可在可行候选中人工改选，
  改选不改变推荐项本身（响应同时保留推荐踏步数与选用来源）。
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


class InvalidControlPointError(ValueError):
    """中间标高控制点非法（级号/标高不满足约束，或导致单级高度越界）。

    loc 为相对 body.control_points 的定位元组（控制点下标, 字段名），
    路由层据此组装可定位到具体控制点、具体字段的 422。
    """

    def __init__(self, index: int, field: str, message: str):
        self.index = index
        self.field = field
        self.loc = (index, field)
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


def _fmt(value: Fraction) -> str:
    """分数的简短文本：整数不带小数点，否则保留两位小数。"""
    if value.denominator == 1:
        return str(value.numerator)
    return f"{float(value):.2f}"


def _round_half_up_mm(value: Fraction) -> int:
    """四舍五入到 1mm（仅用于踏面深度的展示取值）。"""
    decimal_value = Decimal(value.numerator) / Decimal(value.denominator)
    return int(decimal_value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _half_to_mm(value_half: int):
    """0.5mm 单位的整数换算回毫米：整毫米返回 int，半毫米级返回 x.5 的 float。"""
    return value_half // 2 if value_half % 2 == 0 else value_half / 2


def _build_solution(params: LayoutParams, steps: int, riser: Fraction,
                    deviation: Fraction, run: Fraction,
                    control_points: list[tuple[int, int]] | None = None) -> dict:
    """按指定踏步数生成逐级高度、累计标高与踏面取值。

    无控制点时沿用余数前置序列（q+1 前 r 级，其余 q）；
    携带控制点时按控制点与起点（0 级 0mm）、层高终点（steps 级 H）分段，
    每段累计增量按 0.5mm 向上取整，使控制点精确命中。
    control_points 中的标高以 0.5mm 为单位（录入为整数毫米时即为 2×毫米值）。
    """
    if control_points:
        heights_half, cumulative_half = _controlled_sequence(
            2 * params.floor_height_mm, steps, control_points
        )
        # 半毫米单位换算回毫米：偶数为整数毫米，奇数带 0.5mm，JSON 中均可精确表示
        sequence = [_half_to_mm(h) for h in heights_half]
        cumulative = [_half_to_mm(c) for c in cumulative_half]
        total = params.floor_height_mm
        max_diff = _half_to_mm(max(heights_half) - min(heights_half))
        controlled = True
        cp_hits = [{"step": s, "elevation_mm": _half_to_mm(e)} for s, e in control_points]
    else:
        # 放样序列：商为基础，余数从第一级起每级 +1mm
        quotient, remainder = divmod(params.floor_height_mm, steps)
        sequence = [quotient + 1 if i < remainder else quotient for i in range(steps)]
        cumulative: list[int] = []
        running = 0
        for step_height in sequence:
            running += step_height
            cumulative.append(running)
        total = running
        max_diff = max(sequence) - min(sequence)
        controlled = False
        cp_hits = []

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
    if controlled:
        # 受控方案才出现新字段：不带控制点的旧请求响应保持完全不变
        solution["controlled"] = True
        solution["control_points"] = cp_hits
    return solution


def _controlled_sequence(total_half: int, steps: int,
                         control_points: list[tuple[int, int]]) -> tuple[list[int], list[int]]:
    """按控制点分段生成逐级高度与累计标高，均以 0.5mm 为单位的精确整数。

    锚点为起点 (0, 0)、各控制点、层高终点 (steps, 层高)；每段内第 j 级
    （j=1..段级数）的段内累计增量为 ceil(j×段高差÷段级数)，即按半毫米
    向上取整。段内累计值相对该段理想直线误差 < 0.5mm，段终点精确命中。
    """
    anchors = [(0, 0)] + control_points + [(steps, total_half)]
    heights: list[int] = []
    cumulative: list[int] = []
    for (a_step, a_elev), (b_step, b_elev) in zip(anchors, anchors[1:]):
        seg_steps = b_step - a_step
        seg_rise = b_elev - a_elev
        prev = a_elev
        for j in range(1, seg_steps + 1):
            cur = a_elev + -((-j * seg_rise) // seg_steps)  # ceil(j×段高差÷段级数)
            heights.append(cur - prev)
            cumulative.append(cur)
            prev = cur
    return heights, cumulative


def _validate_control_points(control_points: list[dict] | None, steps: int,
                             total_height: int, riser_min: int,
                             riser_max: int) -> list[tuple[int, int]]:
    """校验控制点并返回规范化的 (级号, 0.5mm 标高) 列表。

    约束：控制点位于首末级之间（级号 1..steps-1），级号与累计标高均严格
    递增，标高位于起点 0mm 与层高终点之间；按分段半毫米向上取整得到的每级
    高度还必须落入原踏步高度闭区间 [riser_min, riser_max]。
    任一违反都抛出定位到具体控制点下标与字段的 InvalidControlPointError。
    """
    if not control_points:
        return []
    normalized: list[tuple[int, int]] = []
    for index, point in enumerate(control_points):
        step = point["step"]
        elevation = point["elevation_mm"]
        if not isinstance(step, int) or isinstance(step, bool):
            raise InvalidControlPointError(index, "step", "控制点级号必须为整数")
        if not (1 <= step <= steps - 1):
            raise InvalidControlPointError(
                index, "step",
                f"控制点级号 {step} 必须位于首末级之间（1–{steps - 1}）",
            )
        if normalized and step <= normalized[-1][0]:
            raise InvalidControlPointError(
                index, "step",
                f"控制点级号 {step} 必须严格递增（上一控制点为 {normalized[-1][0]} 级）",
            )
        if not isinstance(elevation, int) or isinstance(elevation, bool):
            raise InvalidControlPointError(
                index, "elevation_mm", "控制点累计标高必须为整数毫米"
            )
        if not 0 < elevation < total_height:
            raise InvalidControlPointError(
                index, "elevation_mm",
                f"控制点累计标高 {elevation}mm 必须位于起点 0mm 与层高终点 "
                f"{total_height}mm 之间",
            )
        if normalized and elevation * 2 <= normalized[-1][1]:
            raise InvalidControlPointError(
                index, "elevation_mm",
                f"控制点累计标高 {elevation}mm 必须严格递增（上一控制点为 "
                f"{normalized[-1][1] // 2}mm）",
            )
        normalized.append((step, 2 * elevation))

    # 按算法算出每级高度（0.5mm 单位），再逐级做原高度闭区间校验
    heights_half, _ = _controlled_sequence(2 * total_height, steps, normalized)
    anchors = [(0, 0)] + normalized + [(steps, 2 * total_height)]
    offset = 0
    for (a_step, _), (b_step, _) in zip(anchors, anchors[1:]):
        seg_len = b_step - a_step
        # 越界归责段终点控制点：段终点为末级时归责最后一个控制点
        owner_index = (
            len(normalized) - 1 if b_step == steps
            else next(i for i, (s, _) in enumerate(normalized) if s == b_step)
        )
        owner_step, owner_elev_half = normalized[owner_index]
        for j, h in enumerate(heights_half[offset:offset + seg_len], start=1):
            if h < 2 * riser_min or h > 2 * riser_max:
                step_no = a_step + j
                bound = "低于下限" if h < 2 * riser_min else "高于上限"
                raise InvalidControlPointError(
                    owner_index, "elevation_mm",
                    f"控制点 {owner_step} 级（累计标高 {owner_elev_half // 2}mm）导致第 "
                    f"{step_no} 级计算高度 {h / 2:g}mm{bound}，不在踏步高度闭区间 "
                    f"[{riser_min}, {riser_max}]mm 内",
                )
        offset += seg_len
    return normalized


def compute_layout(params: LayoutParams, selected_steps: int | None = None,
                   control_points: list[dict] | None = None) -> dict:
    """计算放样方案。

    返回 {"status": "ok"|"no_solution", "solution": ..., "candidates": [...],
    "recommended_steps": int|None, "selection_source": "auto"|"manual"|None}。
    candidates 覆盖 2..40 全部踏步数，含每个候选的可行性结论、推荐/选中标记与淘汰原因。

    始终先按目标偏差排序得出原推荐项；若传入 selected_steps，再校验其确属当前
    输入的可行候选，并以该踏步数生成放样序列。越界或不可行时抛 InvalidSelectionError。
    control_points 为现场复测录入的中间标高控制点，仅在方案确定（自动推荐或
    人工选用）后参与逐级序列重算；非法或导致单级高度越界时抛 InvalidControlPointError。
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
        # 无解行为保持现状：即便携带控制点也返回 no_solution（现场无控制点录入入口）
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

    # 方案（自动推荐或人工选用）确定后，控制点才参与逐级序列重算；
    # 推荐、选用来源与候选标记均不因控制点改变
    normalized_points = _validate_control_points(
        control_points, chosen_steps, params.floor_height_mm,
        params.riser_min_mm, params.riser_max_mm,
    )

    solution = _build_solution(params, chosen_steps, chosen_riser, chosen_deviation,
                               run, normalized_points)
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
