# Design: Merge Consecutive System Messages for DeepSeek Prefix Cache

**Date**: 2026-07-28
**Status**: draft

## Problem

DeepSeek API prompt cache hit rate is only 13-28% despite all prefix content being byte-identical across calls within a session. Investigation shows the cache only covers `messages[0]` (~4,500 tokens of stable prefix) but never extends to `messages[1]` (~8,000 tokens of session-stable environment info) or the conversation prefix.

Root cause: the messages array contains two consecutive `role: "system"` messages — `messages[0]` (stable prefix: agreement.md + tool descriptions + separator) and `messages[1]` (dynamic env: OS, cwd, time, git, onyx_ai.md, hippocampus). DeepSeek's prefix cache implementation appears to reset at the boundary between consecutive same-role messages.

## Design

### Change: Merge two system messages into one

**Current** (`bin/ai_lib/api.py`, `call_ai_api_sse`, conversation mode branch):

```python
_messages = []
if memory_block:
    _messages.append({"role": "system", "content": memory_block})
_messages.extend(messages)
```

This produces:
```
[0] system: memory_block (~4,500 tokens, stable)
[1] system: env_info     (~8,000 tokens, session-stable) ← cache reset here
[2] user:   question
[3+] conversation ...
```

**Proposed**:

```python
_messages = []
if memory_block:
    if messages and messages[0].get("role") == "system":
        _merged = memory_block + "\n\n" + messages[0]["content"]
        _messages.append({"role": "system", "content": _merged})
        _messages.extend(messages[1:])
    else:
        _messages.append({"role": "system", "content": memory_block})
        _messages.extend(messages)
else:
    _messages.extend(messages)
```

This produces:
```
[0] system: memory_block + "\n\n" + env_info (~13,000 tokens, all stable) ← full cache hit
[1] user:   question
[2+] conversation ...
```

### Expected Outcome

- Cache hit rate in early rounds: 28% → 65%+
- Absolute cached tokens: 3,700-4,500 → 13,000+
- Still declines as conversation grows, but from a higher baseline

### Files Changed

- `bin/ai_lib/api.py`: ~10 lines in `call_ai_api_sse()`, conversation mode branch

### Compatibility

- Single-shot mode (`messages=None`): unaffected, uses different code path
- Non-standard message structures (`messages[0].role != "system"`): falls back to original logic
- Anthropic platform: Anthropic conversion already merges multiple system messages — no negative impact
- Separator marker `--- PROMPT SEPARATOR ---`: preserved in merged content, `strip_prompt_prefix()` still works

### Verification

1. Deploy to Android device
2. Run `ai --debug` with a multi-round conversation
3. Observe `💰 cache X% hit (N cached / M new)` — cached tokens should jump to ~13,000+
4. Confirm `🔍 载荷字节匹配` percentage improves
5. Confirm AI behavior unchanged (same system content, only structure differs)
6. Run `ai` on an unrelated task to confirm no regression
