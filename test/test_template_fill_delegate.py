"""画布委托任务参数组装的对抗性单测（纯函数，不触库/LLM）。"""
from types import SimpleNamespace


class TestCanvasTaskParams:
    def _ph(self, key, mode="llm", default=""):
        return {"key": key, "fill_mode": mode, "default_value": default}

    def test_full_decision_payload(self):
        from agent.component.template_fill import _canvas_task_params
        placeholders = [self._ph("k1", default="d1"), self._ph("k2"), self._ph("k3")]
        llm_items = [{"key": "k2"}]   # 确认后仍要走检索+LLM 的字段
        params = _canvas_task_params(
            begin_fields={"项目名称": "X"}, query="需求描述",
            decision={"changed": {"k1"}, "values": {"k3": ""}},
            llm_item_keys={it["key"] for it in llm_items},
            placeholders=placeholders, user_file_text="文件内容")
        assert params["_direct_values"] == {"k3": ""}
        # 白名单语义：_changed_keys = 确认后要走检索+LLM 的 key 集合
        assert params["_changed_keys"] == ["k2"]
        # k1（未勾选→默认值兜底）与 k3（直填）跳过检索，只有 k2 走检索
        assert params["_retrieve_skip_keys"] == ["k1", "k3"]
        assert params["_user_file_text"] == "文件内容"
        assert params["项目名称"] == "X"
        assert params["用户需求描述"] == "需求描述"

    def test_no_decision_keeps_defaults_path(self):
        from agent.component.template_fill import _canvas_task_params
        params = _canvas_task_params(
            begin_fields={}, query="", decision=None,
            llm_item_keys={"k1"}, placeholders=[self._ph("k1")],
            user_file_text="")
        assert params["_direct_values"] == {}
        assert params["_changed_keys"] == ["k1"]
        assert params["_retrieve_skip_keys"] == []
        assert "用户需求描述" not in params

    def test_empty_query_not_inserted(self):
        from agent.component.template_fill import _canvas_task_params
        params = _canvas_task_params(
            begin_fields={}, query="   ", decision=None,
            llm_item_keys=set(), placeholders=[], user_file_text="x")
        assert "用户需求描述" not in params

    def test_entities_written_to_params(self):
        from agent.component.template_fill import _canvas_task_params
        params = _canvas_task_params(
            begin_fields={}, query="需求", decision=None,
            llm_item_keys={"k1"}, placeholders=[self._ph("k1")],
            user_file_text="", entities={"项目名称": "A项目", "__context__": "市政房建"})
        assert params["_entities"] == {"项目名称": "A项目", "__context__": "市政房建"}

    def test_entities_default_empty(self):
        from agent.component.template_fill import _canvas_task_params
        params = _canvas_task_params(
            begin_fields={}, query="", decision=None,
            llm_item_keys={"k1"}, placeholders=[self._ph("k1")], user_file_text="")
        assert params["_entities"] == {}


class TestMergedEntities:
    def test_union(self):
        from agent.component.template_fill import _merged_entities
        emap = {"t1": {"direct": {}, "entities": {"项目名称": "A", "__context__": "C1"}},
                "t2": {"direct": {}, "entities": {"采购人": "B", "__context__": "C2"}}}
        assert _merged_entities(emap) == {"项目名称": "A", "__context__": "C1", "采购人": "B"}
        assert _merged_entities({}) == {}


class TestUnfilledOf:
    _PHS = [{"key": "a", "name": "甲", "required": True},
            {"key": "b", "name": "乙", "required": False}]

    def test_partial_filled_returns_unfilled_list(self):
        from agent.component.template_fill import _unfilled_of
        row = SimpleNamespace(values={"render": {"a": "x", "b": ""}})
        assert _unfilled_of(self._PHS, row) == [
            {"key": "b", "name": "乙", "required": False}]

    def test_all_filled_returns_none(self):
        from agent.component.template_fill import _unfilled_of
        row = SimpleNamespace(values={"render": {"a": "x", "b": "y"}})
        assert _unfilled_of(self._PHS, row) is None

    def test_values_not_dict_returns_none(self):
        from agent.component.template_fill import _unfilled_of
        row = SimpleNamespace(values=None)
        assert _unfilled_of(self._PHS, row) is None

    def test_row_without_values_attr_returns_none(self):
        # 行对象缺 values 属性（旧 schema/桩对象）：getattr 兜底，不抛 AttributeError
        from agent.component.template_fill import _unfilled_of
        assert _unfilled_of(self._PHS, SimpleNamespace()) is None

    def test_render_none_derives_all_placeholders(self):
        # values={"render": None}（直填路径无 render 键）：按全空派生 → 全部占位符未填充
        from agent.component.template_fill import _unfilled_of
        row = SimpleNamespace(values={"render": None})
        assert _unfilled_of(self._PHS, row) == [
            {"key": "a", "name": "甲", "required": True},
            {"key": "b", "name": "乙", "required": False}]


class TestFilledOf:
    """_filled_of 与 _unfilled_of 逐字镜像（同值源、同异常口径），逐一对照。"""

    _PHS = [{"key": "a", "name": "甲", "required": True},
            {"key": "b", "name": "乙", "required": False}]

    def test_partial_filled_returns_filled_list(self):
        from agent.component.template_fill import _filled_of
        row = SimpleNamespace(values={"render": {"a": "x", "b": ""}})
        assert _filled_of(self._PHS, row) == [{"key": "a", "name": "甲"}]

    def test_all_filled_returns_all(self):
        from agent.component.template_fill import _filled_of
        row = SimpleNamespace(values={"render": {"a": "x", "b": "y"}})
        assert _filled_of(self._PHS, row) == [
            {"key": "a", "name": "甲"}, {"key": "b", "name": "乙"}]

    def test_values_not_dict_returns_none(self):
        from agent.component.template_fill import _filled_of
        row = SimpleNamespace(values=None)
        assert _filled_of(self._PHS, row) is None

    def test_row_without_values_attr_returns_none(self):
        from agent.component.template_fill import _filled_of
        assert _filled_of(self._PHS, SimpleNamespace()) is None

    def test_render_none_derives_none(self):
        # values={"render": None} → 全部按未填充 → 已填清单为空 → None（与 unfilled 互补）
        from agent.component.template_fill import _filled_of
        row = SimpleNamespace(values={"render": None})
        assert _filled_of(self._PHS, row) is None

    def test_complementary_with_unfilled_of(self):
        """反漂移：两者并集必须覆盖全部有 key 的填写点，交集为空。"""
        from agent.component.template_fill import _filled_of, _unfilled_of
        row = SimpleNamespace(values={"render": {"a": "x", "b": "   "}})
        filled = _filled_of(self._PHS, row) or []
        unfilled = _unfilled_of(self._PHS, row) or []
        f_keys = {it["key"] for it in filled}
        u_keys = {it["key"] for it in unfilled}
        assert f_keys == {"a"}
        assert u_keys == {"b"}
        assert f_keys | u_keys == {p["key"] for p in self._PHS}
        assert f_keys & u_keys == set()
