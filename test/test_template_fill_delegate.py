"""画布委托任务参数组装的对抗性单测（纯函数，不触库/LLM）。"""


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
