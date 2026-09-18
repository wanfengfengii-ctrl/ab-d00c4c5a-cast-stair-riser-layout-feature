"""针对运行中 API 的验收测试（默认 http://api:8000，可用 API_BASE_URL 覆盖）。"""
import os

import pytest
import requests

API = os.environ.get("API_BASE_URL", "http://api:8000")

BASE = dict(
    floor_height_mm=3000,
    run_length_mm=4800,
    riser_min_mm=150,
    riser_max_mm=190,
    tread_min_mm=250,
    tread_max_mm=320,
    target_riser_mm=175,
)


def post(payload):
    return requests.post(f"{API}/api/layout", json=payload, timeout=10)


def test_health():
    r = requests.get(f"{API}/api/health", timeout=10)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_main_flow():
    r = post(BASE)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    # 兼容旧请求：默认得到自动推荐，补充推荐踏步数与选用来源
    assert data["recommended_steps"] == 17
    assert data["selection_source"] == "auto"
    sol = data["solution"]
    # 3000/17≈176.47 距目标 175 最近
    assert sol["steps"] == 17
    assert sol["treads"] == 16
    # 3000 = 17*176 + 8：前 8 级 177，其余 176
    assert sol["riser_sequence_mm"] == [177] * 8 + [176] * 9
    assert sol["total_height_mm"] == 3000
    assert sol["max_riser_diff_mm"] <= 1
    assert sol["tread_display_mm"] == 300
    # 候选覆盖 2..40，唯一选中，推荐项与选中一致，未选中者均有淘汰原因
    assert [c["steps"] for c in data["candidates"]] == list(range(2, 41))
    selected = [c for c in data["candidates"] if c["selected"]]
    assert len(selected) == 1 and selected[0]["steps"] == 17
    recommended = [c for c in data["candidates"] if c["recommended"]]
    assert len(recommended) == 1 and recommended[0]["steps"] == 17
    assert all(c["reasons"] for c in data["candidates"] if not c["selected"])


def test_boundary_values_valid():
    # 闭区间边界：3000/20 = 150 恰好等于上下限
    payload = {**BASE, "riser_min_mm": 150, "riser_max_mm": 150, "target_riser_mm": 150}
    data = post(payload).json()
    assert data["status"] == "ok"
    assert data["solution"]["steps"] == 20
    assert data["solution"]["riser_sequence_mm"] == [150] * 20


def test_tie_break_prefers_fewer_steps():
    # 2520/9=280 与 2520/10=252 距目标 266 偏差同为 14mm，取踏步数较小的 9 级
    payload = dict(
        floor_height_mm=2520, run_length_mm=5000,
        riser_min_mm=200, riser_max_mm=300,
        tread_min_mm=100, tread_max_mm=1000,
        target_riser_mm=266,
    )
    data = post(payload).json()
    assert data["status"] == "ok"
    assert data["solution"]["steps"] == 9


def test_tread_rounding_half_up_display_only():
    # 4808/16 = 300.5 → 显示 301
    data = post({**BASE, "run_length_mm": 4808}).json()
    sol = data["solution"]
    assert abs(sol["exact_tread_mm"] - 300.5) < 1e-9
    assert sol["tread_display_mm"] == 301
    # 可行性使用未舍入值：3994/16 = 249.625 < 250，17 级候选必须不可行
    data2 = post({**BASE, "run_length_mm": 3994}).json()
    c17 = next(c for c in data2["candidates"] if c["steps"] == 17)
    assert c17["feasible"] is False


def test_no_solution():
    data = post({**BASE, "riser_min_mm": 170, "riser_max_mm": 172}).json()
    assert data["status"] == "no_solution"
    assert data["solution"] is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("floor_height_mm", 0),
        ("floor_height_mm", -3000),
        ("floor_height_mm", 3000.5),
        ("floor_height_mm", "3000"),
        ("floor_height_mm", True),
        ("run_length_mm", 0),
        ("riser_min_mm", -1),
        ("target_riser_mm", 1.5),
    ],
)
def test_invalid_values_rejected(field, value):
    assert post({**BASE, field: value}).status_code == 422


