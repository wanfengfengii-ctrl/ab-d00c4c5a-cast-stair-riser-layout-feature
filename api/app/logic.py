"""混凝土楼梯支模放样核心计算。

设计原则：
- 可行性判断与方案选择全部使用 Fraction 精确分数运算，不做任何舍入；
- 仅展示层的踏面深度按四舍五入（ROUND_HALF_UP）取整到 1mm；
- 放样序列以层高整除踏步数的商为基础，余数从第一级起每级 +1mm，
  保证总和等于层高且任意两级高度差不超过 1mm；
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


def _build_solution(params: LayoutParams, steps: int, riser: Fraction,
                    deviation: Fraction, run: Fraction) -> dict:
    """按指定踏步数生成逐级高度、累计标高与踏面取值。"""
    # 放样序列：商为基础，余数从第一级起每级 +1mm
    quotient, remainder = divmod(params.floor_height_mm, steps)
    sequence = [quotient + 1 if i < remainder else quotient for i in range(steps)]
    cumulative: list[int] = []
    total = 0
    for step_height in sequence:
        total += step_height
        cumulative.append(total)

    exact_tread = run / (steps - 1)
    return {
        "steps": steps,
        "treads": steps - 1,
        "exact_riser_mm": float(riser),
        "target_riser_mm": params.target_riser_mm,
        "deviation_mm": float(deviation),
        "riser_sequence_mm": sequence,
        "cumulative_height_mm": cumulative,
        "total_height_mm": total,
        "max_riser_diff_mm": max(sequence) - min(sequence),
        "exact_tread_mm": float(exact_tread),
        "tread_display_mm": _round_half_up_mm(exact_tread),
        "run_length_mm": params.run_length_mm,
    }


def compute_layout(params: LayoutParams, selected_steps: int | None = None) -> dict:
    """计算放样方案。

    返回 {"status": "ok"|"no_solution", "solution": ..., "candidates": [...],
    "recommended_steps": int|None, "selection_source": "auto"|"manual"|None}。
    candidates 覆盖 2..40 全部踏步数，含每个候选的可行性结论、推荐/选中标记与淘汰原因。

    始终先按目标偏差排序得出原推荐项；若传入 selected_steps，再校验其确属当前
    输入的可行候选，并以该踏步数生成放样序列。越界或不可行时抛 InvalidSelectionError。
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

    solution = _build_solution(params, chosen_steps, chosen_riser, chosen_deviation, run)
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
