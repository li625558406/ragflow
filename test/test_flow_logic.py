# test/test_flow_logic.py
"""FlowWorkflow 状态机纯逻辑测试（无 DB 依赖，对抗性用例含非法输入）。"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from api.db.services.flow_service import FlowWorkflow


def _flow(status="initiator"):
    return {
        "initiator_id": "u1", "leader_id": "u2", "handler_id": "u3",
        "status": status,
    }


class TestNodeOwner:
    def test_each_node_owner(self):
        assert FlowWorkflow.node_owner_id(_flow(), "initiator") == "u1"
        assert FlowWorkflow.node_owner_id(_flow(), "leader") == "u2"
        assert FlowWorkflow.node_owner_id(_flow(), "handler") == "u3"
        assert FlowWorkflow.node_owner_id(_flow(), "summary") == "u1"  # 汇总归发起人

    def test_terminal_no_owner(self):
        assert FlowWorkflow.owner_of_current(_flow("archived")) == ""
        assert FlowWorkflow.owner_of_current(_flow("cancelled")) == ""


class TestCanView:
    def test_participants_can_view(self):
        for uid in ("u1", "u2", "u3"):
            assert FlowWorkflow.can_view(_flow(), uid)

    def test_outsider_cannot_view(self):
        assert not FlowWorkflow.can_view(_flow(), "stranger")
        assert not FlowWorkflow.can_view(_flow(), "")
        assert not FlowWorkflow.can_view(_flow(), None)


class TestSubmitTarget:
    def test_forward_chain(self):
        assert FlowWorkflow.submit_target("initiator", "next") == "leader"
        assert FlowWorkflow.submit_target("leader", "next") == "handler"
        assert FlowWorkflow.submit_target("handler", "next") == "summary"

    def test_return_chain(self):
        assert FlowWorkflow.submit_target("leader", "return") == "initiator"
        assert FlowWorkflow.submit_target("summary", "return") == "handler"

    def test_summary_next_raises(self):
        try:
            FlowWorkflow.submit_target("summary", "next")
            assert False, "should raise"
        except ValueError as e:
            assert "归档" in str(e)

    def test_initiator_return_raises(self):
        try:
            FlowWorkflow.submit_target("initiator", "return")
            assert False, "should raise"
        except ValueError:
            pass

    def test_invalid_action(self):
        try:
            FlowWorkflow.submit_target("leader", "hack")
            assert False, "should raise"
        except ValueError:
            pass

    def test_terminal_status_raises(self):
        for st in ("archived", "cancelled"):
            for act in ("next", "return"):
                try:
                    FlowWorkflow.submit_target(st, act)
                    assert False, "should raise"
                except ValueError:
                    pass