def test_interval_lower_greater_than_upper_rejected():
    assert post({**BASE, "riser_min_mm": 200, "riser_max_mm": 190}).status_code == 422
    assert post({**BASE, "tread_min_mm": 330, "tread_max_mm": 320}).status_code == 422


def test_missing_field_rejected():
    payload = {k: v for k, v in BASE.items() if k != "run_length_mm"}
    assert post(payload).status_code == 422


def test_huge_integer_preserved_exactly():
    # 2^53 + 1：API 必须按原值精确计算（保持原值），不得静默舍入为 2^53
    huge = 9007199254740993
    payload = dict(
        floor_height_mm=huge, run_length_mm=4800,
        riser_min_mm=1, riser_max_mm=10**18,
        tread_min_mm=1, tread_max_mm=10**9,
        target_riser_mm=1,
    )
    r = post(payload)
    assert r.status_code == 200
    sol = r.json()["solution"]
    assert sol["steps"] == 40
    # JSON 整数往返后序列总和仍精确等于原值
    assert sum(sol["riser_sequence_mm"]) == huge
    assert sol["total_height_mm"] == huge


def test_manual_selection_of_feasible_candidate():
    r = post({**BASE, "selected_steps": 18})
    assert r.status_code == 200
    data = r.json()
    # 推荐项保持原排序结果（17 级），放样按指定的 18 级生成
    assert data["recommended_steps"] == 17
    assert data["selection_source"] == "manual"
    sol = data["solution"]
    assert sol["steps"] == 18
    assert sol["treads"] == 17
    # 3000 = 18*166 + 12：前 12 级 167，其余 166
    assert sol["riser_sequence_mm"] == [167] * 12 + [166] * 6
    assert sol["total_height_mm"] == 3000
    assert sol["tread_display_mm"] == 282  # 4800/17 ≈ 282.35
    by_steps = {c["steps"]: c for c in data["candidates"]}
    assert by_steps[17]["recommended"] is True and by_steps[17]["selected"] is False
    assert by_steps[18]["recommended"] is False and by_steps[18]["selected"] is True


def test_selected_steps_equal_to_recommendation():
    data = post({**BASE, "selected_steps": 17}).json()
    assert data["recommended_steps"] == 17
    assert data["selection_source"] == "manual"
    assert data["solution"]["steps"] == 17


@pytest.mark.parametrize("bad_steps", [1, 41, 0, 100, 21, 15])
def test_infeasible_or_out_of_range_selection_returns_422(bad_steps):
    # 21 级被踏步高度下限淘汰（142.86 < 150）；15 级被上限淘汰（200 > 190）
    r = post({**BASE, "selected_steps": bad_steps})
    assert r.status_code == 422
    detail = r.json()["detail"]
    # 错误可定位到 selected_steps
    locs = [tuple(e["loc"]) for e in detail]
    assert ("body", "selected_steps") in locs
    assert any(str(bad_steps) in e["msg"] for e in detail)


def test_selected_steps_wrong_type_returns_422():
    assert post({**BASE, "selected_steps": "18"}).status_code == 422
    assert post({**BASE, "selected_steps": 18.5}).status_code == 422
    assert post({**BASE, "selected_steps": True}).status_code == 422


def test_selected_steps_with_no_solution_returns_422():
    payload = {**BASE, "riser_min_mm": 170, "riser_max_mm": 172, "selected_steps": 17}
    r = post(payload)
    assert r.status_code == 422
    locs = [tuple(e["loc"]) for e in r.json()["detail"]]
    assert ("body", "selected_steps") in locs


def test_no_solution_response_shape_unchanged():
    data = post({**BASE, "riser_min_mm": 170, "riser_max_mm": 172}).json()
    assert data["status"] == "no_solution"
    assert data["solution"] is None
    assert data["recommended_steps"] is None
    assert data["selection_source"] is None


