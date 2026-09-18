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

def riser_cum_cell(page, row):
    return page.get_by_test_id(f"riser-row-{row}").locator("td").nth(2)


def fill_cp_row(page, index, step, elev):
    page.get_by_test_id(f"cp-step-{index}").fill(str(step))
    page.get_by_test_id(f"cp-elev-{index}").fill(str(elev))


def test_recommended_layout_with_two_control_points_hits_exactly(page):
    """推荐方案录入双控制点后重新生成逐级表：受控标识、精确命中、候选标记保留。"""
    page.goto(WEB)
    expect(page.get_by_test_id("step-count")).to_have_text("17")
    expect(page.get_by_test_id("controlled-badge")).to_have_count(0)

    fill_cp_row(page, 0, 5, 900)
    page.get_by_test_id("cp-add").click()
    fill_cp_row(page, 1, 10, 1750)
    page.get_by_test_id("cp-apply").click()

    # 受控标识出现，仍为自动推荐的 17 级
    expect(page.get_by_test_id("controlled-badge")).to_be_visible()
    expect(page.get_by_test_id("controlled-badge")).to_have_text("控制点受控")
    expect(page.get_by_test_id("selection-source")).to_have_text("自动推荐")
    expect(page.get_by_test_id("step-count")).to_have_text("17")
    # 控制点与层高终点精确命中
    expect(riser_cum_cell(page, 5)).to_have_text("900")
    expect(riser_cum_cell(page, 10)).to_have_text("1750")
    expect(riser_cum_cell(page, 17)).to_have_text("3000")
    # 命中值回显
    expect(page.get_by_test_id("cp-hits")).to_be_visible()
    expect(page.get_by_test_id("cp-hit-value-0")).to_have_text("900")
    expect(page.get_by_test_id("cp-hit-value-1")).to_have_text("1750")
    # 受控级在逐级表中有标记
    expect(page.get_by_test_id("cp-marker-5")).to_have_count(1)
    expect(page.get_by_test_id("cp-marker-10")).to_have_count(1)
    # 推荐、选中与候选按钮标记保留
    expect(page.get_by_test_id("candidate-row-17")).to_contain_text("选中")
    expect(page.get_by_test_id("candidate-recommended")).to_have_count(1)
    expect(page.get_by_test_id("adopt-18")).to_be_visible()
    # 逐级表仍为 17 行
    expect(page.locator('[data-testid^="riser-row-"]')).to_have_count(17)


def test_manual_layout_with_control_point_hits_exactly(page):
    """人工改选后录入控制点：人工标识与推荐标记保留，控制点精确命中。"""
    page.goto(WEB)
    page.get_by_test_id("adopt-18").click()
    expect(page.get_by_test_id("step-count")).to_have_text("18")
    expect(page.get_by_test_id("selection-source")).to_have_text("人工选用")

    fill_cp_row(page, 0, 9, 1500)
    page.get_by_test_id("cp-apply").click()

    # 人工选用 + 控制点受控标识并存
    expect(page.get_by_test_id("controlled-badge")).to_be_visible()
    expect(page.get_by_test_id("selection-source")).to_have_text("人工选用")
    expect(page.get_by_test_id("recommended-steps")).to_have_text("17")
    expect(page.get_by_test_id("step-count")).to_have_text("18")
    # 第 9 级精确命中 1500，终点仍为 3000，共 18 行
    expect(riser_cum_cell(page, 9)).to_have_text("1500")
    expect(riser_cum_cell(page, 18)).to_have_text("3000")
    expect(page.locator('[data-testid^="riser-row-"]')).to_have_count(18)
    expect(page.get_by_test_id("cp-hit-value-0")).to_have_text("1500")
    # 候选标记：18 选中、17 保留自动推荐
    expect(page.get_by_test_id("candidate-row-18")).to_contain_text("选中")
    expect(page.get_by_test_id("candidate-row-17")).to_contain_text("自动推荐")


