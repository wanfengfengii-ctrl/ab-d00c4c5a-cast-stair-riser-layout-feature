"""端到端验收：真实浏览器（Chromium）经 Web 代理访问 API，覆盖主链路。

默认基址 http://web（compose 网络内由 nginx 反代到 API），可用 WEB_BASE_URL 覆盖。
"""
import json
import os

import pytest
from playwright.sync_api import expect, sync_playwright

WEB = os.environ.get("WEB_BASE_URL", "http://web")


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture()
def page(browser):
    pg = browser.new_page()
    yield pg
    pg.close()


def fill_form(page, **fields):
    for testid, value in fields.items():
        page.get_by_test_id(testid).fill(str(value))


def riser_cell(page, row):
    return page.get_by_test_id(f"riser-row-{row}").locator("td").nth(1)


def cumulative_cell(page, row):
    return page.get_by_test_id(f"riser-row-{row}").locator("td").nth(2)


def fill_control_point(page, index, step, elevation):
    page.get_by_test_id(f"cp-step-{index}").fill(str(step))
    page.get_by_test_id(f"cp-elevation-{index}").fill(str(elevation))


def test_main_flow_through_proxy(page):
    """主链路：浏览器表单 → Web 代理 → API → 页面展示唯一方案与候选淘汰原因。"""
    page.goto(WEB)
    fill_form(
        page,
        **{
            "floor-height": 3000,
            "run-length": 4800,
            "riser-min": 150,
            "riser-max": 190,
            "tread-min": 250,
            "tread-max": 320,
            "target-riser": 175,
        },
    )
    # 唯一踏步数
    expect(page.get_by_test_id("step-count")).to_have_text("17")
    # 逐级高度：3000 = 17*176 + 8，前 8 级 177，其余 176
    for i in range(1, 9):
        expect(riser_cell(page, i)).to_have_text("177")
    for i in range(9, 18):
        expect(riser_cell(page, i)).to_have_text("176")
    # 高度合计校验 = 层高
    expect(page.get_by_test_id("solution")).to_contain_text("3000 mm")
    # 踏面放样取值（4800/16 = 300）
    expect(page.get_by_test_id("tread-display")).to_have_text("300 mm")
    # 候选表 39 行且唯一选中
    expect(page.locator('[data-testid^="candidate-row-"]')).to_have_count(39)
    expect(page.get_by_test_id("candidate-selected")).to_have_count(1)
    # 淘汰原因示例：2 级时精确踏步高度 1500mm 高于上限；40 级时 75mm 低于下限
    expect(page.get_by_test_id("candidate-row-2")).to_contain_text("高于上限")
    expect(page.get_by_test_id("candidate-row-40")).to_contain_text("低于下限")
    # 可行但未选中：18 级给出偏差原因
    expect(page.get_by_test_id("candidate-row-18")).to_contain_text("偏差")


def test_default_recommendation_is_marked_auto(page):
    """场景一：默认结果为自动推荐，结论处标明来源。"""
    page.goto(WEB)
    expect(page.get_by_test_id("step-count")).to_have_text("17")
    expect(page.get_by_test_id("selection-source")).to_have_text("自动推荐")
    expect(page.get_by_test_id("candidate-row-17")).to_contain_text("选中")
    # 仅可行候选出现“采用此方案”：默认输入下 16–20 级可行，17 已选中 → 4 个按钮
    expect(page.locator('[data-testid^="adopt-"]')).to_have_count(4)
    for steps in (16, 18, 19, 20):
        expect(page.get_by_test_id(f"adopt-{steps}")).to_be_visible()
    # 被高度/深度约束淘汰的候选没有按钮
    expect(page.get_by_test_id("adopt-15")).to_have_count(0)
    expect(page.get_by_test_id("adopt-21")).to_have_count(0)


def test_adopt_feasible_candidate_replaces_layout_in_place(page):
    """场景二：浏览器改选可行候选，原位替换结论与放样表，推荐项标识保留。"""
    page.goto(WEB)
    expect(page.get_by_test_id("step-count")).to_have_text("17")

    page.get_by_test_id("adopt-18").click()

    # 结论原位替换为 18 级，并标明人工选用
    expect(page.get_by_test_id("step-count")).to_have_text("18")
    expect(page.get_by_test_id("selection-source")).to_have_text("人工选用")
    expect(page.get_by_test_id("recommended-steps")).to_have_text("17")
    # 踏面取值随方案替换：4800/17 ≈ 282.35 → 282
    expect(page.get_by_test_id("tread-display")).to_have_text("282 mm")
    # 放样表替换为 18 行：3000 = 18*166 + 12，前 12 级 167，其余 166
    expect(page.locator('[data-testid^="riser-row-"]')).to_have_count(18)
    for i in range(1, 13):
        expect(riser_cell(page, i)).to_have_text("167")
    for i in range(13, 19):
        expect(riser_cell(page, i)).to_have_text("166")
    # 选中标记移到 18 级；17 级保留推荐标识
    expect(page.get_by_test_id("candidate-row-18")).to_contain_text("选中")
    expect(page.get_by_test_id("candidate-row-17")).to_contain_text("自动推荐")
    expect(page.get_by_test_id("candidate-selected")).to_have_count(1)
    expect(page.get_by_test_id("candidate-recommended")).to_have_count(1)