# ---------------------------------------------------------------------------
# 中间标高控制点
# ---------------------------------------------------------------------------

def test_legacy_request_without_control_points_unchanged():
    """旧请求不带控制点：响应与余数前置序列完全不变。"""
    data = post(BASE).json()
    sol = data["solution"]
    assert "controlled" not in sol
    assert "control_points" not in sol
    assert sol["riser_sequence_mm"] == [177] * 8 + [176] * 9
    assert sol["cumulative_height_mm"][-1] == 3000
    assert data["recommended_steps"] == 17 and data["selection_source"] == "auto"


def test_recommended_layout_with_two_control_points_hits_exactly():
    """推荐方案（17 级）+ 双控制点：精确命中、半毫米分段、每级仍在闭区间。"""
    payload = {**BASE, "control_points": [
        {"step": 5, "cumulative_mm": 900},
        {"step": 10, "cumulative_mm": 1750},
    ]}
    r = post(payload)
    assert r.status_code == 200
    data = r.json()
    # 推荐踏步数、选用来源与候选标记保持原链路
    assert data["recommended_steps"] == 17
    assert data["selection_source"] == "auto"
    sol = data["solution"]
    assert sol["steps"] == 17
    assert sol["controlled"] is True
    cumulative = sol["cumulative_height_mm"]
    # 两个控制点与层高终点精确命中
    assert cumulative[4] == 900
    assert cumulative[9] == 1750
    assert cumulative[-1] == 3000
    assert sol["total_height_mm"] == 3000
    # 命中值回显
    assert sol["control_points"] == [
        {"step": 5, "requested_mm": 900, "hit_mm": 900, "error_mm": 0},
        {"step": 10, "requested_mm": 1750, "hit_mm": 1750, "error_mm": 0},
    ]
    # 每级高度落入原高度闭区间 [150,190]
    assert all(150 <= h <= 190 for h in sol["riser_sequence_mm"])
    # 段内累计相对理想直线误差位于 [0, 0.5mm)
    anchors = [(0, 0), (5, 900), (10, 1750), (17, 3000)]
    for (a, ea), (b, eb) in zip(anchors, anchors[1:]):
        m, delta = b - a, eb - ea
        for j in range(1, m + 1):
            observed = cumulative[a + j - 1] - ea
            ideal = j * delta / m
            assert 0 <= observed - ideal < 0.5
    # 结果按级号唯一确定：重复请求一致
    assert post(payload).json()["solution"]["riser_sequence_mm"] == sol["riser_sequence_mm"]
    # 推荐/选中候选标记保留
    by_steps = {c["steps"]: c for c in data["candidates"]}
    assert by_steps[17]["recommended"] is True and by_steps[17]["selected"] is True


def test_manual_layout_with_control_point_hits_exactly():
    """人工方案（18 级）应用控制点后精确命中，推荐标记仍为 17 级。"""
    payload = {**BASE, "selected_steps": 18,
               "control_points": [{"step": 9, "cumulative_mm": 1500}]}
    r = post(payload)
    assert r.status_code == 200
    data = r.json()
    assert data["selection_source"] == "manual"
    assert data["recommended_steps"] == 17
    sol = data["solution"]
    assert sol["steps"] == 18 and sol["controlled"] is True
    assert sol["cumulative_height_mm"][8] == 1500
    assert sol["cumulative_height_mm"][-1] == 3000
    assert sol["control_points"][0]["hit_mm"] == 1500
    assert sol["control_points"][0]["error_mm"] == 0
    by_steps = {c["steps"]: c for c in data["candidates"]}
    assert by_steps[17]["recommended"] is True and by_steps[18]["selected"] is True


