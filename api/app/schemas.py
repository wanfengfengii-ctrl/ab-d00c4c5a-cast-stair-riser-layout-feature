"""请求模型：所有尺寸必须为正整数毫米，区间下限不得大于上限。"""
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ControlPointIn(BaseModel):
    """现场复测的中间标高控制点：第 step 级处的累计标高 cumulative_mm。"""

    # strict=True：拒绝字符串、浮点、布尔等隐式转换，只接受 JSON 整数
    model_config = ConfigDict(strict=True)

    step: int = Field(gt=0, description="控制点级号（1 起，须位于首末级之间）")
    cumulative_mm: int = Field(gt=0, description="控制点累计标高（毫米，须位于 0 与层高之间）")


class LayoutRequest(BaseModel):
    # strict=True：拒绝字符串、浮点、布尔等隐式转换，只接受 JSON 整数
    model_config = ConfigDict(strict=True)

    floor_height_mm: int = Field(gt=0, description="层高")
    run_length_mm: int = Field(gt=0, description="水平可用长度")
    riser_min_mm: int = Field(gt=0, description="踏步高度下限")
    riser_max_mm: int = Field(gt=0, description="踏步高度上限")
    tread_min_mm: int = Field(gt=0, description="踏面深度下限")
    tread_max_mm: int = Field(gt=0, description="踏面深度上限")
    target_riser_mm: int = Field(gt=0, description="目标踏步高度")
    # 可选：现场人工选用的踏步数；缺省时按目标偏差自动推荐。
    # 取值范围与可行性由领域计算校验，非法值返回可定位到本字段的 422。
    selected_steps: Optional[int] = Field(default=None, description="人工选用的踏步数")
    # 可选：现场复测得到的中间标高控制点（平台下口/转折级），按级号分段重新生成逐级表。
    # 级号区间、标高递增与受控单级高度闭区间由领域计算校验，
    # 非法值返回可定位到具体控制点字段的 422。
    control_points: Optional[List[ControlPointIn]] = Field(
        default=None, description="中间标高控制点列表"
    )

    @model_validator(mode="after")
    def _check_intervals(self) -> "LayoutRequest":
        if self.riser_min_mm > self.riser_max_mm:
            raise ValueError("踏步高度下限不得大于上限")
        if self.tread_min_mm > self.tread_max_mm:
            raise ValueError("踏面深度下限不得大于上限")
        return self
