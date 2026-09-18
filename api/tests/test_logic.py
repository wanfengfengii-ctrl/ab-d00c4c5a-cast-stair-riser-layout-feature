"""核心放样逻辑的单元测试（仅依赖标准库）。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.logic import (
    MAX_STEPS,
    MIN_STEPS,
    ControlPointError,
    InvalidSelectionError,
    LayoutControlPoint,
    LayoutParams,
    compute_layout,
)


def make(**overrides) -> LayoutParams:
    base = dict(
        floor_height_mm=3000,
        run_length_mm=4800,
        riser_min_mm=150,
        riser_max_mm=190,
        tread_min_mm=250,
        tread_max_mm=320,
        target_riser_mm=175,
    )
    base.update(overrides)
    return LayoutParams(**base)


def candidate(result, steps):
    return next(c for c in result["candidates"] if c["steps"] == steps)


def test_main_flow_selects_closest_to_target():
    result = compute_layout(make())
    assert result["status"] == "ok"
    sol = result["solution"]
    # 3000/17≈176.47 距目标 175 最近
    assert sol["steps"] == 17
    assert sol["treads"] == 16
    assert abs(sol["exact_riser_mm"] - 3000 / 17) < 1e-9


def test_riser_sequence_remainder_distributed_from_first_step():
    result = compute_layout(make())
    seq = result["solution"]["riser_sequence_mm"]
    # 3000 = 17*176 + 8：前 8 级 177，其余 176
    assert seq == [177] * 8 + [176] * 9
    assert sum(seq) == 3000
    assert max(seq) - min(seq) <= 1
    assert result["solution"]["max_riser_diff_mm"] == 1
    assert result["solution"]["cumulative_height_mm"][-1] == 3000
    assert result["solution"]["total_height_mm"] == 3000


def test_sequence_sum_invariant_across_range():
    # 任意可行输入下，序列总和必须等于层高且级差不超过 1mm
    for floor in (2000, 2520, 3001, 3333, 4800, 6000):
        result = compute_layout(make(floor_height_mm=floor, riser_min_mm=100, riser_max_mm=400,
                                     tread_min_mm=100, tread_max_mm=1000, target_riser_mm=175))
        assert result["status"] == "ok"
        seq = result["solution"]["riser_sequence_mm"]
        assert sum(seq) == floor
        assert max(seq) - min(seq) <= 1
        assert len(seq) == result["solution"]["steps"]


def test_boundary_values_are_valid():
    # 闭区间：精确踏步高度恰为边界时必须可行
    result = compute_layout(make(riser_min_mm=150, riser_max_mm=150, target_riser_mm=150))
    assert result["status"] == "ok"
    assert result["solution"]["steps"] == 20  # 3000/20 = 150 恰好等于边界
    assert result["solution"]["riser_sequence_mm"] == [150] * 20
    # 踏面深度边界同样有效：4800/15 = 320 恰为上限
    result2 = compute_layout(make())
    assert candidate(result2, 16)["feasible"] is True
    assert candidate(result2, 16)["exact_tread_mm"] == 320.0


def test_just_outside_boundary_is_rejected():
    result = compute_layout(make())
    # 3000/15 = 200 > 190；3000/19 ≈ 157.89 可行；3000/21 ≈ 142.86 < 150
    assert candidate(result, 15)["feasible"] is False
    assert any("高于上限" in r for r in candidate(result, 15)["reasons"])
    assert candidate(result, 21)["feasible"] is False
    assert any("低于下限" in r for r in candidate(result, 21)["reasons"])


def test_tie_break_prefers_fewer_steps():
    # 2520/9=280 与 2520/10=252 距目标 266 的偏差都是 14mm，应选踏步数较小的 9 级
    result = compute_layout(make(floor_height_mm=2520, run_length_mm=5000,
                                 riser_min_mm=200, riser_max_mm=300,
                                 tread_min_mm=100, tread_max_mm=1000,
                                 target_riser_mm=266))
    assert result["status"] == "ok"
    assert result["solution"]["steps"] == 9
    ten = candidate(result, 10)
    assert ten["feasible"] is True
    assert ten["selected"] is False
    assert any("踏步数多于" in r for r in ten["reasons"])


def test_no_solution():
    # 3000/17≈176.47 与 3000/18≈166.67 之间没有落在 [170,172] 的取值
    result = compute_layout(make(riser_min_mm=170, riser_max_mm=172))
    assert result["status"] == "no_solution"
    assert result["solution"] is None
    assert all(not c["feasible"] for c in result["candidates"])


def test_candidates_cover_full_range():
    result = compute_layout(make())
    assert [c["steps"] for c in result["candidates"]] == list(range(MIN_STEPS, MAX_STEPS + 1))
    assert all(c["treads"] == c["steps"] - 1 for c in result["candidates"])
    selected = [c for c in result["candidates"] if c["selected"]]
    assert len(selected) == 1
    assert selected[0]["steps"] == result["solution"]["steps"]
    # 未选中的候选都必须给出淘汰原因
    for c in result["candidates"]:
        if not c["selected"]:
            assert c["reasons"], f"候选 {c['steps']} 缺少淘汰原因"


def test_tread_display_rounds_half_up():
    # 4808/16 = 300.5 → 四舍五入显示为 301
    result = compute_layout(make(run_length_mm=4808))
    sol = result["solution"]
    assert sol["treads"] == 16
    assert abs(sol["exact_tread_mm"] - 300.5) < 1e-9
    assert sol["tread_display_mm"] == 301
    # 4807/16 = 300.4375 → 300
    result2 = compute_layout(make(run_length_mm=4807))
    assert result2["solution"]["tread_display_mm"] == 300


def test_feasibility_uses_unrounded_tread():
    # 17 级踏步 → 16 个踏面，3994/16 = 249.625：四舍五入为 250，
    # 但未舍入值低于下限 250，必须判不可行
    result = compute_layout(make(run_length_mm=3994))
    c17 = candidate(result, 17)
    assert abs(c17["exact_tread_mm"] - 249.625) < 1e-9
    assert c17["feasible"] is False
    assert any("踏面深度" in r and "低于下限" in r for r in c17["reasons"])


def test_feasible_but_not_selected_has_reason():
    result = compute_layout(make())
    c18 = candidate(result, 18)
    assert c18["feasible"] is True
    assert c18["selected"] is False
    assert any("偏差" in r for r in c18["reasons"])


def test_huge_integer_computed_exactly():
    # 2^53 + 1：Python 任意精度整数必须按原值精确计算，不得静默舍入
    huge = 9007199254740993
    result = compute_layout(make(floor_height_mm=huge, riser_min_mm=1,
                                 riser_max_mm=10**18, tread_min_mm=1,
                                 tread_max_mm=10**9, target_riser_mm=1))
    assert result["status"] == "ok"
    sol = result["solution"]
    assert sol["steps"] == 40  # 目标 1mm → 精确高度最小的 40 级最接近
    assert sum(sol["riser_sequence_mm"]) == huge
    assert sol["total_height_mm"] == huge
    assert max(sol["riser_sequence_mm"]) - min(sol["riser_sequence_mm"]) <= 1


def test_default_response_carries_recommendation_and_auto_source():
    # 未携带 selected_steps：原排序结果，补充推荐踏步数与选用来源
    result = compute_layout(make())
    assert result["recommended_steps"] == 17
    assert result["selection_source"] == "auto"
    assert result["solution"]["steps"] == 17
    rec = [c for c in result["candidates"] if c["recommended"]]
    assert [c["steps"] for c in rec] == [17]
    selected = [c for c in result["candidates"] if c["selected"]]
    assert [c["steps"] for c in selected] == [17]


def test_no_solution_has_null_recommendation_fields():
    result = compute_layout(make(riser_min_mm=170, riser_max_mm=172))
    assert result["status"] == "no_solution"
    assert result["recommended_steps"] is None
    assert result["selection_source"] is None
    assert all(not c["recommended"] and not c["selected"] for c in result["candidates"])


def test_manual_selection_uses_chosen_feasible_steps_and_keeps_recommendation():
    # 推荐 17 级；人工改选同为可行候选的 18 级
    result = compute_layout(make(), selected_steps=18)
    assert result["status"] == "ok"
    assert result["recommended_steps"] == 17       # 推荐项标识保留
    assert result["selection_source"] == "manual"
    sol = result["solution"]
    assert sol["steps"] == 18
    assert sol["treads"] == 17
    # 3000 = 18*166 + 12：前 12 级 167，其余 166
    assert sol["riser_sequence_mm"] == [167] * 12 + [166] * 6
    assert sol["cumulative_height_mm"][-1] == 3000
    assert sol["total_height_mm"] == 3000
    assert sol["max_riser_diff_mm"] == 1
    # 4800/17 ≈ 282.35 → 282
    assert abs(sol["exact_tread_mm"] - 4800 / 17) < 1e-9
    assert sol["tread_display_mm"] == 282
    # 推荐标记仍在 17 级，选中标记移到 18 级
    by_steps = {c["steps"]: c for c in result["candidates"]}
    assert by_steps[17]["recommended"] is True and by_steps[17]["selected"] is False
    assert by_steps[18]["recommended"] is False and by_steps[18]["selected"] is True


def test_manual_selection_of_recommended_is_still_manual_layout():
    result = compute_layout(make(), selected_steps=17)
    assert result["recommended_steps"] == 17
    assert result["selection_source"] == "manual"
    assert result["solution"]["steps"] == 17


@pytest.mark.parametrize("bad_steps", [1, 41, 0, -3, 100])
def test_out_of_range_selection_rejected(bad_steps):
    with pytest.raises(InvalidSelectionError) as exc:
        compute_layout(make(), selected_steps=bad_steps)
    assert str(bad_steps) in str(exc.value)


def test_infeasible_selection_rejected_with_constraint_reason():
    # 21 级：3000/21 ≈ 142.86 < 150，已被高度约束淘汰
    with pytest.raises(InvalidSelectionError) as exc:
        compute_layout(make(), selected_steps=21)
    message = str(exc.value)
    assert "21" in message and "可行候选" in message and "低于下限" in message


def test_selection_rejected_when_no_solution():
    with pytest.raises(InvalidSelectionError):
        compute_layout(make(riser_min_mm=170, riser_max_mm=172), selected_steps=17)


# ---------------------------------------------------------------------------
# 中间标高控制点
# ---------------------------------------------------------------------------

def cp(step, elev):
    return LayoutControlPoint(step=step, cumulative_mm=elev)


def test_legacy_response_identical_without_control_points():
    """旧请求不带控制点：逐级序列与响应结构完全不变。"""
    result = compute_layout(make())
    sol = result["solution"]
    assert "controlled" not in sol
    assert "control_points" not in sol
    assert sol["riser_sequence_mm"] == [177] * 8 + [176] * 9
    assert sol["cumulative_height_mm"] == _legacy_cumulative(17, [177] * 8 + [176] * 9)


def _legacy_cumulative(steps, seq):
    out, total = [], 0
    for h in seq:
        total += h
        out.append(total)
    return out


def test_auto_recommendation_with_two_control_points():
    """推荐方案（17 级）+ 双控制点：分段取整、精确命中、0.5mm 线性误差界限。"""
    points = [cp(5, 900), cp(10, 1750)]
    result = compute_layout(make(), control_points=points)
    assert result["status"] == "ok"
    assert result["selection_source"] == "auto"
    assert result["recommended_steps"] == 17
    sol = result["solution"]
    assert sol["controlled"] is True
    assert sol["steps"] == 17
    seq, cumulative = sol["riser_sequence_mm"], sol["cumulative_height_mm"]
    # 控制点与层高终点精确命中
    assert cumulative[4] == 900
    assert cumulative[9] == 1750
    assert cumulative[-1] == 3000
    assert sol["total_height_mm"] == 3000
    # 命中信息
    assert sol["control_points"] == [
        {"step": 5, "requested_mm": 900, "hit_mm": 900, "error_mm": 0},
        {"step": 10, "requested_mm": 1750, "hit_mm": 1750, "error_mm": 0},
    ]
    # 每级高度落入原高度闭区间 [150,190]
    assert all(150 <= h <= 190 for h in seq)
    # 段内累计相对理想直线的误差位于 [0, 0.5mm)
    anchors = [(0, 0), (5, 900), (10, 1750), (17, 3000)]
    for (a, ea), (b, eb) in zip(anchors, anchors[1:]):
        m, delta = b - a, eb - ea
        for j in range(1, m + 1):
            observed = cumulative[a + j - 1] - ea
            ideal = j * delta / m
            assert 0 <= observed - ideal < 0.5, (a, j, observed, ideal)


def test_manual_selection_with_control_point_hits_exactly():
    """人工方案应用控制点后精确命中。"""
    result = compute_layout(make(), selected_steps=18,
                            control_points=[cp(9, 1500)])
    assert result["selection_source"] == "manual"
    assert result["recommended_steps"] == 17  # 推荐标识保留
    sol = result["solution"]
    assert sol["controlled"] is True
    assert sol["steps"] == 18
    assert sol["cumulative_height_mm"][8] == 1500
    assert sol["cumulative_height_mm"][-1] == 3000
    assert sol["control_points"][0]["hit_mm"] == 1500
    assert sol["control_points"][0]["error_mm"] == 0
    by_steps = {c["steps"]: c for c in result["candidates"]}
    assert by_steps[17]["recommended"] is True
    assert by_steps[18]["selected"] is True


def test_control_point_can_rewrite_remainder_prefix_sequence():
    """受控序列由控制点唯一确定，可与原余数前置序列不同（半毫米网格）。"""
    # 单控制点：第 8 级命中 1400；原余数序列第 8 级为 8*177=1416
    result = compute_layout(make(), control_points=[cp(8, 1400)])
    sol = result["solution"]
    assert sol["cumulative_height_mm"][7] == 1400
    assert sol["cumulative_height_mm"][-1] == 3000
    # 后段 1600/9 ≈ 177.78 → 半毫米向上取整出现 x.5 值
    assert any(isinstance(h, float) for h in sol["riser_sequence_mm"])
    assert all(150 <= h <= 190 for h in sol["riser_sequence_mm"])


def test_result_unique_for_same_control_points():
    """同一组控制点结果按级号唯一确定：重复请求序列一致。"""
    points = [cp(3, 540), cp(12, 2110)]
    first = compute_layout(make(), control_points=points)["solution"]
    second = compute_layout(make(), control_points=points)["solution"]
    assert first["riser_sequence_mm"] == second["riser_sequence_mm"]
    assert first["cumulative_height_mm"] == second["cumulative_height_mm"]


def test_empty_control_point_list_is_legacy():
    sol = compute_layout(make(), control_points=[])["solution"]
    assert "controlled" not in sol
    assert sol["riser_sequence_mm"] == [177] * 8 + [176] * 9


def test_out_of_bounds_riser_under_control_points_raises_localized():
    """控制点导致单级高度低于下限：错误定位到具体控制点，含越界级与计算高度。"""
    with pytest.raises(ControlPointError) as exc:
        # 17 级、第 1 级累计 140 → 第 1 级高度 140 < 150
        compute_layout(make(), control_points=[cp(1, 140)])
    e = exc.value
    assert e.index == 0 and e.field == "cumulative_mm"
    assert "第 1 级" in e.message and "140mm" in e.message and "150mm" in e.message


def test_out_of_bounds_riser_in_middle_segment_localized_to_anchor():
    """中间段越界定位到段末控制点；消息含越界级与计算高度。"""
    with pytest.raises(ControlPointError) as exc:
        # 两个控制点：第 5 级 900（段均 180 可行），第 10 级仅 1000
        # → 6..10 级段高差 100，每级 20mm < 150
        compute_layout(make(), control_points=[cp(5, 900), cp(10, 1000)])
    e = exc.value
    assert e.index == 1 and e.field == "cumulative_mm"
    assert "第 6 级" in e.message and "20mm" in e.message


def test_out_of_bounds_riser_in_last_segment_localized_to_last_cp():
    """最后一段（终点为层高）越界改定位到最后一个控制点。"""
    with pytest.raises(ControlPointError) as exc:
        # 17 级：第 16 级 2800（前段均 175 可行）→ 第 17 级高度 3000-2800=200 > 190
        compute_layout(make(), control_points=[cp(16, 2800)])
    e = exc.value
    assert e.index == 0 and e.field == "cumulative_mm"
    assert "第 17 级" in e.message and "200mm" in e.message


def test_control_point_step_must_lie_between_first_and_last():
    with pytest.raises(ControlPointError) as exc:
        compute_layout(make(), control_points=[cp(17, 2900)])  # 等于末级
    assert exc.value.field == "step" and exc.value.index == 0
    with pytest.raises(ControlPointError) as exc:
        compute_layout(make(), control_points=[cp(0, 100)])
    assert exc.value.field == "step"


def test_control_point_elev_must_lie_between_anchors():
    for bad_elev in (0, 3000, -5):
        with pytest.raises(ControlPointError) as exc:
            compute_layout(make(), control_points=[cp(5, bad_elev)])
        assert exc.value.field == "cumulative_mm" and exc.value.index == 0


def test_control_points_must_be_strictly_increasing_in_submission_order():
    with pytest.raises(ControlPointError) as exc:
        compute_layout(make(), control_points=[cp(10, 1750), cp(5, 900)])
    assert exc.value.index == 1 and exc.value.field == "step"
    with pytest.raises(ControlPointError) as exc:
        compute_layout(make(), control_points=[cp(5, 900), cp(6, 900)])
    assert exc.value.index == 1 and exc.value.field == "cumulative_mm"
    with pytest.raises(ControlPointError) as exc:
        compute_layout(make(), control_points=[cp(5, 900), cp(5, 950)])
    assert exc.value.field == "step"


def test_control_points_validated_against_chosen_steps():
    """控制点级号区间随选定踏步数：18 级方案允许第 17 级控制点。"""
    result = compute_layout(make(), selected_steps=18,
                            control_points=[cp(17, 2850)])
    assert result["solution"]["cumulative_height_mm"][16] == 2850


def test_selected_steps_checked_before_control_points():
    with pytest.raises(InvalidSelectionError):
        compute_layout(make(), selected_steps=99,
                       control_points=[cp(1, 140)])