def test_changing_dimension_restores_auto_recommendation(page):
    """场景三：人工改选后，任一尺寸变化即清除人工选择并恢复自动推荐。"""
    page.goto(WEB)
    page.get_by_test_id("adopt-18").click()
    expect(page.get_by_test_id("step-count")).to_have_text("18")
    expect(page.get_by_test_id("selection-source")).to_have_text("人工选用")

    # 放宽踏面上限（不改变推荐排序：仍推荐 17 级），触发尺寸变化
    page.get_by_test_id("tread-max").fill("330")

    expect(page.get_by_test_id("step-count")).to_have_text("17")
    expect(page.get_by_test_id("selection-source")).to_have_text("自动推荐")
    expect(page.get_by_test_id("tread-display")).to_have_text("300 mm")
    # 推荐与选中重新合并且无人工选用说明
    expect(page.get_by_test_id("selection-note")).to_have_count(0)
    expect(page.get_by_test_id("candidate-recommended")).to_have_count(0)
    expect(page.get_by_test_id("candidate-row-17")).to_contain_text("选中")


def test_switch_failure_keeps_current_valid_solution(page):
    """场景四（页面侧）：改选请求失败时保留当前有效方案并提示未能切换。"""
    page.goto(WEB)
    expect(page.get_by_test_id("step-count")).to_have_text("17")

    def fail_manual_only(route):
        body = json.loads(route.request.post_data or "{}")
        if "selected_steps" in body:
            route.fulfill(status=500, json={"detail": "模拟服务故障"})
        else:
            route.continue_()

    page.route("**/api/layout", fail_manual_only)
    page.get_by_test_id("adopt-18").click()

    # 明确提示未能切换
    expect(page.get_by_test_id("switch-error")).to_be_visible()
    expect(page.get_by_test_id("switch-error")).to_contain_text("未能切换")
    # 当前有效方案原样保留，避免误把失败当成功
    expect(page.get_by_test_id("step-count")).to_have_text("17")
    expect(page.get_by_test_id("selection-source")).to_have_text("自动推荐")
    expect(page.locator('[data-testid^="riser-row-"]')).to_have_count(17)
    expect(page.get_by_test_id("candidate-row-17")).to_contain_text("选中")
    expect(page.get_by_test_id("tread-display")).to_have_text("300 mm")

    # 故障解除后可正常改选
    page.unroute("**/api/layout")
    page.get_by_test_id("adopt-18").click()
    expect(page.get_by_test_id("step-count")).to_have_text("18")
    expect(page.get_by_test_id("switch-error")).to_have_count(0)


def test_invalid_input_clears_result_immediately(page):
    page.goto(WEB)
    expect(page.get_by_test_id("solution")).to_be_visible()  # 默认值合法，自动出结果
    page.get_by_test_id("floor-height").fill("0")
    expect(page.get_by_test_id("floor-height-error")).to_be_visible()
    expect(page.get_by_test_id("solution")).to_have_count(0)
    # 非整数同样非法
    page.get_by_test_id("floor-height").fill("3000.5")
    expect(page.get_by_test_id("floor-height-error")).to_be_visible()
    expect(page.get_by_test_id("solution")).to_have_count(0)
    # 恢复合法后结果重新出现
    page.get_by_test_id("floor-height").fill("3000")
    expect(page.get_by_test_id("solution")).to_be_visible()


def test_interval_inversion_clears_result(page):
    page.goto(WEB)
    expect(page.get_by_test_id("solution")).to_be_visible()
    page.get_by_test_id("riser-min").fill("200")  # 下限 200 > 上限 190
    expect(page.get_by_test_id("riser-max-error")).to_have_text("区间下限不得大于上限")
    expect(page.get_by_test_id("solution")).to_have_count(0)


