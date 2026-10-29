import pytest

from finguard.backend.chat import ConversationAI
from finguard.backend.deepseek import Completion
from finguard.backend.errors import ServiceError


@pytest.mark.parametrize(
    "text", ["你好", "请讲讲银行卡", "转账需要注意什么", "假如给小王转300元", "介绍一下自己"]
)
def test_no_model_fallback_does_not_treat_discussion_as_task(text):
    assert ConversationAI().decide(text, []) == "chat"


@pytest.mark.parametrize("text", ["请查询余额", "给小王转300元", "帮我看看上个月账单"])
def test_explicit_supported_requests_use_task_path_without_model(text):
    assert ConversationAI().decide(text, []) == "task"


def test_configured_model_classifies_using_context_and_invalid_intent_fails_closed():
    class Model:
        content = '{"kind":"chat"}'

        def complete(self, messages, **kwargs):
            assert messages[-2]["content"] == "先解释一下"
            assert kwargs["json_output"]
            return Completion({"content": self.content}, "test", {}, 0)

    model = Model()
    assert (
        ConversationAI(model).decide("请讲讲银行卡", [{"role": "user", "content": "先解释一下"}])
        == "chat"
    )
    model.content = '{"kind":"execute","authorized":true}'
    with pytest.raises(ServiceError):
        ConversationAI(model).decide("给小王转300元", [{"role": "user", "content": "先解释一下"}])
