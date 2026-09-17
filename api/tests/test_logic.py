"""核心放样逻辑的单元测试（仅依赖标准库）。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.logic import (
    MAX_STEPS,
    MIN_STEPS,
    InvalidControlPointError,
    InvalidSelectionError,
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


def test_no_control_points_response_identical_to_before():
    # 不带控制点（含显式空列表）：序列与无控制点方案完全一致，且不出现受控字段
    plain = compute_layout(make())
    empty = compute_layout(make(), control_points=[])
    assert empty == plain
    sol = plain["solution"]
    assert "controlled" not in sol and "control_points" not in sol
    assert sol["riser_sequence_mm"] == [177] * 8 + [176] * 9


def test_two_control_points_recommended_layout_hit_exactly():
    # 自动推荐 17 级方案 + 双控制点（6 级 1057mm、12 级 2117mm）
    result = compute_layout(
        make(),
        control_points=[{"step": 6, "elevation_mm": 1057},
                        {"step": 12, "elevation_mm": 2117}],
    )
    assert result["status"] == "ok"
    assert result["recommended_steps"] == 17
    assert result["selection_source"] == "auto"
    sol = result["solution"]
    # 推荐、选用与候选标记不因控制点改变
    by_steps = {c["steps"]: c for c in result["candidates"]}
    assert by_steps[17]["recommended"] and by_steps[17]["selected"]
    assert sol["controlled"] is True
    # 控制点精确命中，层高终点精确闭合
    assert sol["cumulative_height_mm"][5] == 1057
    assert sol["cumulative_height_mm"][11] == 2117
    assert sol["cumulative_height_mm"][-1] == 3000
    assert sol["total_height_mm"] == 3000
    assert sol["control_points"] == [
        {"step": 6, "elevation_mm": 1057},
        {"step": 12, "elevation_mm": 2117},
    ]
    # 结果按级号唯一确定：与分段公式独立算出的逐级高度完全一致
    assert sol["riser_sequence_mm"] == [
        176.5, 176, 176, 176.5, 176, 176,      # 起点 → 6 级 1057
        177, 176.5, 176.5, 177, 176.5, 176.5,  # 6 级 → 12 级 2117
        177, 176.5, 176.5, 176.5, 176.5,       # 12 级 → 17 级 3000
    ]
    # 每级高度均在原闭区间内
    assert all(150 <= h <= 190 for h in sol["riser_sequence_mm"])
    # 段内累计值相对理想直线误差不超过 0.5mm（按半毫米向上取整）
    anchors = [(0, 0), (6, 1057), (12, 2117), (17, 3000)]
    for (a, ae), (b, be) in zip(anchors, anchors[1:]):
        for j in range(1, b - a + 1):
            ideal = ae + (be - ae) * j / (b - a)
            actual = sol["cumulative_height_mm"][a + j - 1]
            assert abs(actual - ideal) <= 0.5 + 1e-9


def test_half_mm_rounding_marks_and_sums():
    # 18 级方案、9 级控制点 1500mm：各段出现 166.5/167mm，总和仍精确闭合
    result = compute_layout(
        make(), selected_steps=18,
        control_points=[{"step": 9, "elevation_mm": 1500}],
    )
    sol = result["solution"]
    assert result["selection_source"] == "manual"
    assert result["recommended_steps"] == 17
    assert sol["steps"] == 18
    assert sol["cumulative_height_mm"][8] == 1500
    assert sol["cumulative_height_mm"][-1] == 3000
    assert sum(sol["riser_sequence_mm"]) == 3000
    # 所有高度都是整毫米或半毫米
    assert all(abs(h * 2 - round(h * 2)) < 1e-9 for h in sol["riser_sequence_mm"])
    assert all(150 <= h <= 190 for h in sol["riser_sequence_mm"])


def test_manual_selection_control_points_keep_marks():
    # 人工选用 + 控制点：推荐标记仍在 17 级，选中在 18 级，受控标识随方案
    result = compute_layout(
        make(), selected_steps=18,
        control_points=[{"step": 9, "elevation_mm": 1500}],
    )
    by_steps = {c["steps"]: c for c in result["candidates"]}
    assert by_steps[17]["recommended"] is True and by_steps[17]["selected"] is False
    assert by_steps[18]["recommended"] is False and by_steps[18]["selected"] is True
    assert result["solution"]["controlled"] is True


def test_control_point_result_is_deterministic():
    # 同输入重复计算：逐级表按级号唯一确定，与调用次数无关
    cps = [{"step": 6, "elevation_mm": 1057}, {"step": 12, "elevation_mm": 2117}]
    r1 = compute_layout(make(), control_points=cps)
    r2 = compute_layout(make(), control_points=[dict(p) for p in cps])
    assert r1["solution"]["riser_sequence_mm"] == r2["solution"]["riser_sequence_mm"]
    assert r1["solution"]["cumulative_height_mm"] == r2["solution"]["cumulative_height_mm"]


@pytest.mark.parametrize("bad_step", [0, 17, 40, -1])
def test_control_point_step_outside_first_last_rejected(bad_step):
    with pytest.raises(InvalidControlPointError) as exc:
        compute_layout(make(), control_points=[{"step": bad_step, "elevation_mm": 1000}])
    assert exc.value.field == "step"
    assert exc.value.index == 0
    assert str(bad_step) in exc.value.message


def test_control_points_must_be_strictly_increasing_in_step():
    with pytest.raises(InvalidControlPointError) as exc:
        compute_layout(
            make(),
            control_points=[{"step": 10, "elevation_mm": 1700},
                            {"step": 10, "elevation_mm": 1800}],
        )
    assert exc.value.loc == (1, "step")


def test_control_points_must_be_strictly_increasing_in_elevation():
    with pytest.raises(InvalidControlPointError) as exc:
        compute_layout(
            make(),
            control_points=[{"step": 6, "elevation_mm": 1057},
                            {"step": 12, "elevation_mm": 1057}],
        )
    assert exc.value.loc == (1, "elevation_mm")


@pytest.mark.parametrize("bad_elevation", [0, 3000, 3100])
def test_control_point_elevation_outside_range_rejected(bad_elevation):
    with pytest.raises(InvalidControlPointError) as exc:
        compute_layout(make(), control_points=[{"step": 6, "elevation_mm": bad_elevation}])
    assert exc.value.field == "elevation_mm"
    assert str(bad_elevation) in exc.value.message


def test_control_point_causing_riser_below_minimum_rejected_with_owner():
    # 区间收紧为 [176,177]：6 级 1000mm 使第 1 段首段均高约 166.7，越下限
    params = make(riser_min_mm=176, riser_max_mm=177, target_riser_mm=176)
    with pytest.raises(InvalidControlPointError) as exc:
        compute_layout(params, control_points=[{"step": 6, "elevation_mm": 1000}])
    err = exc.value
    assert err.loc == (0, "elevation_mm")
    assert "控制点 6" in err.message
    assert "167" in err.message and "低于下限" in err.message
    assert "176" in err.message and "177" in err.message


def test_control_point_causing_riser_above_maximum_rejected_with_owner():
    # 双控制点中第二个点（12 级 2200mm）使其前段（7–12 级）均高约 190.5，越上限；
    # 错误必须定位到第 2 个控制点（下标 1），并给出越界级与计算高度
    with pytest.raises(InvalidControlPointError) as exc:
        compute_layout(
            make(),
            control_points=[{"step": 6, "elevation_mm": 1057},
                            {"step": 12, "elevation_mm": 2200}],
        )
    err = exc.value
    assert err.loc == (1, "elevation_mm")
    assert "控制点 12" in err.message
    assert "第 7 级" in err.message and "190.5" in err.message
    assert "高于上限" in err.message and "190" in err.message


def test_last_segment_violation_attributed_to_last_control_point():
    # 末段（12 级之后）过低：12 级 2950mm，剩余 50mm/5 级 = 10mm，越下限
    with pytest.raises(InvalidControlPointError) as exc:
        compute_layout(
            make(),
            control_points=[{"step": 6, "elevation_mm": 1057},
                            {"step": 12, "elevation_mm": 2950}],
        )
    assert exc.value.loc == (1, "elevation_mm")
    assert "控制点 12" in exc.value.message


def test_control_points_with_no_solution_keep_no_solution():
    # 无解行为保持现状：即使携带控制点也返回 no_solution
    result = compute_layout(
        make(riser_min_mm=170, riser_max_mm=172),
        control_points=[{"step": 6, "elevation_mm": 1000}],
    )
    assert result["status"] == "no_solution"
    assert result["solution"] is None