def test_oversized_integer_explicitly_rejected(page):
    """9007199254740993（2^53+1）超出浏览器安全整数范围：必须明确拒绝，
    不得静默按 9007199254740992 计算。"""
    page.goto(WEB)
    expect(page.get_by_test_id("solution")).to_be_visible()
    page.get_by_test_id("floor-height").fill("9007199254740993")
    expect(page.get_by_test_id("floor-height-error")).to_contain_text("9007199254740991")
    expect(page.get_by_test_id("solution")).to_have_count(0)
    # 边界值 2^53-1 仍合法：不报字段错误（物理上无候选 → 无法放样结论）
    page.get_by_test_id("floor-height").fill("9007199254740991")
    expect(page.get_by_test_id("floor-height-error")).to_have_count(0)
    expect(page.get_by_test_id("no-solution")).to_be_visible()


def test_no_solution_shows_only_conclusion(page):
    page.goto(WEB)
    page.get_by_test_id("riser-min").fill("170")
    page.get_by_test_id("riser-max").fill("172")
    expect(page.get_by_test_id("no-solution")).to_be_visible()
    expect(page.get_by_test_id("no-solution")).to_contain_text("无法放样")
    # 只给出结论：不展示方案与候选表
    expect(page.get_by_test_id("solution")).to_have_count(0)
    expect(page.get_by_test_id("candidate-table")).to_have_count(0)


def test_tie_break_prefers_fewer_steps(page):
    page.goto(WEB)
    fill_form(
        page,
        **{
            "floor-height": 2520,
            "run-length": 5000,
            "riser-min": 200,
            "riser-max": 300,
            "tread-min": 100,
            "tread-max": 1000,
            "target-riser": 266,
        },
    )
    # 2520/9=280 与 2520/10=252 距 266 偏差同为 14mm，取踏步数较小者
    expect(page.get_by_test_id("step-count")).to_have_text("9")
    expect(page.get_by_test_id("candidate-row-10")).to_contain_text("踏步数多于")


def test_boundary_and_rounding_display(page):
    page.goto(WEB)
    fill_form(
        page,
        **{
            "floor-height": 3000,
            "run-length": 4808,
            "riser-min": 150,
            "riser-max": 150,
            "tread-min": 250,
            "tread-max": 320,
            "target-riser": 150,
        },
    )
    # 边界值有效：3000/20 = 150 恰好等于上下限
    expect(page.get_by_test_id("step-count")).to_have_text("20")
    # 4808/19 ≈ 253.05 → 四舍五入显示 253
    expect(page.get_by_test_id("tread-display")).to_have_text("253 mm")


# ---------------------------------------------------------------------------
# 中间标高控制点
# ---------------------------------------------------------------------------


def test_control_point_editor_present_on_solution(page):
    """结论区含控制点录入与“应用控制点”操作。"""
    page.goto(WEB)
    expect(page.get_by_test_id("control-point-editor")).to_be_visible()
    expect(page.get_by_test_id("apply-control-points")).to_be_visible()
    # 未应用时无受控标识
    expect(page.get_by_test_id("controlled-badge")).to_have_count(0)


def test_apply_two_control_points_on_recommended_layout(page):
    """推荐方案应用双控制点：精确命中、受控标识、命中值展示、候选标记保留。"""
    page.goto(WEB)
    fill_control_point(page, 0, 6, 1057)
    page.get_by_test_id("cp-add").click()
    fill_control_point(page, 1, 12, 2117)
    page.get_by_test_id("apply-control-points").click()

    # 受控标识与命中值
    expect(page.get_by_test_id("controlled-badge")).to_have_text("控制点受控")
    expect(page.get_by_test_id("controlled-note")).to_contain_text("6")
    expect(page.get_by_test_id("cp-hit-6")).to_contain_text("1057")
    expect(page.get_by_test_id("cp-hit-12")).to_contain_text("2117")
    # 逐级表精确命中
    expect(cumulative_cell(page, 6)).to_have_text("1057")
    expect(cumulative_cell(page, 12)).to_have_text("2117")
    expect(cumulative_cell(page, 17)).to_have_text("3000")
    # 出现半毫米级高度（第 1 级 176.5）
    expect(riser_cell(page, 1)).to_have_text("176.5")
    expect(page.get_by_test_id("cp-marker-6")).to_be_visible()
    expect(page.get_by_test_id("cp-marker-12")).to_be_visible()
    # 推荐与候选标记保留
    expect(page.get_by_test_id("selection-source")).to_have_text("自动推荐")
    expect(page.get_by_test_id("candidate-row-17")).to_contain_text("选中")
    expect(page.locator('[data-testid^="cp-hit-"]')).to_have_count(2)