def test_control_point_causing_riser_out_of_bounds_returns_field_localized_422():
    """控制点导致单级高度越界：422 定位到具体控制点字段，含越界级与计算高度。"""
    # 第 5 级 900（段均 180 可行）；第 10 级仅 1000 → 第 6 级起段均 20mm < 150
    payload = {**BASE, "control_points": [
        {"step": 5, "cumulative_mm": 900},
        {"step": 10, "cumulative_mm": 1000},
    ]}
    r = post(payload)
    assert r.status_code == 422
    detail = r.json()["detail"]
    locs = [tuple(e["loc"]) for e in detail]
    # 定位到第 2 个控制点（下标 1）的累计标高字段
    assert ("body", "control_points", 1, "cumulative_mm") in locs
    err = next(e for e in detail if tuple(e["loc"]) == ("body", "control_points", 1, "cumulative_mm"))
    # 说明越界级与计算高度
    assert "第 6 级" in err["msg"]
    assert "20mm" in err["msg"]
    assert "150mm" in err["msg"]


def test_first_segment_out_of_bounds_localized_to_first_control_point():
    r = post({**BASE, "control_points": [{"step": 1, "cumulative_mm": 140}]})
    assert r.status_code == 422
    err = r.json()["detail"][0]
    assert tuple(err["loc"]) == ("body", "control_points", 0, "cumulative_mm")
    assert "第 1 级" in err["msg"] and "140mm" in err["msg"]


def test_last_segment_out_of_bounds_localized_to_last_control_point():
    # 第 16 级 2800（前段均 175 可行）→ 第 17 级 200mm 高于上限 190
    r = post({**BASE, "control_points": [{"step": 16, "cumulative_mm": 2800}]})
    assert r.status_code == 422
    err = r.json()["detail"][0]
    assert tuple(err["loc"]) == ("body", "control_points", 0, "cumulative_mm")
    assert "第 17 级" in err["msg"] and "200mm" in err["msg"] and "190mm" in err["msg"]


@pytest.mark.parametrize(
    "points,index,field",
    [
        ([{"step": 17, "cumulative_mm": 2900}], 0, "step"),       # 级号等于末级
        ([{"step": 0, "cumulative_mm": 100}], 0, "step"),         # 级号为 0
        ([{"step": 5, "cumulative_mm": 0}], 0, "cumulative_mm"),  # 标高等于起点
        ([{"step": 5, "cumulative_mm": 3000}], 0, "cumulative_mm"),  # 标高等于终点
        ([{"step": 10, "cumulative_mm": 1750},
          {"step": 5, "cumulative_mm": 900}], 1, "step"),         # 级号未递增
        ([{"step": 5, "cumulative_mm": 900},
          {"step": 10, "cumulative_mm": 900}], 1, "cumulative_mm"),  # 标高未递增
        ([{"step": 5, "cumulative_mm": 900},
          {"step": 5, "cumulative_mm": 950}], 1, "step"),         # 级号重复
    ],
)
def test_invalid_control_point_structure_returns_localized_422(points, index, field):
    r = post({**BASE, "control_points": points})
    assert r.status_code == 422
    locs = [tuple(e["loc"]) for e in r.json()["detail"]]
    assert ("body", "control_points", index, field) in locs


def test_control_point_wrong_type_returns_422():
    assert post({**BASE, "control_points": [{"step": 5.5, "cumulative_mm": 900}]}).status_code == 422
    assert post({**BASE, "control_points": [{"step": "5", "cumulative_mm": 900}]}).status_code == 422
    assert post({**BASE, "control_points": [{"step": 5, "cumulative_mm": 900.5}]}).status_code == 422


def test_empty_control_point_list_is_legacy():
    data = post({**BASE, "control_points": []}).json()
    assert "controlled" not in data["solution"]
    assert data["solution"]["riser_sequence_mm"] == [177] * 8 + [176] * 9


def test_control_points_ignored_on_no_solution_shape():
    """无解时携带控制点：仍保持无解行为（控制点无法作用于不存在的方案）。"""
    payload = {**BASE, "riser_min_mm": 170, "riser_max_mm": 172,
               "control_points": [{"step": 5, "cumulative_mm": 900}]}
    r = post(payload)
    # 无可行候选时无法选定踏步数：返回无解结论（控制点不产生方案）
    assert r.status_code == 200
    assert r.json()["status"] == "no_solution"
