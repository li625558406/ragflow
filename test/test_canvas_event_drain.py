"""canvas._drain_component_events 单测：drain 泛化对任意带 _event_queue 的组件生效，
FanOut 行为不变，无队列组件跳过，range 收敛。"""
from types import SimpleNamespace

from agent.canvas import Canvas


def _canvas_with(components):
    canvas = Canvas.__new__(Canvas)  # 跳过 __init__（DSL/LLM 依赖），仅测 drain 逻辑
    canvas.path = [f"n{i}" for i in range(len(components))]
    canvas.get_component_obj = lambda nid: components[int(nid[1:])]
    return canvas


def _fake_decorate(event, data):
    return {"event": event, "message_id": "m1", "task_id": "t1", "data": data}


def _make_cpn(name, events=None):
    import asyncio
    cpn = SimpleNamespace(component_name=name)
    if events is not None:
        q = asyncio.Queue()
        for e in events:
            q.put_nowait(e)
        cpn._event_queue = q
    return cpn


def test_drain_forwards_all_component_queues():
    canvas = _canvas_with([
        _make_cpn("Begin"),
        _make_cpn("TemplateFill",
                  [{"event": "template_fill_progress", "data": {"stage": "selected"}}]),
        _make_cpn("FanOut", [{"event": "fanout_meta", "data": {"lane_total": 1}},
                             {"event": "message", "data": {"content": "x"}}]),
    ])
    out = list(canvas._drain_component_events(0, 3, _fake_decorate))
    assert [e["event"] for e in out] == [
        "template_fill_progress", "fanout_meta", "message"]
    assert all(e["message_id"] == "m1" and e["task_id"] == "t1" for e in out)
    assert canvas.get_component_obj("n1")._event_queue.empty()   # 队列被清空
    assert canvas.get_component_obj("n2")._event_queue.empty()


def test_drain_skips_components_without_queue():
    canvas = _canvas_with([_make_cpn("LLM"), _make_cpn("Message")])
    assert list(canvas._drain_component_events(0, 2, _fake_decorate)) == []


def test_drain_respects_range():
    canvas = _canvas_with([
        _make_cpn("FanOut", [{"event": "message", "data": {}}]),
        _make_cpn("TemplateFill", [{"event": "template_fill_progress", "data": {}}]),
    ])
    out = list(canvas._drain_component_events(1, 2, _fake_decorate))
    assert [e["event"] for e in out] == ["template_fill_progress"]
