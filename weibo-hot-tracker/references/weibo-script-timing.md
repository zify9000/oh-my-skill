# Weibo Script Timing & LLM Latency

## LLM Response Time

Qwen3.6-35B-A3B via local endpoint (`http://192.168.124.18:13080`):

- **Simple test** (short prompt, 20 tokens): ~2-3 seconds
- **Full hot band eval** (40 topics, 8192 max_tokens): ~48 seconds
- **Empty topics** (0 items, prompt ~175 chars): ~48 seconds (still full generation overhead)

## Why LLM is slow

1. **Local endpoint overhead**: GPU model loading/warmup on each call
2. **Prompt size**: 40 topics × metadata ≈ 1500+ tokens in prompt
3. **Output budget**: 8192 max_tokens reserved even for small output
4. **Model size**: 35B parameter model has inherent compute overhead

## Cron implications

- Script sets `timeout=300` on LLM call — sufficient but leaves no margin for retries
- Empty hot band (0 topics) still takes 48s — should check for empty list BEFORE calling LLM
- Pre-filter step runs in ~200ms — LLM call dominates total runtime

## Optimization opportunities

1. **Skip LLM if prefiltered is empty**: add `if not prefiltered: return` before LLM call
2. **Reduce max_tokens**: for evaluation task, 8192 is overkill — 2000 would suffice (prompt ≈1500, output ≈500)
3. **Parallel LLM calls**: if multiple topics, could batch differently
4. **Model caching**: ensure endpoint model is already loaded (not cold start on cron)

## Observed API behavior

When `finish_reason: length` (truncation), the custom endpoint returns `content=None` — not an error, just truncated output. Script should check for this:

```python
if content is None:
    # LLM generated but truncated — treat as failure, use fallback
    return None
```

This was observed with Qwen3.6-35B-A3B but may vary by model/provider.
