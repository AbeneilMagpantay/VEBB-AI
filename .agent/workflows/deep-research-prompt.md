---
description: Rules for structuring optimal Gemini Deep Research prompts
---

# Deep Research Prompt Structure Rules

Follow these rules when creating any Deep Research prompt for Gemini.

## 1. Source Quality Block (ALWAYS include at top)

```markdown
## SOURCE QUALITY REQUIREMENT

**CRITICAL**: Prioritize sources in this order:
1. Peer-reviewed papers (SSRN, arXiv q-fin, Journal of Financial Econometrics, Quantitative Finance, SIAM Journal on Financial Mathematics)
2. Published textbooks (name specific titles relevant to the topic)
3. Working papers from quant research labs (Two Sigma, AQR, Man AHL, Oxford-Man Institute, CFM)
4. Exchange/regulator documentation (Binance API docs, CME whitepapers, SEC filings)
5. Open-source framework documentation (Nautilus Trader, CCXT Pro, etc.)

**BANNED SOURCES** — DO NOT cite under any circumstances:
- Medium blog posts
- TradingView scripts or indicator pages
- Reddit threads
- YouTube videos
- Generic SEO websites
- Patents unrelated to the topic
- Any URL that doesn't directly relate to the claim

**VERIFICATION RULE**: For each citation, confirm the paper's title and abstract directly relate to your claim. If you cannot verify relevance, omit the citation entirely. Say "no peer-reviewed source found" rather than citing irrelevant material.

**CITATION CAP**: Use no more than 20 high-quality sources. Quality over quantity. Every citation must directly support a specific claim made in the text.
```

## 2. Prompt Structure

Every prompt should follow this skeleton:

1. **Source Quality Block** (from above)
2. **Objective** — 2-3 sentences on what we need answered
3. **Context** — What the bot currently does (include code snippets, formulas, exact variable names)
4. **Research Questions** — Numbered, specific, answerable questions (not vague)
5. **Deliverables** — Exact outputs expected (formulas, pseudocode, recommendations, comparison tables)

## 3. Question Writing Rules

- **Be specific**: "What is the optimal lookback window for Yang-Zhang on 15m BTC candles?" NOT "How should we use Yang-Zhang?"
- **Include the current implementation**: Show the exact code/formula being replaced so the research can compare
- **Ask for trade-offs**: "What are the risks of X vs Y?" not just "Should we use X?"
- **Include a practical constraint question**: "Given that we operate on 15m candles on a $10/month VPS, does this approach actually matter?"
- **Force honesty**: "If no peer-reviewed evidence supports this claim, explicitly state so"

## 4. Anti-Hallucination Rules

Add these lines to every prompt:

```markdown
**HONESTY REQUIREMENTS**:
- If you cannot find academic evidence for a claim, say "no peer-reviewed source found" — do NOT fabricate citations
- If a recommendation is your inference rather than from literature, label it as "INFERENCE: [reasoning]"
- Do NOT pad the citation list with irrelevant papers to appear authoritative
- If two sources contradict each other, present BOTH views and explain the disagreement
```

## 5. Context Injection

Always give the Deep Research model the exact current state:

```markdown
## Current System Context
- Asset: BTC/USDT Perpetual Futures (Binance)
- Timeframe: 15-minute candles with tick-level microstructure
- Infrastructure: Python main loop + Rust data ingestor (shared memory IPC)
- Leverage: 50x
- VPS: Single $10/month server (not institutional infrastructure)
- Current implementation of [THING BEING RESEARCHED]:
  [paste exact code block or formula]
```

## 6. Output Format Request

Always end with:

```markdown
## Deliverables
1. [Specific output — formula, pseudocode, comparison table]
2. [Implementation recommendation with justification]
3. [Risk analysis / trade-offs]
4. Academic source list — peer-reviewed only, max 20 sources, each must directly support a claim
```

## 7. Common Mistakes to Avoid

- Don't ask open-ended essay questions ("Tell me about Hawkes processes") — ask specific design questions
- Don't forget to include the CURRENT code — without it, the research can't compare alternatives
- Don't skip the practical constraint — "does this even matter at our scale/timeframe?"
- Don't ask more than 15 questions — the model spreads itself too thin and quality drops