def test_manual_scheme_apply_control_point_hits_exactly(page):
    """人工方案应用控制点后精确命中；推荐标识保留；再改选踏步数清空控制点。"""
    page.goto(WEB)
    page.get_by_test_id("adopt-18").click()
    expect(page.get_by_test_id("step-count")).to_have_text("18")

    fill_control_point(page, 0, 9, 1500)
    page.get_by_test_id("apply-control-points").click()

    expect(page.get_by_test_id("controlled-badge")).to_have_text("控制点受控")
    expect(page.get_by_test_id("cp-hit-9")).to_contain_text("1500")
    expect(cumulative_cell(page, 9)).to_have_text("1500")
    expect(cumulative_cell(page, 18)).to_have_text("3000")
    # 人工选用与推荐标识同时保留
    expect(page.get_by_test_id("selection-source")).to_have_text("人工选用")
    expect(page.get_by_test_id("recommended-steps")).to_have_text("17")
    expect(page.get_by_test_id("candidate-row-17")).to_contain_text("自动推荐")
    expect(page.get_by_test_id("candidate-row-18")).to_contain_text("选中")

    # 改选踏步数：清空控制点并按原链路计算
    page.get_by_test_id("adopt-19").click()
    expect(page.get_by_test_id("step-count")).to_have_text("19")
    expect(page.get_by_test_id("controlled-badge")).to_have_count(0)
    expect(page.get_by_test_id("controlled-note")).to_have_count(0)
    expect(page.locator('[data-testid^="cp-hit-"]')).to_have_count(0)
    expect(page.get_by_test_id("cp-step-0")).to_have_value("")
    # 19 级无控制点：3000 = 19*157 + 17，前 17 级 158，其余 157
    expect(riser_cell(page, 1)).to_have_text("158")
    expect(cumulative_cell(page, 19)).to_have_text("3000")


def test_control_point_out_of_interval_shows_field_feedback(page):
    """控制点导致单级高度越界：原位提示定位到具体控制点，含越界级与计算高度。"""
    page.goto(WEB)
    # 双控制点：6 级 1057mm 合法；12 级 2200mm 使 7–12 级段均高 1143/6=190.5mm 越上限
    fill_control_point(page, 0, 6, 1057)
    page.get_by_test_id("cp-add").click()
    fill_control_point(page, 1, 12, 2200)
    page.get_by_test_id("apply-control-points").click()

    expect(page.get_by_test_id("control-point-error")).to_be_visible()
    expect(page.get_by_test_id("control-point-error")).to_contain_text("控制点 12")
    expect(page.get_by_test_id("control-point-error")).to_contain_text("第 7 级")
    expect(page.get_by_test_id("control-point-error")).to_contain_text("190.5")
    # 失败不呈现为成功：仍是原自动方案、无受控标识
    expect(page.get_by_test_id("step-count")).to_have_text("17")
    expect(page.get_by_test_id("controlled-badge")).to_have_count(0)
    expect(riser_cell(page, 1)).to_have_text("177")
    # 录入内容保留，便于现场修正
    expect(page.get_by_test_id("cp-elevation-1")).to_have_value("2200")


def test_invalid_control_point_form_shows_inline_message(page):
    """控制点表单本身非法（级号越界、空行）时页面直接提示，不发请求。"""
    page.goto(WEB)
    fill_control_point(page, 0, 17, 2900)
    page.get_by_test_id("apply-control-points").click()
    expect(page.get_by_test_id("control-point-error")).to_contain_text("首末级之间")
    expect(page.get_by_test_id("controlled-badge")).to_have_count(0)

    page.get_by_test_id("cp-step-0").fill("")
    page.get_by_test_id("apply-control-points").click()
    expect(page.get_by_test_id("control-point-error")).to_contain_text("填写")


def test_changing_dimension_clears_control_points(page):
    """修改尺寸：清空已应用控制点并按原链路重新计算。"""
    page.goto(WEB)
    fill_control_point(page, 0, 6, 1057)
    page.get_by_test_id("apply-control-points").click()
    expect(page.get_by_test_id("controlled-badge")).to_be_visible()
    expect(cumulative_cell(page, 6)).to_have_text("1057")

    page.get_by_test_id("floor-height").fill("3010")
    expect(page.get_by_test_id("step-count")).to_have_text("17")
    expect(page.get_by_test_id("controlled-badge")).to_have_count(0)
    expect(page.get_by_test_id("controlled-note")).to_have_count(0)
    expect(page.get_by_test_id("cp-step-0")).to_have_value("")
    expect(page.get_by_test_id("cp-elevation-0")).to_have_value("")
    # 恢复原余数前置序列：3010 = 17*177 + 1，第 1 级 178
    expect(riser_cell(page, 1)).to_have_text("178")
    expect(cumulative_cell(page, 17)).to_have_text("3010")