def test_control_point_out_of_bounds_shows_field_feedback_and_keeps_layout(page):
    """控制点使单级高度越界：字段级反馈、保留原方案。"""
    page.goto(WEB)
    expect(page.get_by_test_id("step-count")).to_have_text("17")

    # 第 1 级累计 140 → 单级高度 140mm 低于下限 150mm
    fill_cp_row(page, 0, 1, 140)
    page.get_by_test_id("cp-apply").click()

    # 错误定位到第 1 行“累计标高”字段，含越界级与计算高度
    expect(page.get_by_test_id("cp-elev-0-error")).to_be_visible()
    expect(page.get_by_test_id("cp-elev-0-error")).to_contain_text("第 1 级")
    expect(page.get_by_test_id("cp-elev-0-error")).to_contain_text("140")
    expect(page.get_by_test_id("cp-error")).to_be_visible()
    # 未受控：原 17 级余数前置方案保留
    expect(page.get_by_test_id("controlled-badge")).to_have_count(0)
    expect(page.get_by_test_id("step-count")).to_have_text("17")
    expect(page.locator('[data-testid^="riser-row-"]')).to_have_count(17)
    # 原余数前置序列不变（前 8 级 177）
    expect(riser_cell(page, 1)).to_have_text("177")

    # 改为合法控制点后可成功应用
    page.get_by_test_id("cp-step-0").fill("5")
    page.get_by_test_id("cp-elev-0").fill("900")
    page.get_by_test_id("cp-apply").click()
    expect(page.get_by_test_id("controlled-badge")).to_be_visible()
    expect(riser_cum_cell(page, 5)).to_have_text("900")
    expect(page.get_by_test_id("cp-error")).to_have_count(0)
    expect(page.get_by_test_id("cp-elev-0-error")).to_have_count(0)


def test_changing_steps_or_dimension_clears_control_points(page):
    """改选踏步数或修改尺寸：清空控制点并按原链路计算。"""
    page.goto(WEB)
    fill_cp_row(page, 0, 5, 900)
    page.get_by_test_id("cp-add").click()
    fill_cp_row(page, 1, 10, 1750)
    page.get_by_test_id("cp-apply").click()
    expect(page.get_by_test_id("controlled-badge")).to_be_visible()
    expect(riser_cum_cell(page, 5)).to_have_text("900")

    # 改选踏步数：控制点录入与受控状态一并清空
    page.get_by_test_id("adopt-18").click()
    expect(page.get_by_test_id("step-count")).to_have_text("18")
    expect(page.get_by_test_id("controlled-badge")).to_have_count(0)
    expect(page.get_by_test_id("cp-hits")).to_have_count(0)
    expect(page.get_by_test_id("cp-step-0")).to_have_value("")
    expect(page.get_by_test_id("cp-elev-0")).to_have_value("")
    # 18 级原余数前置序列恢复：前 12 级 167
    expect(riser_cell(page, 1)).to_have_text("167")

    # 在人工方案上再次应用控制点
    fill_cp_row(page, 0, 9, 1500)
    page.get_by_test_id("cp-apply").click()
    expect(page.get_by_test_id("controlled-badge")).to_be_visible()
    expect(riser_cum_cell(page, 9)).to_have_text("1500")

    # 修改尺寸：清空人工选用与控制点，恢复自动推荐原链路
    page.get_by_test_id("tread-max").fill("330")
    expect(page.get_by_test_id("step-count")).to_have_text("17")
    expect(page.get_by_test_id("selection-source")).to_have_text("自动推荐")
    expect(page.get_by_test_id("controlled-badge")).to_have_count(0)
    expect(page.get_by_test_id("cp-hits")).to_have_count(0)
    # 余数前置序列恢复：前 8 级 177
    expect(riser_cell(page, 1)).to_have_text("177")
    expect(riser_cum_cell(page, 5)).to_have_text("885")  # 5×177


def test_control_point_client_form_validation(page):
    """级号/标高结构错误在客户端即时拦截，不进入受控状态。"""
    page.goto(WEB)
    fill_cp_row(page, 0, 17, 2900)  # 17 级方案仅允许 1–16 级
    page.get_by_test_id("cp-apply").click()
    expect(page.get_by_test_id("cp-step-0-error")).to_contain_text("16")
    expect(page.get_by_test_id("controlled-badge")).to_have_count(0)

    page.get_by_test_id("cp-step-0").fill("5")
    page.get_by_test_id("cp-elev-0").fill("3000")  # 标高不得等于层高终点
    page.get_by_test_id("cp-apply").click()
    expect(page.get_by_test_id("cp-elev-0-error")).to_contain_text("3000")
    expect(page.get_by_test_id("controlled-badge")).to_have_count(0)
