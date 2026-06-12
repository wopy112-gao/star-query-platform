"""星宝语料场景查询系统 — 图表生成器"""

import re
import math
from typing import Optional


# 配色方案
COLORS = [
    "#1890ff", "#52c41a", "#faad14", "#f5222d", "#722ed1",
    "#13c2c2", "#eb2f96", "#fa8c16", "#a0d911", "#2f54eb",
]


class ChartBuilder:
    """自动分析结果数据并生成 ECharts option"""

    def build(self, rows: list[dict]) -> dict:
        """
        根据结果行生成图表配置
        返回: {"type": str, "option": dict}
        """
        if not rows:
            return {"type": "table_only", "option": None}

        # 分析列
        columns = list(rows[0].keys())
        col_types = self._infer_column_types(rows, columns)

        # 尝试推断分类列和数值列
        cat_cols = [c for c, t in col_types.items() if t == "category"]
        num_cols = [c for c, t in col_types.items() if t == "numeric"]
        time_cols = [c for c, t in col_types.items() if t == "time"]

        # 选择图表类型
        if time_cols and num_cols:
            # 时间+数值 → 折线图
            return self._build_line(rows, time_cols[0], num_cols[0])
        elif cat_cols and num_cols:
            if len(num_cols) == 1:
                # 单数值 → 检查是否占比（总和≈100）
                values = [r[num_cols[0]] for r in rows if r[num_cols[0]] is not None]
                if values and abs(sum(values) - 100) < 5:
                    return self._build_pie(rows, cat_cols[0], num_cols[0])
                elif len(rows) <= 15:
                    return self._build_bar(rows, cat_cols[0], num_cols[0])
                else:
                    return self._build_bar(rows, cat_cols[0], num_cols[0])
            else:
                # 多数值 → 分组柱状图
                return self._build_grouped_bar(rows, cat_cols[0], num_cols)
        elif len(num_cols) >= 2:
            return self._build_bar(rows, num_cols[0], num_cols[1])
        else:
            return {"type": "table_only", "option": None}

    def _infer_column_types(self, rows: list[dict], columns: list[str]) -> dict:
        """推断每列的类型: category / numeric / time"""
        types = {}
        for col in columns:
            values = [r[col] for r in rows[:20] if r[col] is not None]
            if not values:
                types[col] = "category"
                continue

            # 检查是否为数值
            numeric_count = 0
            time_count = 0
            for v in values:
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    numeric_count += 1
                elif isinstance(v, str):
                    # 检查是否时间格式 yyyy-MM 或 yyyy-MM-dd
                    if re.match(r"^\d{4}-\d{2}(-\d{2})?$", v):
                        time_count += 1

            if numeric_count > len(values) * 0.8:
                types[col] = "numeric"
            elif time_count > len(values) * 0.8:
                types[col] = "time"
            else:
                types[col] = "category"

        return types

    def _build_bar(self, rows: list[dict], cat_col: str, num_col: str) -> dict:
        """柱状图"""
        cats = []
        vals = []
        for r in rows:
            v = r[num_col]
            if v is not None:
                cats.append(str(r[cat_col]) if r[cat_col] is not None else "未知")
                vals.append(float(v))

        return {
            "type": "bar",
            "option": {
                "title": {"text": f"{cat_col} vs {num_col}", "left": "center"},
                "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
                "grid": {"left": "3%", "right": "4%", "bottom": "15%", "top": "60px", "containLabel": True},
                "xAxis": {
                    "type": "category",
                    "data": cats,
                    "axisLabel": {"rotate": 45 if len(cats) > 8 else 0},
                },
                "yAxis": {"type": "value"},
                "series": [
                    {
                        "type": "bar",
                        "data": vals,
                        "itemStyle": {"color": COLORS[0]},
                        "barMaxWidth": 50,
                    }
                ],
            },
        }

    def _build_pie(self, rows: list[dict], cat_col: str, num_col: str) -> dict:
        """饼图"""
        data = []
        for i, r in enumerate(rows):
            v = r[num_col]
            if v is not None:
                data.append({
                    "name": str(r[cat_col]) if r[cat_col] is not None else "未知",
                    "value": float(v),
                    "itemStyle": {"color": COLORS[i % len(COLORS)]},
                })

        return {
            "type": "pie",
            "option": {
                "title": {"text": f"{cat_col} 分布", "left": "center"},
                "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
                "legend": {"orient": "vertical", "left": "left", "top": "40px"},
                "series": [
                    {
                        "type": "pie",
                        "radius": ["0%", "60%"],
                        "center": ["55%", "55%"],
                        "data": data,
                        "label": {"formatter": "{b}: {d}%"},
                    }
                ],
            },
        }

    def _build_line(self, rows: list[dict], time_col: str, num_col: str) -> dict:
        """折线图"""
        times = []
        vals = []
        for r in rows:
            v = r[num_col]
            if v is not None:
                times.append(str(r[time_col]) if r[time_col] is not None else "未知")
                vals.append(float(v))

        return {
            "type": "line",
            "option": {
                "title": {"text": f"{time_col} 趋势", "left": "center"},
                "tooltip": {"trigger": "axis"},
                "grid": {"left": "3%", "right": "4%", "bottom": "15%", "top": "60px", "containLabel": True},
                "xAxis": {"type": "category", "data": times},
                "yAxis": {"type": "value"},
                "series": [
                    {
                        "type": "line",
                        "data": vals,
                        "smooth": True,
                        "itemStyle": {"color": COLORS[0]},
                        "areaStyle": {"color": COLORS[0], "opacity": 0.1},
                    }
                ],
            },
        }

    def _build_grouped_bar(self, rows: list[dict], cat_col: str, num_cols: list[str]) -> dict:
        """分组柱状图"""
        cats = []
        for r in rows:
            cats.append(str(r[cat_col]) if r[cat_col] is not None else "未知")

        series = []
        for i, col in enumerate(num_cols):
            vals = []
            for r in rows:
                v = r[col]
                vals.append(float(v) if v is not None else 0)
            series.append({
                "type": "bar",
                "name": col,
                "data": vals,
                "itemStyle": {"color": COLORS[i % len(COLORS)]},
            })

        return {
            "type": "grouped_bar",
            "option": {
                "title": {"text": cat_col, "left": "center"},
                "tooltip": {"trigger": "axis"},
                "legend": {"top": "30px"},
                "grid": {"left": "3%", "right": "4%", "bottom": "15%", "top": "80px", "containLabel": True},
                "xAxis": {"type": "category", "data": cats},
                "yAxis": {"type": "value"},
                "series": series,
            },
        }


chart_builder = ChartBuilder()
