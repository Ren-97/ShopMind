package com.example.shopmind.viewmodel

import com.example.shopmind.domain.AgentEvent

/**
 * SSE event → ViewModel 状态更新(§4.7.7)。
 *
 * 拆出来便于:
 *   - 单测(给 ViewModel 灌 fake event,断言状态)
 *   - 后续扩展(card 类型新增 / event 类型新增,只改这里)
 *
 * 8 种 event(§4.7.1):meta / thinking / tool_call / card / text / suggestions / done / error
 */
object EventDispatcher {

    fun dispatch(event: AgentEvent, vm: ChatViewModel) {
        when (event) {
            is AgentEvent.Meta -> vm.onMeta(turnId = event.turnId, sessionId = event.sessionId)
            is AgentEvent.Thinking -> vm.onThinkingDelta(event.delta)
            is AgentEvent.ToolCall -> vm.onToolCall(event.name)
            is AgentEvent.CardEvent -> vm.onCard(event.card)
            is AgentEvent.Text -> vm.onTextDelta(event.delta)
            is AgentEvent.Suggestions -> vm.onSuggestions(event.items)
            is AgentEvent.Done -> vm.onDone(event.finishReason)
            is AgentEvent.ErrorEvent -> vm.onError(event.code, event.message)
            is AgentEvent.Unknown -> { /* 静默忽略未知 event(向前兼容) */ }
        }
    }
}
