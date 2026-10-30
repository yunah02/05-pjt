from comments.services import llm
from comments.services.crawler import crawl_comments
from comments.services.preprocess import calc_iqr, rule_based_clean

# 1) 크롤링 (F603, F604)
name, raw = crawl_comments('삼성', headless=True)
print('종목명:', name, '| 원본:', len(raw))
print('IQR:', calc_iqr(raw))

# 2) 규칙 기반 전처리 (F605)
rule_cleaned = rule_based_clean(raw)
print('규칙 정제 후:', len(rule_cleaned))
for c in rule_cleaned:
    print('  -', c)

# 3) LLM 부적절 댓글 필터 (F605)
cleaned = llm.filter_inappropriate(rule_cleaned)
print('LLM 필터 후:', len(cleaned))

# 4) 증강 (F606)
augmented = llm.augment(cleaned)
print('증강:', len(augmented))
for a in augmented[:5]:
    print('  +', a)

# 5) 요약 (F610)
print('요약:', llm.summarize(raw))